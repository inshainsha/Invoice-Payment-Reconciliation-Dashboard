from dashboard import main

main()
raise SystemExit

from decimal import Decimal
import pandas as pd
import streamlit as st

from import_csv import import_csv
from matching import generate_match_suggestions
from storage import ReconciliationStore
from review import ReviewActions, parse_review_amount
from reconciliation_report import reconciliation_report

st.set_page_config(page_title="Invoice & Payment CSV Import", layout="wide")
st.title("Invoice & Payment CSV Import")

invoice_file = st.sidebar.file_uploader("Invoice CSV", type=["csv"])
payment_file = st.sidebar.file_uploader("Payment CSV", type=["csv"])
use_samples = st.sidebar.checkbox("Load labeled sample files", value=False)

store = ReconciliationStore("reconciliation.sqlite3")
results = {}
if use_samples and invoice_file is None:
    invoice_file = "data/invoices.csv"
if use_samples and payment_file is None:
    payment_file = "data/payments.csv"

if use_samples:
    st.caption("Demo mode: labeled sample CSV files covering matches, ambiguities, mismatches, and duplicate-looking records")

for kind, uploaded in (("invoice", invoice_file), ("payment", payment_file)):
    label = "Invoice" if kind == "invoice" else "Payment"
    if uploaded is None:
        st.info(f"Upload the {label.lower()} CSV to begin validation.")
        continue

    st.subheader(f"{label} import")
    try:
        result = import_csv(uploaded, kind)
        results[kind] = result
    except Exception as error:
        st.error(f"Could not read {label.lower()} CSV: {error}")
        continue

    st.markdown("**Import summary**")
    columns = st.columns(4)
    columns[0].metric("Total uploaded", result["total"])
    columns[1].metric("Valid records", len(result["valid"]))
    columns[2].metric("Invalid records", result["invalid_count"])
    columns[3].metric("Validation errors", len(result["errors"]))

    if result["errors"]:
        st.error(f"{result['invalid_count']} invalid record(s) will be retained for review and will not be stored.")
        st.dataframe(pd.DataFrame(result["errors"]), use_container_width=True)
    else:
        st.success("No validation errors found.")

    if st.button(f"Import valid {label.lower()} records", key=f"import-{kind}", type="primary"):
        try:
            imported_count = store.import_records(kind, result)
            st.success(f"Imported {imported_count} valid {label.lower()} records.")
        except ValueError as error:
            st.error(str(error))

invoice_button = results.get("invoice")
payment_button = results.get("payment")

st.subheader("Reconciliation Summary")
report = reconciliation_report(store)
summary_columns = st.columns(6)
summary_columns[0].metric("Total invoice value", f'{report["total_invoice_value"]:.2f}')
summary_columns[1].metric("Received payments", f'{report["total_received_payments"]:.2f}')
summary_columns[2].metric("Allocated payments", f'{report["total_allocated_payments"]:.2f}')
summary_columns[3].metric("Outstanding balance", f'{report["total_outstanding_balance"]:.2f}')
summary_columns[4].metric("Unallocated payments", f'{report["total_unallocated_payments"]:.2f}')
summary_columns[5].metric("Mismatch cases", len(report["mismatch_cases"]))

st.dataframe(pd.DataFrame(report["invoices"]), use_container_width=True, hide_index=True) if report["invoices"] else st.info("No imported invoices are available for balance calculation.")

if report["unallocated_payments"]:
    with st.expander("Unallocated payments"):
        st.dataframe(pd.DataFrame(report["unallocated_payments"]), use_container_width=True, hide_index=True)
if report["mismatch_cases"]:
    with st.expander("Amount mismatch cases"):
        st.dataframe(pd.DataFrame(report["mismatch_cases"]), use_container_width=True, hide_index=True)

st.subheader("Stored imported records")
if invoice_button is not None:
    st.caption("Valid invoice rows from the latest successful import")
    if results["invoice"]["valid"]:
        st.dataframe(pd.DataFrame(results["invoice"]["valid"]), use_container_width=True)
if payment_button is not None:
    st.caption("Valid payment rows from the latest successful import")
    if results["payment"]["valid"]:
        st.dataframe(pd.DataFrame(results["payment"]["valid"]), use_container_width=True)

