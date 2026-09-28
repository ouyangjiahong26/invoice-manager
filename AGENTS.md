# Repository Guidelines

## Project Overview
Personal invoice (发票) management tool: ingests Chinese VAT-invoice PDFs, extracts fields via Huawei Cloud OCR, and manages them through a Django admin (categorize, mark used, batch print/export). Chinese-language UI (`zh-hans`), admin-centric — there is no custom front-end app.

## Architecture & Data Flow
Django 3.1 project, one app (`manager`), admin-first design. Ingestion happens **outside** the request cycle:

```
./import/*.pdf
  → python import_invoice.py          (standalone script; django.setup() bootstrap)
    → Wand/ImageMagick rasterizes PDF → 300dpi PNG → base64
    → Huawei Cloud OCR /v1.0/ocr/vat-invoice  (HWOcrClientToken, region cn-north-4)
    → creates manager.Invoice row
    → renames/moves PDF to ./invoices/<发票号>.pdf
/admin/ (simpleui skin)               → categorize, mark used, sum selected prices
manager/views.py                      → read PDFs back from disk:
    merge_invoices/  ?ids=… → PyPDF4-merged single PDF response
    dump_invoices/   ?ids=… → zip attachment invoice_dump.zip ({category}_{id}.pdf)
```

Key invariants:
- Invoice **PDFs live on the filesystem** (`settings.INVOICES_PATH`), not Django media/static; DB rows reference them by id only.
- `settings.IMPORT_PATH` (`./import`) and `INVOICES_PATH` (`./invoices`) are paths **relative to CWD** — scripts must run from repo root.
- OCR credentials are hardcoded placeholders (`xxx`) at module level in `import_invoice.py` — edit there to configure; no env-var support.

## Key Directories
- `manager/` — the only Django app: `models.py`, `views.py`, `urls.py`, `admin.py`, `templates/`, `migrations/`
- `invoice_manager/` — project config: `settings.py`, `urls.py` (root URLconf), `wsgi.py`, `asgi.py`
- `huaweicloud_ocr_sdk/` — **vendored** Huawei OCR SDK (not pip-installed, not a Django app): `HWOcrClientToken.py` (IAM token auth; the only client the app uses), `HWOcrClientAKSK.py` + `apig_sdk/signer.py` (AK/SK signing, demo-only), `OCRDemo.py`/`AutoClassificationDemo.py` (samples with test images in `data/`)
- `./import/`, `./invoices/` — runtime data dirs (gitignored, created at CWD)

## Development Commands
```bash
pip install -r requirements.txt        # deps: Django~=3.1.3, PyPDF4~=1.27.0, Wand~=0.6.3, requests~=2.25.0
python manage.py migrate
python manage.py runserver             # admin at http://127.0.0.1:8000/admin/ (default admin/admin)
python import_invoice.py               # batch-ingest PDFs from ./import/
python manage.py makemigrations manager
python manage.py test                  # runs, but test suite is an empty stub
```
System prerequisites for Wand: **ImageMagick + Ghostscript** must be installed (Windows installer: check "Install development headers and libraries").

## Code Conventions & Common Patterns
- **Function-based views** taking `request.GET['ids']` (comma-separated invoice ids); no DRF, no forms, no class-based views.
- **Admin is the UI**: `manager/admin.py` `InvoiceAdmin` uses `list_display`/`list_filter`/`search_fields`; admin actions are **dynamically generated** via `compile()` + `FunctionType` from source strings (per-category update actions, `use_invoices`). Changelist template overridden at `manager/templates/admin/manager/actions.html` (jQuery price summation); `manager/templates/use_invoices.html` is a JS-only redirect page.
- **Single model** `manager.Invoice`: CharField primary key `id` (发票号码), `company_name`, `company_id`, `price` (FloatField), `used` (Boolean), `category` with choices 交通费/餐饮费/未选择. Change → `makemigrations`.
- File responses via `FileResponse`/`HttpResponse` with `zipfile`/`PyPDF4` in-memory buffers.
- Error handling is minimal — OCR/network failures and missing files are not caught; preserve caller behavior when editing `import_invoice.py`.
- Naming: Chinese field labels/choices in models and templates; English identifiers in code.

## Important Files
- `import_invoice.py` — OCR ingest pipeline; OCR result-dict parsing → `Invoice` fields; also enforces company_id/name match against a hardcoded value
- `invoice_manager/settings.py` — `INSTALLED_APPS` (includes third-party `simpleui`), SQLite at `BASE_DIR/db.sqlite3`, `DEBUG=True`, `IMPORT_PATH`/`INVOICES_PATH` constants (lines ~119-120)
- `manager/admin.py` — all admin actions incl. the `compile()`-based generator
- `manager/views.py` — `merge_invoices`, `dump_invoices`
- `huaweicloud_ocr_sdk/HWOcrClientToken.py` — token client: IAM login → X-Subject-Token, auto-refresh on 401/403, sends base64 image JSON to `ocr.<region>.myhuaweicloud.com`

## Runtime/Tooling Preferences
- **Python + pip only**; `requirements.txt` with `~=` compatible-release pins. No lockfile, no virtualenv config, no Docker/Makefile/CI, no linter/formatter config — don't introduce one unless asked.
- Django 3.1-era code (function views, `django.setup()` manual bootstrap); avoid modern-Django-only APIs unless also pinning an upgrade.
- Vendored SDKs (`huaweicloud_ocr_sdk/`, `apig_sdk/`) are upstream samples — treat as third-party; don't refactor them into app code.

## Testing & QA
- No tests exist: `manager/tests.py` is the untouched scaffold stub. No pytest/coverage tooling configured.
- If adding tests, use Django's built-in `TestCase` (`python manage.py test`); mock `HWOcrClientToken` and note that views/script depend on real files under `INVOICES_PATH`/`IMPORT_PATH` (no test seams currently).
