"""Kalendarz (parts/kalendarz.py, ten sam plik w CRM PL i LT/DE): domyślnie „Zlecenia” — same wygrane / zrealizowane
leady z dniami wynajmu, bez zadań; „Wszystko” — zadania i wszystkie terminy. Kolory jako klasy (zmienne motywu)."""
from datetime import date, datetime, timedelta

import pytest

import crm

CODE = crm.markets.DEFAULT
MADE = []


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


@pytest.fixture
def client(app):
    c = crm.app.test_client()
    c.set_cookie("crm_user", "2")
    if len(crm.markets.MARKETS) > 1:
        c.set_cookie("crm_market", CODE)
    return c


@pytest.fixture(autouse=True)
def cleanup(app):
    yield
    crm.db.session.rollback()
    for lead_id in MADE:
        crm.Activity.query.filter(crm.Activity.lead_id == lead_id).delete()
        lead = crm.db.session.get(crm.Lead, lead_id)
        if lead is not None:
            lead.stage = "Stracony"
    MADE.clear()
    crm.db.session.commit()


def mk(company, stage, day=None, **kw):
    lead = crm.Lead(company=company, stage=stage, assigned_to=1, market=CODE, currency=crm.M(CODE)["currency"], **kw)
    crm.db.session.add(lead)
    crm.db.session.commit()
    MADE.append(lead.id)
    if day:
        crm.set_lead_event_days(lead, [day], {})
        crm.db.session.commit()
    return lead


def test_orders_view_shows_only_orders(app, client):
    day = date.today() + timedelta(days=10)
    won = mk("Kal Zlecenie", "Wygrany", day, value_pln=6000, contract_signed=True, location="Kraków, hala A")
    unsigned = mk("Kal Bez Umowy", "Wygrany", day, value_pln=5000)
    offer = mk("Kal Oferta", "Oferta", day)
    task = crm.Activity(lead_id=offer.id, kind="call", assigned_to=1, notes="zadzwonić",
                        due_date=datetime.combine(day, datetime.min.time()))
    crm.db.session.add(task)
    crm.db.session.commit()
    rng = {"start": (day - timedelta(days=3)).isoformat() + "T00:00:00+01:00",
           "end": (day + timedelta(days=3)).isoformat() + "T00:00:00+01:00"}

    orders = client.get("/crm/api/activities", query_string=dict(rng, widok="zlecenia")).get_json()
    ids = {e["extendedProps"].get("lead_id") for e in orders}
    assert won.id in ids and unsigned.id in ids and offer.id not in ids, "same zlecenia, bez terminów z ofert"
    assert all(e["extendedProps"]["event_type"] == "lead_event" for e in orders), "bez telefonów, maili i zadań"
    w = next(e for e in orders if e["extendedProps"]["lead_id"] == won.id)
    assert "ev-order" in w["classNames"] and "Kraków" in w["title"] and "backgroundColor" not in w
    u = next(e for e in orders if e["extendedProps"]["lead_id"] == unsigned.id)
    assert "ev-nocontract" in u["classNames"] and u["title"].startswith("📝")

    everything = client.get("/crm/api/activities", query_string=dict(rng, widok="wszystko")).get_json()
    kinds = {e["extendedProps"]["event_type"] for e in everything}
    assert kinds == {"lead_event", "activity"}
    t = next(e for e in everything if e["id"] == f"act-{task.id}")
    assert "ev-task" in t["classNames"] and any(c.startswith("ev-") and c != "ev-task" for c in t["classNames"])
    assert any("ev-tentative" in e["classNames"] for e in everything if e["extendedProps"].get("lead_id") == offer.id)
    legacy = client.get("/crm/api/activities", query_string=rng).get_json()
    assert len(legacy) == len(everything), "bez parametru — jak dawniej (wszystko)"


def test_calendar_page_default_orders_and_undated_list(app, client):
    mk("Kal Termin Później", "Wygrany", value_pln=2300)
    html = client.get("/crm/calendar").get_data(as_text=True)
    assert 'const CAL_VIEW = "zlecenia"' in html and "Zlecenia z terminem do ustalenia" in html
    assert "Kal Termin Później" in html
    html = client.get("/crm/calendar?widok=wszystko").get_data(as_text=True)
    assert 'const CAL_VIEW = "wszystko"' in html and "Przeterminowane" in html
    assert "Zlecenia z terminem do ustalenia" not in html
    tpl = open("templates/crm/calendar.html", encoding="utf-8").read()
    assert "#ef4444" not in tpl and "#db2777" not in tpl, "kolory tylko ze zmiennych motywu"
