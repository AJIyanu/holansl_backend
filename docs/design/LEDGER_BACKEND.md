# Ledger backend specification

Status: design approved and backend implementation added 2026-09-15. The
[application documentation](../apps/ledger.md) is authoritative for delivered routes,
policies, limits and rollout. The tables below retain the original design and its
pre-build observations; proposed names are not a substitute for runtime contracts.
The report name is **Reconciliation and Statement**. The user confirmed recognition
on client acceptance and self-approval with assigned authority and audit history.
Native-currency operation, monthly periods, HALF_UP per line, explicit tax collection
attribution and manager-authorized standing task routing are implemented. Statutory
rates/applicability and consolidated FX valuation are not inferred.

## 1. Scope and existing integration points

Build inside the installed `ledger` Django app, using DRF, PostgreSQL, the custom
UUID user and the existing service-layer conventions. Do not restore removed
`Category`, `Expectation` or `Transaction` models or their former contracts.

| Existing source | Observed contract and design implication |
| --- | --- |
| [Ledger removal migration](../../ledger/migrations/0002_remove_ledger_domain.py) | Deletes the old domain. Preserve migration history; inspect the complete target migration plan before any application database rollout. |
| [Procurement models](../../procurement/models.py) | Finance requests are drafts; PO lines require `quote_line`. Direct orders need an additive migration and compatible services/serializers. |
| [Document registry](../../procurement/document_registry.py) | Approval currently raises `ledger_unavailable`; approval requests and `ledger_reference` are not proof of approval. |
| [Document rendering](../../procurement/rendering.py) | Rendering, assets and scope depend on a procurement request. Reuse layouts/numbering but design access for ledger records without a request. |
| [CRM integrations](../../crm/integrations.py) | Reuse new-business Party/client/contact validation. Historical payments/corrections require historical-reference rules, not forced active-client reselection. |
| [Permission formatting](../../accounts/serializers.py) | Backend uses native permissions; `format_permission` splits at the first underscore. Preserve this contract. |
| [Executive provisioning](../../accounts/executive_permissions.py) | Newly created permissions are granted additively. No routine restoration of revoked grants. |
| [Tasks](../apps/tasks.md) and [notifications](../apps/notifications.md) | Reuse existing services, access and delivery queues. Task acceptance cannot approve a financial document. |

This design includes official finance operations and statements. A full payroll
calculation engine, bank-feed integration and automatic bank transfers are not
implied by recording salary expenses, bank accounts or payments.

## 2. Module boundaries

Use packages when implementation begins; importing the model package must register
every concrete model with Django. Avoid a single service file containing all money
operations. Suggested boundaries:

| Area | Responsibility |
| --- | --- |
| `models/` | Configuration, accounting, financial documents, settlement, tax and audit records. |
| `services/posting.py` | The sole business entry point for balanced posting and reversals. |
| `services/invoices.py`, `receipts.py`, `allocations.py` | Invoice lifecycle, actual money and application of settlement. |
| `services/approvals.py`, `tax.py`, `advances.py`, `payments.py` | Explicit permission-checked business actions. |
| `selectors/` | Scoped read queries, statements, balances, outstanding items and tax reports. |
| `integrations/procurement.py` | Typed source validation, approval lookup and transactional settlement events. |
| `documents.py` | Ledger document snapshots, rendering and private downloads. |
| `permissions.py`, `serializers/`, `views/`, `urls.py` | Thin API validation, scope and service invocation. |
| `tests/` | Focused accounting, API, permissions, integration and PostgreSQL concurrency tests. |

Ledger may reference accounts, CRM and procurement. Procurement calls narrow ledger
service interfaces at runtime; do not make CRM depend on finance transactions or
create mutually dependent model imports. Keep migration dependencies acyclic.

## 3. Data model

All new business records use UUIDs, creation timestamps and actor/name snapshots.
Use `settings.AUTH_USER_MODEL` and protected business relationships. Financial
dates are distinct from the timestamp when staff recorded an event. Human document
numbers are separate from UUIDs. Decimal amounts are serialized as strings.

### Foundation

