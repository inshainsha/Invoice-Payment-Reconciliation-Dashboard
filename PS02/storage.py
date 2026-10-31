import sqlite3
import json
from decimal import Decimal, InvalidOperation


class ReconciliationStore:
    def __init__(self, path="reconciliation.sqlite3"):
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS import_batches (
            kind TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(kind, fingerprint)
        );
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id TEXT PRIMARY KEY,
            customer_name TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            invoice_amount TEXT NOT NULL,
            invoice_date TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payments (
            payment_id TEXT PRIMARY KEY,
            customer_name TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            payment_amount TEXT NOT NULL,
            payment_date TEXT NOT NULL,
            invoice_reference TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS import_errors (
            kind TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            record_id TEXT NOT NULL,
            field TEXT NOT NULL,
            message TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS decisions (
            invoice_id TEXT NOT NULL,
            payment_id TEXT NOT NULL,
            decision TEXT NOT NULL CHECK(decision IN ('confirmed','rejected','corrected')),
            amount TEXT NOT NULL,
            explanation TEXT NOT NULL,
            UNIQUE(invoice_id, payment_id)
        );
        CREATE TABLE IF NOT EXISTS allocations (
            invoice_id TEXT NOT NULL,
            payment_id TEXT NOT NULL,
            amount TEXT NOT NULL,
            PRIMARY KEY(invoice_id, payment_id)
        );
        CREATE TABLE IF NOT EXISTS duplicate_prevention_events (
            payment_id TEXT NOT NULL,
            attempted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS allocation_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id TEXT NOT NULL,
            payment_id TEXT NOT NULL,
            amount TEXT NOT NULL,
            action TEXT NOT NULL,
            explanation TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS anomaly_findings (
            anomaly_key TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            record_type TEXT NOT NULL,
            record_id TEXT NOT NULL,
            severity TEXT NOT NULL CHECK(severity IN ('Critical','Warning','Informational')),
            explanation TEXT NOT NULL,
            related_ids TEXT NOT NULL DEFAULT '',
            algorithm_version TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'Unreviewed',
            reviewed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS matching_suggestions (
            invoice_id TEXT NOT NULL,
            payment_id TEXT NOT NULL,
            algorithm_version TEXT NOT NULL,
            score INTEGER NOT NULL,
            category TEXT NOT NULL,
            weights_json TEXT NOT NULL,
            thresholds_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            contributions_json TEXT NOT NULL,
            competing_invoice_ids TEXT NOT NULL DEFAULT '',
            conflicting INTEGER NOT NULL DEFAULT 0,
            ambiguous INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(invoice_id, payment_id)
        );
        """)
        self.connection.commit()
        self._migrate_payment_only_allocations()

    def _migrate_payment_only_allocations(self):
        unique_payment_index = False
        for index in self.connection.execute("PRAGMA index_list(allocations)").fetchall():
            if not index[2]:
                continue
            columns = [row[2] for row in self.connection.execute(f"PRAGMA index_info('{index[1]}')")]
            if columns == ["payment_id"]:
                unique_payment_index = True
                break
        if not unique_payment_index:
            return
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute("ALTER TABLE allocations RENAME TO allocations_legacy")
            self.connection.execute("CREATE TABLE allocations (invoice_id TEXT NOT NULL, payment_id TEXT NOT NULL, amount TEXT NOT NULL, PRIMARY KEY(invoice_id, payment_id))")
            self.connection.execute("INSERT INTO allocations(invoice_id, payment_id, amount) SELECT invoice_id, payment_id, amount FROM allocations_legacy")
            self.connection.execute("DROP TABLE allocations_legacy")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def import_records(self, kind, result):
        fingerprint = result["fingerprint"]
        existing = self.connection.execute(
            "SELECT 1 FROM import_batches WHERE kind = ? AND fingerprint = ?", (kind, fingerprint)
        ).fetchone()
        if existing:
            raise ValueError("This exact " + kind + " CSV has already been imported")

        duplicate_ids = []
        id_field = "invoice_id" if kind == "invoice" else "payment_id"
        table = "invoices" if kind == "invoice" else "payments"
        for record in result["valid"]:
            record_id = str(record[id_field])
            exists = self.connection.execute(f"SELECT 1 FROM {table} WHERE {id_field} = ?", (record_id,)).fetchone()
            if exists:
                duplicate_ids.append(record_id)

        if duplicate_ids:
            raise ValueError("Duplicate " + id_field + " already stored: " + ", ".join(duplicate_ids))

        for record in result["valid"]:
            if kind == "invoice":
                self.connection.execute("INSERT INTO invoices VALUES (?, ?, ?, ?, ?)", (
                    str(record["invoice_id"]), record["customer_name"], str(record["customer_id"]),
                    str(record["invoice_amount"]), str(record["invoice_date"])))
            else:
                self.connection.execute("INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?, ?)", (
                    str(record["payment_id"]), record["customer_name"], str(record["customer_id"]),
                    str(record["payment_amount"]), str(record["payment_date"]),
                    str(record.get("invoice_reference", "")), str(record.get("description", ""))))

        self.connection.execute("INSERT INTO import_batches(kind, fingerprint) VALUES (?, ?)", (kind, fingerprint))
        for error in result["errors"]:
            self.connection.execute("INSERT INTO import_errors VALUES (?, ?, ?, ?, ?, ?)", (
                kind, fingerprint, error["row"], error["record_id"], error["field"], error["error"]))
        self.connection.commit()
        return len(result["valid"])

    def _allocation_total(self, invoice_id):
        row = self.connection.execute(
            "SELECT COALESCE(SUM(CAST(amount AS TEXT)), 0) AS total FROM allocations WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchone()
        return Decimal(str(row["total"])) if row and str(row["total"]) else Decimal("0.00")

    def _payment_total(self, payment_id):
        row = self.connection.execute(
            "SELECT payment_amount FROM payments WHERE payment_id = ?", (payment_id,),
        ).fetchone()
        if not row:
            raise ValueError("Unknown payment ID")
        return Decimal(row["payment_amount"])

    def _invoice_total(self, invoice_id):
        row = self.connection.execute(
            "SELECT invoice_amount FROM invoices WHERE invoice_id = ?", (invoice_id,),
        ).fetchone()
        if not row:
            raise ValueError("Unknown invoice ID")
        return Decimal(row["invoice_amount"])

    def _validate_allocation(self, invoice_id, payment_id, amount, allow_existing=False):
        try:
            allocation = Decimal(str(amount)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError, AttributeError):
            raise ValueError("Allocation amount is invalid")
        if not allocation.is_finite() or allocation <= 0:
            raise ValueError("Allocation amount must be greater than zero")

        invoice_total = self._invoice_total(invoice_id)
        payment_total = self._payment_total(payment_id)
        allocated_to_invoice = self._allocation_total(invoice_id)
        existing = self.connection.execute(
            "SELECT amount FROM allocations WHERE invoice_id = ? AND payment_id = ?", (invoice_id, payment_id),
        ).fetchone()

        if existing:
            self.connection.execute("INSERT INTO duplicate_prevention_events(payment_id) VALUES (?)", (payment_id,))
            self.connection.commit()
            raise ValueError("This invoice and payment pair is already allocated")

        allocated_to_payment = self.connection.execute(
            "SELECT COALESCE(SUM(CAST(amount AS TEXT)), 0) AS total FROM allocations WHERE payment_id = ?",
            (payment_id,),
        ).fetchone()["total"]
        allocated_to_payment = Decimal(str(allocated_to_payment))

        if allocation > invoice_total - allocated_to_invoice:
            raise ValueError("Allocation exceeds the invoice outstanding balance")
        if allocation > payment_total - allocated_to_payment:
            raise ValueError("Allocation exceeds the remaining payment amount")
        return allocation

    def confirm(self, invoice_id, payment_id, amount=None, explanation="Confirmed match"):
        try:
            allocation = self._validate_allocation(
                invoice_id,
                payment_id,
                self._payment_total(payment_id) if amount is None else amount,
            )
        except ValueError:
            raise
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            allocation = self._validate_allocation(invoice_id, payment_id, allocation)
            self.connection.execute(
                "INSERT OR REPLACE INTO decisions VALUES (?, ?, 'confirmed', ?, ?)",
                (invoice_id, payment_id, str(allocation), explanation),
            )
            self.connection.execute(
                "INSERT INTO allocations(invoice_id, payment_id, amount) VALUES (?, ?, ?)",
                (invoice_id, payment_id, str(allocation)),
            )
            self.connection.execute(
                "INSERT INTO allocation_history(invoice_id, payment_id, amount, action, explanation) VALUES (?, ?, ?, 'confirmed', ?)",
                (invoice_id, payment_id, str(allocation), explanation),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return allocation

    def correct(self, invoice_id, payment_id, amount, explanation="Manual assignment"):
        existing = self.connection.execute(
            "SELECT amount FROM allocations WHERE invoice_id = ? AND payment_id = ?", (invoice_id, payment_id),
        ).fetchone()
        if existing:
            raise ValueError("This invoice and payment pair already has an allocation; corrections require a unique allocation")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            allocation = self._validate_allocation(invoice_id, payment_id, amount)
            self.connection.execute(
                "INSERT OR REPLACE INTO decisions VALUES (?, ?, 'corrected', ?, ?)",
                (invoice_id, payment_id, str(allocation), explanation),
            )
            self.connection.execute(
                "INSERT INTO allocations(invoice_id, payment_id, amount) VALUES (?, ?, ?)",
                (invoice_id, payment_id, str(allocation)),
            )
            self.connection.execute(
                "INSERT INTO allocation_history(invoice_id, payment_id, amount, action, explanation) VALUES (?, ?, ?, 'corrected', ?)",
                (invoice_id, payment_id, str(allocation), explanation),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return allocation

    def reject(self, invoice_id, payment_id, explanation="Rejected during review"):
        existing_payment = self.connection.execute(
            "SELECT 1 FROM allocations WHERE invoice_id = ? AND payment_id = ?", (invoice_id, payment_id),
        ).fetchone()
        if existing_payment:
            raise ValueError("Cannot reject an allocated payment; correct the allocation first")
        self.connection.execute(
            "INSERT OR REPLACE INTO decisions VALUES (?, ?, 'rejected', '0.00', ?)",
            (invoice_id, payment_id, explanation),
        )
        self.connection.commit()

    def allocations(self):
        return [dict(row) | {"amount": Decimal(row["amount"])} for row in self.connection.execute("SELECT * FROM allocations ORDER BY invoice_id, payment_id")]

    def allocation_history(self):
        return [dict(row) | {"amount_decimal": Decimal(row["amount"])} for row in self.connection.execute(
            "SELECT * FROM allocation_history ORDER BY history_id DESC"
        )]

    def decisions(self):
        return [dict(row) for row in self.connection.execute("SELECT * FROM decisions ORDER BY rowid DESC")]

    def invoice_rows(self):
        return [dict(row) for row in self.connection.execute("SELECT * FROM invoices ORDER BY invoice_id")]

    def payment_rows(self):
        return [dict(row) for row in self.connection.execute("SELECT * FROM payments ORDER BY payment_id")]

    def counts(self):
        invoice_count = self.connection.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
        payment_count = self.connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        return {"invoices": invoice_count, "payments": payment_count}

    def save_matching_suggestions(self, suggestions):
        self.connection.execute("DELETE FROM matching_suggestions")
        for suggestion in suggestions:
            competing = json.dumps(suggestion.get("competing_invoice_ids", []))
            self.connection.execute("""
                INSERT INTO matching_suggestions
                (invoice_id, payment_id, algorithm_version, score, category, weights_json,
                 thresholds_json, evidence_json, contributions_json, competing_invoice_ids,
                 conflicting, ambiguous)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                suggestion["invoice_id"], suggestion["payment_id"], suggestion["algorithm_version"],
                suggestion["score"], suggestion["category"], json.dumps(suggestion["weights"]),
                json.dumps(suggestion["thresholds"]), json.dumps(suggestion["evidence"]),
                json.dumps(suggestion["contributions"]), competing,
                int(suggestion["conflicting"]), int(suggestion["ambiguous"]),
            ))
        self.connection.commit()

    def matching_suggestions(self):
        rows = []
        for row in self.connection.execute("SELECT * FROM matching_suggestions ORDER BY score DESC, invoice_id, payment_id"):
            item = dict(row)
            item["weights"] = json.loads(item["weights_json"])
            item["thresholds"] = json.loads(item["thresholds_json"])
            item["evidence"] = json.loads(item["evidence_json"])
            item["contributions"] = json.loads(item["contributions_json"])
            item["competing_invoice_ids"] = json.loads(item["competing_invoice_ids"])
            rows.append(item)
        return rows

    def save_anomalies(self, anomalies):
        for anomaly in anomalies:
            identity = "|".join([
                anomaly["kind"], anomaly["record_type"], anomaly["record_id"], anomaly["explanation"],
            ])
            import hashlib
            key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            existing = self.connection.execute("SELECT review_status, reviewed_at FROM anomaly_findings WHERE anomaly_key = ?", (key,)).fetchone()
            if existing:
                continue
            self.connection.execute("""
                INSERT INTO anomaly_findings
                (anomaly_key, kind, record_type, record_id, severity, explanation, related_ids, algorithm_version, review_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Unreviewed')
            """, (
                key, anomaly["kind"], anomaly["record_type"], anomaly["record_id"], anomaly["severity"],
                anomaly["explanation"], anomaly.get("related_ids", ""), anomaly["algorithm_version"],
            ))
        self.connection.commit()

    def anomalies(self, severity=None, status=None):
        query = "SELECT * FROM anomaly_findings WHERE 1=1"
        parameters = []
        if severity:
            query += " AND severity = ?"
            parameters.append(severity)
        if status:
            query += " AND review_status = ?"
            parameters.append(status)
        query += " ORDER BY CASE severity WHEN 'Critical' THEN 1 WHEN 'Warning' THEN 2 ELSE 3 END, record_id"
        return [dict(row) for row in self.connection.execute(query, parameters)]

    def mark_anomaly_reviewed(self, anomaly_key):
        row = self.connection.execute("UPDATE anomaly_findings SET review_status='Reviewed', reviewed_at=CURRENT_TIMESTAMP WHERE anomaly_key=?", (anomaly_key,))
        if row.rowcount != 1:
            raise ValueError("Anomaly record was not found")
        self.connection.commit()

    def recent_decisions(self, limit=8):
        return [dict(row) for row in self.connection.execute(
            "SELECT rowid, invoice_id, payment_id, decision, amount, explanation FROM decisions ORDER BY rowid DESC LIMIT ?",
            (limit,),
        )]

    def import_errors(self):
        return [dict(row) for row in self.connection.execute(
            "SELECT kind, row_number, record_id, field, message FROM import_errors ORDER BY rowid DESC"
        )]

    def invoice_outstanding(self, invoice_id):
        invoice_total = self._invoice_total(invoice_id)
        allocated = self._allocation_total(invoice_id)
        return max(invoice_total - allocated, Decimal("0.00"))

    def duplicate_prevention_count(self):
        return self.connection.execute("SELECT COUNT(*) FROM duplicate_prevention_events").fetchone()[0]

    def payment_remaining(self, payment_id):
        payment_total = self._payment_total(payment_id)
        allocated = self.connection.execute(
            "SELECT COALESCE(SUM(CAST(amount AS TEXT)), 0) AS total FROM allocations WHERE payment_id = ?",
            (payment_id,),
        ).fetchone()["total"]
        return max(payment_total - Decimal(str(allocated)), Decimal("0.00"))

    def close(self):
        self.connection.close()
