"""
Recipe App - Flask Backend
Spoonacular API + Claude AI for recipe suggestions and alcohol pairings
Config: recipe_config.json (DO NOT write API keys in chat or code comments)
"""

import json
import os
import re
import sys
import requests
import anthropic
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "recipe_config.json")

# Load config file if it exists (local dev), else fall back to env vars (Render)
CFG = {}
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        CFG = json.load(f)

SPOONACULAR_KEY = os.environ.get("SPOONACULAR_API_KEY") or CFG.get("spoonacular_api_key", "")
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY")   or CFG.get("anthropic_api_key", "")
MODEL           = os.environ.get("MODEL")                or CFG.get("model", "claude-haiku-4-5")
PORT            = int(os.environ.get("PORT", CFG.get("port", 5050)))
MAX_RECIPES     = int(os.environ.get("MAX_RECIPES", CFG.get("max_recipes_from_spoonacular", 8)))

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

app = Flask(__name__, static_folder=BASE_DIR)

# ---------------------------------------------------------------------------
# Spoonacular helpers
# ---------------------------------------------------------------------------
SPOON_BASE = "https://api.spoonacular.com"

def spoon_find_by_ingredients(ingredients: list[str], number: int = MAX_RECIPES) -> list[dict]:
    """Search recipes by ingredient list."""
    url = f"{SPOON_BASE}/recipes/findByIngredients"
    params = {
        "apiKey": SPOONACULAR_KEY,
        "ingredients": ",".join(ingredients),
        "number": number,
        "ranking": 1,      # maximize used ingredients
        "ignorePantry": True,
    }
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()  # list of recipe summaries


def spoon_get_recipe_info(recipe_id: int) -> dict:
    """Get full recipe details (ingredients + instructions)."""
    url = f"{SPOON_BASE}/recipes/{recipe_id}/information"
    params = {"apiKey": SPOONACULAR_KEY, "includeNutrition": False}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def build_spoonacular_url(title: str, recipe_id: int) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    return f"https://spoonacular.com/recipes/{slug}-{recipe_id}"


def build_recipe_summaries(candidates: list[dict]) -> tuple[str, dict]:
    """Format candidate list for Claude prompt.
    Returns (text, id_map) where id_map = {title: recipe_id}."""
    lines = []
    id_map = {}
    for i, r in enumerate(candidates[:MAX_RECIPES], 1):
        used = [ing["name"] for ing in r.get("usedIngredients", [])]
        missed = [ing["name"] for ing in r.get("missedIngredients", [])]
        id_map[r["title"]] = r["id"]
        lines.append(
            f"{i}. {r['title']} (使用食材: {', '.join(used)}; "
            f"不足食材: {', '.join(missed) or 'なし'}; "
            f"id={r['id']})"
        )
    return "\n".join(lines), id_map


# ---------------------------------------------------------------------------
# Claude helpers
# ---------------------------------------------------------------------------
def translate_ingredients_to_english(ingredients: list[str]) -> list[str]:
    """Translate Japanese ingredient names to English for Spoonacular."""
    joined = "、".join(ingredients)
    message = anthropic_client.messages.create(
        model=MODEL,
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": (
                f"次の食材名を英語に翻訳してください。出力はカンマ区切りの英単語のみ。余計な説明不要。\n{joined}"
            ),
        }],
    )
    raw = message.content[0].text.strip()
    translated = [w.strip() for w in raw.split(",") if w.strip()]
    return translated if translated else ingredients


