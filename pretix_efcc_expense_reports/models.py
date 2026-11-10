from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class ExpenseReport(models.Model):
    STATUS_SUBMITTED = "submitted"
    STATUS_PAID = "paid"
    STATUS_DELETED = "deleted"
    STATUS_CHOICES = (
        (STATUS_SUBMITTED, _("Awaiting payment")),
        (STATUS_PAID, _("Paid")),
        (STATUS_DELETED, _("Deleted in QuickBooks")),
    )

    event = models.ForeignKey(
        "pretixbase.Event", on_delete=models.CASCADE, related_name="expense_reports"
    )
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created = models.DateTimeField(auto_now_add=True)
    date = models.DateField(verbose_name=_("Date of the expense"))
    payee = models.CharField(max_length=500)
    category = models.CharField(max_length=500)
    description = models.TextField(
        verbose_name=_("Description"),
        max_length=1000,
        help_text=_("What was bought, and why."),
    )
    amount = models.DecimalField(
        verbose_name=_("Amount"),
        max_digits=13,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text=_("Total paid, taxes included."),
    )
    # The QuickBooks company the bill lives in: bill ids are only unique within
    # one company, so a status refresh must never match them against another.
    realm_id = models.CharField(max_length=190)
    bill_id = models.CharField(max_length=190)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SUBMITTED)

    class Meta:
        ordering = ("-created",)