| Proposed model | Essential fields and constraints |
| --- | --- |
| `Currency` | Unique uppercase three-letter code, name, symbol, minor units, active flag. Seed NGN, USD and GBP; NGN is the primary display currency. Disabling blocks new use but preserves history. |
| `LedgerAccount` | Unique code, name, classification (asset/liability/equity/income/expense), optional parent, control purpose, active flag. Prevent hierarchy cycles; posting only to enabled leaf accounts. Classification/control purpose cannot change after use. |
| `AccountCurrency` | Unique account/currency pair; determines the denomination of postings. A chart category can have several currency balances. |
| `CashAccount` | Bank/cash kind, display name, one unique `AccountCurrency`, protected/masked bank details as appropriate. Currency and ledger link become immutable once used. No default overdraft restriction is assumed. |
| `FinancialYear` | Unique year, 1 January start and 31 December end. Opening/closing actions are audited. |
| `AccountingPeriod` | Year, start/end, open/closed state and closure history. Dates contained in the year; no overlapping periods. Monthly periods are recommended, pending policy confirmation. |
| `JournalEntry` | Draft/posted state, accounting date/period, currency, recorded/posted timestamps, source operation, posting-policy version, description and optional reversal-of reference. Unique source event prevents duplicate posting. |
| `JournalLine` | Entry, sequence, account/currency, debit, credit, optional Party/staff and typed source-line links. Exactly one positive side per line; other side zero. Entry and account currency must match. |
| `PostingPolicyVersion` | Immutable approved account mappings and calculation-policy identifier. No silent mapping change for historical documents. Configuration completeness is a posting prerequisite. |
| `OpeningBalanceBatch` and lines | Approved cutover date, account/currency balances, client/supplier open items and evidence. Balanced posting and reconciliation of detailed opening items to their control totals; never import both a summary debt and the same debt's invoices as additional balances. |
| `LedgerOperation`, `LedgerEvent`, `IntegrationIntent` | Idempotency result, append-only actor/action history and durable integration work respectively. Financial history is distinct from runtime logs and the local engineering log. |

Start with independent native-currency journals and trial balances. A USD journal
must balance in USD, not by including an NGN amount on its other side. Combined NGN
financial reports, currency exchange and revaluation require the agreed FX source,
rate date and gain/loss mappings. Do not invent rates or call separate currency
totals a consolidated NGN balance sheet. Reserve optional functional-value/rate
snapshots in the eventual FX design before enabling those operations.

Proposed precision: quantities/unit prices retain at least procurement's four
decimal places; intermediate calculations use Decimal with higher precision;
posted money is quantized to the currency's minor units (two for NGN/USD/GBP).
Finalize rounding mode and per-line versus document rounding before financial
posting. A draft-to-posting rounding difference must be explicit and reproducible,
never a hidden arbitrary journal line that makes a broken journal balance.

### Financial records and approvals

Use a common `FinancialRecord` identity for type, client/supplier or staff context,
currency, revision, dates, lifecycle metadata and optional procurement origin.
Typed one-to-one detail tables below carry domain fields; do not store all business
data in a free-form JSON document. A record must have exactly the detail type its
kind requires, enforced by creation services and database integrity checks.

| Proposed detail/related model | Purpose |
| --- | --- |
| `Invoice`, `InvoiceLine` | Client snapshot, issue/due dates, description/unit, quantity, unit price, discount and net/tax/gross snapshots. Optional request/item, quote, PO and delivery traceability with validated same-client relationships. |
| `Receipt` | Actual amount/currency received, purpose, receipt date, cash account, payer identity/reference and evidence. Client receipts can be recorded before any invoice is identified; advance returns/supplier refunds have their own typed source. Unknown-payer money needs suspense attribution before entering a client statement. |
| `SupplierBill`, `Expense` and lines | Supplier/staff, occurrence/due dates, approved actual costs, account/tax breakdown and evidence. Procurement estimates cannot be posted as actual bills. |
| `Payment` and application lines | One actual outgoing cash movement with typed purposes: bill settlement, direct expense, advance, refund or tax remittance. Exactly one source of its cash journal; purpose records reference it rather than creating another outflow. |
| `Advance`, `AdvanceClearance` and lines | Custodian, purpose, authorized amount, linked disbursements, approved cost evidence, returns and clearance. Track extra funding as another authorized disbursement. |
| `CreditNote`, `CreditNoteLine` | Original invoice and eligible quantities/amounts, reason, outcome/return evidence, approval and linked correction journal. No deleting posted invoices. |
| `FinancialRevision` | Immutable submitted content snapshot/hash, revision number and calculation policy. Approved/exported content must match this exact snapshot. |
| `ApprovalRequest`, `ApprovalDecision` | Explicit target: financial revision OR procurement quotation revision, never both. One request per target/revision; append decisions with actor, timestamp and reason. No public writable `approved=true`. |
| `FinanceSubmission` | Unique procurement finance request/revision, its source fingerprint, processing status and protected links to resulting ledger records. Retries reuse the submission; substantive amendments require a new version. |
| `LedgerDocument` | Private evidence/generated file metadata, checksum, recorded provider, uploader and access-bearing parent. Reuse storage/privacy checks; never expose raw storage keys. |

