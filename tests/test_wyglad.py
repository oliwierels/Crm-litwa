"""Nowy wygląd (wspólny base.html, nagłówek karty leada, „Mój dzień”): strony się renderują, zmiana opiekuna z nagłówka
przenosi zadania i zostawia ślad, a CRM nie zależy od zewnętrznych CDN (ikony, czcionka i kalendarz są w static/)."""
import os
from datetime import date, datetime

import pytest

import crm

CODE = crm.markets.DEFAULT
OLIWIER, BARTEK, JERZY = 1, 2, 3
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


@pytest.fixture
def client(app):
    c = crm.app.test_client()
    c.set_cookie("crm_user", str(BARTEK))
    if len(crm.markets.MARKETS) > 1:
        c.set_cookie("crm_market", CODE)
    return c


@pytest.fixture
def lead(app):
    lead = crm.Lead(company="Wygląd Test Sp. z o.o.", stage="Oferta", assigned_to=OLIWIER, market=CODE,
                    contact_name="Anna Test", contact_phone="+48 600 000 111", contact_email="anna@example.com",
                    value_pln=4900, location="Warszawa")
    crm.db.session.add(lead)
    crm.db.session.commit()
    crm.db.session.add(crm.Activity(lead_id=lead.id, kind="call", assigned_to=OLIWIER, created_by=OLIWIER,
                                    due_date=datetime.combine(date.today(), datetime.min.time()), notes="Telefon"))
    crm.db.session.commit()
    yield lead
    crm.db.session.rollback()
    crm.clear_open_tasks(lead)
    lead.stage = "Stracony"
    crm.db.session.commit()


def test_lead_page_has_header_with_key_facts(client, lead):
    html = client.get(f"/crm/leads/{lead.id}").get_data(as_text=True)
    assert 'class="ld-head panel"' in html and 'class="ld-grid"' in html
    for text in ("Opiekun", "Następny krok", "Wydarzenie", "+48 600 000 111", "anna@example.com", "Stracony"):
        assert text in html, text
    assert f'action="/crm/leads/{lead.id}/opiekun"' in html
    assert html.count('class="ld-step') == len(crm.STAGES), "etapy jako jeden pasek (Stracony osobno)"


def test_owner_change_from_header_moves_tasks_and_leaves_note(client, lead):
    r = client.post(f"/crm/leads/{lead.id}/opiekun", data={"assigned_to": str(JERZY)})
    assert r.status_code == 302
    crm.db.session.expire_all()
    lead = crm.db.session.get(crm.Lead, lead.id)
    assert lead.assigned_to == JERZY
    assert all(a.assigned_to == JERZY for a in crm.pending_activities(lead)), "otwarte zadania idą za opiekunem"
    notes = [a.notes for a in lead.activities if a.kind == "note"]
    assert any(n.startswith("🔁 Opiekun: Oliwier → Jerzy") for n in notes)
    client.post(f"/crm/leads/{lead.id}/opiekun", data={"assigned_to": "999"})
    crm.db.session.expire_all()
    assert crm.db.session.get(crm.Lead, lead.id).assigned_to == JERZY, "nieznana osoba — bez zmiany"


def test_shell_pages_render_new_layout(client, lead):
    for path in ("/crm/", "/crm/leads?view=kanban", "/crm/leads?view=list", "/crm/calendar"):
        html = client.get(path).get_data(as_text=True)
        assert 'class="quick-new"' in html and 'class="me"' in html, path
        assert "cdn.jsdelivr.net" not in html and "unpkg.com" not in html, f"{path}: zewnętrzny CDN"
    assert "Mój dzień" in client.get("/crm/").get_data(as_text=True)


def test_bundled_assets_exist():
    for rel in ("static/icons/bootstrap-icons.min.css", "static/icons/fonts/bootstrap-icons.woff2",
                "static/fonts/inter/inter-latin-wght-normal.woff2", "static/fonts/inter/inter-latin-ext-wght-normal.woff2",
                "static/vendor/fullcalendar/index.global.min.js", "static/vendor/fullcalendar/locales/pl.global.min.js"):
        assert os.path.getsize(os.path.join(ROOT, rel)) > 200, rel
