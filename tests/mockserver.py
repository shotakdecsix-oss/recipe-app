# -*- coding: utf-8 -*-
"""UIテスト用モックサーバー。AI応答を固定値で返す（ポート8900）。

    python3 tests/mockserver.py
"""
import base64
import os
from flask import Flask, jsonify, request, send_from_directory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__, static_folder=None)

RECIPES = [
    {"title_ja": "鶏の照り焼き", "reason": "手持ちで作れる",
     "ingredients_ja": ["鶏もも肉 300g", "醤油 大さじ2", "長ねぎ 1本"],
     "steps_ja": ["【下準備】鶏肉を一口大に切る", "【調理】中火で5分焼く",
                  "【仕上げ】タレを3分の1まで煮詰める"],
     "tips": ["コツ1", "コツ2"], "image_query": "teriyaki chicken",
     "suggested_additions": [{"name_ja": "バター", "reason": "コクが出る"}],
     "substitutions": [{"ingredient_name": "醤油", "alternative": "たまり醤油",
                        "can_omit": False, "omit_note": ""}],
     "pairing": {"drink": "日本酒", "reason": "相性良し"},
     "cook_time_min": 20, "difficulty": "簡単"},
    {"title_ja": "にんじんサラダ", "reason": "副菜に",
     "ingredients_ja": ["にんじん 1本", "オリーブオイル 大さじ1"],
     "steps_ja": ["【下準備】千切りにする", "【調理】10分冷やす"],
     "tips": ["コツ"], "image_query": "carrot salad",
     "suggested_additions": [], "substitutions": [],
     "pairing": {"drink": "白ワイン", "reason": "さっぱり"},
     "cook_time_min": 10, "difficulty": "簡単"},
]


@app.route("/")
def index():
    return send_from_directory(ROOT, "index.html")


@app.route("/<path:f>")
def static_files(f):
    if f in ("app.js", "style.css", "manifest.json", "sw.js", "icon.svg"):
        return send_from_directory(ROOT, f)
    return jsonify({}), 404


# ネットワークに出ずに画像を返せるよう、data URI のダミー写真を使う
_SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="160" height="90">'
        b'<rect width="160" height="90" fill="#8a5"/></svg>')
PHOTO_URL = "data:image/svg+xml;base64," + base64.b64encode(_SVG).decode()


@app.route("/api/photo")
def photo():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"photo": None, "reason": "no_query"})
    return jsonify({"photo": {
        "url": PHOTO_URL, "thumb": PHOTO_URL,
        "credit": "テスト撮影者", "credit_url": "https://example.com/photographer",
        "page": "https://example.com/photo",
    }})


@app.route("/api/version")
def version():
    return jsonify({"deployed_at": "2026-01-01 00:00 JST", "theory_loaded": True,
                    "photos_enabled": True,
                    "models": [{"id": "fast", "label": "はやい（標準）"},
                               {"id": "slow", "label": "じっくり（高品質）"}]})


@app.route("/api/recipe", methods=["POST"])
@app.route("/api/recipe/next", methods=["POST"])
def recipe():
    return jsonify({"job_id": "j1"})


@app.route("/api/job/<j>")
def job(j):
    return jsonify({"status": "done", "recipes": RECIPES,
                    "generated_at": "2026-01-01 00:00 JST", "candidate_count": 2})


@app.route("/api/chat", methods=["POST"])
def chat():
    return jsonify({"reply": "回答ベータです"})


@app.route("/api/rewrite", methods=["POST"])
@app.route("/api/rewrite-from-chat", methods=["POST"])
def rewrite():
    return jsonify({"updated": {"ingredients_ja": ["更新食材 1個"],
                                "steps_ja": ["更新手順1", "更新手順2で7分煮る"],
                                "tips": ["更新コツ"]}})


@app.route("/api/shopping-list", methods=["POST"])
def shopping_list():
    return jsonify({"list": {
        "categories": [{"name": "野菜", "items": [
            {"name": "長ねぎ", "amount": "1本", "used_in": ["鶏の照り焼き"]}]}],
        "have_at_home": [{"name": "醤油", "amount": "大さじ2"}],
        "notes": ["まとめ買い推奨"]}})


@app.route("/api/timeline", methods=["POST"])
def timeline():
    return jsonify({"timeline": {"total_min": 35, "steps": [
        {"at_min": 0, "recipe": "鶏の照り焼き", "text": "鶏肉を切る",
         "duration_min": 5, "is_wait": False},
        {"at_min": 5, "recipe": "にんじんサラダ", "text": "冷やす",
         "duration_min": 10, "is_wait": True}],
        "notes": ["コンロ2口必要"]}})


if __name__ == "__main__":
    app.run(port=8900, threaded=True)
