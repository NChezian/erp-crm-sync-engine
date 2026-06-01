"""
Synthetic CRM/ERP Data Generator
Generates realistic business records with controlled quality issues
for the ERP-CRM Data Integrity Engine demo.
"""

import json
import random
import uuid
import sqlite3
import os
from datetime import datetime, timedelta
from faker import Faker

fake = Faker()
random.seed(42)

# ── Quality issue profiles ───────────────────────────────────────────────────
# Each record gets a quality profile that determines what flaws it carries.
QUALITY_PROFILES = {
    "clean":          {"weight": 0.40, "issues": []},
    "missing_fields": {"weight": 0.20, "issues": ["missing_phone", "missing_industry", "missing_region"]},
    "format_errors":  {"weight": 0.15, "issues": ["bad_email", "bad_phone_format", "bad_date"]},
    "duplicates":     {"weight": 0.10, "issues": ["duplicate_company", "duplicate_email"]},
    "inconsistent":   {"weight": 0.10, "issues": ["negative_value", "future_close_date", "wrong_currency"]},
    "mixed":          {"weight": 0.05, "issues": ["missing_phone", "bad_email", "negative_value"]},
}

INDUSTRIES    = ["Technology", "Manufacturing", "Healthcare", "Finance", "Retail",
                 "Logistics", "Energy", "Education", "Consulting", "Automotive"]
REGIONS       = ["DACH", "Benelux", "Nordics", "UK", "Southern Europe",
                 "Eastern Europe", "North America", "APAC"]
CURRENCIES    = ["EUR", "USD", "GBP", "CHF"]
DEAL_STAGES   = ["Prospecting", "Qualification", "Proposal", "Negotiation", "Closed Won", "Closed Lost"]
ACCOUNT_TYPES = ["Enterprise", "Mid-Market", "SMB", "Partner", "Reseller"]


def pick_profile():
    profiles = list(QUALITY_PROFILES.keys())
    weights  = [QUALITY_PROFILES[p]["weight"] for p in profiles]
    return random.choices(profiles, weights=weights, k=1)[0]


def generate_deal(deal_id=None, seen_emails=None, seen_companies=None):
    profile_name = pick_profile()
    issues       = QUALITY_PROFILES[profile_name]["issues"].copy()

    seen_emails    = seen_emails    or set()
    seen_companies = seen_companies or set()

    # Base clean record
    company   = fake.company()
    email     = fake.company_email()
    phone     = fake.phone_number()
    industry  = random.choice(INDUSTRIES)
    region    = random.choice(REGIONS)
    currency  = random.choice(CURRENCIES)
    deal_value = round(random.uniform(5_000, 500_000), 2)
    close_date = (datetime.now() + timedelta(days=random.randint(7, 180))).strftime("%Y-%m-%d")
    created_at = (datetime.now() - timedelta(days=random.randint(1, 90))).isoformat()

    # ── Apply quality issues ─────────────────────────────────────────────────
    if "missing_phone" in issues:
        phone = None
    if "missing_industry" in issues:
        industry = None
    if "missing_region" in issues:
        region = None
    if "bad_email" in issues:
        email = email.replace("@", "").replace(".", "@@")   # malformed
    if "bad_phone_format" in issues:
        phone = "".join(random.choices("abcdefghij", k=10))  # letters instead of digits
    if "bad_date" in issues:
        close_date = "99/99/9999"
    if "duplicate_email" in issues and seen_emails:
        email = random.choice(list(seen_emails))
    if "duplicate_company" in issues and seen_companies:
        company = random.choice(list(seen_companies))
    if "negative_value" in issues:
        deal_value = round(random.uniform(-50_000, -100), 2)
    if "future_close_date" in issues:
        close_date = (datetime.now() + timedelta(days=random.randint(500, 1000))).strftime("%Y-%m-%d")
    if "wrong_currency" in issues:
        currency = "XYZ"   # invalid ISO currency

    seen_emails.add(email)
    seen_companies.add(company)

    return {
        "deal_id":        deal_id or str(uuid.uuid4()),
        "company_name":   company,
        "contact_email":  email,
        "contact_phone":  phone,
        "industry":       industry,
        "region":         region,
        "deal_value":     deal_value,
        "currency":       currency,
        "deal_stage":     random.choice(DEAL_STAGES),
        "account_type":   random.choice(ACCOUNT_TYPES),
        "close_date":     close_date,
        "created_at":     created_at,
        "crm_source":     "SalesForce-Mock",
        "quality_profile": profile_name,   # ground-truth label for ML training
    }


def generate_dataset(n=500):
    records        = []
    seen_emails    = set()
    seen_companies = set()

    for i in range(n):
        deal = generate_deal(
            deal_id=f"CRM-{str(i+1).zfill(5)}",
            seen_emails=seen_emails,
            seen_companies=seen_companies,
        )
        records.append(deal)

    return records


def save_to_json(records, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(records, f, indent=2)
    print(f"[✓] Saved {len(records)} records → {path}")


def save_to_sqlite(records, db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()

    cur.execute("DROP TABLE IF EXISTS crm_deals")
    cur.execute("""
        CREATE TABLE crm_deals (
            deal_id        TEXT PRIMARY KEY,
            company_name   TEXT,
            contact_email  TEXT,
            contact_phone  TEXT,
            industry       TEXT,
            region         TEXT,
            deal_value     REAL,
            currency       TEXT,
            deal_stage     TEXT,
            account_type   TEXT,
            close_date     TEXT,
            created_at     TEXT,
            crm_source     TEXT,
            quality_profile TEXT,
            synced         INTEGER DEFAULT 0,
            sync_timestamp TEXT
        )
    """)

    for r in records:
        cur.execute("""
            INSERT OR REPLACE INTO crm_deals VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,NULL)
        """, (
            r["deal_id"], r["company_name"], r["contact_email"],
            r["contact_phone"], r["industry"], r["region"],
            r["deal_value"], r["currency"], r["deal_stage"],
            r["account_type"], r["close_date"], r["created_at"],
            r["crm_source"], r["quality_profile"],
        ))

    # Sync log table — used by n8n and dashboard
    cur.execute("DROP TABLE IF EXISTS sync_log")
    cur.execute("""
        CREATE TABLE sync_log (
            log_id          TEXT PRIMARY KEY,
            deal_id         TEXT,
            timestamp       TEXT,
            quality_score   REAL,
            quality_label   TEXT,
            action          TEXT,   -- 'synced' | 'quarantined' | 'flagged'
            erp_response    TEXT,
            failure_reason  TEXT
        )
    """)

    conn.commit()
    conn.close()
    print(f"[✓] SQLite DB initialised → {db_path}")


if __name__ == "__main__":
    print("Generating synthetic CRM dataset...")
    records = generate_dataset(500)

    save_to_json(records,   "../data/crm_records.json")
    save_to_sqlite(records, "../data/sync_engine.db")

    # Quick quality distribution summary
    from collections import Counter
    dist = Counter(r["quality_profile"] for r in records)
    print("\nQuality profile distribution:")
    for profile, count in sorted(dist.items(), key=lambda x: -x[1]):
        print(f"  {profile:<20} {count:>4} records  ({count/len(records)*100:.1f}%)")
