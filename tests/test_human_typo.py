"""Teksty do klientów (maile, wiadomości na komunikatory) bez „AI-owej” typografii."""
from datetime import date

import pytest

import crm


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


def test_messages_and_ai_texts_are_clean(app, monkeypatch):
    assert crm.human_text("Laba diena — robotas… 16:00–20:00 → „gerai”") == 'Laba diena - robotas... 16:00-20:00 -> "gerai"'
    lead = crm.Lead(company="UAB Tipografija", stage="Nowy", assigned_to=1, currency="EUR", market="lt",
                    contact_phone="+37061234567", contact_email="t@tipo.lt", location="Vilnius")
    crm.db.session.add(lead)
    crm.db.session.commit()
    crm.set_lead_event_days(lead, [date(2027, 4, 2)], {})
    crm.db.session.commit()
    with app.test_request_context():
        q = crm.lt_quote(lead)
    for text in (q["message"], q.get("message_price") or ""):
        assert "—" not in text and "–" not in text
    monkeypatch.setattr(crm, "_ai_forced_tool", lambda m, s, t, tool, max_tokens=0: (
        {"language": "lt", "message": "Laba diena — čia Oliwier… Ar dar aktualu?", "message_pl": "Dzień dobry — tu Oliwier…",
         "hook": "x"} if tool["name"] == "write_message" else
        {"subject_pl": "Oferta — robot", "body_pl": "Dzień dobry — w załączniku…", "subject_out": "Pasiūlymas — robotas",
         "body_out": "Laba diena — prisegame pasiūlymą…"}))
    seen = {}
    real = crm._ai_forced_tool
    monkeypatch.setattr(crm, "_ai_forced_tool", lambda m, s, t, tool, max_tokens=0: seen.update(system=s) or real(m, s, t, tool))
    d = crm.lt_msg_ai(lead.id, 1)["draft"]
    assert "—" not in d["message"] + d["message_pl"] and "…" not in d["message"]
    assert "NIGDY nie używaj" in seen["system"]
    m = crm.mail_ai_draft("lt", "Napisz ofertę", lead_id=lead.id, writer_id=1)["draft"]
    assert all("—" not in v and "…" not in v for v in m.values()) and "NIGDY nie używaj" in seen["system"]
