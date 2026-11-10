from __future__ import annotations

from decimal import Decimal
from urllib.parse import urljoin

from django.conf import settings
from django.contrib import messages
from django.db.models import Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.crypto import constant_time_compare, get_random_string
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import FormView, ListView
from pretix.base.models import Order, OrderFee, OrderPosition, Organizer
from pretix.control.permissions import (
    EventPermissionRequiredMixin,
    OrganizerPermissionRequiredMixin,
)
from pretix.control.views.organizer import OrganizerSettingsFormView

from . import quickbooks
from .forms import ExpenseReportForm, OrganizerSettingsForm
from .models import ExpenseReport
from .signals import EVENT_PERMISSION, NAMESPACE, ORGANIZER_PERMISSION
from .tasks import refresh_statuses

PLUGIN = "pretix_efcc_expense_reports"
OAUTH_STATE_SESSION_KEY = "efcc_expense_reports_oauth_state"


def _absolute_url(name: str, organizer) -> str:
    # Built from SITE_URL: these are registered with Intuit and must not depend
    # on how the current request reached pretix.
    return urljoin(
        settings.SITE_URL, reverse(f"{NAMESPACE}:{name}", kwargs={"organizer": organizer.slug})
    )


def _net_revenue(event) -> Decimal:
    # Paid orders, fees included, taxes excluded: the "paid, net" total of
    # pretix' own order overview.
    total = Decimal("0.00")
    for model, field in ((OrderPosition, "price"), (OrderFee, "value")):
        sums = model.objects.filter(
            order__event=event, order__status=Order.STATUS_PAID
        ).aggregate(gross=Sum(field), tax=Sum("tax_value"))
        total += (sums["gross"] or 0) - (sums["tax"] or 0)
    return total


class EventViewMixin(EventPermissionRequiredMixin):
    permission = EVENT_PERMISSION

    def dispatch(self, request, *args, **kwargs):
        # pretix routes a plugin's URLs whether or not the event enabled it.
        event = request.event
        if PLUGIN not in event.get_plugins() or PLUGIN not in event.organizer.get_plugins():
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self) -> str:
        event = self.request.event
        return reverse(
            f"{NAMESPACE}:list", kwargs={"organizer": event.organizer.slug, "event": event.slug}
        )


class ExpenseReportListView(EventViewMixin, ListView):
    template_name = "pretix_efcc_expense_reports/list.html"
    context_object_name = "reports"

    def get_queryset(self):
        return ExpenseReport.objects.filter(event=self.request.event).select_related(
            "submitted_by"
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["expenses"] = sum(
            report.amount
            for report in ctx["reports"]
            if report.status != ExpenseReport.STATUS_DELETED
        )
        ctx["revenue"] = _net_revenue(self.request.event)
        ctx["balance"] = ctx["revenue"] - ctx["expenses"]
        return ctx


class ExpenseReportCreateView(EventViewMixin, FormView):
    template_name = "pretix_efcc_expense_reports/add.html"
    form_class = ExpenseReportForm

    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except quickbooks.QuickBooksError as exc:
            # Raised while loading the form's choices; without them there is no
            # form to show the error on.
            messages.error(request, str(exc))
            return redirect(self.get_success_url())

    def get_form_kwargs(self):
        organizer = self.request.organizer
        return {
            **super().get_form_kwargs(),
            "vendors": quickbooks.vendors(organizer),
            "accounts": quickbooks.expense_accounts(organizer),
        }

    def form_valid(self, form):
        event = self.request.event
        user = self.request.user
        data = form.cleaned_data
        report = form.save(commit=False)
        report.event = event
        report.submitted_by = user
        # The form works with QuickBooks ids, the table shows the names.
        report.payee = dict(form.fields["payee"].choices)[data["payee"]]
        report.category = dict(form.fields["category"].choices)[data["category"]]
        report.realm_id = quickbooks.get_setting(event.organizer, "realm_id")
        try:
            report.bill_id = quickbooks.create_bill(
                event.organizer,
                vendor_id=data["payee"],
                account_id=data["category"],
                amount=report.amount,
                currency=event.currency,
                date=report.date,
                description=report.description,
                memo=f"Expense report for {event.name} ({event.slug}), "
                f"submitted in pretix by {user.get_full_name()}",
                receipt=data["receipt"],
            )
        except quickbooks.QuickBooksError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        report.save()
        messages.success(self.request, _("Your expense report was sent to QuickBooks."))
        return redirect(self.get_success_url())


class OrganizerSettingsView(OrganizerSettingsFormView):
    form_class = OrganizerSettingsForm
    template_name = "pretix_efcc_expense_reports/organizer_settings.html"
    permission = ORGANIZER_PERMISSION

    def get_success_url(self) -> str:
        return reverse(
            f"{NAMESPACE}:organizer.settings", kwargs={"organizer": self.request.organizer.slug}
        )

    def get_context_data(self, **kwargs):
        organizer = self.request.organizer
        return super().get_context_data(
            redirect_uri=_absolute_url("oauth.callback", organizer),
            webhook_url=_absolute_url("webhook", organizer),
            realm_id=quickbooks.get_setting(organizer, "realm_id"),
            has_client=bool(quickbooks.get_setting(organizer, "client_id")),
            **kwargs,
        )


class ConnectView(OrganizerPermissionRequiredMixin, View):
    permission = ORGANIZER_PERMISSION

    def get(self, request, *args, **kwargs):
        state = get_random_string(32)
        request.session[OAUTH_STATE_SESSION_KEY] = state
        return redirect(
            quickbooks.authorize_url(
                request.organizer, _absolute_url("oauth.callback", request.organizer), state
            )
        )


class CallbackView(OrganizerPermissionRequiredMixin, View):
    permission = ORGANIZER_PERMISSION

    def get(self, request, *args, **kwargs):
        organizer = request.organizer
        state = request.session.pop(OAUTH_STATE_SESSION_KEY, None)
        code = request.GET.get("code")
        realm_id = request.GET.get("realmId")
        if not (
            state
            and code
            and realm_id
            and constant_time_compare(state, request.GET.get("state", ""))
        ):
            messages.error(
                request, _("QuickBooks did not confirm the connection, please try again.")
            )
        else:
            try:
                quickbooks.connect(
                    organizer, code, realm_id, _absolute_url("oauth.callback", organizer)
                )
            except quickbooks.QuickBooksError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _("QuickBooks is now connected."))
        return redirect(
            reverse(f"{NAMESPACE}:organizer.settings", kwargs={"organizer": organizer.slug})
        )


@csrf_exempt
@require_POST
def webhook(request, organizer):
    organizer = get_object_or_404(Organizer, slug=organizer)
    signature = request.headers.get("intuit-signature", "")
    if not quickbooks.is_valid_webhook(organizer, request.body, signature):
        return HttpResponse(status=401)
    # The payload is deliberately not read: whatever changed in QuickBooks, the
    # task re-checks every open report of this organizer, which also catches up
    # on any notification that got lost. Intuit wants its answer within 3 s, so
    # the checking happens in the background.
    refresh_statuses.apply_async(args=(organizer.pk,))
    return HttpResponse()
