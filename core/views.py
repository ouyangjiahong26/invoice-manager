import json
import zipfile
from decimal import Decimal, InvalidOperation
from io import BytesIO
from itertools import groupby

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from openpyxl import Workbook
from openpyxl.styles import Font

from .audit import attachment_entries, attachment_entry, mark_attachments
from .forms import AttachmentForm, ItemForm
from .models import Attachment, Category, Item
from .validation import check_item
from .vision import prefill as vision_prefill, validate_upload

STATUS_FILTERS = {Item.STATUS_PENDING, Item.STATUS_APPROVED, Item.STATUS_REJECTED}
AMOUNT_FORMAT = "0.00_ "
KIND_LABELS = dict(Attachment.KIND_CHOICES)

# 上传文件字段名、POST 字段前缀、附件类型 三元组
UPLOAD_FIELDS = (
    ("invoices", "new_invoice", Attachment.KIND_INVOICE),
    ("payments", "new_payment", Attachment.KIND_PAYMENT),
    ("refunds", "new_refund", Attachment.KIND_REFUND),
)


@login_required
def board(request):
    status = request.GET.get("status", "")
    items = Item.objects.select_related("owner", "category").prefetch_related("attachments")
    if status in STATUS_FILTERS:
        items = items.filter(status=status)
    by_category = {c.id: [] for c in Category.objects.all()}
    for item in items:
        by_category[item.category_id].append(item)
    groups = [(c, by_category[c.id]) for c in Category.objects.all()]
    return render(request, "core/board.html", {"groups": groups, "status": status})


@login_required
def item_create(request):
    if request.method == "POST":
        form = ItemForm(request.POST, request.FILES)
        attachments, errors = _collect_new_attachments(request)
        if form.is_valid() and not errors:
            item = form.save(commit=False)
            item.owner = request.user
            mark_attachments(item, [attachment_entry(a) for a in attachments], [])
            item.save()
            for attachment in attachments:
                attachment.item = item
                attachment.save()
            for warning in check_item(item):
                messages.warning(request, warning)
            messages.success(request, "已提交，等待管理员审核。")
            return redirect("board")
        for error in errors:
            form.add_error(None, error)
    else:
        form = ItemForm()
    return render(request, "core/item_form.html", {"form": form, "is_create": True})


@login_required
def item_update(request, pk):
    item = get_object_or_404(Item, pk=pk)
    if item.owner_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied("只能修改自己的条目")
    audit_logs = item.audit_logs.all()
    if request.method == "POST":
        form = ItemForm(request.POST, request.FILES, instance=item)
        attachments, errors = _collect_new_attachments(request)
        if form.is_valid() and not errors:
            before = attachment_entries(item)
            item = form.save(commit=False)
            mark_attachments(item, before + [attachment_entry(a) for a in attachments], before)
            item.save()
            for attachment in attachments:
                attachment.item = item
                attachment.save()
            for warning in check_item(item):
                messages.warning(request, warning)
            messages.success(request, "已更新。")
            return redirect("board")
        for error in errors:
            form.add_error(None, error)
    else:
        form = ItemForm(instance=item)
    return render(request, "core/item_form.html", {
        "form": form,
        "item": item,
        "is_create": False,
        "audit_logs": audit_logs,
        "attachment_groups": _attachment_groups(item),
    })


@login_required
@require_POST
def item_delete(request, pk):
    item = get_object_or_404(Item, pk=pk)
    if item.owner_id != request.user.id:
        raise PermissionDenied("只能删除自己的条目")
    mark_attachments(item, attachment_entries(item), [])
    item.delete()
    messages.success(request, "已删除。")
    return redirect("board")


@login_required
def file_serve(request, pk):
    attachment = get_object_or_404(Attachment, pk=pk)
    return FileResponse(attachment.file.open("rb"), as_attachment=False)


@login_required
@require_POST
def attachment_update(request, pk):
    attachment = _owned_attachment(request, pk)
    item = attachment.item
    before = attachment_entries(item)
    form = AttachmentForm(request.POST, instance=attachment)
    if form.is_valid():
        form.save()
        mark_attachments(item, attachment_entries(item), before)
        item.save()
        messages.success(request, "附件已更新。")
    else:
        messages.error(request, f"附件保存失败：{next(iter(form.errors.values()))[0]}")
    return redirect("item_update", pk=item.pk)


@login_required
@require_POST
def attachment_delete(request, pk):
    attachment = _owned_attachment(request, pk)
    item = attachment.item
    before = attachment_entries(item)
    attachment.file.delete(save=False)
    attachment.delete()
    mark_attachments(item, attachment_entries(item), before)
    item.save()
    messages.success(request, "已删除附件。")
    return redirect("item_update", pk=item.pk)


@require_POST
@login_required
def prefill(request):
    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse({"error": "缺少 file 字段"}, status=400)
    kind = request.POST.get("kind", "")
    if kind not in KIND_LABELS:
        return JsonResponse({"error": "kind 必须是 invoice/payment/refund"}, status=400)
    error = validate_upload(upload)
    if error:
        return JsonResponse({"error": error}, status=400)
    return JsonResponse(vision_prefill(upload.read(), upload.name, kind))


