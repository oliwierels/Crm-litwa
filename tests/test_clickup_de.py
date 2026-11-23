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


def connected(ago):
    """Niemiecka lista podłączona do tego CRM `ago` temu (None = jeszcze nigdy — pierwszy odczyt przed nami)."""
    crm.set_setting(crm.CLICKUP_DE_SINCE_KEY, (datetime.utcnow() - ago).isoformat(timespec="seconds") if ago else "")
    crm.db.session.commit()


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
    tasks = [de_task("de-1", "Norbert Ratka", "+4915207690833", "norbert@example.de", now - timedelta(minutes=3),
                     due=now - timedelta(minutes=1)),
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
    connected(timedelta(days=30))  # lista czytana tu od miesiąca; de-2 to zgłoszenie, które doszło z opóźnieniem

    ok, n, msg = crm.sync_clickup_leads()
    assert ok and n == 2, msg
    assert seen and all(tok == "tok-de" for _u, tok in seen)
    q = crm.all_markets(crm.Lead.query)
    fresh = q.filter_by(clickup_task_id="de-1").one()
    old = crm.all_markets(crm.Lead.query).filter_by(clickup_task_id="de-2").one()
    assert fresh.market == "de" and fresh.source == crm.CLICKUP_DE_SOURCE and fresh.currency == "EUR"
    assert fresh.contact_phone == "+4915207690833" and fresh.contact_email == "norbert@example.de"
    assert fresh.contact_name == "Norbert Ratka" and "Ihre Antwort: Firmenfeier" in fresh.notes
    assert not fresh.clickup_pending and not old.clickup_pending, "wszystkie od razu na tablicy"
    assert fresh.event_date is None, "termin taska = dzień zgłoszenia to nie data wydarzenia"
    assert old.event_date.date() == datetime(2027, 3, 14).date()
    assert old.created_at >= now and "Zgłoszenie z ClickUp z" in old.notes, "zaległe: w CRM od chwili importu"
    # wszystkie (także zaległe) w oknie „obsłuż dziś”
    assert fresh in crm.unhandled_leads("de") and old in crm.unhandled_leads("de")
    new_task = de_task("de-9", "Kevin Schaper", "+491721682442", "kevin@example.de", datetime.utcnow() + timedelta(seconds=5))
    tasks.append(new_task)
    ok, n, _ = crm.sync_clickup_leads()
    new = crm.all_markets(crm.Lead.query).filter_by(clickup_task_id="de-9").one()
    assert ok and n == 1 and new in crm.unhandled_leads("de")
    # kolejna synchronizacja — bez duplikatów
    ok, n, _ = crm.sync_clickup_leads()
    assert ok and n == 0
    assert crm.all_markets(crm.Lead.query).filter(crm.Lead.clickup_task_id.in_(["de-1", "de-2", "de-9"])).count() == 3


def test_first_direct_read_does_not_flood_crm_with_old_tasks(app, monkeypatch):
    """Pierwszy odczyt niemieckiej listy wprost (wcześniej zgłoszenia szły przez CRM PL): stare zgłoszenia, których
    tu nie ma, nie stają się leadami ani notatkami; świeże (ostatnia godzina i późniejsze) wchodzą normalnie."""
    now = datetime.utcnow().replace(microsecond=0)
    moved = crm.Lead(company="Przeniesiony z PL", stage="Oferta", assigned_to=1, clickup_task_id="hist-1", market="de",
                     contact_email="stary@beispiel.de", created_at=now - timedelta(days=40))
    crm.db.session.add(moved)
    crm.db.session.commit()
    tasks = [de_task("hist-1", "Przeniesiony z PL", "+4915100000001", "stary@beispiel.de", now - timedelta(days=40)),
             de_task("hist-2", "Stary Usunięty", "+4915100000002", "usuniety@beispiel.de", now - timedelta(days=25)),
             de_task("hist-3", "Stary Sprzed Tygodnia", "+4915100000003", "tydzien@beispiel.de", now - timedelta(days=7)),
             de_task("hist-4", "Świeży Sprzed Kwadransa", "+4915100000004", "swiezy@beispiel.de", now - timedelta(minutes=20))]
    monkeypatch.setenv("CLICKUP_LIST_ID_DE", "DE-LIST")
    monkeypatch.setenv("CLICKUP_API_TOKEN_DE", "tok-de")
    monkeypatch.delenv("CLICKUP_LIST_ID", raising=False)
    monkeypatch.setattr(crm.requests, "get", lambda url, headers=None, params=None, timeout=None: Resp(
        {"tasks": tasks if (params or {}).get("page", 0) == 0 else [], "last_page": True}))
    connected(None)
    notes_before = crm.Activity.query.filter_by(lead_id=moved.id).count()

    ok, n, msg = crm.sync_clickup_de_leads()
    assert ok and n == 1, msg
    assert "zgłoszeń na liście: 4" in msg and "w tym 2 sprzed podłączenia" in msg
    by_task = lambda t: crm.all_markets(crm.Lead.query.filter_by(clickup_task_id=t)).first()  # noqa: E731
    assert by_task("hist-2") is None and by_task("hist-3") is None, "stare zgłoszenia nie zalewają CRM"
    assert by_task("hist-4") is not None and by_task("hist-4").market == "de", "z ostatniej godziny — wchodzi"
    assert crm.Activity.query.filter_by(lead_id=moved.id).count() == notes_before, "przeniesiony wcześniej lead bez zmian"
    since = crm.clickup_de_since()
    assert now - timedelta(minutes=crm.CLICKUP_DE_SINCE_MARGIN_MIN + 1) <= since <= now - timedelta(minutes=59)

    # granica zostaje — następne zgłoszenie wchodzi, stare dalej nie, nawet jeśli import miał przerwę
    tasks.append(de_task("hist-5", "Nowy Po Podłączeniu", "+4915100000005", "nowy@beispiel.de", datetime.utcnow()))
    ok, n, msg = crm.sync_clickup_de_leads()
    assert ok and n == 1 and by_task("hist-5") is not None and by_task("hist-2") is None, msg
    assert crm.clickup_de_since() == since


def test_broken_config_does_not_start_the_clock(app, monkeypatch):
    """Lista bez klucza (albo ClickUp nie odpowiada): moment podłączenia NIE jest zapisywany — liczy się od pierwszego
    udanego odczytu, inaczej zgłoszenia z czasu awarii konfiguracji zostałyby uznane za „stare”."""
    monkeypatch.setenv("CLICKUP_LIST_ID_DE", "DE-LIST")
    monkeypatch.delenv("CLICKUP_API_TOKEN_DE", raising=False)
    monkeypatch.delenv("CLICKUP_API_TOKEN", raising=False)
    connected(None)
    ok, n, msg = crm.sync_clickup_de_leads()
    assert not ok and "niepodłączona" in msg and crm.get_setting(crm.CLICKUP_DE_SINCE_KEY) == ""


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


def test_german_list_found_by_key_alone(app, monkeypatch):
    """Sam klucz CLICKUP_API_TOKEN_DE (bez ID listy): CRM LT/DE sam znajduje niemiecką listę, jak CRM PL.
    W zapytaniu (przycisk) bez szukania — tylko zapamiętana lista."""
    now = datetime.utcnow().replace(microsecond=0)
    tasks = [de_task(f"auto-{i}", f"Kunde {i}", f"+49152076908{i:02d}", f"k{i}@firma{i}.de", now - timedelta(minutes=2))
             for i in range(3)]
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(url)
        assert headers.get("Authorization") == "tok-only"
        if url.endswith("/team"):
            return Resp({"teams": [{"id": "T"}]})
        if url.endswith("/team/T/space"):
            return Resp({"spaces": [{"id": "S", "name": "Meta"}]})
        if url.endswith("/space/S/folder"):
            return Resp({"folders": []})
        if url.endswith("/space/S/list"):
            return Resp({"lists": [{"id": "PL-LISTE", "name": "Polska"}, {"id": "DE-AUTO", "name": "Leads Deutschland"}]})
        if "/list/PL-LISTE/task" in url:
            return Resp({"tasks": [{"id": "pl-1", "name": "Jan", "text_content": "tel 600100200", "custom_fields": []}],
                         "last_page": True})
        if "/list/DE-AUTO/task" in url:
            return Resp({"tasks": tasks if (params or {}).get("page", 0) == 0 else [], "last_page": True})
        raise AssertionError(url)

    monkeypatch.delenv("CLICKUP_LIST_ID_DE", raising=False)
    monkeypatch.delenv("CLICKUP_LIST_ID", raising=False)
    monkeypatch.setenv("CLICKUP_API_TOKEN_DE", "tok-only")
    monkeypatch.setattr(crm.time, "sleep", lambda s: None)
    monkeypatch.setattr(crm.requests, "get", fake_get)
    crm.set_setting(crm.CLICKUP_DE_LISTS_KEY, "")
    crm.db.session.commit()
    connected(None)
    assert crm.clickup_de_configured()
    with app.test_request_context():
        assert crm.clickup_de_config() == ([], "tok-only") and not calls, "w zapytaniu bez szukania list"
    ok, n, msg = crm.sync_clickup_de_leads()
    assert ok and n == 3, msg
    leads = crm.all_markets(crm.Lead.query.filter(crm.Lead.clickup_task_id.like("auto-%"))).all()
    assert len(leads) == 3 and all(l.market == "de" and l.source == crm.CLICKUP_DE_SOURCE for l in leads)
    with app.test_request_context():
        assert crm.clickup_de_config() == (["DE-AUTO"], "tok-only"), "zapamiętana lista działa też z przycisku"
    assert "clickup_de" in crm.health_jobs()


def test_pl_move_skips_lead_already_imported_here(app, monkeypatch, tmp_path):
    """Zgłoszenie z ClickUp jest już w CRM LT/DE (lista czytana wprost), a CRM PL też je zaimportował — przy
    przenoszeniu z PL nie powstaje druga kopia; w PL duplikat znika i nie wróci z ClickUp."""
    from sqlalchemy import Column, MetaData, Table, create_engine, insert, select
    import pl_move
    path = tmp_path / "pl.db"
    eng = create_engine(f"sqlite:///{path}")
    md = MetaData()
    for name in ("leads", "stage_history", "lead_event_dates", "activities", "app_settings"):
        Table(name, md, *[Column(c.name, c.type, primary_key=c.primary_key, nullable=c.primary_key is False)
                          for c in crm.db.metadata.tables[name].columns])
    md.create_all(eng)
    here = crm.Lead(company="Kunde Doppelt", stage="Nowy", assigned_to=1, market="de", clickup_task_id="dup-77",
                    contact_phone="+4915112345678", source=crm.CLICKUP_DE_SOURCE)
    crm.db.session.add(here)
    crm.db.session.commit()
    with eng.begin() as c:
        c.execute(insert(md.tables["leads"]).values(id=501, company="Kunde Doppelt", stage="Nowy", assigned_to=1,
                                                    clickup_task_id="dup-77", contact_phone="+4915112345678",
                                                    source="ClickUp DE", billing_country="DE", country="DE",
                                                    created_at=datetime.utcnow()))
    M = {"Lead": crm.Lead, "LeadEventDate": crm.LeadEventDate, "StageHistory": crm.StageHistory,
         "Activity": crm.Activity}
    moved, errors = pl_move.move_all(eng, crm.db, M, 1, code="de")
    assert not errors and moved == 1
    assert crm.all_markets(crm.Lead.query.filter_by(clickup_task_id="dup-77")).count() == 1, "bez drugiej kopii"
    with eng.connect() as c:
        assert c.execute(select(md.tables["leads"])).first() is None, "duplikat usunięty w PL"
        keys = [r.key for r in c.execute(select(md.tables["app_settings"]))]
    assert "clickup_dup:dup-77" in keys, "PL nie zaimportuje go z ClickUp drugi raz"
