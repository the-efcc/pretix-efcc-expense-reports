# pretix-efcc-expense-reports

A pretix plugin that lets the people running an event submit its expense reports
to QuickBooks Online, and follow them, from the event's pretix backend. It has
nothing to do with ticketing: it reuses pretix accounts and team permissions so
that we don't have to run another tool.

## What it does

- **Event → Expense reports** (needs the *Orders: view* team permission) lists the
  event's expense reports with their status and total, and shows the event's net
  revenue (paid orders, taxes excluded) minus the expenses so far.
- **Submit an expense report** asks for the date, who to pay, a category, a
  description, the amount and a receipt. Each report becomes an unpaid **Bill**
  in QuickBooks, with the receipt attached and the event and submitter in its
  memo. Payees are QuickBooks suppliers and categories are QuickBooks expense
  accounts; both lists are read live from QuickBooks.
- QuickBooks **webhooks** keep the status up to date: *Awaiting payment* until
  accounting pays the bill, then *Paid*; *Deleted in QuickBooks* (and no longer
  counted) if accounting deletes it.

## Setup

1. Enable the plugin for the organizer, and for each event that should use it.
2. In the [Intuit developer portal](https://developer.intuit.com/), create an app
   with the `com.intuit.quickbooks.accounting` scope.
3. Open **Organizer → Expense reports** in pretix. Copy the *Redirect URI* shown
   there into the app's redirect URIs, and the *Webhook endpoint* into the app's
   webhooks, subscribed to the **Bill** and **Bill Payment** events.
4. Back in pretix, enter the app's client ID, client secret and webhook verifier
   token, pick the environment the keys belong to (sandbox keys only reach
   sandbox companies), and save.
5. Click **Connect to QuickBooks** and authorize the company. The connection
   stays valid as long as it is used at least once every 100 days.

## Installation on NixOS

```nix
{ inputs, pkgs, ... }:
{
  services.pretix.plugins = [
    inputs.pretix-efcc-expense-reports.packages.${pkgs.stdenv.hostPlatform.system}.default
  ];
}
```

## Development

```bash
uv venv
uv pip install -e ".[dev]"
uv run ruff check .
uv run pytest tests
```

## License

GNU Affero General Public License v3.0 (AGPLv3)
