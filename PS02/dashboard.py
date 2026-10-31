from decimal import Decimal
from datetime import date

import pandas as pd
import streamlit as st

from import_csv import import_csv
from anomalies import detect_all
from matching_advanced import generate_match_suggestions, DEFAULT_WEIGHTS, DEFAULT_THRESHOLDS
from reconciliation_report import reconciliation_report
from reports import reconciliation_rows, unmatched_invoice_export, unallocated_payment_export, ambiguous_mismatch_export, csv_bytes, matching_performance
from review import ReviewActions, parse_review_amount
from storage import ReconciliationStore

NAVY = "#102A43"
GREEN = "#1F9D6A"


def apply_theme():
    st.set_page_config(page_title="LedgerFlow Reconciliation", page_icon="LF", layout="wide")
    st.markdown("""
    <style>
      .stApp { background: #F7F9FC; }
      [data-testid="stSidebar"] { background: #FFFFFF; border-right: 1px solid #E7EDF5; }
      [data-testid="stMetric"] { background: #FFFFFF; border: 1px solid #E7EDF5; border-radius: 8px; padding: 16px; }
      [data-testid="stMetricLabel"] { color: #5B6B7C; }
      h1, h2, h3 { color: #102A43; letter-spacing: 0 !important; }
      div[data-testid="stDataFrame"] { border: 1px solid #E7EDF5; border-radius: 8px; overflow: hidden; }
      .brand { color: #102A43; font-size: 20px; font-weight: 800; margin-bottom: 18px; }
      .brand span { color: #1F9D6A; }
      .alert { border-left: 4px solid #D97706; background: #FFF8EB; padding: 10px 14px; border-radius: 6px; }
    </style>
    """, unsafe_allow_html=True)


def date_value(value):
    try:
        return pd.to_datetime(value, errors="coerce").date()
    except Exception:
        return None


def metric_cards(store, report):
    counts = store.counts()
    decisions = store.decisions()
    confirmed = sum(1 for row in decisions if row["decision"] in ("confirmed", "corrected"))
    pending = report["pending_review_count"]
    cards = st.columns(5)
    cards[0].metric("Total Invoices", counts["invoices"])
    cards[1].metric("Total Payments", counts["payments"])
    cards[2].metric("Confirmed Matches", confirmed)
    cards[3].metric("Pending Reviews", pending)
    cards[4].metric("Outstanding Balance", f"{report['total_outstanding_balance']:.2f}")


def filter_controls(report, store):
    invoices = report["invoices"]
    invoice_frame = pd.DataFrame(invoices) if invoices else pd.DataFrame(columns=["customer_name", "status"])
    customers = sorted(set(invoice_frame["customer_name"].dropna().astype(str))) if not invoice_frame.empty else []
    statuses = sorted(set(invoice_frame["status"].dropna().astype(str))) if not invoice_frame.empty else []

    search = st.text_input("Search by invoice, payment, or customer", key="global-search")
    status_filter = st.multiselect("Status", statuses, key="status-filter")
    customer_filter = st.multiselect("Customer", customers, key="customer-filter")
    return search.strip().lower(), status_filter, customer_filter


