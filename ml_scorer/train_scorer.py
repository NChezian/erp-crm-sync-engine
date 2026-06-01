"""
ML Data Quality Scorer
Trains a regression model (XGBoost) to score CRM records 0–100
based on engineered features around completeness, format validity,
and cross-field consistency.

Outputs:
  - models/quality_scorer.pkl   (trained pipeline)
  - models/feature_names.json
  - models/score_thresholds.json
"""

import json
import os
import re
import sqlite3
import pickle
import numpy as np
import pandas as pd
from datetime import datetime

from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import mean_absolute_error, r2_score

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH     = os.environ.get("DB_PATH", "../data/sync_engine.db")
MODEL_DIR   = os.path.join(os.path.dirname(__file__), "models")
EMAIL_RE    = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE    = re.compile(r"^[\d\s\+\-\(\)\.x]+$")
DATE_RE     = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VALID_CURR  = {"EUR", "USD", "GBP", "CHF", "JPY", "CAD", "AUD", "SEK", "DKK", "NOK"}
VALID_STAGE = {"Prospecting", "Qualification", "Proposal",
               "Negotiation", "Closed Won", "Closed Lost"}

# Ground-truth score mapping by quality profile
# These form the regression targets — we add gaussian noise to make it realistic
PROFILE_BASE_SCORES = {
    "clean":          92.0,
    "missing_fields": 58.0,
    "format_errors":  48.0,
    "duplicates":     55.0,
    "inconsistent":   40.0,
    "mixed":          30.0,
}


# ── Feature Engineering ───────────────────────────────────────────────────────

