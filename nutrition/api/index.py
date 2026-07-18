"""
Single-file Vercel serverless handler for the Food Macro Analyzer.
Also runnable directly for local development (`python index.py`).
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
from pathlib import Path
from typing import Any

import anthropic
import requests
from flask import Flask, Response, jsonify, request
from flask_cors import CORS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
app = Flask(__name__, static_folder=str(STATIC_DIR))
CORS(app)

CLAUDE_MODEL = "claude-opus-4-8"
USDA_API_KEY = os.environ.get("USDA_FDC_API_KEY", "DEMO_KEY")
USDA_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"

MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

_anthropic_client: anthropic.Anthropic | None = None


def _client() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic()
    return _anthropic_client


# ── Claude vision analysis ───────────────────────────────────────

MEAL_SCHEMA = {
    "type": "object",
    "properties": {
        "meal_description": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "usda_search_term": {
                        "type": "string",
                        "description": "Short generic term to look up this food in a nutrition database, e.g. 'grilled chicken breast'",
                    },
                    "estimated_grams": {"type": "number"},
                    "estimated_calories": {"type": "number"},
                    "estimated_protein_g": {"type": "number"},
                    "estimated_carbs_g": {"type": "number"},
                    "estimated_fat_g": {"type": "number"},
                },
                "required": [
                    "name",
                    "usda_search_term",
                    "estimated_grams",
                    "estimated_calories",
                    "estimated_protein_g",
                    "estimated_carbs_g",
                    "estimated_fat_g",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["meal_description", "confidence", "items"],
    "additionalProperties": False,
}

ANALYZE_SYSTEM_PROMPT = """You are a nutrition coach's vision assistant. Given a photo of a meal, identify each \
distinct food item, estimate its portion size in grams using visual cues (plate size, utensils, known food \
density), and provide your own best-estimate macros for that portion as a fallback. Be a careful, realistic \
estimator — like an experienced dietitian eyeballing a plate. If the photo contains sauces, oils, or cooking \
fat that materially affect calories, list them as separate items. Keep item names short and specific."""


def _analyze_image(image_b64: str, media_type: str, note: str) -> dict:
    user_text = "Identify the foods in this photo and estimate portions and macros."
    if note:
        user_text += f" Additional context from the user: {note}"

    response = _client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=ANALYZE_SYSTEM_PROMPT,
        output_config={
            "format": {"type": "json_schema", "schema": MEAL_SCHEMA},
            "effort": "high",
        },
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": user_text},
                ],
            }
        ],
    )

    if response.stop_reason == "refusal":
        raise ValueError("The model declined to analyze this image.")

    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


# ── USDA FoodData Central lookup ─────────────────────────────────

_USDA_NUTRIENT_IDS = {
    "calories": 1008,   # Energy (kcal)
    "protein_g": 1003,  # Protein
    "carbs_g": 1005,    # Carbohydrate, by difference
    "fat_g": 1004,      # Total lipid (fat)
}


def _usda_lookup(query: str, grams: float) -> dict | None:
    try:
        resp = requests.get(
            USDA_SEARCH_URL,
            params={
                "query": query,
                "api_key": USDA_API_KEY,
                "pageSize": 1,
                "dataType": "Foundation,SR Legacy",
            },
            timeout=6,
        )
        resp.raise_for_status()
        foods = resp.json().get("foods") or []
        if not foods:
            return None
        nutrients = {n.get("nutrientId"): n.get("value") for n in foods[0].get("foodNutrients", [])}
        per_100g = {}
        for key, nid in _USDA_NUTRIENT_IDS.items():
            val = nutrients.get(nid)
            if val is None:
                return None
            per_100g[key] = val
        scale = grams / 100.0
        return {
            "calories": round(per_100g["calories"] * scale, 1),
            "protein_g": round(per_100g["protein_g"] * scale, 1),
            "carbs_g": round(per_100g["carbs_g"] * scale, 1),
            "fat_g": round(per_100g["fat_g"] * scale, 1),
            "matched_food": foods[0].get("description"),
        }
    except (requests.RequestException, ValueError, KeyError) as exc:
        logger.warning("USDA lookup failed for %r: %s", query, exc)
        return None


