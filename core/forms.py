from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Item


class SignUpForm(UserCreationForm):
    first_name = forms.CharField(
        label="真实姓名",
        max_length=30,
        help_text="将作为报销表中的「付款人」显示，请填写真实姓名",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data["first_name"]
        if commit:
            user.save()
        return user


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