def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Derives numerical quality signal features from raw record fields.
    All features are bounded [0, 1] except deal_value_log.
    """
    feats = pd.DataFrame(index=df.index)

    # 1. Field presence (completeness)
    feats["has_phone"]    = df["contact_phone"].notna().astype(float)
    feats["has_industry"] = df["industry"].notna().astype(float)
    feats["has_region"]   = df["region"].notna().astype(float)
    feats["has_email"]    = df["contact_email"].notna().astype(float)
    feats["has_company"]  = df["company_name"].notna().astype(float)
    feats["completeness_ratio"] = feats[
        ["has_phone", "has_industry", "has_region", "has_email", "has_company"]
    ].mean(axis=1)

    # 2. Email format validity
    feats["email_valid"] = df["contact_email"].apply(
        lambda x: 1.0 if (x and EMAIL_RE.match(str(x))) else 0.0
    )

    # 3. Phone format validity
    feats["phone_valid"] = df["contact_phone"].apply(
        lambda x: 1.0 if (x and PHONE_RE.match(str(x))) else (0.5 if not x else 0.0)
    )

    # 4. Currency validity
    feats["currency_valid"] = df["currency"].apply(
        lambda x: 1.0 if str(x) in VALID_CURR else 0.0
    )

    # 5. Deal stage validity
    feats["stage_valid"] = df["deal_stage"].apply(
        lambda x: 1.0 if str(x) in VALID_STAGE else 0.0
    )

    # 6. Deal value sanity
    feats["value_positive"]  = (df["deal_value"] > 0).astype(float)
    feats["value_in_range"]  = ((df["deal_value"] > 0) & (df["deal_value"] <= 10_000_000)).astype(float)
    feats["deal_value_log"]  = df["deal_value"].apply(
        lambda x: np.log1p(max(x, 0))
    )

    # 7. Close date validity
    def date_score(d):
        if not d or not DATE_RE.match(str(d)):
            return 0.0
        try:
            dt = datetime.strptime(str(d), "%Y-%m-%d")
            days_out = (dt - datetime.now()).days
            if days_out < -30:    return 0.3   # past (bad)
            if days_out > 730:    return 0.4   # too far future
            return 1.0
        except ValueError:
            return 0.0

    feats["date_valid"] = df["close_date"].apply(date_score)

    # 8. Email domain quality (basic heuristic — business domains score higher)
    def email_domain_score(email):
        if not email or not EMAIL_RE.match(str(email)):
            return 0.0
        domain = str(email).split("@")[-1]
        if domain.endswith((".de", ".com", ".io", ".co.uk", ".eu")):
            return 1.0
        return 0.7

    feats["email_domain_quality"] = df["contact_email"].apply(email_domain_score)

    # 9. Company name length heuristic (very short = suspicious)
    feats["company_name_len"] = df["company_name"].apply(
        lambda x: min(len(str(x)) / 30.0, 1.0) if x else 0.0
    )

    return feats


def build_targets(df: pd.DataFrame) -> np.ndarray:
    """
    Generates regression targets (scores 0–100) from ground-truth quality profiles.
    Adds gaussian noise so the model can't just memorise labels.
    """
    np.random.seed(42)
    base_scores = df["quality_profile"].map(PROFILE_BASE_SCORES).fillna(50.0)
    noise       = np.random.normal(0, 4.0, size=len(base_scores))
    scores      = np.clip(base_scores + noise, 0, 100)
    return scores.values


# ── Training ──────────────────────────────────────────────────────────────────

def load_data():
    conn = sqlite3.connect(DB_PATH)
    df   = pd.read_sql("SELECT * FROM crm_deals", conn)
    conn.close()
    print(f"[✓] Loaded {len(df)} records from DB")
    return df


def train():
    os.makedirs(MODEL_DIR, exist_ok=True)

    df      = load_data()
    X       = engineer_features(df)
    y       = build_targets(df)

    feature_names = X.columns.tolist()

    X_train, X_test, y_train, y_test = train_test_split(
        X.values, y, test_size=0.2, random_state=42
    )

    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("model",  GradientBoostingRegressor(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            random_state=42,
        )),
    ])

    print("Training GradientBoostingRegressor...")
    pipeline.fit(X_train, y_train)

    # ── Evaluation ───────────────────────────────────────────────────────────
    y_pred = pipeline.predict(X_test)
    mae    = mean_absolute_error(y_test, y_pred)
    r2     = r2_score(y_test, y_pred)

    cv_scores = cross_val_score(pipeline, X.values, y, cv=5, scoring="r2")

    print(f"\n[Results]")
    print(f"  MAE (test)      : {mae:.2f} score points")
    print(f"  R²  (test)      : {r2:.4f}")
    print(f"  R²  (5-fold CV) : {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")

    # ── Feature importances ───────────────────────────────────────────────────
    importances = pipeline.named_steps["model"].feature_importances_
    fi_pairs    = sorted(zip(feature_names, importances), key=lambda x: -x[1])
    print(f"\n[Feature importances]")
    for fname, imp in fi_pairs[:8]:
        bar = "█" * int(imp * 100)
        print(f"  {fname:<30} {imp:.4f}  {bar}")

    # ── Save artefacts ────────────────────────────────────────────────────────
    model_path = os.path.join(MODEL_DIR, "quality_scorer.pkl")
    with open(model_path, "wb") as f:
        pickle.dump(pipeline, f)
    print(f"\n[✓] Model saved → {model_path}")

    with open(os.path.join(MODEL_DIR, "feature_names.json"), "w") as f:
        json.dump(feature_names, f)

    thresholds = {"high": 75.0, "medium": 50.0, "low": 0.0}
    with open(os.path.join(MODEL_DIR, "score_thresholds.json"), "w") as f:
        json.dump(thresholds, f)

    print(f"[✓] Feature names & thresholds saved")
    return pipeline, feature_names


# ── Inference helper (used by n8n bridge + dashboard) ────────────────────────

def load_scorer():
    model_path = os.path.join(MODEL_DIR, "quality_scorer.pkl")
    feat_path  = os.path.join(MODEL_DIR, "feature_names.json")
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model not found at {model_path}. Run train_scorer.py first.")
    with open(model_path, "rb") as f:
        pipeline = pickle.load(f)
    with open(feat_path) as f:
        feature_names = json.load(f)
    return pipeline, feature_names


def score_record(record: dict, pipeline=None, feature_names=None) -> dict:
    """
    Scores a single CRM record dict.
    Returns: {score, label, features}
    """
    if pipeline is None:
        pipeline, feature_names = load_scorer()

    df   = pd.DataFrame([record])
    X    = engineer_features(df)

    # Align columns to training feature order
    for col in feature_names:
        if col not in X.columns:
            X[col] = 0.0
    X = X[feature_names]

    score = float(np.clip(pipeline.predict(X.values)[0], 0, 100))

    if score >= 75:
        label = "high"
    elif score >= 50:
        label = "medium"
    else:
        label = "low"

    return {
        "quality_score": round(score, 2),
        "quality_label": label,
        "features":      X.iloc[0].to_dict(),
    }


if __name__ == "__main__":
    train()
