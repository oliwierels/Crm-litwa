"""Litewska lista ClickUp („CRM | 33Bots | Litwa”) → CRM LT/DE: leady rynku Litwa w zakładce „Z ClickUp”
(do zatwierdzenia, jak w CRM PL), bez duplikatów i bez żadnego maila do klienta przed zatwierdzeniem."""
from datetime import datetime, timedelta

import pytest

import crm


class Resp:
    def __init__(self, data):
        self.ok, self.status_code, self._data, self.text = True, 200, data, ""

    def json(self):
        return self._data


def ms(dt):
    return str(int(dt.timestamp() * 1000))


def lt_task(tid, name, phone, email, created):
    cfs = [{"name": "📩 Email", "type": "email", "value": email},
           {"name": "☎️ Numer telefonu", "type": "phone", "value": phone},
           {"name": "Lead id | FB", "type": "short_text", "value": "123"}]
    return {"id": tid, "name": name, "text_content": "Renginys Vilniuje", "custom_fields": cfs,
            "date_created": ms(created), "assignees": []}


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


def test_lithuanian_list_imports_to_waiting_room_without_duplicates(app, monkeypatch):
    now = datetime.utcnow().replace(microsecond=0)
    known = crm.Lead(company="Jau Yra", stage="Oferta", assigned_to=1, market="lt", contact_email="jau@imone.lt",
                     created_at=now - timedelta(days=5))
    crm.db.session.add(known)
    crm.db.session.commit()
    tasks = [lt_task("lt-1", "Jonas Petraitis", "+37061234567", "jonas@imone.lt", now - timedelta(days=3)),
             lt_task("lt-2", "Jau Yra", "+37069999999", "jau@imone.lt", now - timedelta(days=5))]
    monkeypatch.setenv("CLICKUP_API_TOKEN", "tok-lt")
    monkeypatch.setenv("CLICKUP_LIST_ID", "LT-LIST")
    monkeypatch.delenv("CLICKUP_LIST_ID_DE", raising=False)
    monkeypatch.delenv("CLICKUP_API_TOKEN_DE", raising=False)
    monkeypatch.setattr(crm.requests, "get", lambda url, headers=None, params=None, timeout=None: Resp(
        {"tasks": tasks if "/list/LT-LIST/task" in url and (params or {}).get("page", 0) == 0 else [], "last_page": True}))
    ok, n, msg = crm.sync_clickup_main_leads()
    assert ok and n == 1, msg
    lead = crm.all_markets(crm.Lead.query.filter_by(clickup_task_id="lt-1")).one()
    assert lead.market == "lt" and lead.clickup_pending and lead.contact_email == "jonas@imone.lt"
    assert lead.contact_phone and lead.created_at == now - timedelta(days=3), "data zgłoszenia z ClickUp"
    assert lead not in crm.unhandled_leads("lt"), "czeka w „Z ClickUp”, nie w oknie „obsłuż dziś”"
    crm.db.session.refresh(known)
    assert known.clickup_task_id == "lt-2", "ten sam klient już jest — połączony, bez drugiej kopii"
    ok, n, _ = crm.sync_clickup_main_leads()
    assert ok and n == 0 and crm.all_markets(crm.Lead.query.filter_by(clickup_task_id="lt-1")).count() == 1
    assert "clickup" in crm.health_jobs()


def test_no_automatic_mail_before_lead_is_accepted(app, monkeypatch):
    now = datetime.utcnow().replace(microsecond=0)
    crm.set_setting(crm.LT_SINCE_KEY, (now - timedelta(days=30)).isoformat())
    crm.db.session.commit()
    waiting = crm.Lead(company="Laukia Patvirtinimo", stage="Nowy", assigned_to=1, market="lt", clickup_pending=True,
                       contact_email="laukia@imone.lt", created_at=now - timedelta(hours=1), source="ClickUp")
    accepted = crm.Lead(company="Patvirtintas", stage="Nowy", assigned_to=1, market="lt", clickup_pending=False,
                        contact_email="patvirtintas@imone.lt", created_at=now - timedelta(hours=1), source="ClickUp")
    crm.db.session.add_all([waiting, accepted])
    crm.db.session.commit()
    sent = []
    monkeypatch.setenv("LT_AUTO_MAIL", "1")
    monkeypatch.setattr(crm, "lt_mailbox_ready", lambda code="lt": True)
    monkeypatch.setattr(crm, "lt_auto_quote_enabled", lambda code="lt": True)
    monkeypatch.setattr(crm, "send_lt_quote_mail", lambda lead, *a, **k: sent.append(lead.id) or crm.LT_NO_QUOTE)
    crm.lt_auto_mail()
    assert waiting.id not in sent and accepted.id in sent