Retain source snapshots and party identity even if names/affiliations change.
CRM merging must not rewrite immutable journal evidence or silently combine client
balances. Design explicit alias resolution/reporting before treating a merged Party
and its survivor as one financial account. Existing debts can still be collected
when a client is inactive; creation of new business remains subject to CRM rules.

### Settlement and statements

| Proposed model | Essential invariant |
| --- | --- |
| `ClientAccount` | One stable ledger relationship per CRM Party/currency. A locking anchor for settlement and statement consistency, not another client master record. |
| `ClientMovement` | Append-only debit/credit effect from a posted source event, with accounting date and recorded timestamp. Created atomically with journal/control-account lines and unique per source/effect. |
| `SettlementSource` | Available value from a posted receipt, accepted withholding, approved credit or imported opening credit; exactly one typed origin. Cash evidence stays on Receipt. |
| `AllocationEvent` | Apply/release an amount from a settlement source to an invoice; positive value, effective date, actor and reversal link. Releases cannot exceed the still-active application. |
| `StatementSnapshot` | Client/currency/range, generation timestamp, accounting cutoff, rows/totals and fingerprint for reproducible printing/export. Contains no editable accounting balance. |

Posting may split a client's control balance between receivables and deposits.
Client movements must reconcile to the signed net of those designated control
accounts. Internal reclassification between them changes neither the statement
balance nor the amount received. Do not post manual entries to these control
accounts without the same typed client/subledger effects and reconciliation checks.

Allocations change which invoice is settled, not how much cash was received. They
must not create another client credit. Lock the client/currency account, source and
invoice before checking available value. Source and target must have the same
client/currency. Sum of active applications cannot exceed source capacity or the
invoice's remaining eligible amount. Reallocation appends releases and applications
atomically; history remains queryable at prior dates.

Posting an invoice-specific withholding or credit also applies its settlement
source to that invoice in the same transaction; do not leave a posted client credit
while falsely labelling the target invoice unpaid. Reversing a receipt/withholding
first releases its active allocations through dated events in the same transaction.
Original payment and deduction evidence remains intact. Duplicate checks must
respect bank/reference context; arbitrary free-text references are not globally
unique proof that two cash entries are the same transaction.

### Tax

| Proposed model | Purpose |
| --- | --- |
| `TaxType`, `TaxPolicyVersion` | Tax identity, authority, versioned rate/base, effective dates and verified posting/remittance mapping. No statutory rate inferred from examples or procurement's form default. |
| `ClientWithholdingAuthorization` | Party, applicable tax type, effective interval, approver permission evidence, authorization/revocation actors, reason and documents. Append changes; prevent overlapping conflicting authorizations. |
| `InvoiceTaxComponent` | Tax type/policy snapshot, invoice line, base/rate/amount/currency; separate from revenue. |
| `WithholdingRecord` | Client, invoice/component, amount/currency, deduction date, authorization version, evidence, recording/approval staff and decision history. Produces settlement value only after approval. |
| `TaxCollectionAllocation` | Attributes part of an actual receipt allocation to invoice tax components. Bounded by cash applied and component amount; not another cash receipt. |
| `TaxRemittance` and lines | Authority, tax type, covered periods, amount/currency, payment reference/evidence and allocation to remittable amounts. Exactly one linked Payment posts the outflow. |
| `ClientRemittanceEvidence` | Subsequent certificate/reference and verification for a client's withholding. Does not mark Holan cash paid or create another settlement credit. |

Internal client authorization is specific to withholding and grants no authority
to change an invoice's tax charge. Proposed temporal rule: eligibility is checked
as of deduction date against retained authorization; later revocation does not
undo previously approved deductions. Retroactive authorization needs an explicit
approved policy; do not silently infer it from current eligibility.

