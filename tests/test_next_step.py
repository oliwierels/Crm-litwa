"""Następny krok ustawia człowiek (parts/nastepny_krok.py, ten sam plik w CRM PL i LT/DE): CRM nie dodaje zadań sam,
wygrany lead traci otwarte zadania z lejka (dalej Customer Journey), a karta aktywnego leada bez zadania prosi
o następny krok."""
from datetime import date, datetime, timedelta

import pytest

import crm

LT = "lt" in crm.markets.MARKETS


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


MADE = []


@pytest.fixture(autouse=True)
def defaults(app, monkeypatch):
    monkeypatch.delenv("AUTO_TASKS", raising=False)
    monkeypatch.setattr(crm, "_ai_client", lambda: None)
    yield
    # sprzątanie: leady z tych testów nie mogą zostać aktywne bez kroku (inne testy liczą np. audyt ofert)
    crm.db.session.rollback()
    for lead_id in MADE:
        lead = crm.db.session.get(crm.Lead, lead_id)
        if lead is not None:
            crm.clear_open_tasks(lead)
            lead.stage = "Stracony"
    MADE.clear()
    crm.db.session.commit()


def mk(company, stage="Oferta", **kw):
    if LT:
        kw.setdefault("market", "lt")
    lead = crm.Lead(company=company, stage=stage, assigned_to=1, **kw)
    crm.db.session.add(lead)
    crm.db.session.commit()
    MADE.append(lead.id)
    return lead


def task(lead, done=False, kind="call", notes="Zadzwonić", **kw):
    a = crm.Activity(lead_id=lead.id, kind=kind, assigned_to=1, done=done, notes=notes,
                     done_at=datetime.utcnow() if done else None,
                     due_date=datetime.combine(date.today(), datetime.min.time()), **kw)
    crm.db.session.add(a)
    crm.db.session.commit()
    return a


def open_tasks(lead):
    return crm.lead_open_tasks(lead.id).all()


def client():
    c = crm.app.test_client()
    if LT:
        c.set_cookie("crm_market", "lt")
    return c


def test_crm_adds_no_tasks_by_itself(app):
    assert not crm.auto_tasks_on()
    lead = mk("Bez Automatów", stage="Nowy", contact_phone="600100200")
    assert crm.record_stage_change(lead, "Oferta", 1)
    crm.db.session.commit()
    assert open_tasks(lead) == [], "oferta poszła — telefonu po ofercie CRM już nie dodaje"
    assert crm.ensure_followup_call(lead) is None and crm.backfill_next_steps() == 0
    for _ in range(3):
        task(lead, done=True, outcome="nie_odebral")
    assert crm.check_contact_limit(lead) is None and open_tasks(lead) == [], "bez zadania „Zdecyduj”"


def test_switch_brings_old_automation_back(app, monkeypatch):
    monkeypatch.setenv("AUTO_TASKS", "1")
    lead = mk("Z Automatami", stage="Nowy", contact_phone="600100201")
    assert crm.record_stage_change(lead, "Oferta", 1)
    crm.db.session.commit()
    assert [a.kind for a in open_tasks(lead)] == ["call"]


def test_won_lead_loses_open_tasks_keeps_history(app):
    lead = mk("Wygrana Firma")
    planned = task(lead, notes="Telefon po ofercie")
    crm.db.session.add(crm.ActivityComment(activity_id=planned.id, author_id=1, text="📵 Nie odebrał"))
    done = task(lead, done=True, outcome="odebral", notes="Rozmowa — akceptuje ofertę")
    note = task(lead, done=True, kind="note", notes="Notatka")
    crm.db.session.commit()
    assert crm.record_stage_change(lead, "Wygrany", 1)
    crm.db.session.commit()
    assert open_tasks(lead) == [], "otwarte zadania z lejka znikają — dalej Customer Journey"
    assert crm.db.session.get(crm.Activity, done.id) and crm.db.session.get(crm.Activity, note.id), "historia zostaje"
    later = task(lead, notes="Zawieźć roll-up na event", created_by=1)  # człowiek dodał zadanie już po wygranej
    assert [a.id for a in open_tasks(lead)] == [later.id]
    assert crm.record_stage_change(lead, "Zrealizowany", None)
    crm.db.session.commit()
    assert open_tasks(lead) == [], "zrealizowany lead nie ma otwartych zadań (np. telefonu za miesiąc)"
    kept = crm.db.session.get(crm.Activity, later.id)
    assert kept is not None and kept.kind == "note" and kept.notes.startswith(crm.CLEAN_PREFIX), \
        "zadanie od człowieka zostaje w historii jako „nieaktualne”, nie znika"
    assert "Zawieźć roll-up" in kept.notes


