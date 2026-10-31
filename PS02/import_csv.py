import hashlib
from decimal import Decimal, InvalidOperation
import pandas as pd

INVOICE_COLUMNS = ["invoice_id", "customer_name", "customer_id", "invoice_amount", "invoice_date"]
PAYMENT_COLUMNS = ["payment_id", "customer_name", "customer_id", "payment_amount", "payment_date", "invoice_reference", "description"]


def normalize_customer_name(value):
    if value is None or pd.isna(value):
        return ""
    return " ".join(str(value).replace("_", " ").strip().split()).title()


def normalize_columns(frame):
    frame = frame.copy()
    frame.columns = [str(column).strip().lower().replace(" ", "_") for column in frame.columns]
    return frame


def parse_amount(value):
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    try:
        amount = Decimal(str(value).replace(",", "").replace("$", "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    return amount.quantize(Decimal("0.01"))


def parse_date(value):
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def file_fingerprint(frame, kind):
    content = frame.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(kind.encode("utf-8") + b":" + content).hexdigest()


def import_csv(source, kind):
    frame = pd.read_csv(source)
    frame = normalize_columns(frame)
    required = INVOICE_COLUMNS if kind == "invoice" else PAYMENT_COLUMNS
    missing = [column for column in required if column not in frame.columns]
    errors = []
    valid_rows = []

    if missing:
        errors.append({"row": 0, "record_id": "N/A", "field": "columns", "error": "Missing required columns: " + ", ".join(missing)})
        return {"total": len(frame), "valid": [], "invalid_count": len(frame), "errors": errors, "fingerprint": file_fingerprint(frame, kind)}

    id_field = "invoice_id" if kind == "invoice" else "payment_id"
    amount_field = "invoice_amount" if kind == "invoice" else "payment_amount"
    date_field = "invoice_date" if kind == "invoice" else "payment_date"

    seen_ids = {}
    invalid_rows = set()
    for index, row in frame.iterrows():
        excel_row = index + 2
        record_id = "" if pd.isna(row[id_field]) else str(row[id_field]).strip()
        row_errors = []

        if not record_id:
            row_errors.append(("record_id", "Missing " + id_field))
        elif record_id in seen_ids:
            row_errors.append((id_field, f"Duplicate {id_field}: {record_id}; first appears on row {seen_ids[record_id]}"))
        else:
            seen_ids[record_id] = excel_row

        for field in required:
            if field in (id_field, amount_field, date_field, "customer_name", "customer_id"):
                value = row[field]
                if pd.isna(value) or str(value).strip() == "":
                    row_errors.append((field, "Empty value for " + field))

        amount = parse_amount(row[amount_field]) if amount_field in row else None
        if pd.isna(row[amount_field]) or str(row[amount_field]).strip() == "":
            pass
        elif amount is None:
            row_errors.append((amount_field, "Invalid amount"))
        elif amount < 0:
            row_errors.append((amount_field, "Negative amount is not allowed"))

        parsed_date = parse_date(row[date_field])
        if pd.isna(row[date_field]) or str(row[date_field]).strip() == "":
            pass
        elif parsed_date is None:
            row_errors.append((date_field, "Invalid date; expected a recognizable date value"))

        if row_errors:
            invalid_rows.add(excel_row)
            for field, message in row_errors:
                errors.append({"row": excel_row, "record_id": record_id or "N/A", "field": field, "error": message})
            continue

        valid = row.to_dict()
        valid[id_field] = record_id
        valid["customer_name"] = normalize_customer_name(row["customer_name"])
        valid["customer_id"] = "" if pd.isna(row["customer_id"]) else str(row["customer_id"]).strip()
        valid[amount_field] = amount
        valid[date_field] = parsed_date
        valid_rows.append(valid)

    return {"total": len(frame), "valid": valid_rows, "invalid_count": len(invalid_rows), "errors": errors, "fingerprint": file_fingerprint(frame, kind)}