Preserve the user's workflow: NGN 5,000 tax charged, NGN 3,000 collected and
NGN 2,000 authorized withholding are three distinct measures. The intended
client-remittance mapping schedules only the NGN 3,000 for Holan cash remittance;
the NGN 2,000 identifies the responsible client and evidence. Do not equate
withholding with proven remittance. Activate this mapping only for tax types whose
applicability/evidence has been verified; other withholding mappings remain distinct.

Reports must distinguish charged/unsettled tax, actual collections, authorized
deductions, Holan remittances and client remittance evidence. Partial cash payments
need an agreed tax attribution rule (proportional or explicit approved allocation);
do not decide that rule by assuming every shortfall is withholding. Reject a
deduction exceeding the eligible unpaid component or double-counting the same
evidence. Reporting period is not automatically the legal remittance due date.
Under the collection-split mapping, collected plus withheld tax cannot exceed the
component's remaining settled capacity. Serialize these actions on the same
invoice/component and track reductions/reversals without overwriting old amounts.

## 4. Posting and state transitions

Recommended financial lifecycle: draft → submitted immutable revision → approved
or rejected. Rejected drafts can be revised/resubmitted with retained history.
Approval and posting are separate capabilities. Post only the exact approved
revision, once; invoice issue/official numbering is tied to its authorized posting
workflow. A quotation approval creates no journal. A receipt means cash was
received, not that a sale was earned or goods were accepted.

An advance approval has no cash effect; its Payment records disbursement. A
clearance records costs once and reduces the advance; it must reference already
posted costs or post newly approved costs, never both for the same expenditure.
Returned cash is one incoming cash event linked to the advance, not a client sale
or a second negative expense. Likewise supplier-refund evidence becomes cash only
through one verified incoming event. Receipt detail must distinguish client money,
advance returns and supplier refunds; only client money affects client statements.

Represent delivery, approval, settlement and posting independently. An invoice
can be posted, unpaid and undelivered at once; one overloaded `status` must not
erase these facts. Expose derived labels and permitted actions to the frontend.

Recommended posting sequence, inside one transaction:

1. Validate active staff, action permission and every relevant scope.
2. Acquire idempotency record and relevant source/account locks in documented order.
3. Lock/recheck period, record revision, approval, account/currency and capacities.
4. Recompute amounts from trusted lines/policy; reject inconsistent client totals.
5. Write balanced journal lines, source posting, client/tax movements and allocations.
6. Record audit and durable integration intent, then commit the operation result.
7. Process external document/provider work after commit with retryable identifiers.

Use a consistent lock order across posting, reversals, period close and procurement
integration: operation key; sorted procurement requests where involved; sorted
periods; sorted client/currency accounts; sorted source documents; then settlement
sources/invoices. Document any additional lock class before adding it. Period close
must contend with posting on the same period row so neither races past the other.
External network calls must not be held inside a money/sequence lock transaction.

Enforce row-level nonnegative/one-sided amount checks and uniqueness in PostgreSQL.
Balanced line totals span rows: a normal row CHECK cannot enforce them. Require
service validation plus a deferred PostgreSQL constraint trigger for final posted
balance/line count and account-currency compatibility. Protect posted lines/amounts
against UPDATE/DELETE (including bulk ORM/admin paths), allow drafts only through
validated services, and test the database protections directly. Trigger definitions
and their reversible migrations are part of implementation review, not yet created.

Reversal creates opposite entries linked to the original and reverses related
settlement/tax effects exactly once. It uses an open accounting date and mandatory
reason; it never removes the original. Do not expose a generic journal reversal
that bypasses invoice delivery/cancellation eligibility. Domain journals reverse
through their owning workflow; manual-journal reversal is limited to manual sources.
Whether/how closed periods can reopen remains an explicit policy decision.

No automatic revenue-recognition mapping is assumed for pre-delivery invoices.
Proposed approach: separate billing, customer funds and deferred/earned amounts;
post recognition only on an agreed fulfilment event. Confirm the event and inventory
versus direct-cost mappings before posting procurement sales/costs. The foundation
can be implemented and tested before these operational mappings are activated.

## 5. Direct invoices and document ownership

### Procurement compatibility

