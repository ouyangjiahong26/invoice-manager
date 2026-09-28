from io import BytesIO
from itertools import groupby

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from openpyxl import Workbook
from openpyxl.styles import Font

import zipfile

from .forms import ItemForm
from .models import Category, Item
from .validation import check_item

STATUS_FILTERS = {Item.STATUS_PENDING, Item.STATUS_APPROVED, Item.STATUS_REJECTED}
AMOUNT_FORMAT = "0.00_ "


@login_required
def board(request):
    status = request.GET.get("status", "")
    items = Item.objects.select_related("owner", "category")
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
        if form.is_valid():
            item = form.save(commit=False)
            item.owner = request.user
            item.save()
            for warning in check_item(item):
                messages.warning(request, warning)
            messages.success(request, "已提交，等待管理员审核。")
            return redirect("board")
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
        if form.is_valid():
            item = form.save()
            for warning in check_item(item):
                messages.warning(request, warning)
            messages.success(request, "已更新。")
            return redirect("board")
    else:
        form = ItemForm(instance=item)
    return render(request, "core/item_form.html",
                  {"form": form, "item": item, "is_create": False, "audit_logs": audit_logs})


@login_required
@require_POST
def item_delete(request, pk):
    item = get_object_or_404(Item, pk=pk)
    if item.owner_id != request.user.id:
        raise PermissionDenied("只能删除自己的条目")
    item.delete()
    messages.success(request, "已删除。")
    return redirect("board")


@login_required
def file_serve(request, pk, kind):
    item = get_object_or_404(Item, pk=pk)
    fields = {"invoice": item.invoice_file, "payment": item.payment_screenshot}
    if kind not in fields or not fields[kind]:
        raise Http404
    return FileResponse(fields[kind].open("rb"))


def _staff(user):
    return user.is_staff


def _approved_by_category():
    """approved 条目，按类别顺序 + 提交时间排序，返回 [(category, items)]。"""
    approved = (
        Item.objects.filter(status=Item.STATUS_APPROVED)
        .select_related("owner", "category")
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
    header = ["序号", "明细", "类别", "实付款", "付款人", "支付截图", "票据"]
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
            ws.cell(row=row, column=6).hyperlink = request.build_absolute_uri(
                reverse("file_serve", args=[item.pk, "payment"])
            )
            ws.cell(row=row, column=7).hyperlink = request.build_absolute_uri(
                reverse("file_serve", args=[item.pk, "invoice"])
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
                for kind, field in (("invoice", item.invoice_file), ("payment", item.payment_screenshot)):
                    if not field:
                        continue
                    extension = field.name.rsplit(".", 1)[-1]
                    name = f"{category.name}_{seq:02d}_{item.payer_name}_{kind}.{extension}"
                    if name in seen:
                        name = f"{category.name}_{seq:02d}_{item.payer_name}_{kind}_{item.pk}.{extension}"
                    seen.add(name)
                    zf.writestr(name, field.read())
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="报销材料.zip"'
    return response