def ask_claude(ingredients: list[str], mood: str, servings: int,
               max_time: str, candidates_text: str, drink: str = "") -> dict:
    """Call Claude to pick best recipes and add pairing info."""
    drink_line = f"- 手元にあるお酒: {drink}（このお酒に合う料理を最優先で選ぶこと）" if drink else ""
    drink_pairing_note = (
        f"- pairingのdrinkは必ず「{drink}」を記載し、なぜそのお酒がこの料理に合うかを具体的に説明すること"
        if drink else
        "- pairingには合うお酒とその理由を記載する"
    )

    prompt = f"""あなたは料理とお酒のプロです。以下の情報をもとに、最適なレシピを日本語で詳しく提案してください。

## ユーザー情報
- 冷蔵庫の食材: {', '.join(ingredients)}
- 今の気分・食べたいもの: {mood or '特になし'}
- 人数: {servings}人前
- 調理時間の目安: {max_time or '特になし'}
{drink_line}

## Spoonacularが見つけたレシピ候補
{candidates_text}

## タスク
上記候補から最も気分・食材に合う **2品** を選び、以下のJSON形式で回答してください。
- 説明文はすべて日本語で
- 材料は分量を必ず{servings}人前で記載（例: 「鶏もも肉 300g」「醤油 大さじ2」）
- 調味料・塩気・旨味のバランスを考慮し、必要な調味料を補完して記載すること
- 手順は8〜12ステップで、火加減・時間・コツを具体的に書く（例: 「中火で2分炒める」「塩少々で味を調える」）
- ポイントは初心者でもわかるよう丁寧に
- suggested_additionsには、ユーザーが持っていない食材の中で追加すると味や見た目がぐっと良くなるもの2〜4個を提案する（調味料以外の食材を優先）
- substitutionsには、レシピの不足食材（candidates情報の「不足食材」）ごとに代替品または省略方法を配列で記載する。ingredient_nameはingredients_jaに記載した食材名（分量なし）をそのまま使うこと。省略可能なら can_omit: true とし、省略した場合の影響を一言で添える
- {drink_pairing_note}

```json
[
  {{
    "title_en": "英語のレシピ名",
    "title_ja": "日本語のレシピ名（意訳）",
    "reason": "このレシピを選んだ理由（60字以内）",
    "ingredients_ja": [
      "食材1 分量",
      "食材2 分量",
      "調味料1 分量"
    ],
    "steps_ja": [
      "【下準備】手順の説明（火加減・時間・コツを含む）",
      "【調理】手順の説明",
      "..."
    ],
    "tips": ["コツや注意点1", "コツや注意点2"],
    "suggested_additions": [
      {{"name_ja": "追加食材名", "reason": "加えると美味しくなる理由（20字以内）"}},
      {{"name_ja": "追加食材名2", "reason": "理由"}}
    ],
    "substitutions": [
      {{
        "ingredient_name": "不足食材名（ingredients_jaに記載した分量なしの名前）",
        "alternative": "代替品の説明（例: オリーブオイル大さじ1）",
        "can_omit": true,
        "omit_note": "省略した場合の影響（例: コクが薄れるが問題なし）"
      }}
    ],
    "pairing": {{
      "drink": "お酒の名前（例: 白ワイン、ビール、日本酒）",
      "reason": "ペアリングの理由（40字以内）"
    }},
    "cook_time_min": 30,
    "difficulty": "簡単 / 普通 / 難しい"
  }},
  {{ ... 2品目 ... }}
]
```

JSON以外は出力しないでください。"""

    message = anthropic_client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()

    # Log stop reason for debugging
    stop_reason = message.stop_reason
    if stop_reason == "max_tokens":
        print(f"[WARN] Claude hit max_tokens limit — output may be truncated")

    # Extract JSON block
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"[ERROR] Claude raw output (first 800 chars):\n{raw[:800]}")
        raise


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/api/suggest", methods=["POST"])
def suggest():
    body = request.get_json(force=True)
    ingredients = [i.strip() for i in body.get("ingredients", []) if i.strip()]
    mood        = body.get("mood", "")
    servings    = int(body.get("servings", 2))
    max_time    = body.get("max_time", "")
    drink       = body.get("drink", "")

    if not ingredients:
        return jsonify({"error": "食材を入力してください"}), 400

    try:
        # 1. Translate ingredients to English for Spoonacular
        ingredients_en = translate_ingredients_to_english(ingredients)

        # 2. Spoonacular: find by ingredients
        candidates = spoon_find_by_ingredients(ingredients_en)
        if not candidates:
            return jsonify({"error": "その食材ではレシピが見つかりませんでした"}), 404

        candidates_text, id_map = build_recipe_summaries(candidates)

        # 3. Claude: pick best + translate + pairing
        recipes = ask_claude(ingredients, mood, servings, max_time, candidates_text, drink)

        # 4. Attach source URLs
        for r in recipes:
            rid = id_map.get(r.get("title_en", ""))
            r["source_url"] = build_spoonacular_url(r.get("title_en", ""), rid) if rid else None

        return jsonify({
            "recipes": recipes,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "candidate_count": len(candidates),
        })

    except requests.HTTPError as e:
        return jsonify({"error": f"Spoonacular APIエラー: {e}"}), 502
    except anthropic.APIError as e:
        return jsonify({"error": f"Claude APIエラー: {e}"}), 502
    except json.JSONDecodeError:
        return jsonify({"error": "AIの応答を解析できませんでした。もう一度お試しください"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/retry", methods=["POST"])
def retry():
    """Same as suggest but asks Claude to pick *different* recipes."""
    body = request.get_json(force=True)
    ingredients = [i.strip() for i in body.get("ingredients", []) if i.strip()]
    mood        = body.get("mood", "")
    servings    = int(body.get("servings", 2))
    max_time    = body.get("max_time", "")
    drink       = body.get("drink", "")
    exclude     = body.get("exclude_titles", [])

    if not ingredients:
        return jsonify({"error": "食材を入力してください"}), 400

    try:
        ingredients_en = translate_ingredients_to_english(ingredients)
        candidates = spoon_find_by_ingredients(ingredients_en, number=MAX_RECIPES + 4)
        # filter out previously shown titles
        candidates = [c for c in candidates if c["title"] not in exclude]
        if not candidates:
            return jsonify({"error": "他のレシピが見つかりませんでした"}), 404

        candidates_text, id_map = build_recipe_summaries(candidates)
        recipes = ask_claude(ingredients, mood, servings, max_time, candidates_text, drink)
        for r in recipes:
            rid = id_map.get(r.get("title_en", ""))
            r["source_url"] = build_spoonacular_url(r.get("title_en", ""), rid) if rid else None
        return jsonify({
            "recipes": recipes,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "candidate_count": len(candidates),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rewrite", methods=["POST"])
def rewrite():
    """Rewrite a recipe applying user-selected substitutions."""
    body         = request.get_json(force=True)
    recipe       = body.get("recipe", {})
    selected     = body.get("selected_subs", {})  # {ingredient: "alternative text" | "省略"}
    additions    = body.get("additions", [])       # ["食材名", ...]
    servings     = int(body.get("servings", 2))

    if not recipe or (not selected and not additions):
        return jsonify({"error": "レシピまたは変更情報がありません"}), 400

    changes = []
    for ing, val in selected.items():
        changes.append(f"- {ing}: {'省略する' if val == '省略' else '「' + val + '」に変更'}")
    for add in additions:
        changes.append(f"- 「{add}」を新たに追加する（分量・使い方・手順への組み込みも記載）")
    changes_text = "\n".join(changes)

    prompt = f"""以下のレシピを、指定された食材の変更・追加を適用して書き直してください。

## 元レシピ
- レシピ名: {recipe.get('title_ja', '')}
- {servings}人前
- 元の材料: {', '.join(recipe.get('ingredients_ja', []))}
- 元の手順（概要）: {' / '.join((recipe.get('steps_ja') or [])[:4])}

## 適用する変更
{changes_text}

## 出力形式（JSON）
変更を反映した材料リストと手順を出力してください。

```json
{{
  "ingredients_ja": ["食材1 分量", "食材2 分量"],
  "steps_ja": ["手順1", "手順2", "..."],
  "tips": ["変更に関するコツや注意点"]
}}
```

JSON以外は出力しないでください。"""

    try:
        message = anthropic_client.messages.create(
            model=MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        updated = json.loads(raw)
        return jsonify({"updated": updated})
    except json.JSONDecodeError:
        return jsonify({"error": "AIの応答を解析できませんでした"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
def chat():
    """Chat with Claude about a specific recipe."""
    body    = request.get_json(force=True)
    recipe  = body.get("recipe", {})          # full recipe object
    history = body.get("history", [])          # [{role, content}, ...]
    message = body.get("message", "").strip()

    if not message:
        return jsonify({"error": "メッセージを入力してください"}), 400

    # Build system prompt with recipe context
    system = f"""あなたは料理とお酒のプロアシスタントです。
ユーザーは以下のレシピについて質問しています。

## レシピ情報
- レシピ名: {recipe.get('title_ja', '')}（{recipe.get('title_en', '')}）
- 調理時間: {recipe.get('cook_time_min', '不明')}分
- 難易度: {recipe.get('difficulty', '不明')}
- 材料: {', '.join(recipe.get('ingredients_ja', []))}
- 作り方: {' / '.join(recipe.get('steps_ja', []))}
- お酒ペアリング: {recipe.get('pairing', {}).get('drink', '')}

このレシピに関する質問に日本語で丁寧に答えてください。
代替食材・アレンジ・調理のコツ・保存方法なども回答可能です。
回答は簡潔に（200字以内を目安）。"""

    messages = history + [{"role": "user", "content": message}]

    try:
        resp = anthropic_client.messages.create(
            model=MODEL,
            max_tokens=600,
            system=system,
            messages=messages,
        )
        reply = resp.content[0].text.strip()
        return jsonify({"reply": reply})
    except anthropic.APIError as e:
        return jsonify({"error": f"Claude APIエラー: {e}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"[Recipe App] Starting on http://localhost:{PORT}")
    print(f"[Recipe App] Open your browser at http://localhost:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