Add an explicit item link, price/description snapshots and origin discriminator to
PO lines. Existing lines retain quotation origin and quote relationship; new direct
lines reference a submitted request item and verified invoice approval source.
Quotation becomes nullable only for the direct origin, with conditional constraints.
Backfill the explicit item from existing quote lines using a reviewed migration;
never change their historic quantities/prices or manufacture quote revisions.

Avoid a model dependency cycle by keeping the invoice-to-PO relationship in a
ledger-owned typed `InvoiceOrderLink` (invoice line, PO line, approved quantity).
Procurement's direct-order service obtains trusted ledger invoice data and writes
the order/link atomically through the adapter. The direct invoice does not require
a PO to exist first. Service input UUIDs must resolve to current, authorized,
matching-client source records; a free-text invoice reference is not authority.

Audit `create_po`, fulfilment creation, finance draft validation, PO read serializers
and every `quote_line` traversal. Keep old quotation-led request payloads working.
Payment may serve as client purchase approval evidence, but do not interpret an
unallocated deposit or arbitrary partial payment as approval to buy every item.
Record the exact invoice revision/lines/quantities authorized to purchase; the
full/partial-payment threshold rule still needs confirmation.

### Financial document numbers and storage

Reuse the existing shared annual sequence and `Hol-[CLIENTCODE]-[TYPE]-[YEAR]-[SERIAL]`
scheme. Do not create a second IN/RC sequence. Preserve old QT/WB numbers, assets,
client codes and Lagos year semantics. Sequence gaps must never justify reusing an
issued number. Credit/statement numbering remains separate policy work; do not
invent another official type code in an API without agreement.

Recommended compatibility step: extend the existing register with an explicit
protected client and permit an absent procurement request for IN/RC only. Backfill
client on existing rows; validate client matches request when present. Use
ledger-owned one-to-one source links to registered invoice/receipt versions, keeping
the registry migration independent of ledger tables. QT/WB still require their
existing procurement source. Keep private ledger assets on LedgerDocument and
reuse the layout/storage services without pretending they are procurement uploads.

Existing procurement queries assume `register.request`: scope QT/WB through their
current request and exclude ledger-owned documents from those generic endpoints.
Ledger endpoints resolve IN/RC access through financial records. Test both paths;
making a request nullable must not expose invoice/receipt metadata to procurement.
One receipt covering multiple requests still receives one number and cash record.

Approval lookup uses an authoritative ledger decision for an exact revision/hash,
not a writable registry string. Changed revisions require new approval. PDF/JPG
failures leave approved financial records intact and retry rendering without
reposting or renumbering. Official receipt layout must be implemented; existing
invoice layout support is not proof of receipt rendering.

## 6. Permissions and scope

Every API and direct service requires active authenticated staff or a superuser.
Superusers bypass permissions, not accounting constraints. Default Django view/add/
change permissions cover resources; there is no hard-delete financial API.
Use explicit custom action codenames with one action before the first underscore:

| Proposed native permission (under `ledger`) | Capability |
| --- | --- |
| `approve_invoice`, `approve_quotation` | Decide the exact invoice/quotation revision. |
| `post_invoice`, `post_receipt`, `post_payment` | Commit the corresponding approved monetary event. |
| `approve_expense`, `approve_supplierbill`, `approve_advance` | Authorize costs, obligations and funding. |
| `approve_payment`, `approve_creditnote` | Authorize outgoing amounts and invoice credits. |
| `post_supplierbill`, `post_expense`, `post_advanceclearance` | Post approved obligations/costs and clearances; cash remains on the linked receipt/payment event. |
| `approve_advanceclearance`, `approve_receipt`, `approve_journalentry` | Approve the corresponding immutable revisions under the agreed approval policy. |
| `cancel_invoice` | Request/execute an eligible cancellation with reason; posted credits also require credit approval/posting permissions. |
| `post_creditnote`, `allocate_receipt` | Post approved credits; apply/release receipt value. |
| `allocate_settlementsource` | Apply/release noncash settlement sources, with their underlying view permission. |
| `authorize_clientwithholdingauthorization` | Grant/revoke client eligibility; intended for the Accounts manager's assigned permissions. |
| `approve_withholdingrecord`, `post_withholdingrecord` | Accept documented deductions and commit their settlement/accounting effect. |
| `approve_taxremittance` | Authorize recorded tax remittance; its Payment still needs posting authority. |
| `post_journalentry`, `reverse_journalentry` | Post/reverse manual journals within allowed account mappings. |
| `close_accountingperiod`, `reopen_accountingperiod` | Period actions once the reopening policy is agreed. |
| `view_clientstatement`, `export_clientstatement`, `view_taxreport` | Complete client statements, exports and tax reports. |
| `download_ledgerdocument`, `generate_financialrecord` | Private evidence access and approved document generation. |
| `viewall_financialrecord`, `viewdepartment_financialrecord` | Widen finance visibility beyond owned records. |

