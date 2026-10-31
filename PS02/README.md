# Invoice and Payment Reconciliation Dashboard

## Project overview

PS02 is a Streamlit application for importing invoice and payment CSV files, validating records, suggesting evidence-based matches, reviewing decisions, maintaining persistent allocations, and exporting reconciliation reports.

## Features

- CSV import and row-level validation
- SQLite persistence and duplicate-import protection
- Decimal-based matching and allocation calculations
- RapidFuzz customer-name and description matching
- Interactive confirm, reject, and manual-assignment actions
- Outstanding balances, payment statuses, and mismatch alerts
- Reconciliation, unmatched, unallocated, and ambiguous/mismatch CSV downloads
- Search, status, customer, and date-ready data views
- Responsive fintech-style Streamlit interface

## Technology stack

- Python
- Pandas
- Decimal
- RapidFuzz
- SQLite
- Streamlit
- pytest

## Installation

From the repository root:

```bash
cd PS02
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Python 3.10 or newer is recommended.

## Run the application

```bash
cd PS02
streamlit run app.py --server.address 0.0.0.0
```

Open the URL printed by Streamlit. The application uses `PS02/reconciliation.sqlite3` and preserves confirmed allocations across restarts.

## Sample dataset instructions

The labeled demo files are in:

- `PS02/data/invoices.csv`
- `PS02/data/payments.csv`

Enable **Load labeled sample files** in the Import Data page. The samples include exact matches, fuzzy records, amount mismatches, ambiguous payments, unmatched invoices, unallocated payments, and duplicate-looking transactions.

Invoice CSV columns:

```text
invoice_id,customer_name,customer_id,invoice_amount,invoice_date
```

Payment CSV columns:

```text
payment_id,customer_name,customer_id,payment_amount,payment_date,invoice_reference,description
```

## Run tests

```bash
cd PS02
pytest -q
```

## Exact command summary

```bash
cd PS02
python -m pip install -r requirements.txt
pytest -q
streamlit run app.py --server.address 0.0.0.0
```

## Screenshots

Screenshots will be added here after a browser verification session.
