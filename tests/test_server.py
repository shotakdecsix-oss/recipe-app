# -*- coding: utf-8 -*-
"""recipe_server.py のサーバー側テスト（外部API呼び出しなし）。

    pip install flask
    python3 tests/test_server.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "stub"))   # anthropic をスタブ化
sys.path.insert(0, ROOT)

import recipe_server as R  # noqa: E402

results = []


def ck(name, cond, extra=None):
    results.append(("PASS  " if cond else "FAIL  ") + name
                   + ("" if cond else "  << " + repr(extra)))


def main():
    # ---- 入力バリデーション ----
    ck("servings不正値でも落ちない", R._safe_int("abc", 2, 1, 12) == 2)
    ck("servings範囲外はクランプ", R._safe_int(999, 2, 1, 12) == 12)
    ck("食材の件数を制限", len(R._clean_list(["x"] * 50, 20, 40)) == 20)
    ck("食材の長さを制限", len(R._clean_list(["あ" * 100], 20, 40)[0]) == 40)
    ck("未知モデルは既定に落ちる", R._pick_model("evil-model") == R.MODEL)
    ck("許可モデルは通る", R._pick_model(R.QUALITY_MODEL) == R.QUALITY_MODEL)

    # ---- AI応答のスキーマ検証 ----
    bad = [{"no_title": 1}, "文字列", {"title_ja": "  "}, None]
    ck("壊れたレシピは捨てられる", R._normalize_recipes(bad) == [])
    ok = R._normalize_recipes([{
        "title_ja": "テスト", "ingredients_ja": ["鶏 300g"], "steps_ja": ["焼く"],
        "cook_time_min": "20", "pairing": {"drink": "日本酒", "reason": "合う"},
        "substitutions": [{"ingredient_name": "鶏", "alternative": "豚"}],
        "suggested_additions": [{"name_ja": "バター", "reason": "コク"}],
    }])
    ck("正常レシピは通る", len(ok) == 1 and ok[0]["cook_time_min"] == 20, ok)
    ck("pairingが不正ならNone",
       R._normalize_recipes([{"title_ja": "a", "pairing": "x"}])[0]["pairing"] is None)
    ck('{"recipes":[...]}形式も救済',
       len(R._normalize_recipes({"recipes": [{"title_ja": "a"}]})) == 1)

    # ---- ジョブのTTL（メモリリーク防止） ----
    R._jobs.clear()
    R._jobs["old"] = {"status": "done", "_ts": time.time() - 99999}
    R._jobs["new"] = {"status": "done", "_ts": time.time()}
    R._purge_jobs()
    ck("古いジョブがTTLで消える", "old" not in R._jobs and "new" in R._jobs, list(R._jobs))

    # ---- 料理セオリー ----
    ck("cooking_theory.md が読めている", len(R.COOKING_THEORY) > 500, len(R.COOKING_THEORY))

    # ---- ルーティング ----
    c = R.app.test_client()
    ck("/ が返る", c.get("/").status_code == 200)
    ck("/app.js が返る", c.get("/app.js").status_code == 200)
    ck("/style.css が返る", c.get("/style.css").status_code == 200)
    ck("/manifest.json が返る", c.get("/manifest.json").status_code == 200)
    ck("/sw.js が返る", c.get("/sw.js").status_code == 200)
    ck("設定ファイルは配信されない", c.get("/recipe_config.json").status_code == 404)
    ck("サーバーソースは配信されない", c.get("/recipe_server.py").status_code == 404)

    v = c.get("/api/version").get_json()
    ck("/api/version にモデル一覧が入る", len(v.get("models", [])) == 2, v)
    ck("/api/version がセオリー読込を報告", v.get("theory_loaded") is True)

    ck("食材なしは400", c.post("/api/recipe", json={"ingredients": []}).status_code == 400)
    ck("/api/recipe/next も生きている",
       c.post("/api/recipe/next", json={"ingredients": ["鶏"]}).status_code == 200)
    ck("存在しないjobは404", c.get("/api/job/nope").status_code == 404)
    ck("rewriteは変更なしで400",
       c.post("/api/rewrite", json={"recipe": {"title_ja": "a"}}).status_code == 400)
    ck("chatは空メッセージで400", c.post("/api/chat", json={"message": ""}).status_code == 400)
    ck("shopping-listはレシピなしで400",
       c.post("/api/shopping-list", json={"recipes": []}).status_code == 400)
    ck("timelineはレシピなしで400",
       c.post("/api/timeline", json={"recipes": []}).status_code == 400)
    ck("壊れたJSONでも500にならない", c.post("/api/recipe", data="not json").status_code == 400)

    # ---- 料理写真 ----
    ck("image_query が正規化される",
       R._normalize_recipes([{"title_ja": "a", "image_query": "teriyaki chicken"}])[0]["image_query"]
       == "teriyaki chicken")
    ck("クエリなしの写真検索", c.get("/api/photo").get_json()["reason"] == "no_query")
    if not R.PEXELS_KEY:
        ck("キー未設定なら写真は無効",
           c.get("/api/photo?q=chicken").get_json() == {"photo": None, "reason": "no_key"})
        ck("キー未設定は /api/version に反映される",
           c.get("/api/version").get_json()["photos_enabled"] is False)
    else:
        ck("キー設定済みなら写真が引ける",
           c.get("/api/photo?q=chicken").get_json().get("photo") is not None)

    print("\n".join(results))
    passed = sum(1 for r in results if r.startswith("PASS"))
    print("\n%d/%d passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