if invoice_button is not None and payment_button is not None:
    st.subheader("Match Review")
    invoice_frame = pd.DataFrame(results["invoice"]["valid"])
    payment_frame = pd.DataFrame(results["payment"]["valid"])
    suggestions = generate_match_suggestions(invoice_frame, payment_frame)
    review_actions = ReviewActions(store)
    decisions = {row["invoice_id"] + ":" + row["payment_id"]: row for row in store.decisions()}

    allocations = store.allocations()
    allocation_count = len(allocations)
    confirmed_amount = sum((row["amount"] for row in allocations), start=Decimal("0.00"))
    metric_columns = st.columns(3)
    metric_columns[0].metric("Confirmed allocations", allocation_count)
    metric_columns[1].metric("Confirmed amount", f"{confirmed_amount:.2f}")
    metric_columns[2].metric("Review decisions", len(store.decisions()))

    if not suggestions:
        st.info("No match candidates met the minimum evidence threshold.")
    else:
        review_rows = []
        for suggestion in suggestions:
            invoice_row = invoice_frame.loc[invoice_frame["invoice_id"].astype(str) == suggestion["invoice_id"]].iloc[0]
            payment_row = payment_frame.loc[payment_frame["payment_id"].astype(str) == suggestion["payment_id"]].iloc[0]
            status_row = decisions.get(suggestion["invoice_id"] + ":" + suggestion["payment_id"])
            status = status_row["decision"] if status_row else ("Ambiguous review" if suggestion["ambiguous"] else "Pending review")
            review_rows.append({
                "Invoice ID": suggestion["invoice_id"],
                "Customer name": invoice_row["customer_name"],
                "Invoice amount": invoice_row["invoice_amount"],
                "Payment ID": suggestion["payment_id"],
                "Payment amount": payment_row["payment_amount"],
                "Confidence score": suggestion["score"],
                "Matching explanation": "; ".join(suggestion["evidence"]),
                "Amount difference": suggestion["amount_difference"],
                "Match status": status,
            })
        st.dataframe(pd.DataFrame(review_rows), use_container_width=True, hide_index=True)

        for suggestion in suggestions:
            key = suggestion["invoice_id"] + ":" + suggestion["payment_id"]
            status_row = decisions.get(key)
            if status_row and status_row["decision"] in ("confirmed", "corrected", "rejected"):
                continue
            invoice_row = invoice_frame.loc[invoice_frame["invoice_id"].astype(str) == suggestion["invoice_id"]].iloc[0]
            payment_row = payment_frame.loc[payment_frame["payment_id"].astype(str) == suggestion["payment_id"]].iloc[0]
            with st.container(border=True):
                st.write(f"**{suggestion['invoice_id']}** - **{suggestion['payment_id']}**")
                if suggestion["ambiguous"]:
                    st.warning("Ambiguous match: manual review is recommended")
                invoice_allocatable = parse_review_amount(invoice_row["invoice_amount"])
                payment_allocatable = parse_review_amount(payment_row["payment_amount"])
                allocation_amount = min(invoice_allocatable, payment_allocatable) if invoice_allocatable and payment_allocatable else Decimal("0.01")
                amount = st.number_input(
                    "Allocation amount", min_value=0.01, value=float(allocation_amount or 0.01), step=0.01,
                    key=f"amount-{suggestion['invoice_id']}-{suggestion['payment_id']}", format="%.2f",
                )
                confirm_button, reject_button = st.columns(2)
                if confirm_button.button("Confirm Match", key=f"confirm-{key}"):
                    try:
                        review_actions.confirm_match(suggestion["invoice_id"], suggestion["payment_id"], amount,
                                                     "Confirmed reviewed suggestion: " + "; ".join(suggestion["evidence"]))
                        st.success("Match confirmed and allocated.")
                        st.rerun()
                    except ValueError as error:
                        st.error(str(error))
                if reject_button.button("Reject Match", key=f"reject-{key}"):
                    review_actions.reject_match(suggestion["invoice_id"], suggestion["payment_id"],
                                                "Rejected reviewed suggestion: " + "; ".join(suggestion["evidence"]))
                    st.success("Match rejected and decision saved.")
                    st.rerun()

    st.subheader("Manual Assign")
    invoice_options = invoice_frame["invoice_id"].astype(str).tolist()
    payment_options = payment_frame["payment_id"].astype(str).tolist()
    manual_invoice = st.selectbox("Invoice", invoice_options, key="manual-invoice")
    manual_payment = st.selectbox("Payment", payment_options, key="manual-payment")
    manual_amount_text = st.text_input("Manual allocation amount", value="", key="manual-amount")
    if st.button("Manual Assign", key="manual-assign-action"):
        allocation_amount = parse_review_amount(manual_amount_text)
        if allocation_amount is None:
            st.error("Allocation amount must be a valid positive Decimal amount.")
        else:
            try:
                review_actions.manual_assign(manual_invoice, manual_payment, allocation_amount,
                                             "Manual assignment corrected the suggested pair")
                st.success("Manual allocation saved successfully.")
                st.rerun()
            except ValueError as error:
                st.error(str(error))
