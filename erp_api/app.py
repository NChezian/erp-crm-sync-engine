"""
Mock ERP API
Simulates an SAP-style ERP system.
Receives synced records from n8n, validates them against ERP business rules,
and writes accepted records to its own orders table.
"""

import sqlite3
import json
import uuid
import os
import re
from datetime import datetime
from flask import Flask, jsonify, request, abort

app = Flask(__name__)

DB_PATH = os.environ.get("DB_PATH", "../data/sync_engine.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_erp_tables():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS erp_orders (
            order_id        TEXT PRIMARY KEY,
            crm_deal_id     TEXT,
            company_name    TEXT,
            contact_email   TEXT,
            industry        TEXT,
            region          TEXT,
            order_value     REAL,
            currency        TEXT,
            deal_stage      TEXT,
            account_type    TEXT,
            close_date      TEXT,
            received_at     TEXT,
            quality_score   REAL,
            status          TEXT DEFAULT 'active'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS erp_rejections (
            rejection_id    TEXT PRIMARY KEY,
            crm_deal_id     TEXT,
            rejected_at     TEXT,
            reason_codes    TEXT,
            payload         TEXT
        )
    """)
    conn.commit()
    conn.close()


# ── ERP Validation Rules ─────────────────────────────────────────────────────

VALID_CURRENCIES = {"EUR", "USD", "GBP", "CHF", "JPY", "CAD", "AUD", "SEK", "DKK", "NOK"}
VALID_STAGES     = {"Prospecting", "Qualification", "Proposal", "Negotiation",
                    "Closed Won", "Closed Lost"}
EMAIL_RE         = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATE_RE          = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_record(data):
    """
    Applies ERP business rules to an incoming record.
    Returns (is_valid: bool, reason_codes: list[str])
    """
    reasons = []

    # Required fields
    for field in ["company_name", "contact_email", "deal_value", "currency", "close_date"]:
        if not data.get(field):
            reasons.append(f"MISSING_REQUIRED:{field}")

    # Email format
    email = data.get("contact_email", "")
    if email and not EMAIL_RE.match(email):
        reasons.append("INVALID_EMAIL_FORMAT")

    # Deal value
    val = data.get("deal_value")
    if val is not None:
        if val <= 0:
            reasons.append("NEGATIVE_OR_ZERO_DEAL_VALUE")
        if val > 10_000_000:
            reasons.append("DEAL_VALUE_EXCEEDS_ERP_LIMIT")

    # Currency
    currency = data.get("currency", "")
    if currency and currency not in VALID_CURRENCIES:
        reasons.append(f"INVALID_CURRENCY:{currency}")

    # Date format
    close_date = data.get("close_date", "")
    if close_date and not DATE_RE.match(close_date):
        reasons.append("INVALID_DATE_FORMAT:close_date")
    elif close_date and DATE_RE.match(close_date):
        try:
            cd = datetime.strptime(close_date, "%Y-%m-%d")
            if cd < datetime.now():
                reasons.append("CLOSE_DATE_IN_PAST")
            if (cd - datetime.now()).days > 730:
                reasons.append("CLOSE_DATE_TOO_FAR_FUTURE")
        except ValueError:
            reasons.append("UNPARSEABLE_DATE:close_date")

    # Deal stage
    stage = data.get("deal_stage", "")
    if stage and stage not in VALID_STAGES:
        reasons.append(f"INVALID_DEAL_STAGE:{stage}")

    is_valid = len(reasons) == 0
    return is_valid, reasons


# ── Health ───────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "ERP API", "timestamp": datetime.utcnow().isoformat()})


# ── Receive synced deal from n8n ─────────────────────────────────────────────

@app.route("/api/orders", methods=["POST"])
def receive_order():
    """
    Main endpoint called by n8n after ML scoring.
    Validates record, accepts or rejects, logs result.
    """
    data = request.get_json()
    if not data:
        abort(400, description="JSON body required")

    crm_deal_id  = data.get("deal_id")
    quality_score = data.get("quality_score", 0.0)

    is_valid, reasons = validate_record(data)

    conn = get_db()

    if is_valid:
        order_id = f"ERP-{str(uuid.uuid4())[:8].upper()}"
        conn.execute("""
            INSERT OR REPLACE INTO erp_orders
            (order_id, crm_deal_id, company_name, contact_email, industry,
             region, order_value, currency, deal_stage, account_type,
             close_date, received_at, quality_score)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            order_id,
            crm_deal_id,
            data.get("company_name"),
            data.get("contact_email"),
            data.get("industry"),
            data.get("region"),
            data.get("deal_value"),
            data.get("currency"),
            data.get("deal_stage"),
            data.get("account_type"),
            data.get("close_date"),
            datetime.utcnow().isoformat(),
            quality_score,
        ))

        # Write to sync log
        conn.execute("""
            INSERT INTO sync_log (log_id, deal_id, timestamp, quality_score, quality_label, action, erp_response, failure_reason)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            str(uuid.uuid4()), crm_deal_id, datetime.utcnow().isoformat(),
            quality_score, data.get("quality_label", "unknown"),
            "synced", json.dumps({"order_id": order_id}), None
        ))

        conn.commit()
        conn.close()

        return jsonify({
            "status":   "accepted",
            "order_id": order_id,
            "deal_id":  crm_deal_id,
            "message":  "Record successfully ingested into ERP"
        }), 201

    else:
        rejection_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO erp_rejections (rejection_id, crm_deal_id, rejected_at, reason_codes, payload)
            VALUES (?,?,?,?,?)
        """, (
            rejection_id, crm_deal_id,
            datetime.utcnow().isoformat(),
            json.dumps(reasons),
            json.dumps(data),
        ))

        # Write to sync log
        conn.execute("""
            INSERT INTO sync_log (log_id, deal_id, timestamp, quality_score, quality_label, action, erp_response, failure_reason)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            str(uuid.uuid4()), crm_deal_id, datetime.utcnow().isoformat(),
            quality_score, data.get("quality_label", "unknown"),
            "quarantined", None, json.dumps(reasons)
        ))

        conn.commit()
        conn.close()

        return jsonify({
            "status":        "rejected",
            "rejection_id":  rejection_id,
            "deal_id":       crm_deal_id,
            "reason_codes":  reasons,
            "message":       "Record failed ERP validation and has been quarantined"
        }), 422


# ── Query endpoints ───────────────────────────────────────────────────────────

@app.route("/api/orders", methods=["GET"])
def list_orders():
    limit = int(request.args.get("limit", 100))
    conn  = get_db()
    rows  = conn.execute(
        "SELECT * FROM erp_orders ORDER BY received_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return jsonify({"count": len(rows), "orders": [dict(r) for r in rows]})


@app.route("/api/rejections", methods=["GET"])
def list_rejections():
    limit = int(request.args.get("limit", 100))
    conn  = get_db()
    rows  = conn.execute(
        "SELECT * FROM erp_rejections ORDER BY rejected_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return jsonify({"count": len(rows), "rejections": [dict(r) for r in rows]})


@app.route("/api/stats", methods=["GET"])
def erp_stats():
    conn = get_db()

    accepted   = conn.execute("SELECT COUNT(*) FROM erp_orders").fetchone()[0]
    rejected   = conn.execute("SELECT COUNT(*) FROM erp_rejections").fetchone()[0]
    total_value = conn.execute("SELECT SUM(order_value) FROM erp_orders").fetchone()[0] or 0

    avg_score  = conn.execute(
        "SELECT AVG(quality_score) FROM erp_orders"
    ).fetchone()[0] or 0

    top_reasons = conn.execute("""
        SELECT reason_codes, COUNT(*) as cnt
        FROM erp_rejections
        GROUP BY reason_codes
        ORDER BY cnt DESC
        LIMIT 10
    """).fetchall()

    conn.close()

    return jsonify({
        "accepted_orders":   accepted,
        "rejected_records":  rejected,
        "acceptance_rate":   round(accepted / max(accepted + rejected, 1) * 100, 1),
        "total_erp_value":   round(total_value, 2),
        "avg_quality_score": round(avg_score, 2),
        "top_rejection_reasons": [dict(r) for r in top_reasons],
    })


if __name__ == "__main__":
    init_erp_tables()
    port = int(os.environ.get("ERP_PORT", 5002))
    print(f"[ERP API] Starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
