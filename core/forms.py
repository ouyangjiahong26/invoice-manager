from django import forms
from django.contrib.auth.models import User

from .models import Attachment, Category, Item


class ItemPanelForm(forms.ModelForm):
    """侧边栏新建/编辑表单；非 staff 剔除审核状态与付款人字段。"""

    class Meta:
        model = Item
        fields = ["title", "category", "actual_amount", "invoice_amount", "status", "owner"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "actual_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "invoice_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
        }
        help_texts = {
            "title": "买了什么，如：夸克网盘会员、DeepSeek API 词元；多本书用顿号连接书名",
            "actual_amount": "自己实际掏的钱 = 支付合计 − 退款。有优惠/立减按实付填"
                             "（发票 19.90、花呗立减后实付 19.72 → 填 19.72）",
            "invoice_amount": "发票票面价税合计；多张发票会自动加总，也可手动修改",
        }

    def __init__(self, *args, staff=False, **kwargs):
        super().__init__(*args, **kwargs)
        notes = [
            f"{c.name}：{c.description}"
            for c in Category.objects.all()
            if c.description
        ]
        self.fields["category"].help_text = "；".join(notes)
        self.fields["owner"] = forms.ModelChoiceField(
            queryset=User.objects.filter(is_active=True),
            label="付款人",
        )
        if not staff:
            del self.fields["status"]
            del self.fields["owner"]


class AttachmentForm(forms.ModelForm):
    class Meta:
        model = Attachment
        fields = ["kind", "amount", "order_no", "merchant_no", "invoice_no"]
        widgets = {
            "kind": forms.Select(attrs={"class": "form-control"}),
            "amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "order_no": forms.TextInput(attrs={"class": "form-control"}),
            "merchant_no": forms.TextInput(attrs={"class": "form-control"}),
            "invoice_no": forms.TextInput(attrs={"class": "form-control"}),
        }
