"""
Sync Bridge (n8n Simulator)
----------------------------
Runs the full CRM → Score → Route → ERP pipeline locally.
Use this to:
  1. Test the pipeline without Docker/n8n
  2. Pre-populate the sync_log for dashboard demo

In production this logic lives inside the n8n workflow (sync_workflow.json).
"""

import importlib.util
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────

DB_PATH       = os.environ.get("DB_PATH", "data/sync_engine.db")
BATCH_SIZE    = int(os.environ.get("BATCH_SIZE", 50))
SLEEP_BETWEEN = float(os.environ.get("SLEEP_BETWEEN", 0.0))  # seconds between records


# ── Lazy-load modules to avoid import collisions ──────────────────────────────

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Core pipeline ─────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def run_sync(batch_size=BATCH_SIZE, verbose=True):
    scorer_mod = load_module("train_scorer", "ml_scorer/train_scorer.py")
    pipeline, feature_names = scorer_mod.load_scorer()

    erp_mod = load_module("erp_app", "erp_api/app.py")
    erp_mod.DB_PATH = DB_PATH
    erp_mod.init_erp_tables()

    crm_mod = load_module("crm_app", "crm_api/app.py")
    crm_mod.DB_PATH = DB_PATH

    # Pull unsynced records — close connection immediately after fetch
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM crm_deals WHERE synced = 0 LIMIT ?", (batch_size,)
    ).fetchall()
    conn.close()

    if not rows:
        print("No unsynced records found.")
        conn.close()
        return

    if verbose:
        print(f"\n{'='*60}")
        print(f"  Sync run started: {datetime.utcnow().isoformat()}")
        print(f"  Records to process: {len(rows)}")
        print(f"{'='*60}\n")

    stats = {"synced": 0, "quarantined": 0, "flagged": 0, "errors": 0}

    for row in rows:
        record = dict(row)
        deal_id = record["deal_id"]

        try:
            # Step 1: Score the record
            result = scorer_mod.score_record(record, pipeline, feature_names)
            score  = result["quality_score"]
            label  = result["quality_label"]

            # Attach score to record for ERP
            record["quality_score"] = score
            record["quality_label"] = label

            # Step 2: Route based on score
            if score >= 75:
                # → Send to ERP (ERP app opens its own connection)
                with erp_mod.app.test_client() as c:
                    resp = c.post("/api/orders", json=record)
                    body = resp.get_json()

                if resp.status_code == 201:
                    action = "synced"
                    erp_response = json.dumps({"order_id": body.get("order_id")})
                    failure_reason = None
                    stats["synced"] += 1
                    status_icon = "✓"
                    # Mark as synced in CRM — fresh connection
                    wconn = get_conn()
                    wconn.execute(
                        "UPDATE crm_deals SET synced=1, sync_timestamp=? WHERE deal_id=?",
                        (datetime.utcnow().isoformat(), deal_id)
                    )
                    wconn.commit()
                    wconn.close()
                else:
                    action = "quarantined"
                    erp_response = None
                    failure_reason = json.dumps(body.get("reason_codes", []))
                    stats["quarantined"] += 1
                    status_icon = "✗"

            elif score >= 50:
                action = "flagged"
                erp_response = None
                failure_reason = json.dumps(["SCORE_BELOW_AUTO_SYNC_THRESHOLD"])
                stats["flagged"] += 1
                status_icon = "⚠"

            else:
                action = "quarantined"
                erp_response = None
                failure_reason = json.dumps(["LOW_QUALITY_SCORE"])
                stats["quarantined"] += 1
                status_icon = "✗"

            # Write to sync log — fresh connection per write
            lconn = get_conn()
            lconn.execute("""
                INSERT INTO sync_log
                (log_id, deal_id, timestamp, quality_score, quality_label, action, erp_response, failure_reason)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                str(uuid.uuid4()), deal_id,
                datetime.utcnow().isoformat(),
                score, label, action, erp_response, failure_reason
            ))
            lconn.commit()
            lconn.close()

            if verbose:
                print(f"  [{status_icon}] {deal_id:<14} score={score:5.1f}  label={label:<6}  action={action}")

        except Exception as e:
            stats["errors"] += 1
            if verbose:
                print(f"  [!] {deal_id} — ERROR: {e}")

        if SLEEP_BETWEEN > 0:
            time.sleep(SLEEP_BETWEEN)

    if verbose:
        total = sum(stats.values())
        print(f"\n{'='*60}")
        print(f"  Run complete")
        print(f"  Synced     : {stats['synced']:>4}  ({stats['synced']/total*100:.1f}%)")
        print(f"  Flagged    : {stats['flagged']:>4}  ({stats['flagged']/total*100:.1f}%)")
        print(f"  Quarantined: {stats['quarantined']:>4}  ({stats['quarantined']/total*100:.1f}%)")
        print(f"  Errors     : {stats['errors']:>4}")
        print(f"{'='*60}\n")

    return stats


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run CRM→ERP sync pipeline")
    parser.add_argument("--batch", type=int, default=BATCH_SIZE, help="Records per run")
    parser.add_argument("--all",   action="store_true",           help="Process all unsynced records")
    args = parser.parse_args()

    batch = 9999 if args.all else args.batch
    run_sync(batch_size=batch)
