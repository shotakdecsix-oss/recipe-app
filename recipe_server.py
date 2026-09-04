"""
Recipe App - Flask Backend
Claude AI for recipe suggestions and alcohol pairings (no external recipe API)
Config: recipe_config.json (DO NOT write API keys in chat or code comments)
"""

import json
import os
import threading
import time
import urllib.parse
import urllib.request
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
QUALITY_MODEL = os.environ.get("QUALITY_MODEL")      or CFG.get("quality_model", "claude-sonnet-4-5")
PORT          = int(os.environ.get("PORT", CFG.get("port", 5050)))
# 料理写真（Pexels）。未設定なら写真機能は無効になるだけでアプリは動く
PEXELS_KEY    = os.environ.get("PEXELS_API_KEY")     or CFG.get("pexels_api_key", "")

# UIのモデル選択に出す候補。設定ファイル/環境変数で差し替え可能
MODEL_CHOICES = [
    {"id": MODEL,         "label": "はやい（標準）"},
    {"id": QUALITY_MODEL, "label": "じっくり（高品質）"},
]
ALLOWED_MODELS = {m["id"] for m in MODEL_CHOICES}

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# static_folder=None: ディレクトリ全体の自動配信を止め、必要なファイルだけ明示的に返す
app = Flask(__name__, static_folder=None)

JST = timezone(timedelta(hours=9))
SERVER_START = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

# ---------------------------------------------------------------------------
# 入力バリデーション
# ---------------------------------------------------------------------------
MAX_INGREDIENTS   = 20
MAX_ING_LEN       = 40
MAX_TEXT_LEN      = 120
MAX_CHAT_LEN      = 500
MAX_CHAT_TURNS    = 8      # 直近何メッセージをAIに渡すか
MAX_STEPS_IN_CTX  = 14     # systemプロンプトに載せる手順の上限


def _safe_int(value, default, lo, hi):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _clean_text(value, limit=MAX_TEXT_LEN):
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _clean_list(value, limit_items, limit_len):
    if not isinstance(value, list):
        return []
    out = []
    for v in value:
        if isinstance(v, str) and v.strip():
            out.append(v.strip()[:limit_len])
        if len(out) >= limit_items:
            break
    return out


def _pick_model(value):
    return value if value in ALLOWED_MODELS else MODEL


def _read_params(body):
    """レシピ提案系エンドポイントの共通パラメータを検証して返す。"""
    return {
        "ingredients": _clean_list(body.get("ingredients"), MAX_INGREDIENTS, MAX_ING_LEN),
        "mood":        _clean_text(body.get("mood")),
        "servings":    _safe_int(body.get("servings"), 2, 1, 12),
        "max_time":    _clean_text(body.get("max_time"), 20),
        "drink":       _clean_text(body.get("drink"), 60),
        "exclude":     _clean_list(body.get("exclude_titles"), 40, 60),
        "model":       _pick_model(body.get("model")),
    }


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
if not COOKING_THEORY:
    print("[WARN] cooking_theory.md が見つからないため、料理セオリーはプロンプトに含まれません")

# ---------------------------------------------------------------------------
# Job queue (in-memory, single-process + threads)
#   gunicorn は --workers 1 --threads N 前提。workerを増やすとjob_idが引けなくなる
# ---------------------------------------------------------------------------
_jobs: dict = {}
_jobs_lock = threading.Lock()
JOB_TTL_SEC = 1800   # 30分で破棄（メモリリーク防止）


def _purge_jobs() -> None:
    """TTLを過ぎたジョブを削除する。呼び出し側で _jobs_lock を保持していること。"""
    now = time.time()
    for jid in [k for k, v in _jobs.items() if now - v.get("_ts", now) > JOB_TTL_SEC]:
        _jobs.pop(jid, None)


