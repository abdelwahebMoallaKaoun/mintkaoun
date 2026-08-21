# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**Mint** is a Frappe/ERPNext app providing an enhanced bank reconciliation UI. It adds a modern React SPA on top of ERPNext's standard bank reconciliation, with AI-assisted bank statement import via Google Cloud Document AI, automatic matching rules, and bank clearance reports.

## Commands

### Frontend (run from `frontend/`)
```bash
yarn dev        # Dev server on port 8080 with HMR
yarn build      # Production build → mint/public/mint/
yarn lint       # ESLint
```

### Backend (run from the Frappe bench root, not this repo)
```bash
bench run-tests --app mint                                          # All backend tests
bench run-tests --app mint "mint.mint.doctype.<module>.test_<module>"  # Single test module
bench --site <site> install-app mint                               # Install app on a site
bench build --app mint                                             # Build frontend assets via bench
```

### Root workspace
```bash
yarn dev        # Delegates to frontend/
yarn build      # Delegates to frontend/
```

## Versioning & releases

The version follows [Semantic Versioning](https://semver.org/). The single source of truth is `__version__` in `mint/__init__.py` — `pyproject.toml` reads it dynamically via flit (`dynamic = ["version"]`). Both `package.json` files are kept in sync with it, and notable changes are recorded in `CHANGELOG.md` (Keep a Changelog format) under `## [Unreleased]`.

To cut a release, run from the repo root:
```bash
python scripts/bump_version.py patch     # or: minor | major | an explicit X.Y.Z
```
This updates all version files, moves the `[Unreleased]` changelog entries under the new version with today's date, then creates a `chore: release v<version>` commit and an annotated `v<version>` git tag. Push with `git push && git push origin v<version>`. Use `--no-git` to edit files only, or `--dry-run` to preview.

## Architecture

### Two-layer structure

**Backend (`mint/`)** — Frappe/Python layer:
- `mint/apis/` — Whitelisted API endpoints called by the frontend. Each file owns a domain: `transactions.py` (fetching bank transactions), `bank_reconciliation.py` (matching logic), `rules.py` (auto-matching rule evaluation), `statement_import.py` (CSV/PDF import pipeline), `google_ai.py` (Document AI PDF parsing), `bank_clearance.py` (clearance reports), `bank_account.py` (account helpers).
- `mint/mint/doctype/` — Custom Frappe doctypes: `mint_bank_transaction_rule`, `mint_settings`, `mint_bank_statement_import`, and others.
- `mint/mint/page/bank_reconciliation/` — A stub Frappe page (`bank-reconciliation`) that only redirects to `/mint`. It does not render the React app.
- `mint/overrides/` — Overrides standard ERPNext doctypes (e.g. `bank_account.py`).
- `mint/hooks.py` — App lifecycle hooks: doc events, scheduler jobs, permissions, fixtures.
- `mint/www/mint.py` — Serves the React SPA at the `/mint` route.

**Frontend (`frontend/src/`)** — React/TypeScript SPA:
- `pages/` — Two top-level pages: `BankReconciliation.tsx` and `BankStatementImporter.tsx`.
- `components/features/` — Feature folders (`BankReconciliation/`, `BankStatementImporter/`, `Settings/`, `ActionLog/`) that contain all UI specific to each feature.
- `components/ui/` — Radix UI-based primitives (Button, Dialog, Table, etc.) used across features.
- `components/common/` — Shared cross-feature components.
- `hooks/` — Custom React hooks, many wrapping `frappe-react-sdk` calls.
- `lib/` — Utilities: `date.ts`, `currency.ts`, `frappe.ts` (API helpers), `namespace/` (Frappe JS namespace extensions).
- `types/` — TypeScript types for Mint entities, Frappe API responses, accounts, etc.

### Frontend → Backend communication

All API calls go through Frappe's whitelist mechanism. The frontend uses `frappe-react-sdk` and direct `frappe.call()` to invoke `@frappe.whitelist()` Python functions in `mint/apis/`. No REST layer — just direct Frappe method calls.

### State management

- **Jotai atoms** for global/shared state (filters, selections, reconciliation state).
- **React Hook Form + Zod** for all form validation.
- **TanStack Table** for all data tables with sorting/filtering.

### Build output

`yarn build` in `frontend/` runs Vite and outputs to `mint/public/mint/`. Frappe serves this at `/assets/mint/mint/`. The build's `copy-html-entry` step then copies `mint/public/mint/index.html` to `mint/www/mint.html`, which is the page Frappe renders at `/mint` (its Jinja context comes from `mint/www/mint.py`).

Both `mint/public/mint/` and `mint/www/mint.html` are gitignored — they are build artifacts, produced at image build time. A checkout without a build has no servable frontend.

## Key dependencies

| Area | Library |
|------|---------|
| Frontend framework | React 19, React Router 7 |
| Build | Vite 6, TypeScript 5.7 |
| Styling | TailwindCSS 4 |
| UI primitives | Radix UI |
| Frappe integration | frappe-react-sdk 1.12 |
| Forms | React Hook Form + Zod |
| Tables | TanStack React Table 8 |
| State | Jotai |
| Fuzzy search | Fuse.js |
| Dates | date-fns, dayjs |
| Icons | lucide-react |
| Toasts | sonner |
| PDF parsing | Google Cloud Document AI |
| Backend framework | Frappe ≥15, ≤17-dev |

## Frappe-specific patterns

- **DocType controllers** live alongside their JSON definition in `doctype/<name>/`.
- **Patches** (data migrations) are listed in `mint/patches.txt` and implemented in `mint/patches/`.
- **Fixtures** (default data like rules/settings) are declared in `hooks.py` under `fixtures`.
- **Scheduler jobs** are registered in `hooks.py` under `scheduler_events`.
- **Permissions** follow Frappe's role-based permission model; the app applies user-level bank account permissions in `mint/apis/bank_account.py`.
