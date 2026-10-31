from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
import pandas as pd

MONEY_FIELDS = ("amount", "invoice_amount", "paid_amount")


def normalize_text(value):
    if pd.isna(value):
        return ""
    return " ".join(str(value).strip().lower().replace("_", " ").split())


def money(value):
    try:
        amount = Decimal(str(value).strip().replace(",", "").replace("$", ""))
    except (InvalidOperation, AttributeError, ValueError):
        return None
    return amount.quantize(Decimal("0.01"))


def parse_date(value):
    if pd.isna(value) or value == "":
        return None
    parsed = pd.to_datetime(value, errors="coerce", utc=False)
    return None if pd.isna(parsed) else parsed.date()


def validate_csv(frame, kind):
    required = {
        "invoice": {"invoice_id", "customer_id", "customer_name", "amount", "invoice_date", "due_date"},
        "payment": {"payment_id", "customer_id", "customer_name", "amount", "payment_date"},
    }[kind]
    missing = sorted(required.difference(frame.columns))
    errors = []
    if missing:
        errors.append({"row": 0, "error": f"Missing columns: {', '.join(missing)}"})
    for index, row in frame.iterrows():
        id_field = "invoice_id" if kind == "invoice" else "payment_id"
        if missing:
            break
        if pd.isna(row[id_field]) or not str(row[id_field]).strip():
            errors.append({"row": index + 2, "error": f"Missing {id_field}"})
        if money(row["amount"]) is None or money(row["amount"]) < 0:
            errors.append({"row": index + 2, "error": "Invalid amount"})
        date_field = "invoice_date" if kind == "invoice" else "payment_date"
        if kind == "invoice" and parse_date(row["due_date"]) is None:
            errors.append({"row": index + 2, "error": "Invalid due_date"})
        if parse_date(row[date_field]) is None:
            errors.append({"row": index + 2, "error": f"Invalid {date_field}"})
    return errors


def load_records(path, kind):
    frame = pd.read_csv(path)
    frame.columns = [normalize_text(column).replace(" ", "_") for column in frame.columns]
    errors = validate_csv(frame, kind)
    if errors:
        raise ValueError(errors)
    return frame


def similarity(left, right):
    return SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()


def suggest_matches(invoices, payments):
    suggestions = []
    used_payments = set()
    for _, invoice in invoices.iterrows():
        invoice_amount = money(invoice.get("invoice_amount", invoice.get("amount")))
        candidates = []
        for _, payment in payments.iterrows():
            payment_id = str(payment["payment_id"])
            if payment_id in used_payments:
                continue
            payment_amount = money(payment.get("payment_amount", payment.get("amount")))
            evidence = []
            score = 0
            if normalize_text(invoice["customer_id"]) and normalize_text(invoice["customer_id"]) == normalize_text(payment["customer_id"]):
                evidence.append("customer ID matches")
                score += 35
            name_score = similarity(invoice["customer_name"], payment["customer_name"])
            if name_score >= 0.85:
                evidence.append("customer name closely matches")
                score += 20
            elif name_score >= 0.55:
                evidence.append("customer name partially matches")
                score += 10
            if invoice_amount == payment_amount:
                evidence.append("amount matches exactly")
                score += 35
            elif payment_amount and invoice_amount:
                difference = abs(invoice_amount - payment_amount)
                if difference <= Decimal("0.01"):
                    evidence.append("amount matches within rounding")
                    score += 30
                elif payment_amount < invoice_amount:
                    evidence.append(f"partial payment: {money_to_string(payment_amount)} of {money_to_string(invoice_amount)}")
                    score += 15
                else:
                    evidence.append(f"payment is over invoice by {money_to_string(difference)}")
                    score -= 20
            reference = normalize_text(invoice.get("invoice_reference", ""))
            if reference and reference == normalize_text(payment.get("description", "")):
                evidence.append("invoice reference appears in payment description")
                score += 20
            elif reference and reference in normalize_text(payment.get("description", "")):
                evidence.append("invoice reference found in payment description")
                score += 15
            if evidence:
                candidates.append({
                    "invoice_id": str(invoice["invoice_id"]), "payment_id": payment_id,
                    "score": max(0, min(100, score)), "evidence": evidence,
                    "amount_difference": money_to_string((payment_amount or Decimal("0")) - invoice_amount),
                })
        candidates.sort(key=lambda item: item["score"], reverse=True)
        if candidates:
            best = candidates[0]
            if len(candidates) > 1 and candidates[1]["score"] == best["score"]:
                best["score"] = max(best["score"] - 10, 0)
                best["evidence"].append("ambiguous: another payment received similar evidence")
            suggestions.append(best)
            if best["score"] >= 55 and "ambiguous" not in " ".join(best["evidence"]):
                used_payments.add(best["payment_id"])
    return suggestions


def money_to_string(value):
    if value is None:
        return "0.00"
    return f"{value.quantize(Decimal('0.01')):.2f}"


def confirmed_balances(invoices, allocations):
    allocations_by_invoice = {}
    for allocation in allocations:
        allocations_by_invoice[allocation["invoice_id"]] = allocations_by_invoice.get(allocation["invoice_id"], Decimal("0")) + allocation["amount"]
    rows = []
    for _, invoice in invoices.iterrows():
        invoice_id = str(invoice["invoice_id"])
        invoice_amount = money(invoice.get("invoice_amount", invoice.get("amount")))
        paid = allocations_by_invoice.get(invoice_id, Decimal("0"))
        rows.append({"invoice_id": invoice_id, "invoice_amount": invoice_amount, "paid_amount": paid, "outstanding": max(invoice_amount - paid, Decimal("0"))})
    return rows