def _coverage_score(recipe: dict, ingredients: list) -> int:
    """指定食材のうちrecipeのingredients_jaに含まれる数を返す（網羅スコア）。"""
    if not ingredients:
        return 0
    recipe_ings = ' '.join(recipe.get("ingredients_ja") or []).lower()
    return sum(1 for ing in ingredients if ing.lower() in recipe_ings)


def _sort_by_coverage(recipes: list, ingredients: list) -> list:
    """網羅スコア降順でレシピをソートして返す。"""
    return sorted(recipes, key=lambda r: _coverage_score(r, ingredients), reverse=True)


def _run_job(job_id: str, prompt: str, ingredients: list, model: str) -> None:
    try:
        recipes = call_claude(prompt, model=model)
        recipes = _normalize_recipes(recipes)
        if not recipes:
            raise ValueError("有効なレシピが得られませんでした")
        recipes = _sort_by_coverage(recipes, ingredients)
        result = {
            "status": "done",
            "recipes": recipes,
            "generated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
            "candidate_count": len(recipes),
        }
    except json.JSONDecodeError:
        result = {"status": "error", "error": "AIの応答を解析できませんでした。もう一度お試しください"}
    except ValueError as e:
        result = {"status": "error", "error": str(e)}
    except Exception as e:
        print(f"[ERROR] job {job_id} failed: {e}")
        result = {"status": "error", "error": "レシピの生成に失敗しました。もう一度お試しください"}
    result["_ts"] = time.time()
    with _jobs_lock:
        _jobs[job_id] = result


def _start_job(prompt: str, ingredients: list, model: str) -> str:
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _purge_jobs()
        _jobs[job_id] = {"status": "pending", "_ts": time.time()}
    threading.Thread(
        target=_run_job, args=(job_id, prompt, ingredients, model), daemon=True
    ).start()
    return job_id


