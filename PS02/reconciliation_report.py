from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value):
    try:
        amount = Decimal(str(value).strip().replace(",", "").replace("$", ""))
    except (InvalidOperation, AttributeError, ValueError):
        return None
    if not amount.is_finite() or amount < ZERO:
        return None
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def sum_money(values):
    return sum((value for value in values if value is not None), ZERO).quantize(CENT)


def classify_invoice(invoice_amount, confirmed_paid, mismatch=False):
    if mismatch:
        return "Requires Review"
    if confirmed_paid <= ZERO:
        return "Unpaid"
    if confirmed_paid >= invoice_amount:
        return "Paid"
    return "Partially Paid"


def reconciliation_report(store):
    invoice_rows = []
    allocations_by_invoice = {}
    allocations_by_payment = {}

    for row in store.allocations():
        allocations_by_invoice[row["invoice_id"]] = allocations_by_invoice.get(row["invoice_id"], ZERO) + row["amount"]
        allocations_by_payment[row["payment_id"]] = allocations_by_payment.get(row["payment_id"], ZERO) + row["amount"]

    mismatch_cases = []
    for allocation in store.allocations():
        invoice_amount = store._invoice_total(allocation["invoice_id"])
        payment_amount = store._payment_total(allocation["payment_id"])
        allocated_amount = allocation["amount"]
        if allocated_amount != invoice_amount or payment_amount != invoice_amount:
            review_required = allocated_amount != invoice_amount and allocated_amount != payment_amount
            mismatch_cases.append({
                "invoice_id": allocation["invoice_id"],
                "payment_id": allocation["payment_id"],
                "invoice_amount": invoice_amount.quantize(CENT),
                "payment_amount": payment_amount.quantize(CENT),
                "allocated_amount": allocated_amount.quantize(CENT),
                "amount_difference": (allocated_amount - invoice_amount).quantize(CENT),
                "review_required": review_required,
            })

    mismatch_pairs = {
        (item["invoice_id"], item["payment_id"]): item["review_required"]
        for item in mismatch_cases
    }
    total_invoice_value = ZERO

    invoice_records = store.connection.execute(
        "SELECT invoice_id, customer_name, invoice_amount FROM invoices ORDER BY invoice_id"
    ).fetchall()
    for record in invoice_records:
        invoice_amount = money(record["invoice_amount"])
        if invoice_amount is None:
            continue
        total_invoice_value += invoice_amount
        confirmed_paid = allocations_by_invoice.get(record["invoice_id"], ZERO).quantize(CENT)
        outstanding = max(invoice_amount - confirmed_paid, ZERO).quantize(CENT)
        has_mismatch = any(invoice_id == record["invoice_id"] and review_required for (invoice_id, payment_id), review_required in mismatch_pairs.items())
        status = classify_invoice(invoice_amount, confirmed_paid, has_mismatch)
        invoice_rows.append({
            "invoice_id": record["invoice_id"],
            "customer_name": record["customer_name"],
            "invoice_amount": invoice_amount,
            "confirmed_paid": confirmed_paid,
            "outstanding": outstanding,
            "status": status,
        })

    payment_records = store.connection.execute(
        "SELECT payment_id, payment_amount FROM payments ORDER BY payment_id"
    ).fetchall()
    total_received_payments = ZERO
    unallocated_payments = []
    for record in payment_records:
        payment_amount = money(record["payment_amount"])
        if payment_amount is None:
            continue
        total_received_payments += payment_amount
        allocated = allocations_by_payment.get(record["payment_id"], ZERO).quantize(CENT)
        remaining = max(payment_amount - allocated, ZERO).quantize(CENT)
        if remaining > ZERO:
            unallocated_payments.append({
                "payment_id": record["payment_id"],
                "payment_amount": payment_amount,
                "allocated_amount": allocated,
                "unallocated_amount": remaining,
            })

    total_allocated_payments = sum_money(row["amount"] for row in store.allocations())
    return {
        "invoices": invoice_rows,
        "total_invoice_value": total_invoice_value.quantize(CENT),
        "total_received_payments": total_received_payments.quantize(CENT),
        "total_allocated_payments": total_allocated_payments,
        "total_outstanding_balance": sum_money(row["outstanding"] for row in invoice_rows),
        "unallocated_payments": unallocated_payments,
        "total_unallocated_payments": sum_money(row["unallocated_amount"] for row in unallocated_payments),
        "mismatch_cases": mismatch_cases,
    }
