"""Opiekun leada (parts/opiekun.py, ten sam plik w CRM PL i LT/DE): nowy lead bez wskazanej osoby dostaje domyślnego
opiekuna rynku (CRM PL: Jerzy), zadania idą za opiekunem, a jednorazowe porządki przenoszą leady, które import
przypisał na sztywno Oliwierowi, a on nic przy nich nie zrobił."""
from datetime import date, datetime, timedelta

import pytest

import crm

CODE = crm.markets.DEFAULT
OLIWIER, BARTEK, JERZY = 1, 2, 3
MADE = []


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


@pytest.fixture(autouse=True)
def tidy(app):
    yield
    crm.db.session.rollback()
    for lead_id in MADE:
        lead = crm.db.session.get(crm.Lead, lead_id)
        if lead is not None:
            crm.clear_open_tasks(lead)
            lead.stage = "Stracony"
    MADE.clear()
    crm.db.session.commit()


def mk(company, owner, stage="Nowy", **kw):
    lead = crm.Lead(company=company, stage=stage, assigned_to=owner, market=CODE, source="ClickUp", **kw)
    crm.db.session.add(lead)
    crm.db.session.commit()
    MADE.append(lead.id)
    return lead


def task(lead, who, created_by=None, done=False, kind="call", notes="Zadzwonić"):
    a = crm.Activity(lead_id=lead.id, kind=kind, assigned_to=who, created_by=created_by, done=done, notes=notes,
                     due_date=datetime.combine(date.today(), datetime.min.time()),
                     done_at=datetime.utcnow() if done else None)
    crm.db.session.add(a)
    crm.db.session.commit()
    return a


def test_default_owner_comes_from_the_market(app):
    expected = crm.markets.get(CODE)["default_owner"]
    assert crm.default_owner(CODE) == expected
    assert crm.markets.get("pl")["default_owner"] == JERZY, "CRM PL: wszystkie polskie leady domyślnie Jerzy"
    fields = crm._clickup_task_to_lead({"id": "t-own-1", "name": "Firma Bez Osoby", "assignees": [],
                                        "custom_fields": [], "date_created": "1763900000000"})
    assert "assigned_to" not in fields, "bez osoby w ClickUp import nie wybiera nikogo na sztywno"


def test_owner_change_moves_open_tasks_but_not_delegated_ones(app):
    lead = mk("Zmiana Opiekuna", OLIWIER)
    own = task(lead, OLIWIER)
    delegated = task(lead, BARTEK, created_by=JERZY, notes="Przygotuj robota")
    done = task(lead, OLIWIER, done=True, notes="Rozmowa")
    lead.assigned_to = JERZY
    crm.db.session.commit()
    for a in (own, delegated, done):
        crm.db.session.refresh(a)
    assert own.assigned_to == JERZY, "otwarte zadanie poprzedniego opiekuna przechodzi na nowego"
    assert delegated.assigned_to == BARTEK, "zadanie zlecone komuś trzeciemu zostaje"
    assert done.assigned_to == OLIWIER, "historia bez zmian"


def test_one_time_cleanup(app):
    for key in (crm.OWNER_CLEANUP_KEY, crm.OWNER_CLEANUP_KEY + "_lock"):
        row = crm.db.session.get(crm.AppSetting, key)
        if row is not None:
            crm.db.session.delete(row)
    crm.db.session.commit()
    untouched = mk("Nikt Nie Ruszał", OLIWIER)
    # wpis „utworzono leada” w imieniu Oliwiera (dawna skrzynka kontakt@) to nie jest praca przy leadzie
    crm.db.session.add(crm.StageHistory(lead_id=untouched.id, from_stage=None, to_stage="Nowy", changed_by=OLIWIER))
    crm.db.session.commit()
    auto = task(untouched, OLIWIER, notes="📞 Drugi krok: telefon po ofercie")
    worked = mk("Oliwier Prowadzi", OLIWIER, stage="Oferta")
    task(worked, OLIWIER, created_by=OLIWIER, done=True, notes="Rozmowa z klientem")
    worked_open = task(worked, OLIWIER, notes="Telefon w piątek")
    lost = mk("Stracony Z Zadaniem", JERZY, stage="Stracony")
    lost_task = task(lost, OLIWIER)
    other = mk("Zadanie Automatu U Innej Osoby", BARTEK, stage="Oferta")
    stray = task(other, OLIWIER, notes="📞 Zadzwoń")
    report = crm.owner_cleanup_once()
    assert report is not None and crm.owner_cleanup_once() is None, "tylko raz"
    for obj in (untouched, worked, auto, worked_open, other, stray):
        crm.db.session.refresh(obj)
    owner = crm.default_owner(CODE)
    if owner != OLIWIER:  # CRM PL
        assert untouched.assigned_to == owner and auto.assigned_to == owner and untouched.id in report["moved"]
    else:  # CRM LT/DE: domyślny opiekun to i tak Oliwier — nic się nie przenosi
        assert untouched.assigned_to == OLIWIER and not report["moved"]
    assert worked.assigned_to == OLIWIER and worked_open.assigned_to == OLIWIER, "lead prowadzony przez Oliwiera zostaje"
    assert crm.db.session.get(crm.Activity, lost_task.id) is None, "stracony lead bez otwartych zadań"
    assert stray.assigned_to == BARTEK, "zadanie automatu idzie do opiekuna leada"


def test_inbox_lead_goes_to_market_owner_or_inbox_assign_to(app, monkeypatch):
    from types import SimpleNamespace
    calls = iter(["", "Bartek"])

    def inquiry(*_a, **_kw):
        n = len(MADE)
        return {"is_inquiry": True, "company": f"Zapytanie Skrzynka {n}", "contact_email": f"skrzynka{n}@example.com",
                "contact_phone": "", "summary": "Robot na event", "reason": "zapytanie"}

    monkeypatch.setattr(crm, "_ai_forced_tool", inquiry)
    for expected in (crm.default_owner(CODE), BARTEK):
        monkeypatch.setenv("INBOX_ASSIGN_TO", next(calls))
        rec = SimpleNamespace(from_addr="klient@example.com", subject="Zapytanie", body="Robot na event",
                              received_at=datetime.utcnow(), market=CODE, status=None, lead_id=None, reason=None)
        crm.process_inbox_message(rec, force=True)
        crm.db.session.commit()
        assert rec.status == "lead", rec.reason
        MADE.append(rec.lead_id)
        assert crm.db.session.get(crm.Lead, rec.lead_id).assigned_to == expected


def test_new_task_defaults_to_logged_in_person(app):
    lead = mk("Domyślnie Ja", JERZY)
    c = crm.app.test_client()
    if len(crm.markets.MARKETS) > 1:
        c.set_cookie("crm_market", CODE)
    me = crm.get_current_user  # zalogowany w testach = użytkownik domyślny
    due = (date.today() + timedelta(days=1)).isoformat()
    r = c.post(f"/crm/leads/{lead.id}/activities/add", data={"kind": "call", "due_date": due, "notes": "Oddzwonić"})
    assert r.status_code == 302
    a = crm.lead_open_tasks(lead.id).first()
    with crm.app.test_request_context():
        assert a.assigned_to == me(crm.request)["id"]
