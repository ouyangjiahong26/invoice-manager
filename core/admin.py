from django import forms
from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group, User
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from .models import AuditLog, Batch, Category, GroupSettings, Item


class GroupAdminForm(forms.ModelForm):
    """组页面直接维护成员，省得逐个进用户页勾选。"""

    members = forms.ModelMultipleChoiceField(
        queryset=User.objects.order_by("first_name", "username"),
        required=False,
        label="成员",
        widget=forms.SelectMultiple(attrs={"size": 12}),
    )
    self_service = forms.BooleanField(
        required=False,
        label="成员可自建批次并审批自己的批次",
        help_text="开启后，组成员可以创建批次，并审批自己创建批次中的条目",
    )

    class Meta:
        model = Group
        fields = ("name",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["members"].initial = self.instance.user_set.all()
            settings_obj = getattr(self.instance, "settings", None)
            self.fields["self_service"].initial = bool(
                settings_obj and settings_obj.self_service
            )

    def _save_m2m(self):
        super()._save_m2m()
        if self.instance.pk:
            self.instance.user_set.set(self.cleaned_data["members"])
            GroupSettings.objects.update_or_create(
                group=self.instance,
                defaults={"self_service": self.cleaned_data["self_service"]},
            )


admin.site.unregister(Group)


@admin.register(Group)
class MemberGroupAdmin(GroupAdmin):
    form = GroupAdminForm
    list_display = ("name", "member_count", "batch_count")
    fields = ("name", "members", "self_service")
    filter_horizontal = ()
    search_fields = ("name",)
    ordering = ("id",)

    @admin.display(description="成员数")
    def member_count(self, obj):
        return obj.user_set.count()

    @admin.display(description="批次")
    def batch_count(self, obj):
        return obj.batches.count()


admin.site.unregister(User)


@admin.register(User)
class MemberUserAdmin(UserAdmin):
    list_display = ("username", "first_name", "group_names", "is_active", "is_staff")
    list_filter = ("groups", "is_active", "is_staff", "is_superuser")

    @admin.display(description="组")
    def group_names(self, obj):
        return "、".join(obj.groups.values_list("name", flat=True)) or "—"


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("title", "payer_name", "category", "group", "batch", "actual_amount", "invoice_amount", "status", "created_at")
    list_filter = ("status", "category", "batch__group", "batch")
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

    @admin.display(description="组")
    def group(self, obj):
        return obj.batch.group.name

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


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ("name", "group", "created_by", "created_at", "archived", "item_count")
    list_filter = ("group", "archived")
    list_editable = ("archived",)
    list_select_related = ("group", "created_by")
    search_fields = ("name", "created_by__username", "created_by__first_name")

    @admin.display(description="条目数")
    def item_count(self, obj):
        return obj.items.count()