For example, `ledger.approve_invoice` becomes `ledger.invoice.approve`;
`ledger.viewall_financialrecord` becomes `ledger.financialrecord.viewall` under the
existing formatter. Store custom permissions on appropriate content types without
changing formatted-permission logic. Finalize all action/resource mappings when
serializers and views are implemented; this is not a claim permissions exist.

Recommended scope: owned finance drafts by default; broader finance department/all
access needs explicit scope permissions. Finance operational records use the
configured Accounts department identity, not whichever department created their
procurement source. Resolve that existing department rather than creating a duplicate.
Company journals, tax reports and bank balances require their full-scope permissions.

Client statements must not be computed from a silently filtered subset of invoices.
Require complete client/currency coverage (initially full-finance scope plus statement
permission); otherwise deny the statement. A partial list is not a reconciliation.
Permission to approve a submitted quotation grants access to its scoped submission
snapshot, not all client CRM data, bank details or unrelated procurement requests.
Source downloads require their own authorization or explicitly approved delegated
submission-evidence access; no helper may bypass this by omitting the actor.

Self-approval restrictions and thresholds remain unapproved policy. Do not hard-code
job titles, grant all staff department access, or invent a maker/checker rule during
implementation. Re-check current permission/scope even on idempotent retries.

## 7. Proposed API contract

Design paths below are relative to `/ledger/`. See [current API contracts](../apps/ledger.md)
for exact implemented names and actions.
Use existing authentication, StandardPagination and DRF error conventions. Decimal
strings and ISO dates are required. Read endpoints do not post, expire or close data.

| Methods and paths | Contract |
| --- | --- |
| GET `capabilities/` | Implemented readiness, missing configuration and actor capabilities; do not equate an installed app with enabled workflows. |
| GET/POST `currencies/`, `accounts/`, `cash-accounts/`, `financial-years/`, `periods/` | Scoped setup; audited PATCH only for allowed mutable configuration. |
| POST `periods/{id}/close/`, `periods/{id}/reopen/` | Version/reason and lifecycle validation. |
| GET `journals/`, `journals/{id}/` | Posted history and permitted manual drafts. |
| POST `manual-journals/`, `manual-journals/{id}/post/`, `manual-journals/{id}/reverse/` | Dedicated manual workflow; cannot edit domain control balances through a bypass. |
| GET/POST `invoices/`, GET/PATCH `invoices/{id}/` | Create/read; PATCH only an editable draft with expected version. |
| POST `invoices/{id}/submit/`, `approve/`, `reject/`, `post/`, `cancel/` | Actions live beneath the invoice detail path; immutable revision and reason where applicable. |
| GET/POST `receipts/`, `payments/`, `supplier-bills/`, `expenses/`, `advances/`, `credit-notes/` | Typed records and details; submit/approve/reject/post actions applicable to their lifecycle. No arbitrary status PATCH. |
| POST `allocations/`, `allocations/{id}/release/` | Source/invoice/amount/effective date; append events, never edit history. |
| GET/POST `withholding-authorizations/`, POST `withholding-authorizations/{id}/revoke/` | Explicit client/tax type/effective interval and evidence; custom authorization permission. |
| GET/POST `withholdings/`, POST `withholdings/{id}/approve/`, `reject/`, `post/` | Record evidence then authorize/post; no automatic deduction just because client is eligible. |
| GET/POST `tax-remittances/`, GET `tax-reports/` | Typed remittance purpose linked to one Payment and period/currency reports. |
| GET `approval-requests/`, POST `approval-requests/{id}/approve/`, `reject/` | Revision-aware queue; dispatch through target-specific permissions/services. |
| POST `client-statements/` | Required client/date_from/date_to/currency; create a consistent immutable report snapshot without changing accounting data. |
| GET `client-statements/{id}/` | Read snapshot metadata, full-range totals and paginated rows with opening/carry-forward balances. |
| POST `client-statements/{id}/export/` | Render the same authorized snapshot; returns private document/job reference. |
| POST `documents/`, GET `documents/{id}/download/` | Multipart evidence and private authorized download; no provider URLs in metadata. |