def _owned_attachment(request, pk):
    attachment = get_object_or_404(Attachment, pk=pk)
    if attachment.item.owner_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied("只能修改自己的条目")
    return attachment


def _attachment_groups(item):
    """编辑页用：按 kind 汇总已保存附件。"""
    groups = {kind: [] for kind in KIND_LABELS}
    for attachment in item.attachments.all():
        groups[attachment.kind].append(attachment)
    return groups


def _collect_new_attachments(request):
    """从 POST 的文件与同名前缀字段构造未保存的 Attachment；返回 (附件, 错误文案)。"""
    attachments, errors = [], []
    for field, prefix, kind in UPLOAD_FIELDS:
        for index, upload in enumerate(request.FILES.getlist(field)):
            error = validate_upload(upload)
            if error:
                errors.append(f"{upload.name}：{error}")
                continue
            try:
                attachments.append(_new_attachment(request, upload, prefix, index, kind))
            except ValueError as exc:
                errors.append(f"{upload.name}：{exc}")
    return attachments, errors


def _new_attachment(request, upload, prefix, index, kind):
    def value(name):
        return (request.POST.get(f"{prefix}_{index}_{name}") or "").strip()

    attachment = Attachment(kind=kind, file=upload)
    raw_amount = value("amount")
    if raw_amount:
        try:
            attachment.amount = Decimal(raw_amount)
        except InvalidOperation:
            raise ValueError("金额格式不正确")
        if attachment.amount < 0:
            raise ValueError("金额不能为负")
    attachment.order_no = value("order_no")[:64]
    attachment.merchant_no = value("merchant_no")[:64]
    attachment.invoice_no = value("invoice_no")[:50]
    raw_ocr = value("ocr")
    if raw_ocr:
        try:
            attachment.ocr_data = json.loads(raw_ocr)
        except ValueError:
            attachment.ocr_data = None
    return attachment


def _staff(user):
    return user.is_staff


def _approved_by_category():
    """approved 条目，按类别顺序 + 提交时间排序，返回 [(category, items)]。"""
    approved = (
        Item.objects.filter(status=Item.STATUS_APPROVED)
        .select_related("owner", "category")
        .prefetch_related("attachments")
        .order_by("category__order", "category__id", "created_at")
    )
    return [
        (category, list(group))
        for category, group in groupby(approved, key=lambda item: item.category)
    ]


@user_passes_test(_staff)
def export_excel(request):
    wb = Workbook()
    ws = wb.active
    ws.title = "报销明细"
    header = ["序号", "明细", "类别", "实付款", "付款人", "支付记录", "票据"]
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    seq = 0
    subtotal_rows = []
    for category, items in _approved_by_category():
        start = None
        for item in items:
            seq += 1
            ws.append([
                seq,
                item.title,
                category.name,
                float(item.actual_amount),
                item.payer_name,
                "已整理",
                "已整理",
            ])
            row = ws.max_row
            amount_cell = ws.cell(row=row, column=4)
            amount_cell.number_format = AMOUNT_FORMAT
            # 多张附件时超链接指向首张，完整材料走 zip 导出（ADR-0005）
            if item.first_payment is not None:
                ws.cell(row=row, column=6).hyperlink = request.build_absolute_uri(
                    reverse("file_serve", args=[item.first_payment.pk])
                )
            if item.first_invoice is not None:
                ws.cell(row=row, column=7).hyperlink = request.build_absolute_uri(
                    reverse("file_serve", args=[item.first_invoice.pk])
                )
            if start is None:
                start = row
        ws.append(["小记", None, None, f"=SUM(D{start}:D{ws.max_row})", None, None, None])
        subtotal_row = ws.max_row
        ws.cell(row=subtotal_row, column=4).number_format = AMOUNT_FORMAT
        subtotal_rows.append(subtotal_row)

    if subtotal_rows:
        ws.append(["合计", None, None, "=" + "+".join(f"D{r}" for r in subtotal_rows), None, None, None])
        ws.cell(row=ws.max_row, column=4).number_format = AMOUNT_FORMAT

    widths = {"A": 6, "B": 40, "C": 12, "D": 10, "E": 10, "F": 10, "G": 10}
    for column, width in widths.items():
        ws.column_dimensions[column].width = width

    buffer = BytesIO()
    wb.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="报销汇总.xlsx"'
    return response


@user_passes_test(_staff)
def export_zip(request):
    buffer = BytesIO()
    seen = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        seq = 0
        for category, items in _approved_by_category():
            for item in items:
                seq += 1  # 与 Excel"序号"列一致（全局连续）
                counters = {}
                for attachment in item.attachments.all():
                    kind_label = KIND_LABELS[attachment.kind]
                    counters[kind_label] = counters.get(kind_label, 0) + 1
                    extension = attachment.file.name.rsplit(".", 1)[-1]
                    name = (
                        f"{category.name}_{seq:02d}_{item.payer_name}"
                        f"_{kind_label}_{counters[kind_label]}.{extension}"
                    )
                    if name in seen:
                        name = (
                            f"{category.name}_{seq:02d}_{item.payer_name}"
                            f"_{kind_label}_{counters[kind_label]}_{item.pk}.{extension}"
                        )
                    seen.add(name)
                    zf.writestr(name, attachment.file.read())
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="报销材料.zip"'
    return response
