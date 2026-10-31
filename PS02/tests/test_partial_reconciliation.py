import io
import tempfile
import pytest

from import_csv import import_csv
from storage import ReconciliationStore
from reconciliation_report import reconciliation_report


INVOICES = """invoice_id,customer_name,customer_id,invoice_amount,invoice_date
INV001,Example Customer,C1,10000.00,2026-01-01
INV002,Split Customer,C2,4000.00,2026-01-02
"""
PAYMENTS = """payment_id,customer_name,customer_id,payment_amount,payment_date,invoice_reference,description
P001,Example Customer,C1,4000.00,2026-01-05,INV001,Partial payment
P002,Example Customer,C1,3000.00,2026-01-06,INV001,Partial payment
P003,Split Customer,C2,5000.00,2026-01-07,INV002,Payment split candidate
"""


@pytest.fixture
def partial_store():
    with tempfile.NamedTemporaryFile(suffix=".sqlite3") as database:
        store = ReconciliationStore(database.name)
        store.import_records("invoice", import_csv(io.StringIO(INVOICES), "invoice"))
        store.import_records("payment", import_csv(io.StringIO(PAYMENTS), "payment"))
        yield store
        store.close()


def test_multiple_payments_partially_pay_invoice(partial_store):
    partial_store.confirm("INV001", "P001", "4000.00", "First partial payment")
    partial_store.confirm("INV001", "P002", "3000.00", "Second partial payment")
    assert partial_store.invoice_outstanding("INV001") == 3000
    assert partial_store.payment_remaining("P001") == 0
    assert partial_store.payment_remaining("P002") == 0
    report = reconciliation_report(partial_store)
    invoice = next(row for row in report["invoices"] if row["invoice_id"] == "INV001")
    assert invoice["confirmed_paid"] == 7000
    assert invoice["outstanding"] == 3000
    assert invoice["status"] == "Partially Paid"


def test_payment_can_be_split_across_two_invoices(partial_store):
    partial_store.confirm("INV001", "P003", "3000.00", "Approved split to invoice one")
    partial_store.confirm("INV002", "P003", "2000.00", "Approved split to invoice two")
    assert partial_store.payment_remaining("P003") == 0
    assert partial_store.invoice_outstanding("INV001") == 7000
    assert partial_store.invoice_outstanding("INV002") == 2000


def test_payment_split_can_leave_remaining_unallocated(partial_store):
    partial_store.confirm("INV002", "P003", "2500.00", "Approved partial split")
    assert partial_store.payment_remaining("P003") == 2500


def test_duplicate_invoice_payment_pair_is_blocked(partial_store):
    partial_store.confirm("INV001", "P001", "4000.00")
    with pytest.raises(ValueError, match="already allocated"):
        partial_store.confirm("INV001", "P001", "1000.00")


def test_invoice_outstanding_cannot_be_overallocated(partial_store):
    with pytest.raises(ValueError, match="invoice outstanding"):
        partial_store.confirm("INV001", "P001", "10001.00")


def test_payment_available_balance_cannot_be_overallocated(partial_store):
    partial_store.confirm("INV001", "P001", "4000.00")
    with pytest.raises(ValueError, match="remaining payment"):
        partial_store.confirm("INV002", "P001", "1.00")


def test_overpayment_is_not_silently_allocated(partial_store):
    partial_store.confirm("INV002", "P003", "4000.00", "Allocate only outstanding amount")
    assert partial_store.invoice_outstanding("INV002") == 0
    assert partial_store.payment_remaining("P003") == 1000


def test_allocation_history_records_partial_and_split_events(partial_store):
    partial_store.confirm("INV001", "P001", "4000.00", "Partial record one")
    partial_store.correct("INV002", "P003", "1500.00", "Approved split record two")
    history = partial_store.allocation_history()
    assert len(history) == 2
    assert {row["action"] for row in history} == {"confirmed", "corrected"}
    assert any(row["payment_id"] == "P001" for row in history)
    assert any(row["payment_id"] == "P003" for row in history)
