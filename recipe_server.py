"""
Recipe App - Flask Backend
Claude AI for recipe suggestions and alcohol pairings (no external recipe API)
Config: recipe_config.json (DO NOT write API keys in chat or code comments)
"""

import json
import os
import threading
import uuid
import anthropic
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime, timezone, timedelta

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

JST = timezone(timedelta(hours=9))
SERVER_START = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

# ---------------------------------------------------------------------------
# 料理セオリー（cooking_theory.md から起動時に読み込む）
# ---------------------------------------------------------------------------
def _load_cooking_theory() -> str:
    path = os.path.join(BASE_DIR, "cooking_theory.md")
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        full = f.read()
    # プロンプトに埋め込む要点だけを抽出（トークン節約のため主要5セクション）
    sections = [
        "## 1. 五味と味の相互作用",
        "## 2. 旨味の相乗効果（最重要）",
        "## 3. フレーバーペアリング理論",
        "## 5. 食感のコントラスト",
        "## 7. ハーブ・スパイスの使い方セオリー",
    ]
    result = []
    lines = full.split("\n")
    capturing = False
    current_section = []
    for line in lines:
        is_section_start = any(line.startswith(s) for s in sections)
        is_next_h2 = line.startswith("## ") and not is_section_start
        if is_section_start:
            if current_section:
                result.append("\n".join(current_section))
            current_section = [line]
            capturing = True
        elif capturing and is_next_h2:
            result.append("\n".join(current_section))
            current_section = []
            capturing = False
        elif capturing:
            current_section.append(line)
    if current_section:
        result.append("\n".join(current_section))
    return "\n\n".join(result)

COOKING_THEORY = _load_cooking_theory()

# ---------------------------------------------------------------------------
# Job queue (in-memory, single-process + threads)
# ---------------------------------------------------------------------------
_jobs: dict = {}
_jobs_lock = threading.Lock()

def _coverage_score(recipe: dict, ingredients: list[str]) -> int:
    """指定食材のうちrecipeのingredients_jaに含まれる数を返す（網羅スコア）。"""
    if not ingredients:
        return 0
    recipe_ings = ' '.join(recipe.get("ingredients_ja") or []).lower()
    return sum(1 for ing in ingredients if ing.lower() in recipe_ings)

def _sort_by_coverage(recipes: list, ingredients: list[str]) -> list:
    """網羅スコア降順でレシピをソートして返す。"""
    return sorted(recipes, key=lambda r: _coverage_score(r, ingredients), reverse=True)

def _run_job(job_id: str, prompt: str, ingredients: list[str]) -> None:
    try:
        recipes = call_claude(prompt)
        recipes = _sort_by_coverage(recipes, ingredients)
        result = {
            "status": "done",
            "recipes": recipes,
            "generated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
            "candidate_count": len(recipes),
        }
    except json.JSONDecodeError:
        result = {"status": "error", "error": "AIの応答を解析できませんでした。もう一度お試しください"}
    except Exception as e:
        result = {"status": "error", "error": str(e)}
    with _jobs_lock:
        _jobs[job_id] = result

def _start_job(prompt: str, ingredients: list[str]) -> str:
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {"status": "pending"}
    threading.Thread(target=_run_job, args=(job_id, prompt, ingredients), daemon=True).start()
    return job_id

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

    theory_block = (
        f"\n【料理セオリー — 以下の知識をレシピ提案に必ず活かすこと】\n{COOKING_THEORY}\n"
        if COOKING_THEORY else ""
    )

    return f"""あなたは料理とお酒のプロです。以下の条件でレシピを3品、日本語のJSONで返してください。
{theory_block}

- 手持ちの食材: {', '.join(ingredients)}
- 気分: {mood or '特になし'}
- 人数: {servings}人前
- 調理時間: {max_time or '特になし'}
{drink_line}

【最優先ルール — 食材の網羅】
- 指定した食材（{', '.join(ingredients)}）は、3品の合計で**必ず全て**使い切ること。どの食材も1品以上に登場させること。
- 各レシピはできる限り多くの指定食材を主要食材として使うこと。相性が良い食材は同じレシピに積極的に組み込む。
- 指定食材を使わない（または脇役にもできない）理由がある場合のみ、reasonにその旨を説明すること。

ルール:
- ジャンル・調理法をバラけさせる
- 材料は{servings}人前の分量を具体的に記載（例: 「鶏もも肉 300g」「醤油 大さじ2」）
- 調味料・塩気・旨味のバランスを必ず補完すること
- 手順は6〜8ステップで、各ステップに火加減・時間・コツを含める
- ハーブ・スパイス・特殊調味料（ナンプラー、豆板醤、クミン等）は必ずsubstitutionsに代替/省略を記載
- tipsには仕上がりをよくするコツを2つ記載
- {drink_pairing_note}
- 【食材チェック】出力前に self-check: ingredients_ja に列挙した全食材が steps_ja のいずれかに登場しているか確認し、漏れがあれば手順に組み込んでから出力すること
- 【あく取り】肉類・魚介・豆類・根菜など灰汁が出る食材を使う場合は、あく取りの手順（タイミング・方法）を steps_ja に必ず明記すること
{exclude_note}

```json
[
  {{
    "title_ja": "レシピ名",
    "reason": "選んだ理由（50字以内）",
    "ingredients_ja": ["食材1 分量", "調味料1 分量"],
    "steps_ja": [
      "【下準備】具体的な手順（火加減・時間含む）",
      "【調理】手順",
      "..."
    ],
    "tips": ["コツ1", "コツ2"],
    "suggested_additions": [{{"name_ja": "食材名", "reason": "理由（20字以内）"}}],
    "substitutions": [
      {{"ingredient_name": "食材名", "alternative": "代替品の説明", "can_omit": true, "omit_note": "省略時の影響"}}
    ],
    "pairing": {{"drink": "お酒名", "reason": "理由（40字以内）"}},
    "cook_time_min": 20,
    "difficulty": "簡単"
  }}
]
```

JSON以外は出力しないでください。"""