# ---------------------------------------------------------------------------
# AI応答のスキーマ検証
# ---------------------------------------------------------------------------
def _normalize_recipes(data) -> list:
    """AIの返したJSONを検証し、表示できる形に整えて返す。壊れた要素は捨てる。"""
    if isinstance(data, dict):
        # {"recipes": [...]} で返ってくる場合の救済
        for key in ("recipes", "results", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        return []

    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = item.get("title_ja")
        if not isinstance(title, str) or not title.strip():
            continue   # タイトルの無いものは表示できないので捨てる

        def slist(key):
            v = item.get(key)
            return [str(x) for x in v if isinstance(x, (str, int, float))] if isinstance(v, list) else []

        pairing = item.get("pairing")
        if not isinstance(pairing, dict) or not pairing.get("drink"):
            pairing = None

        subs = []
        for s in (item.get("substitutions") or []):
            if isinstance(s, dict) and s.get("ingredient_name"):
                subs.append({
                    "ingredient_name": str(s.get("ingredient_name")),
                    "alternative":     str(s.get("alternative") or ""),
                    "can_omit":        bool(s.get("can_omit")),
                    "omit_note":       str(s.get("omit_note") or ""),
                })

        adds = []
        for a in (item.get("suggested_additions") or []):
            if isinstance(a, dict) and a.get("name_ja"):
                adds.append({
                    "name_ja": str(a.get("name_ja")),
                    "reason":  str(a.get("reason") or ""),
                })

        out.append({
            "title_ja":            title.strip(),
            "reason":              str(item.get("reason") or ""),
            "image_query":         str(item.get("image_query") or "")[:60],
            "ingredients_ja":      slist("ingredients_ja"),
            "steps_ja":            slist("steps_ja"),
            "tips":                slist("tips"),
            "suggested_additions": adds,
            "substitutions":       subs,
            "pairing":             pairing,
            "cook_time_min":       _safe_int(item.get("cook_time_min"), 0, 0, 600) or None,
            "difficulty":          str(item.get("difficulty") or ""),
        })
    return out


# ---------------------------------------------------------------------------
# 料理写真の検索（Pexels）
#   レシピはAI生成のため実物の写真は存在しない。ジャンルの近いイメージ写真を
#   当てるだけなので、UI側で「イメージ」と明示すること。
# ---------------------------------------------------------------------------
_photo_cache = {}
_photo_lock = threading.Lock()
PHOTO_CACHE_MAX = 500


def _pexels_search(query: str):
    url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode({
        "query": query, "per_page": 1, "orientation": "landscape", "size": "medium",
    })
    req = urllib.request.Request(url, headers={"Authorization": PEXELS_KEY})
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    photos = data.get("photos") or []
    if not photos:
        return None
    ph = photos[0]
    src = ph.get("src") or {}
    return {
        "url":        src.get("large") or src.get("medium") or src.get("original"),
        "thumb":      src.get("small") or src.get("tiny") or src.get("medium"),
        "credit":     ph.get("photographer") or "",
        "credit_url": ph.get("photographer_url") or "",
        "page":       ph.get("url") or "",
    }


# ---------------------------------------------------------------------------
# Claude helpers
# ---------------------------------------------------------------------------
def build_prompt(ingredients: list, mood: str, servings: int,
                 max_time: str, drink: str, exclude_titles: list) -> str:

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
- image_queryには、その料理の写真をストックフォトで探すための**英語**の検索語を2〜4語で記載すること（例: "teriyaki chicken", "carrot salad", "miso soup"）。固有のレシピ名ではなく、料理のジャンルと主材料がわかる一般的な語にする
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
    "image_query": "teriyaki chicken",
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


def _extract_json(raw: str) -> str:
    raw = raw.strip()
    if "```json" in raw:
        return raw.split("```json")[1].split("```")[0].strip()
    if "```" in raw:
        return raw.split("```")[1].split("```")[0].strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = raw.find(opener), raw.rfind(closer)
        if start != -1 and end != -1 and end > start:
            return raw[start:end + 1]
    return raw


def call_claude(prompt: str, max_tokens: int = 4096, model: str = None):
    message = anthropic_client.messages.create(
        model=model or MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if message.stop_reason == "max_tokens":
        print("[WARN] Claude hit max_tokens limit — response truncated")

    raw = _extract_json(message.content[0].text)
    print(f"[DEBUG] stop_reason={message.stop_reason}, raw length={len(raw)}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON parse failed: {e}")
        print(f"[ERROR] Claude raw output (first 800 chars):\n{raw[:800]}")
        raise


def _ask_json(prompt: str, max_tokens: int, model: str = None):
    """単発でJSONを返させる共通ヘルパー。"""
    message = anthropic_client.messages.create(
        model=model or MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return json.loads(_extract_json(message.content[0].text))


def _steps_block(recipe: dict) -> str:
    return "\n".join(
        f"{i+1}. {st}" for i, st in enumerate(recipe.get("steps_ja") or [])
    )


# ---------------------------------------------------------------------------
# Routes: static
# ---------------------------------------------------------------------------
STATIC_FILES = {"app.js", "style.css", "manifest.json", "sw.js", "icon.svg"}


def _no_cache(resp):
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


@app.route("/")
def index():
    return _no_cache(send_from_directory(BASE_DIR, "index.html"))


@app.route("/<path:filename>")
def static_files(filename):
    if filename in STATIC_FILES:
        return _no_cache(send_from_directory(BASE_DIR, filename))
    return jsonify({"error": "not found"}), 404


# ---------------------------------------------------------------------------
# Routes: API
# ---------------------------------------------------------------------------
@app.route("/api/version")
def version():
    return jsonify({
        "deployed_at": SERVER_START,
        "models": MODEL_CHOICES,
        "theory_loaded": bool(COOKING_THEORY),
        "photos_enabled": bool(PEXELS_KEY),
    })


@app.route("/api/ping", methods=["GET", "POST", "OPTIONS"])
def ping():
    return jsonify({"ok": True, "method": request.method})


@app.route("/api/recipe", methods=["POST"])
@app.route("/api/recipe/next", methods=["POST"])   # 旧クライアント互換
def suggest():
    p = _read_params(request.get_json(force=True, silent=True) or {})
    if not p["ingredients"]:
        return jsonify({"error": "食材を入力してください"}), 400
    prompt = build_prompt(
        p["ingredients"], p["mood"], p["servings"],
        p["max_time"], p["drink"], p["exclude"],
    )
    return jsonify({"job_id": _start_job(prompt, p["ingredients"], p["model"])})


@app.route("/api/job/<job_id>")
def get_job(job_id):
    with _jobs_lock:
        _purge_jobs()
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"status": "not_found"}), 404
    return jsonify({k: v for k, v in job.items() if not k.startswith("_")})


@app.route("/api/photo")
def photo():
    """料理のイメージ写真を1枚返す。キー未設定・該当なしのときは photo: null。"""
    q = _clean_text(request.args.get("q"), 60)
    if not q:
        return jsonify({"photo": None, "reason": "no_query"})
    if not PEXELS_KEY:
        return jsonify({"photo": None, "reason": "no_key"})

    key = q.lower()
    with _photo_lock:
        if key in _photo_cache:
            return jsonify({"photo": _photo_cache[key], "cached": True})

    try:
        result = _pexels_search(q)
    except Exception as e:
        print(f"[WARN] pexels search failed for {q!r}: {e}")
        return jsonify({"photo": None, "reason": "error"})

    with _photo_lock:
        if len(_photo_cache) >= PHOTO_CACHE_MAX:
            _photo_cache.clear()
        _photo_cache[key] = result
    return jsonify({"photo": result})


@app.route("/api/rewrite", methods=["POST"])
def rewrite():
    body      = request.get_json(force=True, silent=True) or {}
    recipe    = body.get("recipe") or {}
    selected  = body.get("selected_subs") or {}
    additions = body.get("additions") or []
    servings  = _safe_int(body.get("servings"), 2, 1, 12)
    model     = _pick_model(body.get("model"))

    if not recipe or (not selected and not additions):
        return jsonify({"error": "レシピまたは変更情報がありません"}), 400

    changes = []
    for ing, val in list(selected.items())[:20]:
        changes.append(f"- {ing}: {'省略する' if val == '省略' else '「' + str(val) + '」に変更'}")
    for add in additions[:10]:
        changes.append(f"- 「{add}」を新たに追加する（分量・使い方・手順への組み込みも記載）")

    steps_all = _steps_block(recipe)

    prompt = f"""以下のレシピを、指定された食材の変更・追加を適用して書き直してください。

## 元レシピ
- レシピ名: {recipe.get('title_ja', '')}
- {servings}人前
- 元の材料: {', '.join(recipe.get('ingredients_ja', []))}
- 元の手順（全{len(recipe.get('steps_ja') or [])}ステップ）:
{steps_all}

## 適用する変更
{chr(10).join(changes)}

## ルール
- 変更に関係しない手順は、元の文面をそのまま維持すること（要約・省略・言い換えをしない）
- steps_ja は元レシピと同じ範囲を最初から最後まで完全に出力すること

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
        return jsonify({"updated": _ask_json(prompt, 3000, model)})
    except json.JSONDecodeError:
        return jsonify({"error": "AIの応答を解析できませんでした"}), 500
    except Exception as e:
        print(f"[ERROR] /api/rewrite: {e}")
        return jsonify({"error": "レシピの更新に失敗しました"}), 500


@app.route("/api/rewrite-from-chat", methods=["POST"])
def rewrite_from_chat():
    body         = request.get_json(force=True, silent=True) or {}
    recipe       = body.get("recipe") or {}
    chat_history = body.get("chat_history") or []
    servings     = _safe_int(body.get("servings"), 2, 1, 12)
    model        = _pick_model(body.get("model"))

    if not recipe or not chat_history:
        return jsonify({"error": "レシピまたはチャット履歴がありません"}), 400

    chat_text = "\n".join([
        ("ユーザー" if m.get("role") == "user" else "AI") + ": " + str(m.get("content", ""))[:MAX_CHAT_LEN]
        for m in chat_history[-MAX_CHAT_TURNS * 2:]
        if isinstance(m, dict)
    ])

    steps_all = _steps_block(recipe)

    prompt = f"""以下のレシピについて、ユーザーとAIの会話内容を踏まえてレシピを改善・更新してください。

## 元レシピ
- レシピ名: {recipe.get('title_ja', '')}
- {servings}人前
- 材料: {', '.join(recipe.get('ingredients_ja', []))}
- 作り方（全{len(recipe.get('steps_ja') or [])}ステップ）:
{steps_all}

## 会話内容（これを反映すること）
{chat_text}

## ルール
- 変更に関係しない手順は、元の文面をそのまま維持すること（要約・省略・言い換えをしない）
- steps_ja は元レシピと同じ範囲を最初から最後まで完全に出力すること
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
        return jsonify({"updated": _ask_json(prompt, 3000, model)})
    except json.JSONDecodeError:
        return jsonify({"error": "AIの応答を解析できませんでした"}), 500
    except Exception as e:
        print(f"[ERROR] /api/rewrite-from-chat: {e}")
        return jsonify({"error": "レシピの更新に失敗しました"}), 500


@app.route("/api/chat", methods=["POST"])
def chat():
    body    = request.get_json(force=True, silent=True) or {}
    recipe  = body.get("recipe") or {}
    history = body.get("history") or []
    message = _clean_text(body.get("message"), MAX_CHAT_LEN)
    model   = _pick_model(body.get("model"))

    if not message:
        return jsonify({"error": "メッセージを入力してください"}), 400

    # 会話が伸びてもコンテキストが肥大しないよう直近だけを渡す
    trimmed = []
    for m in history[-MAX_CHAT_TURNS * 2:]:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            trimmed.append({"role": m["role"], "content": str(m.get("content", ""))[:MAX_CHAT_LEN]})

    steps = (recipe.get("steps_ja") or [])[:MAX_STEPS_IN_CTX]
    steps_txt = ' / '.join(s[:MAX_TEXT_LEN] for s in steps)

    system = f"""あなたは料理とお酒のプロアシスタントです。
ユーザーは以下のレシピについて質問しています。

## レシピ情報
- レシピ名: {recipe.get('title_ja', '')}
- 調理時間: {recipe.get('cook_time_min', '不明')}分
- 難易度: {recipe.get('difficulty', '不明')}
- 材料: {', '.join(recipe.get('ingredients_ja', []))}
- 作り方: {steps_txt}
- お酒ペアリング: {(recipe.get('pairing') or {}).get('drink', '')}

このレシピに関する質問に日本語で丁寧に答えてください。
代替食材・アレンジ・調理のコツ・保存方法なども回答可能です。
回答は簡潔に（200字以内を目安）。"""

    try:
        resp = anthropic_client.messages.create(
            model=model,
            max_tokens=600,
            system=system,
            messages=trimmed + [{"role": "user", "content": message}],
        )
        return jsonify({"reply": resp.content[0].text.strip()})
    except anthropic.APIError as e:
        print(f"[ERROR] /api/chat: {e}")
        return jsonify({"error": "Claude APIエラーが発生しました"}), 502
    except Exception as e:
        print(f"[ERROR] /api/chat: {e}")
        return jsonify({"error": "回答の生成に失敗しました"}), 500


@app.route("/api/shopping-list", methods=["POST"])
def shopping_list():
    """複数レシピの材料を合算した買い物リストを作る。"""
    body     = request.get_json(force=True, silent=True) or {}
    recipes  = body.get("recipes") or []
    servings = _safe_int(body.get("servings"), 2, 1, 12)
    have     = _clean_list(body.get("have"), MAX_INGREDIENTS, MAX_ING_LEN)
    model    = _pick_model(body.get("model"))

    if not recipes:
        return jsonify({"error": "レシピが選択されていません"}), 400

    blocks = []
    for r in recipes[:6]:
        if not isinstance(r, dict):
            continue
        blocks.append(
            f"### {r.get('title_ja','')}\n" +
            "\n".join(f"- {x}" for x in (r.get("ingredients_ja") or []))
        )

    have_line = (
        f"\n## すでに手元にある食材（買い物リストでは「手持ち」として分けること）\n{', '.join(have)}"
        if have else ""
    )

    prompt = f"""以下の複数レシピを{servings}人前で作るための買い物リストを作成してください。

## レシピ
{chr(10).join(blocks)}
{have_line}

## ルール
- 同じ食材は1行にまとめ、分量を合算すること（例: 「醤油 大さじ2」+「醤油 大さじ1」→「醤油 大さじ3」）
- 単位が違う場合は現実的な単位に換算してまとめる（例: 100g + 1/2本 → 「にんじん 1本」）
- カテゴリ（野菜/肉・魚/乳製品・卵/調味料/その他）ごとに分類する
- 手元にある食材は have_at_home に入れ、items には入れないこと
- 買い物しやすいよう、スーパーの売り場順（野菜→肉魚→乳製品→調味料）にカテゴリを並べる

## 出力形式（JSON）
```json
{{
  "categories": [
    {{"name": "野菜", "items": [{{"name": "にんじん", "amount": "1本", "used_in": ["レシピ名"]}}]}}
  ],
  "have_at_home": [{{"name": "醤油", "amount": "大さじ3"}}],
  "notes": ["補足があれば"]
}}
```

JSON以外は出力しないでください。"""

    try:
        return jsonify({"list": _ask_json(prompt, 2000, model)})
    except json.JSONDecodeError:
        return jsonify({"error": "AIの応答を解析できませんでした"}), 500
    except Exception as e:
        print(f"[ERROR] /api/shopping-list: {e}")
        return jsonify({"error": "買い物リストの作成に失敗しました"}), 500


@app.route("/api/timeline", methods=["POST"])
def timeline():
    """複数レシピを並行調理するためのタイムラインを作る。"""
    body    = request.get_json(force=True, silent=True) or {}
    recipes = body.get("recipes") or []
    model   = _pick_model(body.get("model"))

    if len(recipes) < 1:
        return jsonify({"error": "レシピが選択されていません"}), 400

    blocks = []
    for r in recipes[:4]:
        if not isinstance(r, dict):
            continue
        blocks.append(
            f"### {r.get('title_ja','')}（約{r.get('cook_time_min','?')}分）\n" + _steps_block(r)
        )

    prompt = f"""以下の複数の料理を1人で並行して作るための調理タイムラインを作成してください。

## レシピ
{chr(10).join(blocks)}

## ルール
- すべての料理がほぼ同時に出来上がるように工程を並べ替える
- 待ち時間（煮る・焼く・寝かせる）に別の料理の作業を差し込むこと
- 各ステップに「開始時刻（調理開始からの経過分）」「どの料理か」「作業内容」を明記
- 火口（コンロ）が同時にいくつ必要かを考慮し、無理がある場合は notes に書く
- 元の手順の内容を勝手に変えないこと。順番の入れ替えと待ち時間の活用だけを行う
- 最後に盛り付け・提供のステップを入れる

## 出力形式（JSON）
```json
{{
  "total_min": 45,
  "steps": [
    {{"at_min": 0, "recipe": "レシピ名", "text": "作業内容", "duration_min": 5, "is_wait": false}}
  ],
  "notes": ["コンロ2口必要 など"]
}}
```

JSON以外は出力しないでください。"""

    try:
        return jsonify({"timeline": _ask_json(prompt, 3000, model)})
    except json.JSONDecodeError:
        return jsonify({"error": "AIの応答を解析できませんでした"}), 500
    except Exception as e:
        print(f"[ERROR] /api/timeline: {e}")
        return jsonify({"error": "タイムラインの作成に失敗しました"}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"[Recipe App] Starting on http://localhost:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
