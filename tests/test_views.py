import base64
import datetime
import hashlib
import hmac
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils.timezone import now
from pretix.base.models import Order, OrderFee, OrderPosition, Team, User

from pretix_efcc_expense_reports import quickbooks
from pretix_efcc_expense_reports.models import ExpenseReport

LIST_URL = "/control/event/dummy/dummy/expense-reports/"
ADD_URL = LIST_URL + "add"
SETTINGS_URL = "/control/organizer/dummy/expense-reports/settings"
CALLBACK_URL = "/control/organizer/dummy/expense-reports/callback"
WEBHOOK_URL = "/_quickbooks/dummy/webhook/"


def _submission():
    return {
        "date": "2026-11-01",
        "payee": "5",
        "category": "80",
        "description": "Train to the venue",
        "amount": "12.50",
        "receipt": SimpleUploadedFile("ticket.pdf", b"%PDF-1.4"),
    }


def test_list_shows_net_revenue_minus_expenses(client, event, organizer, make_report):
    item = event.items.create(name="Ticket", default_price=Decimal("120.00"))
    order = Order.objects.create(
        code="PAID1",
        event=event,
        email="a@example.org",
        status=Order.STATUS_PAID,
        datetime=now(),
        expires=now() + datetime.timedelta(days=10),
        total=Decimal("120.00"),
        sales_channel=organizer.sales_channels.get(identifier="web"),
    )
    OrderPosition.objects.create(
        order=order,
        item=item,
        price=Decimal("120.00"),
        tax_rate=Decimal("20.00"),
        tax_value=Decimal("20.00"),
        positionid=1,
    )
    OrderFee.objects.create(
        order=order,
        fee_type=OrderFee.FEE_TYPE_PAYMENT,
        value=Decimal("6.00"),
        tax_rate=Decimal("20.00"),
        tax_value=Decimal("1.00"),
    )
    pending = Order.objects.create(
        code="PEND1",
        event=event,
        email="b@example.org",
        status=Order.STATUS_PENDING,
        datetime=now(),
        expires=now() + datetime.timedelta(days=10),
        total=Decimal("120.00"),
        sales_channel=organizer.sales_channels.get(identifier="web"),
    )
    OrderPosition.objects.create(order=pending, item=item, price=Decimal("120.00"), positionid=1)
    make_report(amount=Decimal("30.00"))
    make_report(amount=Decimal("15.00"), status=ExpenseReport.STATUS_PAID)
    make_report(amount=Decimal("50.00"), status=ExpenseReport.STATUS_DELETED)

    response = client.get(LIST_URL)

    assert response.status_code == 200
    assert response.context["revenue"] == Decimal("105.00")  # 120 - 20 + 6 - 1
    assert response.context["expenses"] == Decimal("45.00")
    assert response.context["balance"] == Decimal("60.00")
    assert "Expense reports" in response.content.decode()


def test_list_of_an_event_without_orders_or_reports(client, event):
    response = client.get(LIST_URL)

    assert response.status_code == 200
    assert response.context["balance"] == 0


def test_submitting_creates_the_bill_and_records_the_report(client, event, user, qbo):
    assert client.get(ADD_URL).status_code == 200

    response = client.post(ADD_URL, _submission())

    assert response.status_code == 302, response.context["form"].errors
    report = ExpenseReport.objects.get()
    assert (report.event, report.submitted_by) == (event, user)
    assert (report.payee, report.category) == ("Pat Manager", "Travel")
    assert (report.bill_id, report.realm_id) == ("151", "realm-1")
    assert report.amount == Decimal("12.50")
    assert report.status == ExpenseReport.STATUS_SUBMITTED
    (bill,) = qbo.called("bill")
    assert "Pat Manager" in bill["json"]["PrivateNote"]


def test_a_quickbooks_error_is_shown_and_nothing_is_recorded(client, event, qbo):
    qbo.refuse.add("bill")

    response = client.post(ADD_URL, _submission())

    assert response.status_code == 200
    assert "Detailed nope" in response.content.decode()
    assert not ExpenseReport.objects.exists()