def call_claude(prompt: str, max_tokens: int = 4096) -> list:
    message = anthropic_client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if message.stop_reason == "max_tokens":
        print("[WARN] Claude hit max_tokens limit — response truncated")

    raw = message.content[0].text.strip()

    # Extract JSON array from response
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()
    else:
        # Try to find bare JSON array
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1:
            raw = raw[start:end+1]

    print(f"[DEBUG] stop_reason={message.stop_reason}, raw length={len(raw)}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON parse failed: {e}")
        print(f"[ERROR] Claude raw output (first 800 chars):\n{raw[:800]}")
        raise


def stream_recipe_response(prompt: str, ingredients: list[str] = None):
    """Stream Claude response as SSE events, send parsed JSON when done."""
    full_text = ""
    last_ping = time.time()

    try:
        with anthropic_client.messages.stream(
            model=MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        ) as stream:
            for text in stream.text_stream:
                full_text += text
                now = time.time()
                if now - last_ping >= 1.5:
                    yield ": ping\n\n"
                    last_ping = now

        raw = full_text.strip()
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        recipes = json.loads(raw)
        recipes = _sort_by_coverage(recipes, ingredients or [])
        payload = {
            "done": True,
            "recipes": recipes,
            "generated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
            "candidate_count": len(recipes),
        }
        yield f"data: {json.dumps(payload)}\n\n"

    except json.JSONDecodeError:
        print(f"[ERROR] Claude raw output:\n{full_text[:800]}")
        err = {"error": "AIの応答を解析できませんでした。もう一度お試しください"}
        yield f"data: {json.dumps(err)}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/api/version")
def version():
    return jsonify({"deployed_at": SERVER_START})


@app.route("/api/ping", methods=["GET", "POST", "OPTIONS"])
def ping():
    return jsonify({"ok": True, "method": request.method})


@app.route("/api/recipe", methods=["POST"])
def suggest():
    body = request.get_json(force=True)
    ingredients = [i.strip() for i in body.get("ingredients", []) if i.strip()]
    mood        = body.get("mood", "")
    servings    = int(body.get("servings", 2))
    max_time    = body.get("max_time", "")
    drink       = body.get("drink", "")
    exclude     = body.get("exclude_titles", [])
    if not ingredients:
        return jsonify({"error": "食材を入力してください"}), 400
    prompt = build_prompt(ingredients, mood, servings, max_time, drink, exclude)
    return jsonify({"job_id": _start_job(prompt, ingredients)})


@app.route("/api/recipe/next", methods=["POST"])
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
    prompt = build_prompt(ingredients, mood, servings, max_time, drink, exclude)
    return jsonify({"job_id": _start_job(prompt, ingredients)})


@app.route("/api/job/<job_id>")
def get_job(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"status": "not_found"}), 404
    return jsonify(job)


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


@app.route("/api/rewrite-from-chat", methods=["POST"])
def rewrite_from_chat():
    body         = request.get_json(force=True)
    recipe       = body.get("recipe", {})
    chat_history = body.get("chat_history", [])
    servings     = int(body.get("servings", 2))

    if not recipe or not chat_history:
        return jsonify({"error": "レシピまたはチャット履歴がありません"}), 400

    chat_text = "\n".join([
        ("ユーザー" if m["role"] == "user" else "AI") + ": " + m["content"]
        for m in chat_history
    ])

    prompt = f"""以下のレシピについて、ユーザーとAIの会話内容を踏まえてレシピを改善・更新してください。

## 元レシピ
- レシピ名: {recipe.get('title_ja', '')}
- {servings}人前
- 材料: {', '.join(recipe.get('ingredients_ja', []))}
- 作り方（概要）: {' / '.join((recipe.get('steps_ja') or [])[:4])}

## 会話内容（これを反映すること）
{chat_text}

## ルール
- 会話で提案・確認された内容（代替食材、アレンジ、調理のコツ等）をレシピに反映する
- ingredients_ja に列挙した全食材・調味料が steps_ja のいずれかに登場しているか確認し、漏れがあれば手順に組み込む
- アクが出る食材を使う場合はあく取りの手順を明記する

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
