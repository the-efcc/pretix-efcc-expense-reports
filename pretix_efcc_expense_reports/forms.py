from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _
from pretix.base.forms import SecretKeySettingsField, SettingsForm
from pretix.base.forms.widgets import DatePickerWidget
from pretix.control.forms import ExtFileField

from .models import ExpenseReport


class OrganizerSettingsForm(SettingsForm):
    plugin_efcc_expense_reports_client_id = forms.CharField(label=_("Client ID"), required=False)
    plugin_efcc_expense_reports_client_secret = SecretKeySettingsField(
        label=_("Client secret"), required=False
    )
    plugin_efcc_expense_reports_environment = forms.ChoiceField(
        label=_("Environment"),
        choices=(("production", _("Production")), ("sandbox", _("Sandbox"))),
        help_text=_("Must match the set of keys entered above."),
    )
    plugin_efcc_expense_reports_webhook_token = SecretKeySettingsField(
        label=_("Webhook verifier token"),
        help_text=_("Shown by Intuit once the webhook endpoint above is saved in the app."),
        required=False,
    )


class ExpenseReportForm(forms.ModelForm):
    payee = forms.ChoiceField(
        label=_("Pay to"),
        help_text=_(
            "The person or supplier to be paid. If they are missing, ask accounting "
            "to add them as a supplier in QuickBooks."
        ),
    )
    category = forms.ChoiceField(label=_("Category"))
    receipt = ExtFileField(
        label=_("Receipt"),
        ext_whitelist=(".pdf", ".png", ".jpg", ".jpeg"),
        max_size=10 * 1024 * 1024,
        help_text=_("Invoice or receipt, as a PDF or a picture (10 MB at most)."),
    )

    class Meta:
        model = ExpenseReport
        fields = ("date", "payee", "category", "description", "amount", "receipt")
        widgets = {
            "date": DatePickerWidget(),
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, vendors, accounts, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["payee"].choices = [("", "---------"), *vendors]
        self.fields["category"].choices = [("", "---------"), *accounts]
