from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import pandas as pd
from rapidfuzz.fuzz import ratio

CENT = Decimal("0.01")
ALGORITHM_VERSION = "anomaly-rules-v1"


def money(value):
    try:
        return Decimal(str(value).strip().replace(",", "").replace("$", "")).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, AttributeError):
        return None


def text(value):
    if value is None or pd.isna(value):
        return ""
    return " ".join(str(value).strip().lower().replace("_", " ").split())


def add_anomaly(rows, kind, record_type, record_id, severity, explanation, related_ids=None):
    rows.append({
        "kind": kind,
        "record_type": record_type,
        "record_id": record_id,
        "severity": severity,
        "explanation": explanation,
        "related_ids": "; ".join(related_ids or []),
        "review_status": "Unreviewed",
        "algorithm_version": ALGORITHM_VERSION,
    })


def detect_payment_anomalies(payments):
    rows = []
    frame = payments.copy() if isinstance(payments, pd.DataFrame) else pd.DataFrame(payments)
    if frame.empty:
        return rows

    id_field = "payment_id" if "payment_id" in frame else None
    if id_field:
        counts = frame[id_field].astype(str).str.strip().value_counts()
        for payment_id, count in counts.items():
            if payment_id and count > 1:
                add_anomaly(rows, "Exact duplicate payment ID", "Payment", payment_id, "Critical",
                            f"Payment ID appears {count} times in the uploaded data.")

    if "invoice_reference" in frame:
        groups = frame.groupby(frame["invoice_reference"].map(text), dropna=False)
        for reference, group in groups:
            if reference and len(group) > 1:
                ids = group["payment_id"].astype(str).tolist() if "payment_id" in group else []
                add_anomaly(rows, "Repeated transaction reference", "Payment", "; ".join(ids), "Warning",
                            f"Invoice reference '{reference}' is reused by {len(group)} payments.", ids)

    required_amount = "payment_amount" if "payment_amount" in frame else "amount"
    required_date = "payment_date" if "payment_date" in frame else "transaction_date"
    if required_amount in frame and required_date in frame and "customer_id" in frame:
        frame = frame.copy()
        frame["customer_key"] = frame["customer_id"].map(text)
        frame["date_key"] = frame[required_date].map(text)
        frame["amount_key"] = frame[required_amount].map(lambda value: str(money(value)))
        grouped = frame.groupby(["customer_key", "amount_key", "date_key"])
        for (customer_id, amount, date_key), group in grouped:
            if len(group) > 1:
                ids = group["payment_id"].astype(str).tolist() if "payment_id" in group else []
                add_anomaly(rows, "Same customer, amount and date", "Payment", "; ".join(ids), "Critical",
                            f"Repeated combination: customer {customer_id}, amount {amount}, date {date_key}.", ids)

                suspicious_ids = ids
                add_anomaly(rows, "Suspicious repeated payment", "Payment", "; ".join(suspicious_ids), "Critical",
                            "Multiple payments share the same customer, amount and transaction date.", suspicious_ids)

    if "invoice_reference" in frame:
        for _, payment in frame.iterrows():
            payment_id = str(payment.get("payment_id", "N/A"))
            reference = text(payment.get("invoice_reference", ""))
            if not reference:
                add_anomaly(rows, "Missing invoice reference", "Payment", payment_id, "Informational",
                            "Payment has no invoice reference and may require manual identification.")

    if "customer_id" in frame and "customer_name" in frame:
        by_customer_id = defaultdict(set)
        by_name = defaultdict(set)
        for _, payment in frame.iterrows():
            payment_id = str(payment.get("payment_id", "N/A"))
            customer_id = text(payment.get("customer_id", ""))
            customer_name = text(payment.get("customer_name", ""))
            if customer_id:
                by_customer_id[customer_id].add((customer_name, payment_id))
            if customer_name:
                by_name[customer_name].add((customer_id, payment_id))
        for customer_id, names in by_customer_id.items():
            unique_names = {name for name, _ in names}
            if len(unique_names) > 1:
                ids = sorted({payment_id for _, payment_id in names})
                add_anomaly(rows, "Inconsistent customer identity", "Payment", "; ".join(ids), "Warning",
                            f"Customer ID '{customer_id}' is associated with multiple normalized customer names: {', '.join(sorted(unique_names))}.", ids)
        for customer_name, ids in by_name.items():
            unique_ids = {customer_id for customer_id, _ in ids}
            if len(unique_ids) > 1:
                payment_ids = sorted({payment_id for _, payment_id in ids})
                add_anomaly(rows, "Inconsistent customer identity", "Payment", "; ".join(payment_ids), "Warning",
                            f"Customer name '{customer_name}' is associated with multiple customer IDs.", payment_ids)

    return rows


