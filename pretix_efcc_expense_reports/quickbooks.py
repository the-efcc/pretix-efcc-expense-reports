"""Everything that talks to QuickBooks Online: OAuth 2.0, the accounting API, webhooks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
from contextlib import suppress
from decimal import Decimal
from pathlib import PurePath
from urllib.parse import urlencode

import requests
from django.core.cache import cache
from django.utils.translation import gettext as _

SETTINGS_PREFIX = "plugin_efcc_expense_reports_"

AUTHORIZE_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
SCOPE = "com.intuit.quickbooks.accounting"
API_URLS = {
    "production": "https://quickbooks.api.intuit.com",
    "sandbox": "https://sandbox-quickbooks.api.intuit.com",
}
TIMEOUT = 20


class QuickBooksError(Exception):
    """Its message is written for, and safe to show to, the person using pretix."""


def get_setting(organizer, key: str) -> str:
    return organizer.settings.get(SETTINGS_PREFIX + key, default="") or ""


def authorize_url(organizer, redirect_uri: str, state: str) -> str:
    return (
        AUTHORIZE_URL
        + "?"
        + urlencode(
            {
                "client_id": get_setting(organizer, "client_id"),
                "response_type": "code",
                "scope": SCOPE,
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )
    )


def connect(organizer, code: str, realm_id: str, redirect_uri: str) -> None:
    _fetch_tokens(organizer, grant_type="authorization_code", code=code, redirect_uri=redirect_uri)
    organizer.settings.set(SETTINGS_PREFIX + "realm_id", realm_id)


def _token_cache_key(organizer) -> str:
    return f"{SETTINGS_PREFIX}access_token_{organizer.pk}"


def _fetch_tokens(organizer, **grant: str) -> str:
    try:
        response = requests.post(
            TOKEN_URL,
            data=grant,
            auth=(get_setting(organizer, "client_id"), get_setting(organizer, "client_secret")),
            headers={"Accept": "application/json"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise QuickBooksError(_("QuickBooks could not be reached, please try again.")) from exc
    if not response.ok:
        raise QuickBooksError(
            _(
                "QuickBooks refused the connection. An organizer administrator has to "
                "connect to QuickBooks again from the organizer's expense report settings."
            )
        )
    tokens = response.json()
    # QuickBooks swaps the refresh token for a new one every so often, and only
    # the latest one keeps working.
    organizer.settings.set(SETTINGS_PREFIX + "refresh_token", tokens["refresh_token"])
    # shortcut: the access token only lives in pretix' cache, so without redis
    # (pretix then has a dummy cache) every API call fetches a new one first;
    # store it in the organizer settings if this ever runs without redis.
    cache.set(_token_cache_key(organizer), tokens["access_token"], tokens["expires_in"] - 60)
    return tokens["access_token"]


def _access_token(organizer) -> str:
    token = cache.get(_token_cache_key(organizer))
    if token:
        return token
    refresh_token = get_setting(organizer, "refresh_token")
    if not refresh_token:
        raise QuickBooksError(
            _(
                "QuickBooks is not connected yet. An organizer administrator has to "
                "connect it from the organizer's expense report settings."
            )
        )
    return _fetch_tokens(organizer, grant_type="refresh_token", refresh_token=refresh_token)


def _fault_message(payload: dict) -> str:
    # {"Fault": {"Error": [{"Message": ..., "Detail": ...}]}}, with lower-case
    # keys when the failure is about authentication.
    fault = payload.get("Fault") or payload.get("fault") or {}
    error = (fault.get("Error") or fault.get("error") or [{}])[0]
    return (
        error.get("Detail")
        or error.get("detail")
        or error.get("Message")
        or error.get("message")
        or ""
    )


def _request(organizer, method: str, path: str, **kwargs) -> dict:
    base_url = API_URLS.get(get_setting(organizer, "environment"), API_URLS["production"])
    url = f"{base_url}/v3/company/{get_setting(organizer, 'realm_id')}/{path}"
    headers = {"Authorization": f"Bearer {_access_token(organizer)}", "Accept": "application/json"}
    try:
        response = requests.request(method, url, headers=headers, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise QuickBooksError(_("QuickBooks could not be reached, please try again.")) from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not response.ok:
        raise QuickBooksError(
            _("QuickBooks answered with an error: {error}").format(
                error=_fault_message(payload) or f"HTTP {response.status_code}"
            )
        )
    return payload


def query(organizer, entity: str, where: str = "") -> list[dict]:
    # shortcut: a single page of 1000 rows, the API's maximum; paginate with
    # STARTPOSITION if the vendor list or chart of accounts ever outgrows it.
    statement = f"select * from {entity} {where} maxresults 1000"
    response = _request(organizer, "GET", "query", params={"query": statement})
    return response.get("QueryResponse", {}).get(entity, [])


def vendors(organizer) -> list[tuple[str, str]]:
    return sorted(
        ((v["Id"], v["DisplayName"]) for v in query(organizer, "Vendor")),
        key=lambda choice: choice[1].lower(),
    )


def expense_accounts(organizer) -> list[tuple[str, str]]:
    return sorted(
        (
            (a["Id"], a.get("FullyQualifiedName") or a["Name"])
            for a in query(organizer, "Account")
            if a.get("Classification") == "Expense"
        ),
        key=lambda choice: choice[1].lower(),
    )


def create_bill(
    organizer,
    *,
    vendor_id: str,
    account_id: str,
    amount: Decimal,
    currency: str,
    date,
    description: str,
    memo: str,
    receipt,
) -> str:
    """Create an unpaid bill with its receipt attached and return the bill's id."""
    bill = _request(
        organizer,
        "POST",
        "bill",
        json={
            "VendorRef": {"value": vendor_id},
            "TxnDate": date.isoformat(),
            "CurrencyRef": {"value": currency},
            # shortcut: the amount is booked as paid, with no VAT split. Non-US
            # QuickBooks companies require this field; switch to "TaxInclusive"
            # plus a TaxCodeRef on the line once VAT has to be recovered, and
            # drop the field for a US company.
            "GlobalTaxCalculation": "NotApplicable",
            "PrivateNote": memo,
            "Line": [
                {
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "Amount": float(amount),
                    "Description": description,
                    "AccountBasedExpenseLineDetail": {"AccountRef": {"value": account_id}},
                }
            ],
        },
    )["Bill"]
    try:
        _attach_receipt(organizer, bill["Id"], receipt, f"receipt-{date.isoformat()}")
    except QuickBooksError:
        # Nothing is recorded in pretix when this raises, so the report will be
        # submitted again: don't leave a first, receipt-less copy of the bill.
        with suppress(QuickBooksError):
            _request(
                organizer,
                "POST",
                "bill",
                params={"operation": "delete"},
                json={"Id": bill["Id"], "SyncToken": bill["SyncToken"]},
            )
        raise
    return bill["Id"]


