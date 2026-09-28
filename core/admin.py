from django.contrib import admin

from .models import Category, Item


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("title", "payer_name", "category", "actual_amount", "invoice_amount", "status", "created_at")
    list_filter = ("status", "category")
    search_fields = ("title", "owner__username", "owner__first_name", "order_no", "invoice_no")
    list_editable = ("status",)
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="付款人")
    def payer_name(self, obj):
        return obj.payer_name

    @admin.action(description="标记为已通过")
    def approve_items(self, request, queryset):
        queryset.update(status=Item.STATUS_APPROVED)

    @admin.action(description="标记为已退回")
    def reject_items(self, request, queryset):
        queryset.update(status=Item.STATUS_REJECTED)

    @admin.action(description="标记为待审核")
    def reset_items(self, request, queryset):
        queryset.update(status=Item.STATUS_PENDING)

    actions = ("approve_items", "reject_items", "reset_items")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "order")
    list_editable = ("order",)
