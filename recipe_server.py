"""
Recipe App - Flask Backend
Claude AI for recipe suggestions and alcohol pairings (no external recipe API)
Config: recipe_config.json (DO NOT write API keys in chat or code comments)
"""

import json
import os
import anthropic
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "recipe_config.json")

CFG = {}
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        CFG = json.load(f)

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY") or CFG.get("anthropic_api_key", "")
MODEL         = os.environ.get("MODEL")              or CFG.get("model", "claude-haiku-4-5")
PORT          = int(os.environ.get("PORT", CFG.get("port", 5050)))

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
app = Flask(__name__, static_folder=BASE_DIR)

SERVER_START = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---------------------------------------------------------------------------
# Claude helpers
# ---------------------------------------------------------------------------
def build_prompt(ingredients: list[str], mood: str, servings: int,
                 max_time: str, drink: str, exclude_titles: list[str]) -> str:

    drink_line = f"- 手元にあるお酒: {drink}（このお酒に合う料理を最優先で選ぶこと）" if drink else ""
    drink_pairing_note = (
        f"- pairingのdrinkは必ず「{drink}」を記載し、なぜそのお酒がこの料理に合うかを具体的に説明すること"
        if drink else
        "- pairingには合うお酒とその理由を記載する"
    )
    exclude_note = (
        f"- 以下のレシピはすでに提案済みなので除外すること: {', '.join(exclude_titles)}"
        if exclude_titles else ""
    )

    return f"""あなたは料理とお酒のプロです。以下の情報をもとに、バラエティ豊かなレシピを5品、日本語で詳しく提案してください。

## ユーザー情報
- 手持ちの食材: {', '.join(ingredients)}
- 今の気分・食べたいもの: {mood or '特になし'}
- 人数: {servings}人前
- 調理時間の目安: {max_time or '特になし'}
{drink_line}

## タスク
手持ち食材を活かして作れるレシピを **5品** 考え、以下のJSON形式で回答してください。
- ジャンル（和・洋・中・エスニック等）や調理法（炒め・煮込み・揚げ等）をバラけさせる
- 説明文はすべて日本語で
- 材料は分量を{servings}人前で記載（例: 「鶏もも肉 300g」「醤油 大さじ2」）
- 調味料・塩気・旨味のバランスを考慮し、必要な調味料を補完すること
- 手順は8〜12ステップで、火加減・時間・コツを具体的に書く
- substitutionsには、レシピに必要だがユーザーが持っていない食材があれば代替または省略方法を配列で記載する
- suggested_additionsには、追加すると味や見た目がぐっと良くなる食材を2〜3個提案する
- {drink_pairing_note}
{exclude_note}

```json
[
  {{
    "title_ja": "日本語のレシピ名",
    "reason": "このレシピを選んだ理由（60字以内）",
    "ingredients_ja": [
      "食材1 分量",
      "食材2 分量",
      "調味料1 分量"
    ],
    "steps_ja": [
      "【下準備】手順（火加減・時間・コツを含む）",
      "【調理】手順",
      "..."
    ],
    "tips": ["コツや注意点1", "コツや注意点2"],
    "suggested_additions": [
      {{"name_ja": "追加食材名", "reason": "理由（20字以内）"}}
    ],
    "substitutions": [
      {{
        "ingredient_name": "不足食材名（ingredients_jaの分量なし名称）",
        "alternative": "代替品の説明",
        "can_omit": true,
        "omit_note": "省略した場合の影響"
      }}
    ],
    "pairing": {{
      "drink": "お酒の名前",
      "reason": "ペアリングの理由（40字以内）"
    }},
    "cook_time_min": 30,
    "difficulty": "簡単 / 普通 / 難しい"
  }}
]
```

JSON以外は出力しないでください。"""


def call_claude(prompt: str, max_tokens: int = 8192) -> list:
    message = anthropic_client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if message.stop_reason == "max_tokens":
        print("[WARN] Claude hit max_tokens limit")

    raw = message.content[0].text.strip()
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


@app.route("/api/version")
def version():
    return jsonify({"deployed_at": SERVER_START})


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
        prompt  = build_prompt(ingredients, mood, servings, max_time, drink, [])
        recipes = call_claude(prompt)
        return jsonify({
            "recipes": recipes,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "candidate_count": len(recipes),
        })
    except anthropic.APIError as e:
        return jsonify({"error": f"Claude APIエラー: {e}"}), 502
    except json.JSONDecodeError:
        return jsonify({"error": "AIの応答を解析できませんでした。もう一度お試しください"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/retry", methods=["POST"])
def retry():
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
        prompt  = build_prompt(ingredients, mood, servings, max_time, drink, exclude)
        recipes = call_claude(prompt)
        return jsonify({
            "recipes": recipes,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "candidate_count": len(recipes),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rewrite", methods=["POST"])
def rewrite():
    body      = request.get_json(force=True)
    recipe    = body.get("recipe", {})
    selected  = body.get("selected_subs", {})
    additions = body.get("additions", [])
    servings  = int(body.get("servings", 2))

    if not recipe or (not selected and not additions):
        return jsonify({"error": "レシピまたは変更情報がありません"}), 400

    changes = []
    for ing, val in selected.items():
        changes.append(f"- {ing}: {'省略する' if val == '省略' else '「' + val + '」に変更'}")
    for add in additions:
        changes.append(f"- 「{add}」を新たに追加する（分量・使い方・手順への組み込みも記載）")

    prompt = f"""以下のレシピを、指定された食材の変更・追加を適用して書き直してください。

## 元レシピ
- レシピ名: {recipe.get('title_ja', '')}
- {servings}人前
- 元の材料: {', '.join(recipe.get('ingredients_ja', []))}
- 元の手順（概要）: {' / '.join((recipe.get('steps_ja') or [])[:4])}

## 適用する変更
{chr(10).join(changes)}

## 出力形式（JSON）
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
        return jsonify({"updated": json.loads(raw)})
    except json.JSONDecodeError:
        return jsonify({"error": "AIの応答を解析できませんでした"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
def chat():
    body    = request.get_json(force=True)
    recipe  = body.get("recipe", {})
    history = body.get("history", [])
    message = body.get("message", "").strip()

    if not message:
        return jsonify({"error": "メッセージを入力してください"}), 400

    system = f"""あなたは料理とお酒のプロアシスタントです。
ユーザーは以下のレシピについて質問しています。

## レシピ情報
- レシピ名: {recipe.get('title_ja', '')}
- 調理時間: {recipe.get('cook_time_min', '不明')}分
- 難易度: {recipe.get('difficulty', '不明')}
- 材料: {', '.join(recipe.get('ingredients_ja', []))}
- 作り方: {' / '.join(recipe.get('steps_ja', []))}
- お酒ペアリング: {recipe.get('pairing', {}).get('drink', '')}

このレシピに関する質問に日本語で丁寧に答えてください。
代替食材・アレンジ・調理のコツ・保存方法なども回答可能です。
回答は簡潔に（200字以内を目安）。"""

    try:
        resp = anthropic_client.messages.create(
            model=MODEL,
            max_tokens=600,
            system=system,
            messages=history + [{"role": "user", "content": message}],
        )
        return jsonify({"reply": resp.content[0].text.strip()})
    except anthropic.APIError as e:
        return jsonify({"error": f"Claude APIエラー: {e}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"[Recipe App] Starting on http://localhost:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
