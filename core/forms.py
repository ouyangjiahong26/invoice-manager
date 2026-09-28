from django import forms

from .models import Item


class ItemForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = [
            "title",
            "category",
            "actual_amount",
            "invoice_amount",
            "invoice_no",
            "order_no",
            "invoice_file",
            "payment_screenshot",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "actual_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "invoice_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "invoice_no": forms.TextInput(attrs={"class": "form-control"}),
            "order_no": forms.TextInput(attrs={"class": "form-control"}),
        }
