import io
import tempfile
from import_csv import import_csv, normalize_customer_name
from storage import ReconciliationStore


INVOICE_CSV = """invoice_id,customer_name,customer_id,invoice_amount,invoice_date
INV-1,  Northstar   Foods ,CUST-1,1250.00,2026-01-05
INV-2,Bluebird Studio,CUST-2,-10.00,not-a-date
INV-1,Duplicate,CUST-3,300.00,2026-01-06
"""

PAYMENT_CSV = """payment_id,customer_name,customer_id,payment_amount,payment_date,invoice_reference,description
PAY-1,Northstar Foods,CUST-1,1250.00,2026-01-06,INV-1,Exact match
"""


def test_normalize_customer_name():
    assert normalize_customer_name("  northstar   foods  ") == "Northstar Foods"


def test_invoice_import_reports_rows_and_errors():
    result = import_csv(io.StringIO(INVOICE_CSV), "invoice")
    assert result["total"] == 3
    assert len(result["valid"]) == 1
    assert result["invalid_count"] == 2
    assert {error["field"] for error in result["errors"]} >= {"invoice_amount", "invoice_date", "invoice_id"}


def test_payment_import_accepts_required_columns():
    result = import_csv(io.StringIO(PAYMENT_CSV), "payment")
    assert len(result["valid"]) == 1
    assert result["invalid_count"] == 0


def test_duplicate_import_is_blocked():
    with tempfile.NamedTemporaryFile(suffix=".sqlite3") as database:
        store = ReconciliationStore(database.name)
        result = import_csv(io.StringIO(PAYMENT_CSV), "payment")
        store.import_records("payment", result)
        try:
            store.import_records("payment", result)
            assert False, "duplicate import should raise ValueError"
        except ValueError as error:
            assert "already been imported" in str(error)
        finally:
            store.close()


def test_missing_columns_are_never_silent():
    result = import_csv(io.StringIO("invoice_id,customer_name\nINV-1,Test\n"), "invoice")
    assert result["invalid_count"] == 1
    assert result["errors"][0]["field"] == "columns"


def test_labeled_sample_files_import_end_to_end():
    invoice_result = import_csv("data/invoices.csv", "invoice")
    payment_result = import_csv("data/payments.csv", "payment")
    assert invoice_result["total"] == 10
    assert invoice_result["invalid_count"] >= 2
    assert len(invoice_result["valid"]) >= 8
    assert payment_result["total"] == 10
    assert payment_result["invalid_count"] == 1
    assert len(payment_result["valid"]) == 9
