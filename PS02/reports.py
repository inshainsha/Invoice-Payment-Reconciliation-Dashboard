from decimal import Decimal, InvalidOperation
import io
import pandas as pd

CENT = Decimal("0.01")


def as_money(value):
    try:
        return Decimal(str(value)).quantize(CENT)
    except (InvalidOperation, ValueError, AttributeError):
        return Decimal("0.00")


def reconciliation_rows(store):
    allocations = {row["invoice_id"]: [] for row in store.allocations()}
    allocation_amounts = {}
    decisions = {(row["invoice_id"], row["payment_id"]): row for row in store.decisions()}

    for allocation in store.allocations():
        allocations.setdefault(allocation["invoice_id"], []).append(allocation)
        allocation_amounts[allocation["invoice_id"]] = allocation_amounts.get(allocation["invoice_id"], Decimal("0.00")) + allocation["amount"]

    report_rows = []
    for invoice in store.invoice_rows():
        invoice_id = invoice["invoice_id"]
        invoice_amount = as_money(invoice["invoice_amount"])
        confirmed_paid = allocation_amounts.get(invoice_id, Decimal("0.00")).quantize(CENT)
        outstanding = max(invoice_amount - confirmed_paid, Decimal("0.00")).quantize(CENT)
        invoice_allocations = allocations.get(invoice_id, [])
        payment_ids = "; ".join(row["payment_id"] for row in invoice_allocations)
        review_rows = [decisions[(invoice_id, row["payment_id"])] for row in invoice_allocations if (invoice_id, row["payment_id"]) in decisions]
        status = "Paid" if outstanding == 0 and confirmed_paid > 0 else ("Partially Paid" if confirmed_paid > 0 else "Unpaid")
        review_decision = review_rows[0]["decision"] if review_rows else "No allocation"
        explanation = review_rows[0]["explanation"] if review_rows else ""
        report_rows.append({
            "Invoice ID": invoice_id,
            "Customer name": invoice["customer_name"],
            "Invoice amount": f"{invoice_amount:.2f}",
            "Confirmed payment amount": f"{confirmed_paid:.2f}",
            "Outstanding balance": f"{outstanding:.2f}",
            "Payment IDs": payment_ids,
            "Match status": status,
            "Matching explanation": explanation,
            "Review decision": review_decision,
        })
    return pd.DataFrame(report_rows, columns=[
        "Invoice ID", "Customer name", "Invoice amount", "Confirmed payment amount",
        "Outstanding balance", "Payment IDs", "Match status", "Matching explanation", "Review decision",
    ])


def unmatched_invoice_export(store):
    allocated_ids = {row["invoice_id"] for row in store.allocations()}
    rows = [row for row in store.invoice_rows() if row["invoice_id"] not in allocated_ids]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["invoice_id", "customer_name", "customer_id", "invoice_amount", "invoice_date"])
    return frame[["invoice_id", "customer_name", "customer_id", "invoice_amount", "invoice_date"]]


def unallocated_payment_export(store):
    allocated = {}
    for allocation in store.allocations():
        allocated[allocation["payment_id"]] = allocated.get(allocation["payment_id"], Decimal("0.00")) + allocation["amount"]
    rows = []
    for payment in store.payment_rows():
        remaining = as_money(payment["payment_amount"]) - allocated.get(payment["payment_id"], Decimal("0.00"))
        if remaining > 0:
            rows.append({
                "payment_id": payment["payment_id"],
                "customer_name": payment["customer_name"],
                "customer_id": payment["customer_id"],
                "payment_amount": f'{as_money(payment["payment_amount"]):.2f}',
                "allocated_amount": f'{allocated.get(payment["payment_id"], Decimal("0.00")):.2f}',
                "unallocated_amount": f"{remaining.quantize(CENT):.2f}",
                "payment_date": payment["payment_date"],
            })
    return pd.DataFrame(rows, columns=["payment_id", "customer_name", "customer_id", "payment_amount", "allocated_amount", "unallocated_amount", "payment_date"])


def ambiguous_mismatch_export(store, ambiguous_pairs=None):
    ambiguous_pairs = ambiguous_pairs or []
    rows = []
    for allocation in store.allocations():
        invoice = next((row for row in store.invoice_rows() if row["invoice_id"] == allocation["invoice_id"]), None)
        payment = next((row for row in store.payment_rows() if row["payment_id"] == allocation["payment_id"]), None)
        if invoice and payment and allocation["amount"] != as_money(invoice["invoice_amount"]):
            rows.append({
                "invoice_id": invoice["invoice_id"], "payment_id": payment["payment_id"],
                "invoice_amount": f'{as_money(invoice["invoice_amount"]):.2f}',
                "payment_amount": f'{as_money(payment["payment_amount"]):.2f}',
                "allocated_amount": f'{allocation["amount"]:.2f}',
                "amount_difference": f'{(allocation["amount"] - as_money(invoice["invoice_amount"])):.2f}',
                "type": "Amount mismatch",
            })
    for invoice_id, payment_id in ambiguous_pairs:
        if not any(row["invoice_id"] == invoice_id and row["payment_id"] == payment_id for row in rows):
            rows.append({"invoice_id": invoice_id, "payment_id": payment_id, "invoice_amount": "", "payment_amount": "", "allocated_amount": "", "amount_difference": "", "type": "Ambiguous"})
    return pd.DataFrame(rows, columns=["invoice_id", "payment_id", "invoice_amount", "payment_amount", "allocated_amount", "amount_difference", "type"])


def csv_bytes(frame):
    return frame.to_csv(index=False).encode("utf-8")


def matching_performance(store, labeled_correct_pairs=None):
    labeled_correct_pairs = labeled_correct_pairs or {
        ("INV-1001", "PAY-5001"), ("INV-1002", "PAY-5002"), ("INV-1005", "PAY-5006")
    }
    decisions = store.decisions()
    confirmed_pairs = {(row["invoice_id"], row["payment_id"]) for row in decisions if row["decision"] in ("confirmed", "corrected")}
    suggested_count = len(store.allocations()) + sum(1 for row in decisions if row["decision"] == "rejected")
    confirmed_count = len(confirmed_pairs)
    correct_confirmed = len(confirmed_pairs.intersection(labeled_correct_pairs))
    precision = correct_confirmed / confirmed_count if confirmed_count else 0.0
    correct_suggestion_rate = correct_confirmed / suggested_count if suggested_count else 0.0
    unresolved = sum(1 for row in decisions if row["decision"] == "rejected") + len(store.invoice_rows()) - confirmed_count
    duplicate_allocations_prevented = store.duplicate_prevention_count()
    return {
        "matching_precision": precision,
        "correct_suggestion_rate": correct_suggestion_rate,
        "confirmed_matches": confirmed_count,
        "unresolved_cases": max(unresolved, 0),
        "duplicate_allocations_prevented": duplicate_allocations_prevented,
    }
