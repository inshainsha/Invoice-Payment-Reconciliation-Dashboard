import tempfile
import io
import pytest

from import_csv import import_csv
from storage import ReconciliationStore
from review import ReviewActions, parse_review_amount


INVOICES = """invoice_id,customer_name,customer_id,invoice_amount,invoice_date
INV-1,Acme Ltd,C1,100.00,2026-01-01
INV-2,Other Ltd,C2,50.00,2026-01-02
"""
PAYMENTS = """payment_id,customer_name,customer_id,payment_amount,payment_date,invoice_reference,description
PAY-1,Acme Ltd,C1,80.00,2026-01-03,INV-1,invoice INV-1
PAY-2,Other Ltd,C2,100.00,2026-01-04,INV-2,invoice INV-2
"""


@pytest.fixture
def review_store():
    with tempfile.NamedTemporaryFile(suffix=".sqlite3") as database:
        store = ReconciliationStore(database.name)
        store.import_records("invoice", import_csv(io.StringIO(INVOICES), "invoice"))
        store.import_records("payment", import_csv(io.StringIO(PAYMENTS), "payment"))
        yield store
        store.close()


def test_confirm_match_persists_and_reduces_outstanding(review_store):
    review_store.confirm("INV-1", "PAY-1", "80.00", "Confirmed exact match")
    assert review_store.invoice_outstanding("INV-1") == 20
    assert review_store.payment_remaining("PAY-1") == 0
    assert review_store.decisions()[0]["decision"] == "confirmed"


def test_confirmed_allocations_survive_restart(review_store):
    review_store.confirm("INV-1", "PAY-1", "80.00")
    path = review_store.connection.execute("PRAGMA database_list").fetchone()[2]
    review_store.close()

    reopened = ReconciliationStore(path)
    assert reopened.invoice_outstanding("INV-1") == 20
    assert reopened.payment_remaining("PAY-1") == 0
    reopened.close()


def test_duplicate_allocation_is_blocked(review_store):
    review_store.confirm("INV-1", "PAY-1", "80.00")
    with pytest.raises(ValueError, match="already"):
        review_store.confirm("INV-1", "PAY-1", "1.00")


def test_invoice_overallocation_is_blocked(review_store):
    with pytest.raises(ValueError, match="outstanding balance"):
        review_store.confirm("INV-2", "PAY-1", "60.00")


def test_payment_overallocation_is_blocked(review_store):
    with pytest.raises(ValueError, match="remaining payment"):
        review_store.confirm("INV-1", "PAY-1", "81.00")


def test_reject_match_persists_decision(review_store):
    actions = ReviewActions(review_store)
    actions.reject_match("INV-1", "PAY-1", "Incorrect customer")
    assert review_store.decisions()[0]["decision"] == "rejected"


def test_manual_assignment_persists_corrected_decision(review_store):
    actions = ReviewActions(review_store)
    actions.manual_assign("INV-2", "PAY-2", "25.00", "Corrected wrong suggestion")
    assert review_store.invoice_outstanding("INV-2") == 25
    assert review_store.decisions()[0]["decision"] == "corrected"


def test_invalid_manual_amount_is_rejected(review_store):
    assert parse_review_amount("not money") is None
    assert parse_review_amount("-1.00") == None
