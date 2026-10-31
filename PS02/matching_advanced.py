from decimal import Decimal, InvalidOperation
import json
import pandas as pd
from rapidfuzz.fuzz import ratio, token_sort_ratio, partial_ratio

ALGORITHM_VERSION = "explainable-weighted-v2"

DEFAULT_WEIGHTS = {
    "invoice_reference": 30,
    "customer_id": 18,
    "customer_name_exact": 14,
    "customer_name_fuzzy": 10,
    "payment_amount_exact": 18,
    "payment_amount_partial": 10,
    "date_proximity": 8,
    "description_similarity": 6,
}

DEFAULT_THRESHOLDS = {
    "minimum_score": 35,
    "high_confidence": 80,
    "medium_confidence": 50,
    "conflict_score_gap": 12,
}


def normalize(value):
    if value is None or pd.isna(value):
        return ""
    return " ".join(str(value).strip().lower().replace("_", " ").split())


def parse_money(value):
    try:
        if value is None or pd.isna(value) or str(value).strip() == "":
            return None
        return Decimal(str(value).replace(",", "").replace("$", "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, AttributeError):
        return None


def parse_date(value):
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def add_evidence(contributions, evidence, factor, points, explanation):
    contributions.append({"factor": factor, "points": points, "explanation": explanation})
    evidence.append(f"{explanation} (+{points})")


def evaluate_match(invoice, payment, weights=None, thresholds=None):
    weights = weights or DEFAULT_WEIGHTS.copy()
    thresholds = thresholds or DEFAULT_THRESHOLDS.copy()
    contributions = []
    evidence = []
    score = 0

    invoice_reference = normalize(invoice.get("invoice_reference", invoice.get("invoice_id", "")))
    payment_reference = normalize(payment.get("invoice_reference", ""))
    if invoice_reference and payment_reference:
        if invoice_reference == payment_reference:
            points = weights["invoice_reference"]
            score += points
            add_evidence(contributions, evidence, "invoice_reference", points, "Exact invoice reference match")
        elif invoice_reference in payment_reference or partial_ratio(invoice_reference, payment_reference) >= 90:
            points = int(weights["invoice_reference"] * 0.70)
            score += points
            add_evidence(contributions, evidence, "invoice_reference", points, "Partial invoice reference match")

    invoice_id = normalize(invoice.get("customer_id", ""))
    payment_id = normalize(payment.get("customer_id", ""))
    if invoice_id and payment_id:
        if invoice_id == payment_id:
            points = weights["customer_id"]
            score += points
            add_evidence(contributions, evidence, "customer_id", points, "Exact customer ID match")
        else:
            customer_id_similarity = ratio(invoice_id, payment_id)
            if customer_id_similarity >= 80:
                points = int(weights["customer_id"] * customer_id_similarity / 100)
                score += points
                add_evidence(contributions, evidence, "customer_id", points, f"Customer ID similarity {customer_id_similarity:.0f}%")

    invoice_name = normalize(invoice.get("customer_name", ""))
    payment_name = normalize(payment.get("customer_name", ""))
    name_similarity = ratio(invoice_name, payment_name) if invoice_name and payment_name else 0
    if name_similarity == 100:
        points = weights["customer_name_exact"]
        score += points
        add_evidence(contributions, evidence, "customer_name", points, "Exact customer name match")
    elif name_similarity >= 85:
        points = int(weights["customer_name_fuzzy"] * name_similarity / 100)
        score += points
        add_evidence(contributions, evidence, "customer_name", points, f"Fuzzy customer name match {name_similarity:.0f}%")

    invoice_amount = parse_money(invoice.get("invoice_amount", invoice.get("amount")))
    payment_amount = parse_money(payment.get("payment_amount", payment.get("amount")))
    difference = None if invoice_amount is None or payment_amount is None else payment_amount - invoice_amount
    if invoice_amount is not None and payment_amount is not None:
        if invoice_amount == payment_amount:
            points = weights["payment_amount_exact"]
            score += points
            add_evidence(contributions, evidence, "payment_amount", points, "Exact payment amount match")
        elif abs(difference) <= Decimal("0.01"):
            points = int(weights["payment_amount_exact"] * 0.95)
            score += points
            add_evidence(contributions, evidence, "payment_amount", points, "Amount match within one cent")
        elif abs(difference) / invoice_amount <= Decimal("0.20") if invoice_amount > 0 else False:
            points = int(weights["payment_amount_partial"] * (Decimal("1") - abs(difference) / invoice_amount))
            score += points
            add_evidence(contributions, evidence, "payment_amount", points, f"Amount difference {difference:+.2f}")
        else:
            evidence.append("Payment amount differs substantially (-5 points)")
            score -= 5

    invoice_date = parse_date(invoice.get("invoice_date"))
    payment_date = parse_date(payment.get("payment_date"))
    if invoice_date and payment_date:
        distance = abs((payment_date - invoice_date).days)
        if distance == 0:
            points = weights["date_proximity"]
        elif distance <= 7:
            points = int(weights["date_proximity"] * 0.85)
        elif distance <= 30:
            points = int(weights["date_proximity"] * 0.55)
        elif distance <= 60:
            points = int(weights["date_proximity"] * 0.25)
        else:
            points = 0
        if points:
            score += points
            add_evidence(contributions, evidence, "date_proximity", points, f"Transaction dates are {distance} day(s) apart")

    description = normalize(payment.get("description", ""))
    description_similarity = token_sort_ratio(description, invoice_reference + " " + invoice_name) if description else 0
    if description_similarity >= 85:
        points = weights["description_similarity"]
        score += points
        add_evidence(contributions, evidence, "description_similarity", points, f"Transaction description similarity {description_similarity:.0f}%")
    elif description_similarity >= 70:
        points = int(weights["description_similarity"] * 0.60)
        score += points
        add_evidence(contributions, evidence, "description_similarity", points, f"Transaction description similarity {description_similarity:.0f}%")

    score = max(0, min(100, score))
    category = "High Confidence" if score >= thresholds["high_confidence"] else ("Medium Confidence" if score >= thresholds["medium_confidence"] else "Low Confidence")
    return {
        "invoice_id": str(invoice.get("invoice_id", "")), "payment_id": str(payment.get("payment_id", "")),
        "score": score, "category": category, "evidence": evidence, "contributions": contributions,
        "amount_difference": "Unknown" if difference is None else f"{difference:+.2f}",
        "invoice_amount": "Unknown" if invoice_amount is None else f"{invoice_amount:.2f}",
        "payment_amount": "Unknown" if payment_amount is None else f"{payment_amount:.2f}",
        "algorithm_version": ALGORITHM_VERSION, "ambiguous": False, "conflicting": False,
        "competing_invoice_ids": [], "thresholds": thresholds, "weights": weights,
    }


def generate_match_suggestions(invoices, payments, excluded_payment_ids=None, weights=None, thresholds=None):
    weights = weights or DEFAULT_WEIGHTS.copy()
    thresholds = thresholds or DEFAULT_THRESHOLDS.copy()
    excluded_payment_ids = set(excluded_payment_ids or [])
    invoice_rows = invoices.to_dict("records") if isinstance(invoices, pd.DataFrame) else invoices
    payment_rows = payments.to_dict("records") if isinstance(payments, pd.DataFrame) else payments
    results = []

    for payment in payment_rows:
        payment_id = str(payment.get("payment_id", ""))
        if not payment_id or payment_id in excluded_payment_ids:
            continue
        candidates = []
        for invoice in invoice_rows:
            candidate = evaluate_match(invoice, payment, weights, thresholds)
            if candidate["score"] >= thresholds["minimum_score"] and candidate["contributions"]:
                candidates.append(candidate)
        candidates.sort(key=lambda item: (-item["score"], item["invoice_id"]))
        if not candidates:
            continue
        if len(candidates) > 1 and candidates[0]["score"] - candidates[1]["score"] <= thresholds["conflict_score_gap"]:
            for candidate in candidates[:2]:
                candidate["ambiguous"] = True
                candidate["conflicting"] = True
                candidate["competing_invoice_ids"] = [item["invoice_id"] for item in candidates[:2]]
                candidate["evidence"].append("Conflict: competing invoices have similar evidence; manual review required")
                candidate["contributions"].append({"factor": "conflict_penalty", "points": 0, "explanation": "Competing invoice candidates detected"})
        results.extend(candidates)
    return results


def candidate_match(invoice, payment, weights=None, thresholds=None):
    return evaluate_match(invoice, payment, weights, thresholds)