Business writes require a UUID `Idempotency-Key`; editable drafts/actions also
carry `expected_version` or an exact immutable revision identifier. Store a
canonical request fingerprint (actor, method, path, payload, file checksum), result
identity/status and operation completion atomically. Identical retries return the
same logical result; a key reused differently returns 409. Failed transactions
roll back the claimed key. Source uniqueness also prevents duplicate postings
submitted under different keys. Never persist raw credentials or signed URLs there.

Use 400 for invalid values, existing 401/403 authentication/permission behaviour,
404 for inaccessible objects and 409 for stale versions, duplicate/conflicting
transitions or concurrent capacity conflicts. Use explicit `policy_unconfigured`
or `integration_unavailable` errors when a required mapping/capability is absent;
do not silently issue an official record with zero tax or a guessed exchange rate.
OpenAPI implementation must expose precise serializers and stable error codes.

## 8. Reconciliation and Statement

Use **Reconciliation and Statement** for the feature/navigation name and the title
of its screen, print view and exported documents. Identify the client, currency
and date range beneath that title. This is the client account report with opening
balance, invoiced amounts, receipts, withholding/adjustments and closing balance.
Internal names such as `StatementSnapshot`, `client-statements/` and statement
permission codenames remain concise technical identifiers for the same feature.

The historical calculation follows these rules:

Required parameters: `client`, inclusive `date_from`/`date_to`, `currency`. Accounting
dates use the Lagos business calendar. Recommended initial rule: official invoice
issue date equals its posting accounting date; draft preparation has no statement
effect. Preserve actual occurrence dates separately and agree backdating policy.

1. Authorize complete client/currency coverage and validate start <= end.
2. Establish one consistent database snapshot for opening sum, rows, totals and
   allocations. A timestamp alone is not sufficient while transactions commit.
3. Opening = approved cutover baseline, if used, plus signed posted client movements
   before `date_from` after that baseline. Without imported balances, sum all earlier
   posted movements. The baseline is immediately before its cutover date, not another
   invoice or receipt on that date. Do not add both imported baseline and the source
   history it replaces; flag ranges before available history as incomplete rather
   than reporting fabricated zero balances.
4. Period rows = movements within both endpoints, ordered by accounting date,
   recorded timestamp and stable ID. Running balance starts at opening.
5. Closing = opening + period debits - period credits. Report debit closing as
   balance due, credit closing as credit held, and zero as settled net balance.
6. Show gross invoice issues and actual cash receipts separately from reversal,
   refund, credit and withholding totals. Settlement/allocation events do not
   inflate receipts. Opening balances are not period invoices or receipts.
7. Reconstruct invoice outstanding and unallocated credits using allocation events
   effective by the end date in the same snapshot, not today's settlement status.
8. Freeze snapshot rows/totals, client identity and source cutoff/fingerprint before
   rendering. Subsequent authorized backdating can produce a new snapshot; it
   cannot rewrite an already-exported statement. Interactive pagination must use
   the saved snapshot ID, not rerun live queries for each page. Check current
   client-wide access again for each page/export/download. A snapshot identifier
   is not an authorization token.

Proposed create body (identifiers are illustrative):

```json
{
  "client": "00000000-0000-4000-8000-000000000001",
  "currency": "NGN",
  "date_from": "2026-09-01",
  "date_to": "2026-09-30"
}
```

Snapshot response metadata includes `id`, client identity, dates, currency,
`generated_at`, `opening_balance`, `invoice_total`, `cash_received_total`,
`withheld_total`, separate debit/credit adjustment totals, `closing_balance`,
`balance_due` and `credit_held`. Use decimal strings. Paginated rows retain the
standard `count`, `next`, `previous`, `results` fields and full-range metadata;
include `page_opening_balance` so a later page's running balance is understandable.
Limit snapshot lifetime/storage through the agreed retention policy, not by silently
deleting posted accounting records. Large statement generation can run as a bounded
job with explicit pending/ready/failed state and the same result identity on retry.

