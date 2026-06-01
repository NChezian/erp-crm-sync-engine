"""
ML Scorer API
Lightweight Flask service that wraps the trained quality scorer model.
Called by n8n during the sync workflow for each CRM record.
"""

import os
import sys
import json
from flask import Flask, jsonify, request, abort
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from train_scorer import score_record, load_scorer, engineer_features

app = Flask(__name__)

# Load model once at startup
_pipeline     = None
_feature_names = None


def get_model():
    global _pipeline, _feature_names
    if _pipeline is None:
        _pipeline, _feature_names = load_scorer()
    return _pipeline, _feature_names


@app.route("/health", methods=["GET"])
def health():
    try:
        get_model()
        model_status = "loaded"
    except FileNotFoundError:
        model_status = "not_trained"
    return jsonify({
        "status": "ok",
        "service": "ML Scorer API",
        "model": model_status,
        "timestamp": datetime.utcnow().isoformat()
    })


@app.route("/score", methods=["POST"])
def score():
    """
    Score a single CRM record.
    Body: any CRM deal dict
    Returns: { quality_score, quality_label, features, routed_to }
    """
    data = request.get_json()
    if not data:
        abort(400, description="JSON body required")

    pipeline, feature_names = get_model()
    result = score_record(data, pipeline, feature_names)

    # Routing decision
    score_val = result["quality_score"]
    if score_val >= 75:
        routed_to = "erp"
    elif score_val >= 50:
        routed_to = "review_queue"
    else:
        routed_to = "quarantine"

    return jsonify({
        "deal_id":       data.get("deal_id"),
        "quality_score": result["quality_score"],
        "quality_label": result["quality_label"],
        "routed_to":     routed_to,
        "features":      result["features"],
        "scored_at":     datetime.utcnow().isoformat(),
    })


@app.route("/score/batch", methods=["POST"])
def score_batch():
    """
    Score a batch of records at once.
    Body: { records: [...] }
    """
    data = request.get_json()
    if not data or "records" not in data:
        abort(400, description="Expected { records: [...] }")

    pipeline, feature_names = get_model()
    results = []
    for record in data["records"]:
        result = score_record(record, pipeline, feature_names)
        score_val = result["quality_score"]
        routed_to = "erp" if score_val >= 75 else ("review_queue" if score_val >= 50 else "quarantine")
        results.append({
            "deal_id":       record.get("deal_id"),
            "quality_score": result["quality_score"],
            "quality_label": result["quality_label"],
            "routed_to":     routed_to,
        })

    return jsonify({
        "count":   len(results),
        "results": results,
        "scored_at": datetime.utcnow().isoformat(),
    })


if __name__ == "__main__":
    port = int(os.environ.get("SCORER_PORT", 5003))
    print(f"[ML Scorer API] Starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