def test_one_time_cleanup_of_already_won_leads(app):
    for key in (crm.WON_TASKS_CLEANUP_KEY, crm.WON_TASKS_CLEANUP_KEY + "_lock"):
        row = crm.db.session.get(crm.AppSetting, key)
        if row is not None:
            crm.db.session.delete(row)
    crm.db.session.commit()
    won, done_event, offer = mk("Stara Wygrana", stage="Wygrany"), mk("Zrealizowana", stage="Zrealizowany"), mk("Na Ofercie")
    for lead in (won, won, done_event, offer):
        task(lead)
    assert crm.cleanup_won_tasks_once() >= 3
    assert open_tasks(won) == [] and open_tasks(done_event) == []
    assert len(open_tasks(offer)) == 1, "leady na ofercie zostają bez zmian"
    task(won, notes="Dodane później")
    assert crm.cleanup_won_tasks_once() is None and len(open_tasks(won)) == 1, "tylko raz"


def test_lead_page_asks_a_person_for_the_next_step(app):
    lead = mk("Bez Kroku", stage="Nowy", contact_phone="600 700 800", contact_name="Jan Testowy")
    c = client()
    html = c.get(f"/crm/leads/{lead.id}").get_data(as_text=True)
    assert "Pierwszy kontakt" in html and 'href="tel:600700800"' in html, "nowy lead: numer na wierzchu"
    assert "Ustal następny krok" not in html, "najpierw pierwszy telefon — okno kroku nie zasłania numeru"
    assert c.post(f"/crm/leads/{lead.id}/obsluzony", data={"how": "telefon", "note": "chcą wycenę"}).status_code == 302
    html = c.get(f"/crm/leads/{lead.id}").get_data(as_text=True)
    assert "Pierwszy kontakt" not in html
    assert "Ustal następny krok" in html and f"/crm/leads/{lead.id}/activities/add" in html
    assert "tel:600700800" in html and "Najpierw zadzwonię" in html, "w oknie kroku też numer i „później”"
    due = (date.today() + timedelta(days=2)).isoformat()
    r = c.post(f"/crm/leads/{lead.id}/activities/add",
               data={"kind": "call", "due_date": due, "notes": "Zadzwonić o termin", "assigned_to": "1"})
    assert r.status_code == 302
    a = open_tasks(lead)[0]
    assert a.kind == "call" and a.created_by is not None and a.due_date.date().isoformat() == due
    assert "Ustal następny krok" not in c.get(f"/crm/leads/{lead.id}").get_data(as_text=True)
    for other in (mk("Już Wygrany", stage="Wygrany"), mk("Stracony", stage="Stracony"),
                  mk("W Poczekalni", stage="Nowy", clickup_pending=True)):
        assert not crm.lead_needs_next_step(other)
        assert "Ustal następny krok" not in c.get(f"/crm/leads/{other.id}").get_data(as_text=True)


def test_new_leads_to_call_are_urgent_for_everyone(app):
    """„🔥 Pilne — oddzwoń do nowych leadów” w Moim dniu i pasek alarmu także u osób, które nie są opiekunem
    (okno blokuje tylko opiekuna)."""
    lead = mk("Pilny Nowy Lead", stage="Nowy", contact_phone="600 111 222", contact_name="Anna Pilna")
    lead.assigned_to = 3  # Jerzy — opiekun nowych leadów
    crm.db.session.commit()
    c = client()
    c.set_cookie("crm_user", "2")  # Bartek
    html = c.get("/crm/").get_data(as_text=True)
    assert "Pilne — oddzwoń do nowych leadów" in html and "Pilny Nowy Lead" in html and "tel:600111222" in html
    assert "czeka na telefon" in html or "czekają na telefon" in html, "pasek alarmu z cudzymi nowymi leadami"
    i = html.find('id="al-overlay"')
    assert i > 0 and "hidden" in html[i:i + 80], "cudze leady nie blokują okna"
    st = crm.alerts_state(user_id=2)
    assert any(r["id"] == lead.id and r["owner"] == "Jerzy" for r in st["team_leads"])
    assert all(r["id"] != lead.id for r in st["leads"])
    rows = crm.first_call_leads(3)
    assert rows and rows[0]["mine"] and any(r["id"] == lead.id for r in rows)
    crm.mark_lead_handled(lead, 3, "telefon")
    crm.db.session.commit()
    assert all(r["id"] != lead.id for r in crm.first_call_leads(2)), "po telefonie znika z pilnych"