def _build_meal_result(claude_result: dict) -> dict:
    items_out = []
    totals = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}

    for item in claude_result.get("items", []):
        grams = float(item.get("estimated_grams") or 0)
        usda = _usda_lookup(item.get("usda_search_term", item.get("name", "")), grams) if grams > 0 else None

        if usda:
            macros = {k: usda[k] for k in ("calories", "protein_g", "carbs_g", "fat_g")}
            source = "usda"
            matched_food = usda["matched_food"]
        else:
            macros = {
                "calories": round(float(item.get("estimated_calories") or 0), 1),
                "protein_g": round(float(item.get("estimated_protein_g") or 0), 1),
                "carbs_g": round(float(item.get("estimated_carbs_g") or 0), 1),
                "fat_g": round(float(item.get("estimated_fat_g") or 0), 1),
            }
            source = "ai_estimate"
            matched_food = None

        for key in totals:
            totals[key] += macros[key]

        items_out.append({
            "name": item.get("name"),
            "estimated_grams": grams,
            "source": source,
            "matched_food": matched_food,
            **macros,
        })

    for key in totals:
        totals[key] = round(totals[key], 1)

    return {
        "meal_description": claude_result.get("meal_description", ""),
        "confidence": claude_result.get("confidence", "medium"),
        "items": items_out,
        "totals": totals,
    }


# ── Coaching tip ──────────────────────────────────────────────────

COACH_SYSTEM_PROMPT = """You are an encouraging, knowledgeable nutrition coach helping a client hit a weight or \
muscle-gain goal through calorie and macro tracking. Given their daily targets, what they've logged so far today, \
and recent meal history, give one short, specific, actionable piece of coaching (2-4 sentences). Be concrete \
(reference actual numbers), supportive, and never preachy or repetitive. No emoji, no generic platitudes."""


def _coach_tip(payload: dict) -> str:
    prompt = (
        f"Goal: {payload.get('goal', 'not set')}\n"
        f"Daily targets: {json.dumps(payload.get('targets', {}))}\n"
        f"Logged so far today: {json.dumps(payload.get('today_totals', {}))}\n"
        f"Recent meals (last few days): {json.dumps(payload.get('recent_meals', []))}\n\n"
        "Give me a short coaching note for right now."
    )
    response = _client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        system=COACH_SYSTEM_PROMPT,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        return "Keep logging your meals consistently — that's the habit that moves the needle most."
    return next(b.text for b in response.content if b.type == "text").strip()


# ── Routes ──────────────────────────────────────────────────────

@app.route("/")
@app.route("/nutrition")
@app.route("/nutrition/")
def index():
    p = STATIC_DIR / "index.html"
    if p.exists():
        return Response(p.read_text(), mimetype="text/html")
    return Response("<h1>Food Macro Analyzer loading…</h1>", mimetype="text/html")


@app.route("/api/analyze-meal", methods=["POST"])
def analyze_meal():
    data = request.get_json(silent=True) or {}
    image_b64 = data.get("image_base64", "")
    media_type = data.get("media_type", "image/jpeg")
    note = (data.get("note") or "").strip()[:500]

    if not image_b64:
        return jsonify({"error": "image_base64 is required"}), 400
    if media_type not in ALLOWED_MEDIA_TYPES:
        return jsonify({"error": f"unsupported media_type: {media_type}"}), 400
    try:
        decoded_len = len(base64.b64decode(image_b64, validate=True))
    except (binascii.Error, ValueError):
        return jsonify({"error": "image_base64 is not valid base64"}), 400
    if decoded_len > MAX_IMAGE_BYTES:
        return jsonify({"error": "image exceeds 5MB limit"}), 400

    try:
        claude_result = _analyze_image(image_b64, media_type, note)
        result = _build_meal_result(claude_result)
        return jsonify(result)
    except anthropic.APIStatusError as exc:
        logger.error("Anthropic API error: %s", exc)
        return jsonify({"error": "The vision model is temporarily unavailable. Try again shortly."}), 502
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422


@app.route("/api/coach-tip", methods=["POST"])
def coach_tip():
    data = request.get_json(silent=True) or {}
    try:
        tip = _coach_tip(data)
        return jsonify({"tip": tip})
    except anthropic.APIStatusError as exc:
        logger.error("Anthropic API error: %s", exc)
        return jsonify({"error": "The coach is temporarily unavailable. Try again shortly."}), 502


@app.route("/api/status")
def status():
    return jsonify({
        "message": "Food Macro Analyzer API is running.",
        "usda_configured": USDA_API_KEY != "DEMO_KEY",
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
