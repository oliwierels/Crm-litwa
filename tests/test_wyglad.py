"""Nowy wygląd (wspólny base.html, nagłówek karty leada, „Mój dzień”): strony się renderują, zmiana opiekuna z nagłówka
przenosi zadania i zostawia ślad, a CRM nie zależy od zewnętrznych CDN (ikony, czcionka i kalendarz są w static/)."""
import os
import re
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


def test_search_api_finds_leads_by_company_and_phone(client, lead):
    data = client.get("/crm/api/szukaj?q=Wygląd Test").get_json()
    assert any(l["id"] == lead.id for l in data["leads"])
    data = client.get("/crm/api/szukaj?q=600000111").get_json()
    assert any(l["id"] == lead.id for l in data["leads"]), "po numerze bez spacji"
    assert client.get("/crm/api/szukaj?q=a").get_json() == {"leads": []}


def test_page_views_are_counted_per_page_person_and_day(client, lead):
    def views():
        return crm.db.session.query(crm.db.func.sum(crm.PageView.views)).filter(
            crm.PageView.endpoint == "crm_lead_detail", crm.PageView.user_id == BARTEK).scalar() or 0
    before = views()
    client.get(f"/crm/leads/{lead.id}")
    client.get(f"/crm/leads/{lead.id}")
    client.get("/crm/api/szukaj?q=xx")  # API się nie liczy
    assert views() == before + 2
    usage = dict((ep, n) for ep, n, _p, _l in crm.page_usage(30))
    assert usage.get("crm_lead_detail", 0) >= 2 and "crm_dashboard" in usage, "strony bez wejść też są na liście"


def test_stage_api_returns_lead_for_win_celebration(client, lead):
    r = client.post(f"/crm/api/leads/{lead.id}/stage", json={"stage": "Nowy"})
    d = r.get_json()
    assert d["ok"] and d["changed"] and d["lead"]["company"] == lead.company and d["lead"]["value"] == 4900


def test_menu_sections_tabs_and_bell(client, lead):
    html = client.get("/crm/leads?view=list").get_data(as_text=True)
    assert 'class="subnav"' in html and ">Tablica<" in html and ">Duplikaty<" in html, "Leady: zakładki sekcji"
    assert 'class="bell-wrap"' in html and 'id="palBg"' in html and "crmCelebrate" in html
    assert html.count('class="nav-link') <= 12, "płaskie menu: jedna pozycja na sekcję"


def test_direct_leads_from_mail_and_form_stand_out(client):
    """Leady, które same napisały (formularz na stronie, mail na kontakt@), są wyróżnione na tablicy, liście
    i w nagłówku — leady z ClickUp nie."""
    dom = crm.M(CODE).get("domain") or "33bots.pl"
    assert crm.is_direct_lead(f"Formularz {dom}") and crm.is_direct_lead(f"Mail kontakt@{dom}") and crm.is_direct_lead("Mail")
    assert not crm.is_direct_lead("ClickUp") and not crm.is_direct_lead("ClickUp DE") and not crm.is_direct_lead("")
    assert not crm.is_direct_lead("Polecenie") and crm.direct_label(f"Formularz {dom}") == "Formularz"
    made = []
    for company, source in (("Z Formularza Sp. z o.o.", f"Formularz {dom}"), ("Z Reklamy Sp. z o.o.", "ClickUp")):
        lead = crm.Lead(company=company, stage="Nowy", assigned_to=BARTEK, market=CODE, source=source)
        crm.db.session.add(lead)
        made.append(lead)
    crm.db.session.commit()
    try:
        board = client.get("/crm/leads?view=kanban").get_data(as_text=True)
        cls = {int(i): c for c, i in re.findall(r'<div class="k-card([^"]*)"[^>]*data-lead="(\d+)"', board)}
        assert "k-direct" in cls[made[0].id], "karta leada z formularza ma złotą ramkę"
        assert "k-direct" not in cls[made[1].id], "lead z ClickUp bez wyróżnienia"
        assert 'name="zrodlo"' in board and '<option value="poczta"' in board, "filtr „✦ Z poczty” (parts/wyglad.py)"
        listing = client.get("/crm/leads?view=list").get_data(as_text=True)
        assert "row-direct" in listing
        header = client.get(f"/crm/leads/{made[0].id}").get_data(as_text=True)
        assert "direct-tag-lg" in header
    finally:
        for lead in made:
            lead.stage = "Stracony"
        crm.db.session.commit()


def test_multi_currency_tile_does_not_overflow(app):
    parts = crm.money_parts({"PLN": 503715, "EUR": 16300})
    assert sorted(parts) == ["16 300 EUR", "503 715 PLN"] and parts[0].endswith(crm.M(CODE)["currency"]), \
        "kafelek: waluta rynku duża, druga małą linią"
    assert crm.money_parts({}) == [f"0 {crm.M()['currency']}"]
