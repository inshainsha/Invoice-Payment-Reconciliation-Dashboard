import io
import tempfile
import pandas as pd
import pytest

from import_csv import import_csv
from reports import reconciliation_rows, unmatched_invoice_export, unallocated_payment_export, ambiguous_mismatch_export, csv_bytes, matching_performance
from storage import ReconciliationStore


INVOICES = """invoice_id,customer_name,customer_id,invoice_amount,invoice_date
INV-1,Acme Ltd,C1,100.00,2026-01-01
INV-2,Other Ltd,C2,50.00,2026-01-02
INV-3,Unmatched Ltd,C3,75.00,2026-01-03
"""
PAYMENTS = """payment_id,customer_name,customer_id,payment_amount,payment_date,invoice_reference,description
PAY-1,Acme Ltd,C1,80.00,2026-01-03,INV-1,invoice INV-1
PAY-2,Unused Ltd,C4,20.00,2026-01-04,,unallocated
"""


@pytest.fixture
def report_store():
    with tempfile.NamedTemporaryFile(suffix=".sqlite3") as database:
        store = ReconciliationStore(database.name)
        store.import_records("invoice", import_csv(io.StringIO(INVOICES), "invoice"))
        store.import_records("payment", import_csv(io.StringIO(PAYMENTS), "payment"))
        yield store
        store.close()


def test_reconciliation_csv_has_required_columns(report_store):
    report_store.confirm("INV-1", "PAY-1", "80.00", "Exact reference")
    frame = reconciliation_rows(report_store)
    assert list(frame.columns) == ["Invoice ID", "Customer name", "Invoice amount", "Confirmed payment amount", "Outstanding balance", "Payment IDs", "Match status", "Matching explanation", "Review decision"]
    row = frame.loc[frame["Invoice ID"] == "INV-1"].iloc[0]
    assert row["Confirmed payment amount"] == "80.00"
    assert row["Outstanding balance"] == "20.00"
    assert row["Payment IDs"] == "PAY-1"


def test_unmatched_invoice_export(report_store):
    report_store.confirm("INV-1", "PAY-1", "80.00")
    frame = unmatched_invoice_export(report_store)
    assert set(frame["invoice_id"]) == {"INV-2", "INV-3"}


def test_unallocated_payment_export(report_store):
    frame = unallocated_payment_export(report_store)
    assert list(frame["payment_id"]) == ["PAY-1", "PAY-2"]
    assert frame.loc[frame["payment_id"] == "PAY-2", "unallocated_amount"].iloc[0] == "20.00"


def test_ambiguous_and_mismatch_exports(report_store):
    report_store.confirm("INV-1", "PAY-1", "80.00")
    frame = ambiguous_mismatch_export(report_store, [("INV-2", "PAY-2")])
    assert "Amount mismatch" in set(frame["type"])
    assert "Ambiguous" in set(frame["type"])


def test_csv_export_round_trip(report_store):
    frame = reconciliation_rows(report_store)
    exported = csv_bytes(frame)
    round_trip = pd.read_csv(io.BytesIO(exported))
    assert list(round_trip.columns) == list(frame.columns)
    assert len(round_trip) == len(frame)


def test_matching_performance_metrics_are_numeric(report_store):
    report_store.confirm("INV-1", "PAY-1", "80.00", "Labeled match")
    report_store.reject("INV-2", "PAY-2", "Rejected")
    metrics = matching_performance(report_store, labeled_correct_pairs={("INV-1", "PAY-1")})
    assert metrics["matching_precision"] == 1.0
    assert metrics["confirmed_matches"] == 1
    assert metrics["unresolved_cases"] >= 1
    assert metrics["duplicate_allocations_prevented"] == 0


def test_duplicate_prevention_attempt_is_recorded(report_store):
    report_store.confirm("INV-1", "PAY-1", "80.00", "Confirmed")
    with pytest.raises(ValueError, match="already"):
        report_store.confirm("INV-1", "PAY-1", "10.00")
    assert report_store.duplicate_prevention_count() == 1