def detect_invoice_anomalies(invoices):
    rows = []
    frame = invoices.copy() if isinstance(invoices, pd.DataFrame) else pd.DataFrame(invoices)
    if frame.empty:
        return rows

    amount_field = "invoice_amount" if "invoice_amount" in frame else "amount"
    date_field = "invoice_date" if "invoice_date" in frame else "transaction_date"

    if "invoice_id" in frame:
        counts = frame["invoice_id"].astype(str).str.strip().value_counts()
        for invoice_id, count in counts.items():
            if invoice_id and count > 1:
                add_anomaly(rows, "Possible duplicate invoice", "Invoice", invoice_id, "Critical",
                            f"Invoice ID appears {count} times in the uploaded data.")

    if "customer_id" in frame and amount_field in frame and date_field in frame:
        frame = frame.copy()
        frame["customer_key"] = frame["customer_id"].map(text)
        frame["amount_key"] = frame[amount_field].map(lambda value: str(money(value)))
        frame["date_key"] = frame[date_field].map(text)
        grouped = frame.groupby(["customer_key", "amount_key", "date_key"])
        for (_, amount, date_key), group in grouped:
            if len(group) > 1:
                ids = group["invoice_id"].astype(str).tolist()
                add_anomaly(rows, "Possible duplicate invoice", "Invoice", "; ".join(ids), "Warning",
                            f"Invoices share customer, amount {amount}, and date {date_key}.", ids)

    if "customer_id" in frame and "customer_name" in frame:
        for customer_id, group in frame.groupby(frame["customer_id"].map(text)):
            names = {text(name) for name in group["customer_name"] if text(name)}
            if customer_id and len(names) > 1:
                ids = group["invoice_id"].astype(str).tolist()
                add_anomaly(rows, "Inconsistent customer identity", "Invoice", "; ".join(ids), "Warning",
                            f"Customer ID '{customer_id}' belongs to multiple invoice customer names: {', '.join(sorted(names))}.", ids)

    return rows


def detect_amount_mismatch_anomalies(invoices, payments, large_difference_threshold=Decimal("100.00")):
    rows = []
    invoice_frame = invoices.copy() if isinstance(invoices, pd.DataFrame) else pd.DataFrame(invoices)
    payment_frame = payments.copy() if isinstance(payments, pd.DataFrame) else pd.DataFrame(payments)
    if invoice_frame.empty or payment_frame.empty:
        return rows

    for _, payment in payment_frame.iterrows():
        reference = text(payment.get("invoice_reference", ""))
        if not reference:
            continue
        candidates = []
        for _, invoice in invoice_frame.iterrows():
            invoice_id = str(invoice.get("invoice_id", ""))
            if reference in text(invoice_id) or text(invoice.get("invoice_reference", "")) == reference:
                candidates.append(invoice)
        payment_amount = money(payment.get("payment_amount", payment.get("amount")))
        for invoice in candidates:
            invoice_amount = money(invoice.get("invoice_amount", invoice.get("amount")))
            if payment_amount is None or invoice_amount is None:
                continue
            difference = abs(payment_amount - invoice_amount)
            if difference >= large_difference_threshold:
                add_anomaly(rows, "Unusually large amount difference", "Payment", str(payment.get("payment_id", "N/A")), "Warning",
                            f"Payment differs from {invoice.get('invoice_id', 'invoice')} by {difference:.2f}.", [str(invoice.get("invoice_id", ""))])
    return rows


def detect_all(invoices=None, payments=None, large_difference_threshold=Decimal("100.00")):
    rows = []
    invoices = invoices if invoices is not None else pd.DataFrame()
    payments = payments if payments is not None else pd.DataFrame()
    rows.extend(detect_payment_anomalies(payments))
    rows.extend(detect_invoice_anomalies(invoices))
    rows.extend(detect_amount_mismatch_anomalies(invoices, payments, large_difference_threshold))
    return rows
