import pandas as pd
from decimal import Decimal
from reconciliation import money, suggest_matches, confirmed_balances


def invoice_row(invoice_id="INV-1", amount="100.00"):
    return {"invoice_id": invoice_id, "customer_id": "C1", "customer_name": "Acme Ltd",
            "amount": amount, "invoice_date": "2026-01-01", "due_date": "2026-01-31",
            "invoice_reference": invoice_id}


def payment_row(payment_id="PAY-1", amount="100.00"):
    return {"payment_id": payment_id, "customer_id": "C1", "customer_name": "Acme Ltd",
            "amount": amount, "payment_date": "2026-01-10", "description": "INV-1"}


def test_money_uses_decimal():
    assert money("1,234.56") == Decimal("1234.56")


def test_exact_match_has_evidence():
    matches = suggest_matches(pd.DataFrame([invoice_row()]), pd.DataFrame([payment_row()]))
    assert matches[0]["score"] == 100
    assert "amount matches exactly" in matches[0]["evidence"]


def test_payment_is_not_suggested_twice():
    invoices = pd.DataFrame([invoice_row("INV-1"), invoice_row("INV-2")])
    matches = suggest_matches(invoices, pd.DataFrame([payment_row()]))
    assert len(matches) == 1
    assert matches[0]["payment_id"] == "PAY-1"


def test_confirmed_balance_uses_allocation_once():
    invoices = pd.DataFrame([invoice_row(amount="100.00")])
    rows = confirmed_balances(invoices, [{"invoice_id": "INV-1", "amount": Decimal("40.00")}])
    assert rows[0]["paid_amount"] == Decimal("40.00")
    assert rows[0]["outstanding"] == Decimal("60.00")
