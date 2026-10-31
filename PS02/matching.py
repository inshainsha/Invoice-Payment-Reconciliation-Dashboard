from datetime import datetime
from decimal import Decimal, InvalidOperation
import pandas as pd
from rapidfuzz.fuzz import ratio, token_sort_ratio


def normalize_text(value):
    if value is None or pd.isna(value):
        return ""
    return " ".join(str(value).strip().lower().replace("_", " ").split())


def parse_decimal(value):
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("$", "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, AttributeError):
        return None


def parse_date(value):
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.to_pydatetime().date()


def money_difference(invoice_amount, payment_amount):
    if invoice_amount is None or payment_amount is None:
        return None
    return payment_amount - invoice_amount


def format_money(value):
    if value is None:
        return "Unknown"
    return f"{value.quantize(Decimal('0.01')):.2f}"


def confidence_category(score):
    if score >= 80:
        return "High"
    if score >= 50:
        return "Medium"
    return "Low"


def date_proximity_score(invoice_date, payment_date):
    first = parse_date(invoice_date)
    second = parse_date(payment_date)
    if first is None or second is None:
        return 0, None
    difference = abs((second - first).days)
    if difference == 0:
        return 10, 0
    if difference <= 7:
        return 8, difference
    if difference <= 30:
        return 5, difference
    if difference <= 60:
        return 2, difference
    return 0, difference


def candidate_match(invoice, payment):
    evidence = []
    score = 0

    invoice_reference = normalize_text(invoice.get("invoice_reference", invoice.get("invoice_id", "")))
    payment_reference = normalize_text(payment.get("invoice_reference", ""))
    description = normalize_text(payment.get("description", ""))

    if invoice_reference and payment_reference and invoice_reference == payment_reference:
        evidence.append("Exact invoice reference match")
        score += 35
    elif invoice_reference and payment_reference and invoice_reference in payment_reference:
        evidence.append("Invoice reference appears in payment reference")
        score += 20

    invoice_customer_id = normalize_text(invoice.get("customer_id", ""))
    payment_customer_id = normalize_text(payment.get("customer_id", ""))
    if invoice_customer_id and invoice_customer_id == payment_customer_id:
        evidence.append("Customer ID matches")
        score += 20

    invoice_name = normalize_text(invoice.get("customer_name", ""))
    payment_name = normalize_text(payment.get("customer_name", ""))
    name_score = ratio(invoice_name, payment_name) if invoice_name and payment_name else 0
    if name_score == 100:
        evidence.append("Exact customer name match")
        score += 15
    elif name_score >= 85:
        evidence.append(f"Fuzzy customer name match ({name_score:.0f}% similarity)")
        score += 12
    elif name_score >= 70:
        evidence.append(f"Customer names are similar ({name_score:.0f}% similarity)")
        score += 7

    invoice_amount = parse_decimal(invoice.get("invoice_amount", invoice.get("amount")))
    payment_amount = parse_decimal(payment.get("payment_amount", payment.get("amount")))
    difference = money_difference(invoice_amount, payment_amount)
    if invoice_amount is not None and payment_amount is not None:
        if invoice_amount == payment_amount:
            evidence.append("Exact payment amount match")
            score += 20
        elif abs(difference) <= Decimal("0.01"):
            evidence.append("Payment amount matches within one cent")
            score += 18
        elif payment_amount < invoice_amount:
            evidence.append(f"Payment is short by {format_money(abs(difference))}")
            score += 5
        else:
            evidence.append(f"Payment exceeds invoice by {format_money(difference)}")
            score -= 5

    date_score, day_distance = date_proximity_score(invoice.get("invoice_date"), payment.get("payment_date"))
    if day_distance is not None:
        score += date_score
        if day_distance == 0:
            evidence.append("Transaction dates are identical")
        else:
            evidence.append(f"Transaction dates are {day_distance} day(s) apart")

    description_similarity = token_sort_ratio(invoice_name, description) if invoice_name and description else 0
    if description_similarity >= 85:
        evidence.append(f"Description strongly resembles customer/invoice context ({description_similarity:.0f}% similarity)")
        score += 5
    elif description_similarity >= 70:
        evidence.append(f"Description partially resembles customer/invoice context ({description_similarity:.0f}% similarity)")
        score += 3

    score = max(0, min(100, round(score)))
    if difference is None:
        displayed_difference = "Unknown"
    elif difference > 0:
        displayed_difference = f"+{format_money(difference)}"
    elif difference < 0:
        displayed_difference = format_money(difference)
    else:
        displayed_difference = "0.00"
    return {
        "invoice_id": str(invoice.get("invoice_id", "")),
        "payment_id": str(payment.get("payment_id", "")),
        "score": score,
        "category": confidence_category(score),
        "evidence": evidence,
        "amount_difference": displayed_difference,
        "invoice_amount": format_money(invoice_amount),
        "payment_amount": format_money(payment_amount),
        "ambiguous": False,
    }


def generate_match_suggestions(invoices, payments, excluded_payment_ids=None):
    excluded_payment_ids = set(excluded_payment_ids or [])
    suggestions = []
    allocated_payment_ids = set()

    invoice_rows = invoices.to_dict("records") if isinstance(invoices, pd.DataFrame) else invoices
    payment_rows = payments.to_dict("records") if isinstance(payments, pd.DataFrame) else payments

    for invoice in invoice_rows:
        invoice_candidates = []
        for payment in payment_rows:
            payment_id = str(payment.get("payment_id", ""))
            if payment_id in excluded_payment_ids or payment_id in allocated_payment_ids:
                continue
            candidate = candidate_match(invoice, payment)
            if candidate["score"] >= 35 and candidate["evidence"]:
                invoice_candidates.append(candidate)

        invoice_candidates.sort(key=lambda item: (item["score"], item["payment_id"]), reverse=True)
        if not invoice_candidates:
            continue

        if len(invoice_candidates) > 1:
            leader_score = invoice_candidates[0]["score"]
            second_score = invoice_candidates[1]["score"]
            if leader_score - second_score <= 10:
                for candidate in invoice_candidates[:2]:
                    candidate["ambiguous"] = True
                    candidate["evidence"].append("Ambiguous: another candidate received similar evidence; manual review required")

        for candidate in invoice_candidates:
            if candidate["score"] >= 50 and not candidate["ambiguous"]:
                allocated_payment_ids.add(candidate["payment_id"])
                break
        else:
            candidate = invoice_candidates[0]
            candidate["category"] = confidence_category(candidate["score"])

        suggestions.extend(invoice_candidates)

    return suggestions
