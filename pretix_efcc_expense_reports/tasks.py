from __future__ import annotations

from pretix.base.models import Organizer
from pretix.celery_app import app

from . import quickbooks
from .models import ExpenseReport

BATCH_SIZE = 100


# A missed refresh heals itself: every later webhook re-checks all open reports.
@app.task(autoretry_for=(quickbooks.QuickBooksError,), retry_backoff=60, max_retries=5)
def refresh_statuses(organizer_id: int) -> None:
    organizer = Organizer.objects.get(pk=organizer_id)
    reports = list(
        ExpenseReport.objects.filter(
            event__organizer=organizer,
            status=ExpenseReport.STATUS_SUBMITTED,
            realm_id=quickbooks.get_setting(organizer, "realm_id"),
        )
    )
    for start in range(0, len(reports), BATCH_SIZE):
        batch = reports[start : start + BATCH_SIZE]
        balances = quickbooks.bill_balances(organizer, [report.bill_id for report in batch])
        for report in batch:
            if report.bill_id not in balances:
                report.status = ExpenseReport.STATUS_DELETED
            elif balances[report.bill_id] == 0:
                report.status = ExpenseReport.STATUS_PAID
            else:
                continue
            report.save(update_fields=["status"])