def main():
    apply_theme()
    store = ReconciliationStore("reconciliation.sqlite3")
    review_actions = ReviewActions(store)

    with st.sidebar:
        st.markdown('<div class="brand">Ledger<span>Flow</span></div>', unsafe_allow_html=True)
        st.caption("AUTOMATED RECONCILIATION")
        page = st.radio("Workspace", ["Overview", "Review & Match", "Invoices & Payments", "Anomaly Center", "Import Data", "Matching Configuration"], label_visibility="collapsed")
        st.divider()
        st.caption("Database")
        st.caption("reconciliation.sqlite3")

    base_report = reconciliation_report(store)
    invoice_records = store.invoice_rows()
    payment_records_for_anomalies = store.payment_rows()
    payment_records = store.payment_rows()
    invoice_frame = pd.DataFrame(invoice_records) if invoice_records else pd.DataFrame(columns=["invoice_id", "customer_name", "customer_id", "invoice_amount", "invoice_date"])
    payment_frame = pd.DataFrame(payment_records) if payment_records else pd.DataFrame(columns=["payment_id", "customer_name", "customer_id", "payment_amount", "payment_date", "invoice_reference", "description"])

    decisions = store.decisions()
    decision_keys = {(row["invoice_id"], row["payment_id"]): row for row in decisions}
    if invoice_records and payment_records_for_anomalies:
        store.save_anomalies(detect_all(invoice_records, payment_records_for_anomalies))

    matching_weights = DEFAULT_WEIGHTS.copy()
    matching_thresholds = DEFAULT_THRESHOLDS.copy()
    if "matching_weights" not in st.session_state:
        st.session_state.matching_weights = matching_weights
    if "matching_thresholds" not in st.session_state:
        st.session_state.matching_thresholds = matching_thresholds
    matching_weights = st.session_state.matching_weights
    matching_thresholds = st.session_state.matching_thresholds
    suggestions = generate_match_suggestions(invoice_frame, payment_frame, weights=matching_weights, thresholds=matching_thresholds) if not invoice_frame.empty and not payment_frame.empty else []
    store.save_matching_suggestions(suggestions)
    pending_suggestions = [
        row for row in suggestions
        if (row["invoice_id"], row["payment_id"]) not in decision_keys
        and row["payment_id"] not in {item["payment_id"] for item in store.allocations()}
    ]
    base_report["pending_review_count"] = len(pending_suggestions)

    st.title("Reconciliation Dashboard")
    st.caption("Review, reconcile, and monitor financial allocations")

    search, status_filter, customer_filter = filter_controls(base_report, store)
    metric_cards(store, base_report)

    if page == "Overview":
        st.subheader("Recent reconciliation activity")
        recent = store.recent_decisions(8)
        if recent:
            activity_frame = pd.DataFrame(recent)[["invoice_id", "payment_id", "decision", "amount", "explanation"]]
            st.dataframe(activity_frame, use_container_width=True, hide_index=True)
        else:
            st.info("No reconciliation decisions recorded yet.")

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Invoice reconciliation status")
            status_frame = pd.DataFrame(base_report["invoices"])
            if not status_frame.empty:
                chart = status_frame.groupby("status").size().rename("Invoice count").reset_index()
                st.bar_chart(chart, x="status", y="Invoice count", color=GREEN, horizontal=True)
            else:
                st.info("No invoice status data available.")
        with col2:
            st.subheader("Payment allocation")
            allocated = base_report["total_allocated_payments"]
            unallocated = base_report["total_unallocated_payments"]
            allocation_chart = pd.DataFrame({"Category": ["Allocated", "Unallocated"], "Amount": [float(allocated), float(unallocated)]})
            st.bar_chart(allocation_chart, x="Category", y="Amount", color=NAVY)

        section_tabs = st.tabs(["Match suggestions", "Pending review", "Unmatched", "Unallocated", "Mismatch alerts"])
        with section_tabs[0]:
            suggestion_rows = []
            allocated_ids = {item["payment_id"] for item in store.allocations()}
            for suggestion in pending_suggestions:
                invoice_row = invoice_frame.loc[invoice_frame["invoice_id"].astype(str) == suggestion["invoice_id"]]
                payment_row = payment_frame.loc[payment_frame["payment_id"].astype(str) == suggestion["payment_id"]]
                if invoice_row.empty or payment_row.empty:
                    continue
                suggestion_rows.append({
                    "Invoice ID": suggestion["invoice_id"],
                    "Customer name": invoice_row.iloc[0]["customer_name"],
                    "Invoice amount": invoice_row.iloc[0]["invoice_amount"],
                    "Payment ID": suggestion["payment_id"],
                    "Payment amount": payment_row.iloc[0]["payment_amount"],
                    "Confidence score": suggestion["score"],
                    "Category": suggestion["category"],
                    "Explanation": "; ".join(suggestion["evidence"]),
                    "Amount difference": suggestion["amount_difference"],
                    "Status": "Ambiguous" if suggestion["ambiguous"] else "Pending",
                })
            suggestion_frame = pd.DataFrame(suggestion_rows)
            if not suggestion_frame.empty:
                suggestion_frame = apply_search_status_customer(suggestion_frame, search, status_filter, customer_filter)
                st.dataframe(suggestion_frame, use_container_width=True, hide_index=True)
            else:
                st.info("No pending match suggestions.")

        with section_tabs[1]:
            pending_frame = pd.DataFrame([row for row in suggestion_rows if row["Status"] == "Ambiguous"]) if 'suggestion_rows' in locals() else pd.DataFrame()
            st.dataframe(pending_frame, use_container_width=True, hide_index=True) if not pending_frame.empty else st.info("No ambiguous pending records.")

        with section_tabs[2]:
            allocated_invoice_ids = {row["invoice_id"] for row in store.allocations()}
            unmatched = [row for row in base_report["invoices"] if row["invoice_id"] not in allocated_invoice_ids and row["outstanding"] > 0]
            unmatched_frame = pd.DataFrame(unmatched)
            st.dataframe(unmatched_frame, use_container_width=True, hide_index=True) if unmatched else st.info("No unmatched invoices.")

        with section_tabs[3]:
            unallocated = pd.DataFrame(base_report["unallocated_payments"])
            st.dataframe(unallocated, use_container_width=True, hide_index=True) if not unallocated.empty else st.info("All payments are allocated.")

        with section_tabs[4]:
            mismatches = pd.DataFrame(base_report["mismatch_cases"])
            st.dataframe(mismatches, use_container_width=True, hide_index=True) if not mismatches.empty else st.info("No amount mismatch alerts.")

        st.subheader("Exports")
        export_columns = st.columns(4)
        export_columns[0].download_button("Reconciliation CSV", csv_bytes(reconciliation_rows(store)), "reconciliation_report.csv", "text/csv", key="export-reconciliation")
        export_columns[1].download_button("Unmatched invoices", csv_bytes(unmatched_invoice_export(store)), "unmatched_invoices.csv", "text/csv", key="export-unmatched")
        export_columns[2].download_button("Unallocated payments", csv_bytes(unallocated_payment_export(store)), "unallocated_payments.csv", "text/csv", key="export-unallocated")
        ambiguous_pairs = [(row["invoice_id"], row["payment_id"]) for row in pending_suggestions if row["ambiguous"]]
        export_columns[3].download_button("Ambiguous & mismatched", csv_bytes(ambiguous_mismatch_export(store, ambiguous_pairs)), "ambiguous_mismatched.csv", "text/csv", key="export-ambiguity")

        st.subheader("Labeled dataset performance")
        performance = matching_performance(store)
        performance_columns = st.columns(5)
        performance_columns[0].metric("Matching precision", f'{performance["matching_precision"]:.1%}')
        performance_columns[1].metric("Correct suggestion rate", f'{performance["correct_suggestion_rate"]:.1%}')
        performance_columns[2].metric("Confirmed matches", performance["confirmed_matches"])
        performance_columns[3].metric("Unresolved cases", performance["unresolved_cases"])
        performance_columns[4].metric("Duplicate allocations prevented", performance["duplicate_allocations_prevented"])

    elif page == "Review & Match":
        st.subheader("Match review queue")
        if not pending_suggestions:
            st.success("No pending matches require review.")
        for suggestion in pending_suggestions:
            key = f"{suggestion['invoice_id']}:{suggestion['payment_id']}"
            with st.container(border=True):
                header = st.columns([2, 1, 1])
                header[0].markdown(f"### {suggestion['invoice_id']} / {suggestion['payment_id']}")
                header[1].metric("Confidence", f"{suggestion['score']}%")
                header[2].write(suggestion["category"])
                st.write("; ".join(suggestion["evidence"]))
                st.write(f"Amount difference: **{suggestion['amount_difference']}**")
                if suggestion["conflicting"]:
                    st.error("Conflict detected: competing invoice candidates require manual review.")
                if suggestion["contributions"]:
                    contribution_frame = pd.DataFrame(suggestion["contributions"])
                    st.dataframe(contribution_frame, use_container_width=True, hide_index=True)
                invoice_row = invoice_frame.loc[invoice_frame["invoice_id"].astype(str) == suggestion["invoice_id"]].iloc[0]
                payment_row = payment_frame.loc[payment_frame["payment_id"].astype(str) == suggestion["payment_id"]].iloc[0]
                invoice_remaining = store.invoice_outstanding(suggestion["invoice_id"])
                payment_remaining = store.payment_remaining(suggestion["payment_id"])
                default_amount = min(parse_review_amount(invoice_row["invoice_amount"]), parse_review_amount(payment_row["payment_amount"]), invoice_remaining, payment_remaining)
                amount = st.number_input("Allocation amount", min_value=0.01, value=float(default_amount), step=0.01, key=f"review-{key}", format="%.2f")
                confirm_col, reject_col = st.columns(2)
                if confirm_col.button("Confirm Match", key=f"confirm-{key}", type="primary"):
                    try:
                        review_actions.confirm_match(suggestion["invoice_id"], suggestion["payment_id"], Decimal(str(amount)).quantize(Decimal("0.01")), "; ".join(suggestion["evidence"]))
                        st.success("Confirmed and allocation saved.")
                        st.rerun()
                    except ValueError as error:
                        st.error(str(error))
                if reject_col.button("Reject Match", key=f"reject-{key}"):
                    review_actions.reject_match(suggestion["invoice_id"], suggestion["payment_id"], "; ".join(suggestion["evidence"]))
                    st.success("Rejected and decision saved.")
                    st.rerun()

        st.subheader("Manual Assign")
        if not invoice_frame.empty and not payment_frame.empty:
            manual_invoice = st.selectbox("Invoice", invoice_frame["invoice_id"].astype(str).tolist())
            manual_payment = st.selectbox("Payment", payment_frame["payment_id"].astype(str).tolist())
            if manual_invoice and manual_payment:
                st.caption(f"Invoice remaining: {store.invoice_outstanding(manual_invoice):.2f} | Payment remaining: {store.payment_remaining(manual_payment):.2f}")
            manual_amount_text = st.text_input("Exact allocation amount (Decimal)")
            if st.button("Manual Assign", type="primary"):
                manual_amount = parse_review_amount(manual_amount_text)
                if manual_amount is None:
                    st.error("Enter a valid positive amount.")
                else:
                    try:
                        review_actions.manual_assign(manual_invoice, manual_payment, manual_amount, "Manual assignment")
                        st.success("Manual allocation saved.")
                        st.rerun()
                    except ValueError as error:
                        st.error(str(error))

            st.subheader("Allocation history")
            history = store.allocation_history()
            if history:
                history_frame = pd.DataFrame([{
                    "Time": row["created_at"], "Invoice": row["invoice_id"], "Payment": row["payment_id"],
                    "Amount": row["amount_decimal"], "Action": row["action"], "Explanation": row["explanation"],
                } for row in history])
                st.dataframe(history_frame, use_container_width=True, hide_index=True)
            else:
                st.info("No allocation history has been recorded.")

    elif page == "Invoices & Payments":
        st.subheader("Invoice ledger")
        invoice_display = pd.DataFrame(base_report["invoices"])
        if not invoice_display.empty:
            invoice_display = apply_search_status_customer(invoice_display, search, status_filter, customer_filter)
            st.dataframe(invoice_display, use_container_width=True, hide_index=True)
        else:
            st.info("No invoices imported.")

        st.subheader("Payment ledger")
        payment_display = payment_frame.copy()
        if not payment_display.empty and search:
            mask = payment_display.astype(str).apply(lambda column: column.str.lower().str.contains(search, regex=False)).any(axis=1)
            payment_display = payment_display[mask]
        st.dataframe(payment_display, use_container_width=True, hide_index=True) if not payment_display.empty else st.info("No payments imported.")

    elif page == "Anomaly Center":
        st.subheader("Anomaly Center")
        anomaly_severity = st.selectbox("Severity", ["All", "Critical", "Warning", "Informational"])
        anomaly_status = st.selectbox("Review status", ["All", "Unreviewed", "Reviewed"])
        anomaly_search = st.text_input("Search anomalies")
        severity_filter = None if anomaly_severity == "All" else anomaly_severity
        status_filter_value = None if anomaly_status == "All" else anomaly_status
        anomalies = store.anomalies(severity_filter, status_filter_value)

        critical_count = sum(1 for row in anomalies if row["severity"] == "Critical")
        warning_count = sum(1 for row in anomalies if row["severity"] == "Warning")
        informational_count = sum(1 for row in anomalies if row["severity"] == "Informational")
        anomaly_metrics = st.columns(3)
        anomaly_metrics[0].metric("Critical", critical_count)
        anomaly_metrics[1].metric("Warning", warning_count)
        anomaly_metrics[2].metric("Informational", informational_count)

        if not anomalies:
            st.info("No anomalies match the selected filters.")
        for anomaly in anomalies:
            searchable = " ".join(str(value) for value in anomaly.values()).lower()
            if anomaly_search.strip().lower() and anomaly_search.strip().lower() not in searchable:
                continue
            severity_color = "red" if anomaly["severity"] == "Critical" else ("orange" if anomaly["severity"] == "Warning" else "blue")
            with st.container(border=True):
                header = st.columns([2, 1, 1])
                header[0].markdown(f"### {anomaly['kind']}")
                header[1].markdown(f":{severity_color}[{anomaly['severity']}]")
                header[2].write(anomaly["review_status"])
                st.write(anomaly["explanation"])
                st.caption(f"Record: {anomaly['record_type']} {anomaly['record_id']} | Related: {anomaly['related_ids'] or 'None'} | Algorithm: {anomaly['algorithm_version']}")
                if anomaly["review_status"] == "Unreviewed":
                    if st.button("Mark reviewed", key=f"review-anomaly-{anomaly['anomaly_key']}"):
                        store.mark_anomaly_reviewed(anomaly["anomaly_key"])
                        st.success("Anomaly marked as reviewed.")
                        st.rerun()

    elif page == "Import Data":
        st.subheader("Import invoice and payment CSV files")
        load_samples = st.toggle("Load labeled sample files", value=False)
        uploaded_invoice = st.file_uploader("Invoice CSV", type=["csv"])
        uploaded_payment = st.file_uploader("Payment CSV", type=["csv"])
        if load_samples and uploaded_invoice is None:
            uploaded_invoice = "data/invoices.csv"
        if load_samples and uploaded_payment is None:
            uploaded_payment = "data/payments.csv"

        for kind, uploaded in (("invoice", uploaded_invoice), ("payment", uploaded_payment)):
            if uploaded is None:
                continue
            label = "Invoice" if kind == "invoice" else "Payment"
            try:
                result = import_csv(uploaded, kind)
            except Exception as error:
                st.error(f"Could not read {label.lower()} CSV: {error}")
                continue
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total uploaded", result["total"])
            c2.metric("Valid records", len(result["valid"]))
            c3.metric("Invalid records", result["invalid_count"])
            c4.metric("Validation errors", len(result["errors"]))
            if result["errors"]:
                st.dataframe(pd.DataFrame(result["errors"]), use_container_width=True, hide_index=True)
            if st.button(f"Import valid {label.lower()} records", key=f"import-{kind}", type="primary"):
                try:
                    store.import_records(kind, result)
                    st.success(f"{label} records imported.")
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))

        errors = store.import_errors()
        if errors:
            st.subheader("Stored validation errors")
            st.dataframe(pd.DataFrame(errors), use_container_width=True, hide_index=True)

    else:
        st.subheader("Matching configuration")
        st.caption("Deterministic weighted factors stored with each generated suggestion")
        weight_columns = st.columns(2)
        with weight_columns[0]:
            for factor in matching_weights:
                matching_weights[factor] = st.slider(factor.replace("_", " ").title(), 0, 40, matching_weights[factor], key=f"weight-{factor}")
        with weight_columns[1]:
            threshold_labels = {"minimum_score": "Minimum score", "high_confidence": "High confidence", "medium_confidence": "Medium confidence", "conflict_score_gap": "Conflict score gap"}
            for factor, label in threshold_labels.items():
                matching_thresholds[factor] = st.slider(label, 0, 100, matching_thresholds[factor], key=f"threshold-{factor}")
        if st.button("Reset to defaults", type="secondary"):
            st.session_state.matching_weights = DEFAULT_WEIGHTS.copy()
            st.session_state.matching_thresholds = DEFAULT_THRESHOLDS.copy()
            st.rerun()


def apply_search_status_customer(frame, search, status_filter, customer_filter):
    filtered = frame.copy()
    if search:
        mask = filtered.astype(str).apply(lambda column: column.str.lower().str.contains(search, regex=False)).any(axis=1)
        filtered = filtered[mask]
    if status_filter and "status" in filtered.columns:
        filtered = filtered[filtered["status"].isin(status_filter)]
    if customer_filter and "customer_name" in filtered.columns:
        filtered = filtered[filtered["customer_name"].isin(customer_filter)]
    return filtered


if __name__ == "__main__":
    main()
