import io
import tempfile
from decimal import Decimal
import pytest

from import_csv import import_csv
from reconciliation_report import money, reconciliation_report, classify_invoice
from storage import ReconciliationStore


INVOICES = """invoice_id,customer_name,customer_id,invoice_amount,invoice_date
INV-1,Acme Ltd,C1,100.00,2026-01-01
INV-2,Other Ltd,C2,50.00,2026-01-02
INV-3,Review Ltd,C3,75.25,2026-01-03
"""
PAYMENTS = """payment_id,customer_name,customer_id,payment_amount,payment_date,invoice_reference,description
PAY-1,Acme Ltd,C1,80.00,2026-01-03,INV-1,paid partly
PAY-2,Other Ltd,C2,50.00,2026-01-04,INV-2,paid exact
PAY-3,Review Ltd,C3,100.00,2026-01-05,INV-3,amount mismatch
PAY-4,Unused Ltd,C4,20.00,2026-01-06,,unallocated
"""


@pytest.fixture
def finance_store():
    with tempfile.NamedTemporaryFile(suffix=".sqlite3") as database:
        store = ReconciliationStore(database.name)
        store.import_records("invoice", import_csv(io.StringIO(INVOICES), "invoice"))
        store.import_records("payment", import_csv(io.StringIO(PAYMENTS), "payment"))
        yield store
        store.close()


def test_money_quantizes_to_two_decimal_places():
    assert money("10.125") == Decimal("10.13")


def test_confirmed_paid_and_outstanding_financial_totals(finance_store):
    finance_store.confirm("INV-1", "PAY-1", "80.00")
    finance_store.confirm("INV-2", "PAY-2", "50.00")
    finance_store.confirm("INV-3", "PAY-3", "60.00")
    report = reconciliation_report(finance_store)
    assert report["total_invoice_value"] == Decimal("225.25")
    assert report["total_received_payments"] == Decimal("250.00")
    assert report["total_allocated_payments"] == Decimal("190.00")
    assert report["total_outstanding_balance"] == Decimal("35.25")


def test_invoice_statuses(finance_store):
    finance_store.confirm("INV-1", "PAY-1", "80.00")
    finance_store.confirm("INV-2", "PAY-2", "50.00")
    report = reconciliation_report(finance_store)
    statuses = {row["invoice_id"]: row["status"] for row in report["invoices"]}
    assert statuses["INV-1"] == "Partially Paid"
    assert statuses["INV-2"] == "Paid"
    assert statuses["INV-3"] == "Unpaid"


def test_requires_review_status_for_amount_mismatch(finance_store):
    finance_store.confirm("INV-3", "PAY-3", "70.00")
    report = reconciliation_report(finance_store)
    row = next(item for item in report["invoices"] if item["invoice_id"] == "INV-3")
    assert row["status"] == "Requires Review"


def test_unallocated_payments_do_not_count_as_allocated(finance_store):
    report = reconciliation_report(finance_store)
    assert report["total_allocated_payments"] == Decimal("0.00")
    assert report["total_unallocated_payments"] == Decimal("250.00")
    assert len(report["unallocated_payments"]) == 4


def test_suggestions_and_rejections_do_not_affect_balances(finance_store):
    finance_store.reject("INV-1", "PAY-1", "Rejected suggestion")
    report = reconciliation_report(finance_store)
    assert report["total_allocated_payments"] == Decimal("0.00")
    assert report["total_outstanding_balance"] == Decimal("225.25")


def test_duplicate_invoice_payment_pair_is_blocked(finance_store):
    finance_store.confirm("INV-1", "PAY-1", "80.00")
    with pytest.raises(ValueError, match="already"):
        finance_store.confirm("INV-1", "PAY-1", "10.00")


def test_payment_cannot_be_overallocated(finance_store):
    with pytest.raises(ValueError, match="remaining payment"):
        finance_store.confirm("INV-1", "PAY-1", "81.00")


def test_invoice_cannot_receive_more_than_outstanding(finance_store):
    with pytest.raises(ValueError, match="outstanding balance"):
        finance_store.confirm("INV-1", "PAY-1", "101.00")


def test_confirmed_correction_is_preserved(finance_store):
    finance_store.correct("INV-1", "PAY-1", "75.25", "Corrected allocation")
    report = reconciliation_report(finance_store)
    row = next(item for item in report["invoices"] if item["invoice_id"] == "INV-1")
    assert row["confirmed_paid"] == Decimal("75.25")
    assert row["outstanding"] == Decimal("24.75")
    assert finance_store.decisions()[0]["decision"] == "corrected"
