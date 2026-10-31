import pandas as pd
from matching import candidate_match, generate_match_suggestions


def invoice(invoice_id="INV-1", name="Northstar Foods", customer_id="CUST-1", amount="1250.00", date="2026-01-05"):
    return {"invoice_id": invoice_id, "invoice_reference": invoice_id, "customer_name": name,
            "customer_id": customer_id, "invoice_amount": amount, "invoice_date": date}


def payment(payment_id="PAY-1", name="Northstar Foods", customer_id="CUST-1", amount="1250.00", date="2026-01-06", reference="INV-1", description="invoice settlement"):
    return {"payment_id": payment_id, "customer_name": name, "customer_id": customer_id,
            "payment_amount": amount, "payment_date": date, "invoice_reference": reference,
            "description": description}


def test_exact_match():
    result = candidate_match(invoice(), payment())
    assert result["score"] >= 80
    assert result["category"] == "High"
    assert "Exact invoice reference match" in result["evidence"]
    assert "Exact payment amount match" in result["evidence"]


def test_fuzzy_customer_match():
    result = candidate_match(invoice(), payment(name="North Star Foodz", customer_id="OTHER"))
    assert any("Fuzzy customer name" in item for item in result["evidence"])
    assert result["score"] < 80


def test_ambiguous_match_is_flagged():
    invoices = pd.DataFrame([invoice()])
    payments = pd.DataFrame([
        payment("PAY-1"),
        payment("PAY-2", name="Northstar Foods", amount="1250.00"),
    ])
    suggestions = generate_match_suggestions(invoices, payments)
    assert len(suggestions) == 2
    assert all(item["ambiguous"] for item in suggestions)


def test_incorrect_amount_is_visible():
    result = candidate_match(invoice(amount="1000.00"), payment(amount="850.00"))
    assert "Payment is short by 150.00" in result["evidence"]
    assert result["amount_difference"] == "-150.00"


def test_duplicate_payment_is_never_reused():
    invoices = pd.DataFrame([invoice("INV-1"), invoice("INV-2", customer_id="CUST-2", name="Another Co")])
    payments = pd.DataFrame([payment("PAY-1")])
    suggestions = generate_match_suggestions(invoices, payments)
    payment_ids = [item["payment_id"] for item in suggestions if not item["ambiguous"]]
    assert len(set(payment_ids)) == len(payment_ids)