def _attach_receipt(organizer, bill_id: str, receipt, name: str) -> None:
    # An ASCII name of our own: QuickBooks does not reliably decode the
    # multipart encoding of accented file names.
    filename = name + PurePath(receipt.name).suffix.lower()
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    metadata = {
        "AttachableRef": [{"EntityRef": {"type": "Bill", "value": bill_id}}],
        "FileName": filename,
        "ContentType": content_type,
    }
    receipt.seek(0)  # form validation has already read the file
    response = _request(
        organizer,
        "POST",
        "upload",
        files={
            "file_metadata_01": ("attachment.json", json.dumps(metadata), "application/json"),
            "file_content_01": (filename, receipt, content_type),
        },
    )
    # A rejected file still gets an HTTP 200, with the fault in its own result.
    result = (response.get("AttachableResponse") or [{}])[0]
    if "Attachable" not in result:
        raise QuickBooksError(
            _("QuickBooks did not accept the receipt: {error}").format(
                error=_fault_message(result) or _("unknown error")
            )
        )


def bill_balances(organizer, bill_ids: list[str]) -> dict[str, Decimal]:
    """Map each bill that still exists to what is left to pay on it."""
    ids = ", ".join(f"'{bill_id}'" for bill_id in bill_ids)
    return {
        bill["Id"]: Decimal(str(bill["Balance"]))
        for bill in query(organizer, "Bill", f"where Id in ({ids})")
    }


def is_valid_webhook(organizer, body: bytes, signature: str) -> bool:
    verifier_token = get_setting(organizer, "webhook_token")
    if not verifier_token or not signature:
        return False
    expected = hmac.new(verifier_token.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(expected), signature.encode())
