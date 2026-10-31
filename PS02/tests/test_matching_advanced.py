import pandas as pd
import tempfile
import io

from matching_advanced import evaluate_match, generate_match_suggestions, DEFAULT_WEIGHTS, DEFAULT_THRESHOLDS, ALGORITHM_VERSION
from import_csv import import_csv
from storage import ReconciliationStore


def invoice(invoice_id="INV-1", name="Northstar Foods", customer_id="C1", amount="100.00", date="2026-01-01", reference=None):
    return {"invoice_id": invoice_id, "invoice_reference": reference or invoice_id, "customer_name": name,
            "customer_id": customer_id, "invoice_amount": amount, "invoice_date": date}


def payment(payment_id="PAY-1", name="Northstar Foods", customer_id="C1", amount="100.00", date="2026-01-01", reference="INV-1"):
    return {"payment_id": payment_id, "invoice_reference": reference, "customer_name": name,
            "customer_id": customer_id, "payment_amount": amount, "payment_date": date,
            "description": "Northstar Foods INV-1 payment"}


def test_exact_match_has_weighted_contribution_evidence():
    result = evaluate_match(invoice(), payment())
    assert result["score"] >= 80
    assert result["category"] == "High Confidence"
    assert all("factor" in item and "points" in item and "explanation" in item for item in result["contributions"])
    assert result["algorithm_version"] == ALGORITHM_VERSION


def test_weight_changes_score():
    weights = DEFAULT_WEIGHTS.copy()
    weights["invoice_reference"] = 0
    result = evaluate_match(invoice(), payment(), weights=weights)
    assert result["score"] < 100


def test_custom_threshold_changes_category():
    thresholds = DEFAULT_THRESHOLDS.copy()
    thresholds["high_confidence"] = 95
    thresholds["medium_confidence"] = 70
    result = evaluate_match(invoice(), payment(), thresholds=thresholds)
    assert result["category"] == "High Confidence" if result["score"] >= 95 else "Medium Confidence"


def test_conflicting_invoices_for_one_payment_are_detected():
    invoices = pd.DataFrame([
        invoice("INV-1", amount="100.00"),
        invoice("INV-2", name="Northstar Food", amount="100.00"),
    ])
    payments = pd.DataFrame([payment(reference="")])
    suggestions = generate_match_suggestions(invoices, payments)
    assert len(suggestions) >= 1
    assert any(item["conflicting"] for item in suggestions)


def test_low_confidence_below_minimum_is_not_suggested():
    thresholds = DEFAULT_THRESHOLDS.copy()
    thresholds["minimum_score"] = 95
    suggestions = generate_match_suggestions(
        pd.DataFrame([invoice("INV-9", name="Completely Different", customer_id="OTHER", amount="999.00")]),
        pd.DataFrame([payment("PAY-9", name="Northstar Foods", customer_id="C1", amount="100.00")]),
        thresholds=thresholds,
    )
    assert suggestions == []


def test_suggestion_evidence_persists_in_sqlite():
    suggestions = [evaluate_match(invoice(), payment())]
    with tempfile.NamedTemporaryFile(suffix=".sqlite3") as database:
        store = ReconciliationStore(database.name)
        store.save_matching_suggestions(suggestions)
        persisted = store.matching_suggestions()
        assert persisted[0]["algorithm_version"] == ALGORITHM_VERSION
        assert persisted[0]["evidence"]
        assert persisted[0]["contributions"]
        store.close()
