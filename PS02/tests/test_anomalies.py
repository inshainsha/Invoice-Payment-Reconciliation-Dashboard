import io
import tempfile
import pandas as pd
import pytest
from decimal import Decimal

from anomalies import detect_all, detect_payment_anomalies, detect_invoice_anomalies, ALGORITHM_VERSION
from import_csv import import_csv
from storage import ReconciliationStore


@pytest.fixture
def anomaly_store():
    with tempfile.NamedTemporaryFile(suffix=".sqlite3") as database:
        store = ReconciliationStore(database.name)
        yield store
        store.close()


def test_exact_duplicate_payment_id_is_critical():
    frame = pd.DataFrame([
        {"payment_id":"P1","customer_name":"Acme","customer_id":"C1","payment_amount":"100.00","payment_date":"2026-01-01","invoice_reference":"INV1"},
        {"payment_id":"P1","customer_name":"Acme","customer_id":"C1","payment_amount":"100.00","payment_date":"2026-01-01","invoice_reference":"INV1"},
    ])
    anomalies = detect_payment_anomalies(frame)
    assert any(item["severity"] == "Critical" and item["kind"] == "Exact duplicate payment ID" for item in anomalies)


def test_repeated_invoice_reference_is_warning():
    frame = pd.DataFrame([
        {"payment_id":"P1","customer_name":"Acme","customer_id":"C1","payment_amount":"100.00","payment_date":"2026-01-01","invoice_reference":"INV1"},
        {"payment_id":"P2","customer_name":"Acme","customer_id":"C1","payment_amount":"100.00","payment_date":"2026-01-02","invoice_reference":"INV1"},
    ])
    anomalies = detect_payment_anomalies(frame)
    assert any(item["kind"] == "Repeated transaction reference" for item in anomalies)


def test_same_customer_amount_date_is_suspicious():
    frame = pd.DataFrame([
        {"payment_id":"P1","customer_name":"Acme","customer_id":"C1","payment_amount":"100.00","payment_date":"2026-01-01","invoice_reference":"INV1"},
        {"payment_id":"P2","customer_name":"Acme","customer_id":"C1","payment_amount":"100.00","payment_date":"2026-01-01","invoice_reference":"INV2"},
    ])
    anomalies = detect_payment_anomalies(frame)
    assert any(item["kind"] == "Suspicious repeated payment" for item in anomalies)


def test_equal_amounts_alone_are_not_duplicate_proof():
    frame = pd.DataFrame([
        {"payment_id":"P1","customer_name":"Acme","customer_id":"C1","payment_amount":"100.00","payment_date":"2026-01-01","invoice_reference":"INV1"},
        {"payment_id":"P2","customer_name":"Beta","customer_id":"C2","payment_amount":"100.00","payment_date":"2026-02-01","invoice_reference":"INV2"},
    ])
    anomalies = detect_payment_anomalies(frame)
    assert not any(item["kind"] in ("Suspicious repeated payment", "Same customer, amount and date") for item in anomalies)


def test_missing_invoice_reference_is_informational():
    frame = pd.DataFrame([{"payment_id":"P1","customer_name":"Acme","customer_id":"C1","payment_amount":"100.00","payment_date":"2026-01-01","invoice_reference":""}])
    anomalies = detect_payment_anomalies(frame)
    assert any(item["severity"] == "Informational" and item["kind"] == "Missing invoice reference" for item in anomalies)


def test_inconsistent_customer_identity_is_warning():
    frame = pd.DataFrame([
        {"payment_id":"P1","customer_name":"Acme Ltd","customer_id":"C1","payment_amount":"100.00","payment_date":"2026-01-01","invoice_reference":"INV1"},
        {"payment_id":"P2","customer_name":"Acme Incorporated","customer_id":"C1","payment_amount":"200.00","payment_date":"2026-01-02","invoice_reference":"INV2"},
    ])
    anomalies = detect_payment_anomalies(frame)
    assert any(item["kind"] == "Inconsistent customer identity" and item["severity"] == "Warning" for item in anomalies)


def test_possible_duplicate_invoice_is_detected():
    frame = pd.DataFrame([
        {"invoice_id":"INV1","customer_name":"Acme","customer_id":"C1","invoice_amount":"100.00","invoice_date":"2026-01-01"},
        {"invoice_id":"INV2","customer_name":"Acme","customer_id":"C1","invoice_amount":"100.00","invoice_date":"2026-01-01"},
    ])
    anomalies = detect_invoice_anomalies(frame)
    assert any(item["kind"] == "Possible duplicate invoice" for item in anomalies)


def test_large_amount_difference_is_warning():
    invoices = pd.DataFrame([{"invoice_id":"INV1","customer_name":"Acme","customer_id":"C1","invoice_amount":"1000.00","invoice_date":"2026-01-01"}])
    payments = pd.DataFrame([{"payment_id":"P1","customer_name":"Acme","customer_id":"C1","payment_amount":"500.00","payment_date":"2026-01-01","invoice_reference":"INV1","description":"partial"}])
    anomalies = detect_all(invoices, payments, Decimal("400.00"))
    assert any(item["kind"] == "Unusually large amount difference" and item["severity"] == "Warning" for item in anomalies)


def test_anomaly_review_persists_without_financial_changes(anomaly_store):
    anomalies = [{"kind":"Missing invoice reference","record_type":"Payment","record_id":"P1","severity":"Informational","explanation":"No reference","related_ids":"","algorithm_version":ALGORITHM_VERSION,"review_status":"Unreviewed"}]
    anomaly_store.save_anomalies(anomalies)
    anomaly = anomaly_store.anomalies()[0]
    anomaly_store.mark_anomaly_reviewed(anomaly["anomaly_key"])
    reviewed = anomaly_store.anomalies(status="Reviewed")[0]
    assert reviewed["review_status"] == "Reviewed"
    assert anomaly_store.allocations() == []
    assert anomaly_store.decisions() == []
