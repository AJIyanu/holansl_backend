# HolanSL CRM Documentation

The CRM backend is documented in the following guides:

* [CRM Application and Architecture Guide](CRM_APPLICATION_GUIDE.md) — explains the purpose of CRM, its importance, main concepts, capabilities, security boundaries and intended use.
* [CRM Developer Reference](CRM_DEVELOPER_REFERENCE.md) — explains the important source files, classes, functions and common developer workflows.

## Current application status

The CRM application is active and independent.

The Procurement and Ledger applications are intentionally empty placeholders. Their previous models, APIs and database tables were removed so that both domains can be redesigned from the beginning.

The following applications remain active:

* Accounts
* CRM
* Notifications
* Tasks

Procurement and Ledger remain installed only to preserve migration history and reserve their application names for future development.

## `docs/crm/CRM_APPLICATION_GUIDE.md`

# HolanSL CRM Application and Architecture Guide

## Purpose

The HolanSL CRM application is the master source of information about external people and businesses.

It represents clients, suppliers, prospective clients, service providers, logistics providers, marketplace sellers, market traders and other external parties.

## Importance

CRM ensures that every external person or business has a stable and referenceable identity.

It prevents different modules from maintaining separate copies of the same client or supplier and provides a reliable location for contact details, relationship history, source information, documents and sensitive records.

## Party model

The central CRM record is `Party`.

A Party may represent:

* An organisation.
* An individual.
* A trading name or informal business.

A Party may hold multiple roles through `PartyRole`.

For example, the same business may be both a client and supplier without creating duplicate Party records.

## Informal and online suppliers

A supplier does not need to be formally registered.

CRM supports suppliers discovered through:

* Jumia.
* eBay.
* Other online marketplaces.
* Social media.
* Physical markets.
* Referrals.
* Direct telephone or WhatsApp contact.
* Previous transactions.

A minimal supplier may be recorded with only a name and one traceable source, such as a telephone number, marketplace account, seller URL or market location.

## Organisation contacts

People are stored as individual Party records.

Their relationship with an organisation is represented by `PartyAffiliation`.

This allows a person to:

* Represent more than one organisation.
* Change job title.
* Leave an organisation without losing history.
* Hold different contact roles for different organisations.

## Party lifecycle

Supported Party states are:

* Active.
* Inactive.
* Suspended.
* Blocked.
* Merged.

Archival is separate from status.

Merged Party records remain as permanent tombstones pointing to the surviving Party.

## Duplicate handling

CRM can identify possible duplicates using:

* Normalised names.
* Email addresses.
* Telephone numbers.
* Marketplace seller IDs.
* Seller-profile URLs.
* Listing URLs.

Duplicate records can be merged transactionally by authorised users.

## Sensitive information

Registration numbers, tax identifiers and payment details are stored separately from ordinary Party data.

Sensitive values are:

* Encrypted before database storage.
* Masked in normal API responses.
* Revealed only through permission-controlled endpoints.
* Audited whenever revealed.

## Documents

CRM documents are stored outside the Django database.

Supported storage providers are:

* Google Drive.
* Supabase Storage.

The database stores document metadata, ownership, checksum, storage provider and provider object identifiers.

## Notes and interactions

CRM supports:

* Internal notes.
* Confidential notes.
* Calls.
* Emails.
* WhatsApp interactions.
* Meetings.
* Marketplace messages.
* Site visits.
* Follow-up dates.

## Notifications

CRM uses the shared Notifications application for meaningful events such as:

* Payment-detail changes.
* Document expiry.
* Other important CRM lifecycle events.

CRM does not implement its own notification system.

## Tasks

The Tasks application remains independent.

A future workflow may link a Task to CRM, but CRM does not require Tasks to create or maintain Party records.

## Procurement and Ledger status

The former Procurement and Ledger implementations were removed.

Their apps currently contain:

* No models.
* No API endpoints.
* No database tables.
* No CRM relationships.

They will be redesigned as separate workstreams.

## API areas

Main CRM routes include:

* `/crm/parties/`
* `/crm/party-roles/`
* `/crm/contact-methods/`
* `/crm/addresses/`
* `/crm/sources/`
* `/crm/contact-roles/`
* `/crm/affiliations/`
* `/crm/notes/`
* `/crm/interactions/`
* `/crm/identifiers/`
* `/crm/bank-accounts/`
* `/crm/documents/`

## Security authority

Frontend permissions control presentation only.

The Django backend remains responsible for:

* Authentication.
* Model permissions.
* Action permissions.
* Sensitive-value access.
* Confidential-note access.
* Confidential-document access.
* Audit logging.