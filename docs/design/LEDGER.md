# Ledger design specification

Status: not implemented. No posting/payment API is currently available.

## Ownership

Ledger owns chart of accounts, HolanSL bank/cash accounts, periods, journals/ledger,
client invoices, supplier bills, expenses/salaries/logistics costs, revenue,
advances, payments/receipts, allocations, withholding evidence, credits,
adjustments, reconciliation, reporting and approval/posting.
CRM counterparty bank details are master data, not HolanSL cash ledger accounts.
Finance decides whether procurement requests become official records.

## Invoices, payments and reconciliation

Keep invoice lines traceable to client, PO line, quote item, supplied quantity,
delivery note and request where practical. Finance creates/approves invoices;
authorized procurement staff may export/send only approved documents.

Receipts prove money was received; allocations explain which invoices it settles.
Support many-to-many allocations, bulk/partial payments, overpayments, unallocated
payments and receipts before invoice identification. Changing allocations must
not erase receipt evidence.

Track withholding separately with tax type/rate/amount, client/invoice,
deduction date, certificate/reference/status, evidence and recording/approval staff.
Do not treat illustrative rates as Nigerian tax rules. Confirm lawful settlement
and accounting policy before implementation.

Generate reconciliation from opening balances, invoices, receipts, allocations,
approved withholding/credits, outstanding balances and unallocated payments.
Keep estimates historical; use approved actuals for actual accounting.

## Advances / imprest

An advance is not immediately a final expense. Record issue, funding account,
custodian, authorization, purpose/procurement link and supporting evidence.
Procurement submits actual supplier/logistics/other expenditure and returned cash;
finance approves and clears the advance without recording the original outflow
twice. Track accounted, returned, additionally requested and outstanding amounts.

Candidate states: requested, approved, disbursed, partially accounted, fully
accounted, balance returned, additional funding requested, closed.
Transitions/approvals are not final merely because labels are listed.

## Open decisions

Confirm accounting/posting model, period closing, immutable posting/reversals,
currencies/FX source/timing, precision/rounding, taxes, segregation of duties,
numbering/storage/retention, request integration ownership and reconciliation
rules. These specifications are not an implemented ledger or tax advice.
