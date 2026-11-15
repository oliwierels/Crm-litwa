"""Wywołanie Claude'a z jednym narzędziem: modele, które nie przyjmują wymuszonego narzędzia (Opus / Sonnet 5.5,
Fable), od razu dostają tool_choice=auto — bez straconego zapytania z błędem 400."""
from types import SimpleNamespace

import crm


class FakeClient:
    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **k):
        self.calls.append(k["tool_choice"]["type"])
        block = SimpleNamespace(type="tool_use", name=k["tools"][0]["name"], input={"ok": True})
        return SimpleNamespace(content=[block])


def test_forced_tool_skipped_for_models_without_it(monkeypatch):
    tool = {"name": "t", "description": "x", "input_schema": {"type": "object", "properties": {}}}
    for model, expected in (("claude-sonnet-5-5", ["auto"]), ("claude-opus-5-5", ["auto"]),
                            ("claude-haiku-4-5", ["tool"])):
        fake = FakeClient()
        monkeypatch.setattr(crm, "_ai_client", lambda: fake)
        assert crm._ai_forced_tool(model, "s", "u", tool) == {"ok": True}
        assert fake.calls == expected, model
