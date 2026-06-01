"""
Mock CRM API
Simulates a Salesforce-style CRM system.
Endpoints consumed by n8n for polling new/updated deals.
"""

import sqlite3
import json
import uuid
import os
from datetime import datetime
from flask import Flask, jsonify, request, abort

app = Flask(__name__)

DB_PATH = os.environ.get("DB_PATH", "../data/sync_engine.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Health ───────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "CRM API", "timestamp": datetime.utcnow().isoformat()})


# ── Deals ────────────────────────────────────────────────────────────────────

@app.route("/api/deals", methods=["GET"])
def list_deals():
    """
    Returns deals. Supports filtering:
      ?synced=0       → only unsynced records (used by n8n poller)
      ?limit=50       → max records returned
      ?stage=Closed+Won
    """
    synced = request.args.get("synced")
    limit  = int(request.args.get("limit", 100))
    stage  = request.args.get("stage")

    conn  = get_db()
    query = "SELECT * FROM crm_deals WHERE 1=1"
    params = []

    if synced is not None:
        query += " AND synced = ?"
        params.append(int(synced))
    if stage:
        query += " AND deal_stage = ?"
        params.append(stage)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows  = conn.execute(query, params).fetchall()
    conn.close()

    deals = [dict(row) for row in rows]
    return jsonify({
        "count":  len(deals),
        "deals":  deals,
        "pulled_at": datetime.utcnow().isoformat()
    })


@app.route("/api/deals/<deal_id>", methods=["GET"])
def get_deal(deal_id):
    conn = get_db()
    row  = conn.execute("SELECT * FROM crm_deals WHERE deal_id = ?", (deal_id,)).fetchone()
    conn.close()
    if not row:
        abort(404, description=f"Deal {deal_id} not found")
    return jsonify(dict(row))


@app.route("/api/deals/<deal_id>/mark-synced", methods=["PATCH"])
def mark_synced(deal_id):
    """Called by n8n after successful ERP push."""
    conn = get_db()
    conn.execute(
        "UPDATE crm_deals SET synced = 1, sync_timestamp = ? WHERE deal_id = ?",
        (datetime.utcnow().isoformat(), deal_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"deal_id": deal_id, "synced": True})


@app.route("/api/deals/stats", methods=["GET"])
def deal_stats():
    """Summary stats for dashboard."""
    conn  = get_db()
    total   = conn.execute("SELECT COUNT(*) FROM crm_deals").fetchone()[0]
    synced  = conn.execute("SELECT COUNT(*) FROM crm_deals WHERE synced=1").fetchone()[0]
    pending = total - synced

    by_profile = conn.execute(
        "SELECT quality_profile, COUNT(*) as cnt FROM crm_deals GROUP BY quality_profile"
    ).fetchall()

    by_stage = conn.execute(
        "SELECT deal_stage, COUNT(*) as cnt, SUM(deal_value) as total_value "
        "FROM crm_deals GROUP BY deal_stage"
    ).fetchall()

    conn.close()

    return jsonify({
        "total_deals":   total,
        "synced":        synced,
        "pending_sync":  pending,
        "by_quality_profile": [dict(r) for r in by_profile],
        "by_stage":           [dict(r) for r in by_stage],
    })


# ── Simulate new incoming deal (for demo / testing) ──────────────────────────

@app.route("/api/deals", methods=["POST"])
def create_deal():
    data = request.get_json()
    if not data:
        abort(400, description="JSON body required")

    deal_id = data.get("deal_id") or f"CRM-{str(uuid.uuid4())[:8].upper()}"
    conn    = get_db()

    try:
        conn.execute("""
            INSERT INTO crm_deals
            (deal_id, company_name, contact_email, contact_phone, industry,
             region, deal_value, currency, deal_stage, account_type,
             close_date, created_at, crm_source, quality_profile)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            deal_id,
            data.get("company_name"),
            data.get("contact_email"),
            data.get("contact_phone"),
            data.get("industry"),
            data.get("region"),
            data.get("deal_value"),
            data.get("currency", "EUR"),
            data.get("deal_stage", "Prospecting"),
            data.get("account_type", "SMB"),
            data.get("close_date"),
            datetime.utcnow().isoformat(),
            "CRM-API",
            "unknown",
        ))
        conn.commit()
    except sqlite3.IntegrityError:
        abort(409, description=f"Deal {deal_id} already exists")
    finally:
        conn.close()

    return jsonify({"deal_id": deal_id, "status": "created"}), 201


if __name__ == "__main__":
    port = int(os.environ.get("CRM_PORT", 5001))
    print(f"[CRM API] Starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
