# Tax Ledger — approved operational design

Approved 2026-11-09 for backend and frontend. VAT and WHT share a screen, never a
single interchangeable balance. Holan's selected timing is payment or delivery,
whichever first; document issuance alone does not post VAT. This operational rule
differs from the statutory invoice-trigger rule discussed with the user and is
not a claim of full statutory compliance. Electronic invoicing and WHT certificate
tracking are explicitly deferred.

## Posting and identity

Immutable TaxEntry rows reference existing party transactions/documents and record
signed changes in output/input VAT, VAT cash, WHT assets/liabilities, remittances,
CIT and party VAT balances. Shared document UUID keys group partial payments,
delivery and corrections. Receipt/payment posting remains goods-only in the
existing allocation engine; tax receivables/payables form an additional, explicitly
reported component of party balances. No second journal is generated for historical
cash or taxes already present. Supplemental journal pairs implement delivery VAT,
settlement/reclassification, approved offsets and corrections.

For each linked document, required VAT recognition is the greater of net VAT paid
and delivered VAT remaining after credits. Every event posts only the difference
from recognition already recorded. Later receipts settle delivered VAT; payment
before delivery recognises only its paid portion. VAT-only settlement is available
for goods paid without VAT, without inventing another goods payment. Receipts must
not exceed a linked document's remaining goods/tax amounts. Existing raw allocation
links cannot silently establish or move tax ownership.

Purchase input VAT defaults available for taxable resale; authorised dated reviews
can classify it pending or non-recoverable. Reclassification moves journal amounts
between input VAT, pending input VAT and expense, preserving audit history.

## Operations

Separate permissions: view, remit, approve, adjust, configure, apply CIT credits,
and export. Draft operation requests have no financial effect. Approval validates
current balances and posts atomically; self-approval is allowed and both actors are
retained. Remittance records an external payment already made, not a government API
transfer. Fields include authority, tax type, period, amount, paying account,
reference, description and optional private evidence. CIT liabilities are approved
manual assessments; WHT credits reduce CIT and the WHT asset without cash. Supplier
WHT belongs to suppliers and cannot offset Holan CIT. Partial settlement is allowed.

Transaction/advisory locks and immutable source keys protect retries and concurrent
settlements. Posted corrections append records. File uploads use private recorded
providers and rollback compensation. Cash entries from tax operations cannot be
edited or voided independently. Currency balances are separate; no conversion.

## Reports and rollout

Dashboard separates VAT recognition, collection, outstanding tax, input categories,
VAT liability/credit, client WHT, supplier WHT, remittances and CIT utilisation.
Statements add goods, VAT recognition/cash, WHT and actual cash plus opening/closing
tax and combined balances. Existing snapshots remain unchanged.

Backfill defaults to preview. Apply creates tax references to historical journal
amounts without rewriting financial entries; discrepancies and delivered VAT gaps
are reported for explicit approved opening adjustments. Application migrations and
backfill application are operator actions, not automated development steps.
