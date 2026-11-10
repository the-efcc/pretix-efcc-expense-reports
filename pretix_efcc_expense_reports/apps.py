from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from . import __version__

try:
    from pretix.base.plugins import PLUGIN_LEVEL_EVENT_ORGANIZER_HYBRID, PluginConfig
except ImportError:
    raise RuntimeError("Please use pretix 2026.3 or above to run this plugin!") from None


class PluginApp(PluginConfig):
    default = True
    name = "pretix_efcc_expense_reports"
    verbose_name = "Expense reports"

    class PretixPluginMeta:
        name = _("Expense reports")
        author = "EFCC"
        description = _("Submit an event's expense reports to QuickBooks")
        visible = True
        version = __version__
        category = "INTEGRATION"
        # Hybrid: the QuickBooks connection lives on the organizer, the expense
        # reports page on each event that enables the plugin.
        level = PLUGIN_LEVEL_EVENT_ORGANIZER_HYBRID
        compatibility = "pretix>=2026.3.0"

    def ready(self) -> None:
        from . import signals, tasks  # noqa: F401
