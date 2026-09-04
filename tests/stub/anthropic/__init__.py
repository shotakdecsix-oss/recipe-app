"""テスト用のスタブ。APIキー無しで recipe_server.py を import するために使う。"""
class APIError(Exception):
    pass

class _Messages:
    def create(self, **kwargs):
        raise RuntimeError("anthropic stub: ネットワーク呼び出しはテストでは行いません")

class Anthropic:
    def __init__(self, api_key=None):
        self.messages = _Messages()