Acceptance example (NGN; illustrative arithmetic): opening 20,000; new invoices
100,000; cash receipts 98,000; authorized withholding 2,000; closing due 20,000.
An additional 30,000 receipt on the next day must not affect this period's closing.
Changing an allocation must not change cash received or the net client balance.
A client with no period activity can still have a nonzero opening/closing balance.

## 9. Approval queue and integration delivery

Finance submission validates procurement access at entry, then creates the ledger
approval snapshot in the same database transaction. Accounts reviews the snapshot
under ledger access. Never route through an internal HTTP request or impersonate
the procurement owner to gain request access. Approval/rejection/reissue/cancellation
updates the linked request/task state idempotently for its exact revision.

Store durable integration intent with the business commit so a process crash after
commit cannot lose the task/notification. Reuse notification outbox/deduplication
and task services; do not create another messaging platform. Task assignment needs
the agreed manager-authorized system workflow; ordinary procurement staff do not
gain cross-department assignment rights. Retry task creation with a unique revision
link and recheck authorized recipients. Outbound failure never rolls back a posted
payment or falsely claims a financial decision was undone. Notifications carry
permission-checked links and minimal details; do not attach private tax evidence.

## 10. Implementation sequence and acceptance checks

| Increment | Deliverable and completion evidence |
| --- | --- |
| A — Foundation | Currencies, accounts, periods, immutable balanced journals, manual workflow, permissions and migrations. Isolated PostgreSQL tests prove currency/period/line integrity and concurrency. |
| B — Approvals and documents | Revision-bound approval queue, private evidence, number registry adaptation and notification/task integration under agreed routing policy. Test approval races, revoked access and render retries. |
| C — Invoices and procurement | Flexible invoice timing, direct-order links, cancellation/credit policy and posting mappings. Test both old quote-led and direct flows, partial quantities and delivery races. |
| D — Settlement, tax and statements | Receipts/payments, allocations, withholding eligibility, tax mappings/remittances and date-range statements. Test end-date balances, full/partial payments, unallocated credits, returns and unauthorized deductions. |
| E — Costs and reporting | Supplier bills, expenses, advances/clearance, verified refunds, trial balances and remaining reconciliation reports. Prove bank/source/GL/subledger agreement without double counting. |

For every increment: use Ruff on changed Python files; isolated
`holansladminservices.test_settings` with a verified disposable local PostgreSQL
URL; mock outbound providers; generate/inspect migrations and OpenAPI; update app
docs and ignored development log. Run focused tests and the broadest practical
suite once the change is stable. Never substitute the online DATABASE_URL.

Migration review must include constraints/triggers, reversible additive changes,
backfill counts and existing-data invariants. Fresh disposable tests may run the
full historical chain; application database approval must explicitly cover any
pending old removal migrations. Do not assume the ledger placeholder proves their
deployment state. No application migrations were run by the implementation; runtime validation uses isolated local tests.

Required regressions include permission formatting/provisioning, existing accounts/
CRM/tasks/notifications, quote-led PO creation, document access/numbering and the
replacement-delivery cycle. Replace the placeholder test asserting no ledger models
only when the new models exist; do not remove it just to claim readiness.

## 11. Decisions to settle before dependent implementation

| Decision | Recommendation or boundary |
| --- | --- |
| Rounding/precision | Decimal, minor-unit posting and explicit calculation version; agree rounding mode and aggregation rule. |
| Monthly close and corrections | Monthly periods, immutable postings, linked reversals; agree reopening and privileged backdating. |
| Recognition/inventory | Separate early invoices from earned revenue; agree recognition event and purchase/inventory costing. |
| Partial cancellation | Preserve accepted quantities, release undelivered commitments and credit eligible rejected portions; agree dispatch, return, replacement and paid-invoice cases. |
| Direct purchase approval | Store exact authorized quantities/evidence; agree when payment alone authorizes purchase. |
| Tax mapping | Verify tax type/evidence and partial-payment attribution before activating client-remittance treatment. |
| Approval/scope | Assigned capabilities are agreed; proposed scope above, self-approval and any thresholds still require confirmation. |
| FX | Native-currency operation first; source/timing and consolidated valuation require explicit policy. |
| Task routing/retention | Confirm standing manager authorization versus per-task acceptance, plus evidence retention and credit/statement numbering. |

This specification retains the approved design baseline. The implemented API
documents resolved policies and current boundaries; frontend, deployment and live
financial activity require their own authorized work.
