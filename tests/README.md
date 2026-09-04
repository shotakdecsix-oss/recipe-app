# テスト

外部APIは呼ばないので、APIキーなしで実行できます。

## サーバー側（Python）

```
pip install flask
python3 tests/test_server.py
```

`tests/stub/anthropic` を `PYTHONPATH` に入れて `anthropic` をスタブ化し、
Flask の test_client でバリデーション・スキーマ検証・ルーティングを検証します。

## フロント側（ブラウザ）

Node と Playwright が必要です。

```
npm i playwright
npx playwright install chromium
python3 tests/mockserver.py          # 別ターミナルで（ポート8900）
node tests/ui.test.js
```

`tests/mockserver.py` が AI応答を固定値で返すモックサーバーです。
実際のHTML/CSS/JSをそのまま読み込むので、再描画をまたぐ状態保持や
パネルの重なり順といった実挙動を検証できます。
