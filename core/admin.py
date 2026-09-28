from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from .models import AuditLog, Category, Item


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("title", "payer_name", "category", "actual_amount", "invoice_amount", "status", "created_at")
    list_filter = ("status", "category")
    search_fields = (
        "title", "owner__username", "owner__first_name",
        "attachments__order_no", "attachments__merchant_no", "attachments__invoice_no",
    )
    list_editable = ("status",)
    readonly_fields = ("created_at", "updated_at", "attachments_display")

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("attachments")

    @admin.display(description="附件")
    def attachments_display(self, obj):
        rows = format_html_join(
            "<br>",
            '<a href="{}">{} {} {}</a>',
            (
                (
                    reverse("file_serve", args=[a.pk]),
                    a.get_kind_display(),
                    a.amount if a.amount is not None else "",
                    a.order_no or a.merchant_no or a.invoice_no or "",
                )
                for a in obj.attachments.all()
            ),
        )
        return rows or "无"

    class AuditLogInline(admin.TabularInline):
        model = AuditLog
        extra = 0
        can_delete = False
        fields = ("created_at", "actor_name", "action", "summary")
        readonly_fields = fields

        @admin.display(description="明细")
        def summary(self, obj):
            return "；".join(f"{label}: {text}" for label, text in obj.detail_rows())

        def has_add_permission(self, request, obj=None):
            return False

    inlines = (AuditLogInline,)

    @admin.display(description="付款人")
    def payer_name(self, obj):
        return obj.payer_name

    @admin.action(description="标记为已通过")
    def approve_items(self, request, queryset):
        for obj in queryset:
            obj.status = Item.STATUS_APPROVED
            obj.save()

    @admin.action(description="标记为已退回")
    def reject_items(self, request, queryset):
        for obj in queryset:
            obj.status = Item.STATUS_REJECTED
            obj.save()

    @admin.action(description="标记为待审核")
    def reset_items(self, request, queryset):
        for obj in queryset:
            obj.status = Item.STATUS_PENDING
            obj.save()

    actions = ("approve_items", "reject_items", "reset_items")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor_name", "action", "item_repr", "summary")
    list_filter = ("action",)
    search_fields = ("actor_name", "item__title")

    @admin.display(description="动作")
    def action(self, obj):
        return obj.get_action_display()

    @admin.display(description="条目")
    def item_repr(self, obj):
        if obj.item is not None:
            url = f"../../item/{obj.item.pk}/change/"
            return format_html('<a href="{}">#{}</a>', url, obj.item.pk)
        return f"#{obj.item_pk}（已删除）"

    @admin.display(description="明细")
    def summary(self, obj):
        text = "；".join(f"{label}: {text}" for label, text in obj.detail_rows())
        return text[:80]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "order")
    list_editable = ("order",)
    search_fields = ("name", "description")
