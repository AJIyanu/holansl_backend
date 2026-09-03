# Ledger application

Status: PLACEHOLDER — not implemented. Verified 2026-09-03.

## Current state

[models.py](../../ledger/models.py), [serializers.py](../../ledger/serializers.py) and
[views.py](../../ledger/views.py) deliberately contain no finance implementation.
The app is installed, but its inclusion in the
[root URL configuration](../../holansladminservices/urls.py) is commented out.

## Endpoints and contracts

There are no active ledger endpoints, posting APIs, payment contracts or
implemented accounting permissions. Finance navigation links do not imply that
invoices, receipts or balances currently work.

## Intended responsibility

The supplied design assigns official financial records to ledger: invoices,
supplier bills, bank/cash accounts, chart of accounts, periods, journal/ledger,
expenses, advances, payments/receipts, allocations, withholding evidence, credits,
adjustments and reconciliation. It approves financial requests from procurement.
See [ledger design](../design/LEDGER.md).

Keep receipt evidence separate from invoice allocations; retain estimates for
history but use approved actual records for actual accounting. Advances are
cleared against approved costs and returned money without double-counting.
These are supplied design intentions, not an implemented posting specification.

## Open design, safety and verification

Currencies/FX, precision, tax treatment, approvals, accounting model, closing,
reversals, immutable posting, numbering and reconciliation rules require
confirmation. Handover tax examples are not legal or accounting advice.
Reference CRM parties and procurement origins without circular business ownership.

Document actual models, endpoints, request/response payloads, authorization,
posting invariants, audits and automated tests as implementation proceeds.
No migration, ledger entry or financial transaction was created in this task.
Existing placeholder test files do not establish a completed ledger.
