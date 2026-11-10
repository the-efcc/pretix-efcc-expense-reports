from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path(
        "control/event/<str:organizer>/<str:event>/expense-reports/",
        views.ExpenseReportListView.as_view(),
        name="list",
    ),
    path(
        "control/event/<str:organizer>/<str:event>/expense-reports/add",
        views.ExpenseReportCreateView.as_view(),
        name="add",
    ),
    path(
        "control/organizer/<str:organizer>/expense-reports/settings",
        views.OrganizerSettingsView.as_view(),
        name="organizer.settings",
    ),
    path(
        "control/organizer/<str:organizer>/expense-reports/connect",
        views.ConnectView.as_view(),
        name="oauth.connect",
    ),
    path(
        "control/organizer/<str:organizer>/expense-reports/callback",
        views.CallbackView.as_view(),
        name="oauth.callback",
    ),
    path("_quickbooks/<str:organizer>/webhook/", views.webhook, name="webhook"),
]
