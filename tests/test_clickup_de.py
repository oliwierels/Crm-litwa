"""Niemiecka lista ClickUp (formularze z reklam Meta w DE) → leady rynku Niemcy w CRM LT/DE.
Bez prawdziwego ClickUp — zapytania przechwycone; token testowy."""
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


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


def de_task(tid, name, phone, email, created, due=None, extra_fields=()):
    cfs = [{"name": "Vor- und Nachname", "type": "short_text", "value": name},
           {"name": "Telefonnummer", "type": "phone", "value": phone},
           {"name": "E-Mail", "type": "email", "value": email},
           {"name": "Ihre Antwort", "type": "short_text", "value": "Firmenfeier"}, *extra_fields]
    t = {"id": tid, "name": name, "text_content": "", "custom_fields": cfs, "date_created": ms(created), "assignees": []}
    if due:
        t["due_date"] = ms(due)
    return t


def test_german_list_imports_to_market_de(app, monkeypatch):
    now = datetime.utcnow().replace(microsecond=0)
    tasks = [de_task("de-1", "Norbert Ratka", "+4915207690833", "norbert@example.de", now - timedelta(hours=3),
                     due=now - timedelta(hours=1)),
             de_task("de-2", "Olga Koop", "+491786779159", "olga@example.de", now - timedelta(days=12),
                     extra_fields=[{"name": "Veranstaltungsdatum", "type": "date",
                                    "value": ms(datetime(2027, 3, 14))}])]
    seen = []

    def fake_get(url, headers=None, params=None, timeout=None):
        seen.append((url, headers.get("Authorization")))
        assert "/list/DE-LIST/task" in url
        return Resp({"tasks": tasks if (params or {}).get("page", 0) == 0 else [], "last_page": True})

    monkeypatch.setenv("CLICKUP_LIST_ID_DE", "DE-LIST")
    monkeypatch.setenv("CLICKUP_API_TOKEN_DE", "tok-de")
    monkeypatch.delenv("CLICKUP_LIST_ID", raising=False)
    monkeypatch.setattr(crm.requests, "get", fake_get)

    ok, n, msg = crm.sync_clickup_leads()
    assert ok and n == 2, msg
    assert seen and all(tok == "tok-de" for _u, tok in seen)
    q = crm.all_markets(crm.Lead.query)
    fresh = q.filter_by(clickup_task_id="de-1").one()
    old = crm.all_markets(crm.Lead.query).filter_by(clickup_task_id="de-2").one()
    assert fresh.market == "de" and fresh.source == crm.CLICKUP_DE_SOURCE and fresh.currency == "EUR"
    assert fresh.contact_phone == "+4915207690833" and fresh.contact_email == "norbert@example.de"
    assert fresh.contact_name == "Norbert Ratka" and "Ihre Antwort: Firmenfeier" in fresh.notes
    assert not fresh.clickup_pending, "świeży lead od razu na tablicy"
    assert fresh.event_date is None, "termin taska = dzień zgłoszenia to nie data wydarzenia"
    assert old.clickup_pending, "starszy niż CLICKUP_AUTO_ACCEPT_DAYS — do „Z ClickUp”"
    assert old.event_date.date() == datetime(2027, 3, 14).date()

    # zaległy task (sprzed podłączenia listy) — na tablicy, ale bez okna alarmu; nowy task po podłączeniu — z alarmem
    assert fresh not in crm.unhandled_leads("de")
    new_task = de_task("de-9", "Kevin Schaper", "+491721682442", "kevin@example.de", datetime.utcnow() + timedelta(seconds=5))
    tasks.append(new_task)
    ok, n, _ = crm.sync_clickup_leads()
    new = crm.all_markets(crm.Lead.query).filter_by(clickup_task_id="de-9").one()
    assert ok and n == 1 and new in crm.unhandled_leads("de"), "nowy niemiecki lead = nasz, okno alarmu jak dla innych"
    # kolejna synchronizacja — bez duplikatów
    ok, n, _ = crm.sync_clickup_leads()
    assert ok and n == 0
    assert crm.all_markets(crm.Lead.query).filter(crm.Lead.clickup_task_id.in_(["de-1", "de-2", "de-9"])).count() == 3


def test_german_task_already_pending_from_main_list_moves_to_de(app, monkeypatch):
    now = datetime.utcnow().replace(microsecond=0)
    lead = crm.Lead(company="Burhan Asan", stage="Nowy", assigned_to=1, clickup_task_id="de-3", clickup_pending=True,
                    created_at=now - timedelta(days=1), market="lt", source="ClickUp")
    crm.db.session.add(lead)
    crm.db.session.commit()
    monkeypatch.setenv("CLICKUP_LIST_ID_DE", "DE-LIST")
    monkeypatch.setenv("CLICKUP_API_TOKEN", "tok-main")
    monkeypatch.delenv("CLICKUP_API_TOKEN_DE", raising=False)
    monkeypatch.setattr(crm.requests, "get", lambda url, headers=None, params=None, timeout=None: Resp(
        {"tasks": [de_task("de-3", "Burhan Asan", "+491732416161", "b@example.de", now - timedelta(days=1))],
         "last_page": True}))
    ok, n, msg = crm.sync_clickup_de_leads()
    crm.db.session.refresh(lead)
    assert ok and n == 0 and lead.market == "de" and lead.source == crm.CLICKUP_DE_SOURCE and not lead.clickup_pending, msg
