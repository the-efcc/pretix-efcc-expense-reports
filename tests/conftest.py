import datetime
from decimal import Decimal

import pytest
import requests
from django.test import Client
from django.utils.timezone import now
from django_scopes import scopes_disabled
from pretix.base.models import Event, Organizer, Team, User

from pretix_efcc_expense_reports import quickbooks
from pretix_efcc_expense_reports.models import ExpenseReport

PLUGIN = "pretix_efcc_expense_reports"


@pytest.fixture(autouse=True)
def disable_scopes(db):
    with scopes_disabled():
        yield


@pytest.fixture
def organizer():
    organizer = Organizer.objects.create(name="Dummy", slug="dummy", plugins=PLUGIN)
    for key, value in {
        "client_id": "client-id",
        "client_secret": "client-secret",
        "refresh_token": "refresh-1",
        "realm_id": "realm-1",
        "webhook_token": "verifier",
    }.items():
        organizer.settings.set(quickbooks.SETTINGS_PREFIX + key, value)
    return organizer


@pytest.fixture
def event(organizer):
    return Event.objects.create(
        organizer=organizer, name="Dummy", slug="dummy", date_from=now(), plugins=PLUGIN
    )


@pytest.fixture
def user(organizer):
    user = User.objects.create_user("pm@example.org", "dummypassword123", fullname="Pat Manager")
    team = Team.objects.create(
        organizer=organizer,
        all_events=True,
        all_event_permissions=True,
        all_organizer_permissions=True,
    )
    team.members.add(user)
    return user


@pytest.fixture
def client(user):
    client = Client()
    assert client.login(email=user.email, password="dummypassword123")
    return client


@pytest.fixture
def make_report(event, user):
    def make(**kwargs):
        defaults = {
            "event": event,
            "submitted_by": user,
            "date": datetime.date(2026, 11, 1),
            "payee": "Pat Manager",
            "category": "Travel",
            "description": "Train",
            "amount": Decimal("30.00"),
            "realm_id": "realm-1",
            "bill_id": "1",
        }
        return ExpenseReport.objects.create(**{**defaults, **kwargs})

    return make


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.ok = status_code < 400

    def json(self):
        return self.payload


class FakeQuickBooks:
    """Stands in for the `requests` module and answers like QuickBooks would."""

    RequestException = requests.RequestException

    def __init__(self):
        self.calls = []  # (method, last path segment, kwargs)
        self.balances = {}  # bill id -> what is left to pay
        self.refuse = set()  # path segments answered with a fault

    def post(self, url, **kwargs):  # only the token endpoint is called this way
        assert url == quickbooks.TOKEN_URL
        self.calls.append(("POST", "token", kwargs))
        return FakeResponse(
            {"access_token": "access", "refresh_token": "refresh-2", "expires_in": 3600}
        )

    def request(self, method, url, **kwargs):
        path = url.rsplit("/", 1)[1]
        self.calls.append((method, path, kwargs))
        fault = {"Fault": {"Error": [{"Message": "Nope", "Detail": "Detailed nope"}]}}
        if path == "query":
            statement = kwargs["params"]["query"]
            rows = {
                "Vendor": [
                    {"Id": "7", "DisplayName": "Zoe Supplier"},
                    {"Id": "5", "DisplayName": "Pat Manager"},
                ],
                "Account": [
                    {"Id": "80", "FullyQualifiedName": "Travel", "Classification": "Expense"},
                    {"Id": "81", "FullyQualifiedName": "Sales", "Classification": "Revenue"},
                ],
                "Bill": [
                    {"Id": bill_id, "Balance": balance}
                    for bill_id, balance in self.balances.items()
                    if f"'{bill_id}'" in statement
                ],
            }
            entity = statement.split()[3]
            return FakeResponse({"QueryResponse": {entity: rows[entity]} if rows[entity] else {}})
        if path in self.refuse:
            if path == "upload":
                return FakeResponse({"AttachableResponse": [fault]})
            return FakeResponse(fault, 400)
        if path == "bill":
            return FakeResponse({"Bill": {"Id": "151", "SyncToken": "0"}})
        if path == "upload":
            return FakeResponse({"AttachableResponse": [{"Attachable": {"Id": "9"}}]})
        raise AssertionError(f"unexpected call to {url}")

    def called(self, path):
        return [kwargs for _, called_path, kwargs in self.calls if called_path == path]


@pytest.fixture
def qbo(monkeypatch):
    fake = FakeQuickBooks()
    monkeypatch.setattr(quickbooks, "requests", fake)
    return fake