def test_invalid_submissions_never_reach_quickbooks(client, event, qbo):
    for override in (
        {"amount": "0"},
        {"payee": "999"},
        {"receipt": SimpleUploadedFile("malware.exe", b"MZ")},
    ):
        response = client.post(ADD_URL, {**_submission(), **override})
        assert response.status_code == 200, override
        assert response.context["form"].errors, override

    assert qbo.called("bill") == []


def test_add_page_explains_a_missing_connection(client, event, organizer, qbo):
    organizer.settings.delete(quickbooks.SETTINGS_PREFIX + "refresh_token")

    response = client.get(ADD_URL, follow=True)

    assert response.redirect_chain == [(LIST_URL, 302)]
    assert "not connected" in response.content.decode()


def test_pages_need_order_access_and_the_plugin_enabled(client, event, organizer, qbo):
    User.objects.create_user("checkin@example.org", "dummypassword123")
    team = Team.objects.create(organizer=organizer, all_events=True)
    team.members.add(User.objects.get(email="checkin@example.org"))
    other = Client()
    assert other.login(email="checkin@example.org", password="dummypassword123")
    assert other.get(LIST_URL).status_code in (403, 404)
    assert other.post(ADD_URL, _submission()).status_code in (403, 404)

    event.plugins = ""
    event.save()
    assert client.get(LIST_URL).status_code == 404
    assert client.post(ADD_URL, _submission()).status_code == 404
    assert qbo.called("bill") == []


def test_webhook_refreshes_statuses_only_when_signed(organizer, qbo, make_report):
    report = make_report(bill_id="2")
    qbo.balances = {"2": 0}
    body = b'[{"type": "qbo.billpayment.created.v1", "intuitaccountid": "realm-1"}]'
    signature = base64.b64encode(hmac.new(b"verifier", body, hashlib.sha256).digest()).decode()

    def post(signature):
        # Intuit sends neither a session nor a CSRF token.
        return Client(enforce_csrf_checks=True).post(
            WEBHOOK_URL,
            body,
            content_type="application/json",
            headers={"intuit-signature": signature},
        )

    assert post("forged").status_code == 401
    assert ExpenseReport.objects.get(pk=report.pk).status == ExpenseReport.STATUS_SUBMITTED

    assert post(signature).status_code == 200
    assert ExpenseReport.objects.get(pk=report.pk).status == ExpenseReport.STATUS_PAID


def test_connecting_stores_the_company_and_checks_the_state(client, organizer, qbo):
    response = client.get("/control/organizer/dummy/expense-reports/connect")
    assert response.status_code == 302
    assert response["Location"].startswith(quickbooks.AUTHORIZE_URL + "?client_id=client-id&")
    state = client.session["efcc_expense_reports_oauth_state"]

    client.get(CALLBACK_URL, {"code": "c", "realmId": "realm-2", "state": "forged"})
    assert qbo.calls == []
    assert quickbooks.get_setting(organizer, "realm_id") == "realm-1"

    # The forged attempt used the state up: the genuine one has to start over.
    client.get("/control/organizer/dummy/expense-reports/connect")
    state = client.session["efcc_expense_reports_oauth_state"]
    response = client.get(CALLBACK_URL, {"code": "c", "realmId": "realm-2", "state": state})

    assert response.status_code == 302
    (token_call,) = qbo.called("token")
    assert token_call["data"] == {
        "grant_type": "authorization_code",
        "code": "c",
        "redirect_uri": "http://example.com" + CALLBACK_URL,
    }
    organizer.settings.flush()
    assert quickbooks.get_setting(organizer, "realm_id") == "realm-2"
    assert quickbooks.get_setting(organizer, "refresh_token") == "refresh-2"


def test_organizer_settings_page_shows_the_urls_to_register(client, organizer):
    response = client.get(SETTINGS_URL)

    assert response.status_code == 200
    html = response.content.decode()
    assert "http://example.com" + CALLBACK_URL in html
    assert "http://example.com" + WEBHOOK_URL in html
    assert "client-secret" not in html

    response = client.post(
        SETTINGS_URL,
        {
            "plugin_efcc_expense_reports_client_id": "new-id",
            "plugin_efcc_expense_reports_environment": "sandbox",
        },
    )
    assert response.status_code == 302
    organizer.settings.flush()
    assert quickbooks.get_setting(organizer, "client_id") == "new-id"
    assert quickbooks.get_setting(organizer, "environment") == "sandbox"
