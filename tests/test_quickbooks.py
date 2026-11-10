import base64
import datetime
import hashlib
import hmac
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from pretix_efcc_expense_reports import quickbooks
from pretix_efcc_expense_reports.models import ExpenseReport
from pretix_efcc_expense_reports.tasks import refresh_statuses


def _create_bill(organizer):
    return quickbooks.create_bill(
        organizer,
        vendor_id="5",
        account_id="80",
        amount=Decimal("12.50"),
        currency="EUR",
        date=datetime.date(2026, 11, 1),
        description="Train",
        memo="memo",
        receipt=SimpleUploadedFile("Reçu.PDF", b"%PDF-1.4"),
    )


def test_create_bill_sends_the_bill_and_its_receipt(organizer, qbo):
    assert _create_bill(organizer) == "151"

    token_call = qbo.called("token")[0]
    assert token_call["data"] == {"grant_type": "refresh_token", "refresh_token": "refresh-1"}
    assert token_call["auth"] == ("client-id", "client-secret")
    # The rotated refresh token is the only one that will work next time.
    assert quickbooks.get_setting(organizer, "refresh_token") == "refresh-2"

    (bill_call,) = qbo.called("bill")
    assert bill_call["headers"]["Authorization"] == "Bearer access"
    bill = bill_call["json"]
    assert bill["VendorRef"] == {"value": "5"}
    assert bill["TxnDate"] == "2026-11-01"
    assert bill["CurrencyRef"] == {"value": "EUR"}
    assert bill["Line"][0]["Amount"] == 12.5
    assert bill["Line"][0]["AccountBasedExpenseLineDetail"] == {"AccountRef": {"value": "80"}}

    files = qbo.called("upload")[0]["files"]
    assert '"type": "Bill", "value": "151"' in files["file_metadata_01"][1]
    assert files["file_content_01"][0] == "receipt-2026-11-01.pdf"
    assert files["file_content_01"][2] == "application/pdf"


def test_create_bill_removes_the_bill_when_the_receipt_is_refused(organizer, qbo):
    qbo.refuse.add("upload")

    with pytest.raises(quickbooks.QuickBooksError, match="Detailed nope"):
        _create_bill(organizer)

    delete_call = qbo.called("bill")[-1]
    assert delete_call["params"] == {"operation": "delete"}
    assert delete_call["json"] == {"Id": "151", "SyncToken": "0"}


def test_not_connected_is_reported_without_calling_quickbooks(organizer, qbo):
    organizer.settings.delete(quickbooks.SETTINGS_PREFIX + "refresh_token")

    with pytest.raises(quickbooks.QuickBooksError, match="not connected"):
        quickbooks.vendors(organizer)

    assert qbo.calls == []


def test_choices_are_sorted_and_only_offer_expense_accounts(organizer, qbo):
    assert quickbooks.vendors(organizer) == [("5", "Pat Manager"), ("7", "Zoe Supplier")]
    assert quickbooks.expense_accounts(organizer) == [("80", "Travel")]


def test_webhook_signature(organizer):
    body = b'[{"type": "qbo.bill.updated.v1"}]'
    signature = base64.b64encode(hmac.new(b"verifier", body, hashlib.sha256).digest()).decode()

    assert quickbooks.is_valid_webhook(organizer, body, signature)
    assert not quickbooks.is_valid_webhook(organizer, body + b" ", signature)
    assert not quickbooks.is_valid_webhook(organizer, body, "")
    assert not quickbooks.is_valid_webhook(organizer, body, "sigñature")

    organizer.settings.delete(quickbooks.SETTINGS_PREFIX + "webhook_token")
    empty_key = base64.b64encode(hmac.new(b"", body, hashlib.sha256).digest()).decode()
    assert not quickbooks.is_valid_webhook(organizer, body, empty_key)


def test_refresh_statuses(organizer, qbo, make_report):
    still_open = make_report(bill_id="1")
    paid = make_report(bill_id="2")
    deleted = make_report(bill_id="3")
    # Same bill id in a company the organizer is no longer connected to.
    other_company = make_report(bill_id="2", realm_id="realm-0")
    qbo.balances = {"1": 30.0, "2": 0}

    refresh_statuses(organizer.pk)

    def status(report):
        return ExpenseReport.objects.get(pk=report.pk).status

    assert status(still_open) == ExpenseReport.STATUS_SUBMITTED
    assert status(paid) == ExpenseReport.STATUS_PAID
    assert status(deleted) == ExpenseReport.STATUS_DELETED
    assert status(other_company) == ExpenseReport.STATUS_SUBMITTED
