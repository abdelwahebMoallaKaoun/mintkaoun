# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.7.1] - 2026-09-06

### Fixed
- Bulk reconciliation actions no longer block every other document creation on the site for the duration of the run (ACC-1607). `create_bulk_internal_transfer`, `create_bulk_bank_entry_and_reconcile` and `create_bulk_payment_entry_and_reconcile` inserted, submitted and reconciled every selected transaction inside a single request-long database transaction. Each Payment Entry / Journal Entry insert takes a `FOR UPDATE` lock on its naming series row in `tabSeries`, and InnoDB holds that lock until commit — so the first insert of a bulk run held the series for the whole request and any concurrent insert of the same doctype, anywhere on the site, waited 50 seconds and failed with `Lock wait timeout exceeded; try restarting transaction`. Each transaction is now committed as soon as its voucher is created, submitted and reconciled, which releases the naming series between items.
- A failing transaction in a bulk run no longer discards the whole batch (ACC-1607). Because items are now committed one at a time, a failure rolls back only that item; the ones already done stay reconciled, the run continues with the rest, and the full traceback is written to the Error Log. The bulk endpoints return `{"results": [...], "errors": [...]}` instead of a bare list, and the dialogs log only the successes to the action log and raise a toast naming the transactions that failed. A dialog now closes only when at least one transaction went through, so a run that fails outright keeps the form — and, for a payment, the per-transaction invoice allocations — intact for a retry. The bulk dialogs also handle a rejected request instead of leaving an unhandled promise rejection: the transaction list refreshes, the failure is logged and surfaced as a toast, and the error banner explains what went wrong.

## [1.7.0] - 2026-08-21

### Added
- Invoice allocation in the bulk **Record Payment** flow (ACC-1546). Selecting several bank transactions now lets each one be expanded and allocated across the party's outstanding invoices, so the payment entries settle invoices instead of landing unallocated on the party's account. Allocation stays optional per transaction — leaving one untouched creates an unallocated payment entry, as before. `create_bulk_payment_entry_and_reconcile` accepts the allocations keyed by bank transaction and rejects, before writing anything, a transaction allocating more than it is worth or an invoice allocated beyond its outstanding amount across the batch.

### Fixed
- The **Mode of Payment** picked in the bulk **Record Payment** dialog is no longer silently discarded (ACC-1545). The submit payload never sent the field, so every payment entry created from a multi-transaction selection had an empty mode of payment.

## [1.6.3] - 2026-08-20

### Fixed
- Bank reconciliation now uses net payment amounts so reconciled transactions update Mint balances correctly.

## [1.6.2] - 2026-08-17

### Fixed
- Bank reconciliation no longer lists vouchers with a zero paid amount (`0.000`) as available matches.

## [1.6.1] - 2026-08-05

### Fixed
- Bulk payment entry creation no longer fails with `MandatoryError: [Payment Entry]: company`. `create_bulk_payment_entry_and_reconcile` read `company` off a bank transaction row that was never fetched, so every payment entry was built with an empty company. It is now derived from the bank account's GL account, matching the other payment entry and journal entry creation paths.

## [1.6.0] - 2026-06-29

### Added
- Support 3-decimal currency precision across the reconciliation screen (display, amount inputs, balances and statement importer). Precision now honors Frappe's `currency_precision` system default and falls back to 3 decimals when it is unset, so existing 2-decimal amounts display with a trailing zero (e.g. `10.12` → `10.120`).

## [1.5.4] - 2026-06-18

### Added
- Display the party name next to the party code in the bank reconciliation match cards.
- Versioning workflow: `CHANGELOG.md`, synced version across `mint/__init__.py` and both `package.json` files, and a `scripts/bump_version.py` release helper.

## [1.5.3]

- Baseline version. Changes prior to the introduction of this changelog are not itemized.

[Unreleased]: https://github.com/abdelwahebMoallaKaoun/mintkaoun/compare/v1.7.1...HEAD
[1.7.1]: https://github.com/abdelwahebMoallaKaoun/mintkaoun/compare/v1.7.0...v1.7.1
[1.7.0]: https://github.com/abdelwahebMoallaKaoun/mintkaoun/compare/v1.6.3...v1.7.0
[1.6.3]: https://github.com/abdelwahebMoallaKaoun/mintkaoun/compare/v1.6.2...v1.6.3
[1.6.2]: https://github.com/abdelwahebMoallaKaoun/mintkaoun/compare/v1.6.1...v1.6.2
[1.6.1]: https://github.com/abdelwahebMoallaKaoun/mintkaoun/compare/v1.6.0...v1.6.1
[1.6.0]: https://github.com/abdelwahebMoallaKaoun/mintkaoun/compare/v1.5.4...v1.6.0
[1.5.4]: https://github.com/abdelwahebMoallaKaoun/mintkaoun/compare/v1.5.3...v1.5.4
[1.5.3]: https://github.com/abdelwahebMoallaKaoun/mintkaoun/releases/tag/v1.5.3
