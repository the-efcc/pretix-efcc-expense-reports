from __future__ import annotations

from django.dispatch import receiver
from django.urls import resolve, reverse
from django.utils.translation import gettext_lazy as _
from pretix.control.signals import nav_event, nav_organizer

NAMESPACE = "plugins:pretix_efcc_expense_reports"
EVENT_PERMISSION = "event.orders:read"
ORGANIZER_PERMISSION = "organizer.settings.general:write"


@receiver(nav_event, dispatch_uid="efcc_expense_reports_nav_event")
def add_event_nav(sender, request, **kwargs):
    if not request.user.has_event_permission(
        request.organizer, request.event, EVENT_PERMISSION, request=request
    ):
        return []
    return [
        {
            "label": _("Expense reports"),
            "url": reverse(
                f"{NAMESPACE}:list",
                kwargs={"organizer": request.organizer.slug, "event": request.event.slug},
            ),
            "active": resolve(request.path_info).namespace == NAMESPACE,
            "icon": "money",
        }
    ]


@receiver(nav_organizer, dispatch_uid="efcc_expense_reports_nav_organizer")
def add_organizer_nav(sender, request, organizer, **kwargs):
    if not request.user.has_organizer_permission(
        organizer, ORGANIZER_PERMISSION, request=request
    ):
        return []
    return [
        {
            "label": _("Expense reports"),
            "url": reverse(
                f"{NAMESPACE}:organizer.settings", kwargs={"organizer": organizer.slug}
            ),
            "active": resolve(request.path_info).namespace == NAMESPACE,
            "icon": "money",
        }
    ]
