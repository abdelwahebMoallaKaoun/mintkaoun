# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/abdelwahebMoallaKaoun/mintkaoun/compare/v1.6.1...HEAD
[1.6.1]: https://github.com/abdelwahebMoallaKaoun/mintkaoun/compare/v1.6.0...v1.6.1
[1.6.0]: https://github.com/abdelwahebMoallaKaoun/mintkaoun/compare/v1.5.4...v1.6.0
[1.5.4]: https://github.com/abdelwahebMoallaKaoun/mintkaoun/compare/v1.5.3...v1.5.4
[1.5.3]: https://github.com/abdelwahebMoallaKaoun/mintkaoun/releases/tag/v1.5.3
