"""Filtr „Źródło” (parts/wyglad.py, wspólny dla CRM PL i LT/DE): ✦ Z poczty (formularz, mail — złota ramka),
ClickUp (reklamy) i inne — w Leadach (tablica, lista, eksport) i na liście „Do obdzwonienia” (CRM PL)."""
from datetime import datetime

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
        lead = crm.db.session.get(crm.Lead, lead_id)
        if lead is not None:
            lead.stage = "Stracony"
    MADE.clear()
    crm.db.session.commit()


def mk(company, source, **kw):
    lead = crm.Lead(company=company, stage="Nowy", assigned_to=1, market=CODE, source=source,
                    contact_phone=kw.pop("contact_phone", "600 000 001"), created_at=datetime.utcnow(),
                    handled_at=datetime.utcnow(), **kw)  # obsłużony — nie wchodzi do paska pilnych nowych leadów
    crm.db.session.add(lead)
    crm.db.session.commit()
    MADE.append(lead.id)
    return lead


def test_lead_origin():
    L = crm.Lead
    assert crm.lead_origin(L(source="Formularz 33bots.pl")) == "poczta"
    assert crm.lead_origin(L(source="Mail kontakt@")) == "poczta"
    assert crm.lead_origin(L(source="ClickUp")) == "clickup"
    assert crm.lead_origin(L(source="Meta Ads", clickup_task_id="cu-1")) == "clickup"
    assert crm.lead_origin(L(source="SEO")) == "inne" and crm.lead_origin(L(source=None)) == "inne"
    leads = [L(source="Formularz"), L(source="ClickUp"), L(source="SEO")]
    assert crm.origin_counts(leads) == {"poczta": 1, "clickup": 1, "inne": 1}
    assert len(crm.filter_by_origin(leads, "")) == 3 and len(crm.filter_by_origin(leads, "xyz")) == 3


def test_leads_page_filters_by_origin(client):
    gold = mk("Źródło Złoty Formularz", "Formularz 33bots.pl")
    cu = mk("Źródło Z ClickUp", "ClickUp")
    def shown(html, lead):  # karta na tablicy albo wiersz listy (nazwy są też w okienku „Zadanie” — tam nie patrzymy)
        return f'data-lead="{lead.id}"' in html or f"location.href='/crm/leads/{lead.id}'" in html
    for view in ("kanban", "list"):
        html = client.get(f"/crm/leads?view={view}&zrodlo=poczta").get_data(as_text=True)
        assert shown(html, gold) and not shown(html, cu), view
        assert 'value="poczta" selected' in html
    html = client.get("/crm/leads?view=list").get_data(as_text=True)
    assert shown(html, gold) and shown(html, cu) and 'value="poczta"' in html
    csv = client.get("/crm/leads/export.csv?zrodlo=clickup").get_data(as_text=True)
    assert "Źródło Z ClickUp" in csv and "Źródło Złoty Formularz" not in csv


def test_call_list_filters_gold_leads(client):
    if not crm.has_route("crm_clickup_uncontacted"):
        pytest.skip("lista „Do obdzwonienia” jest tylko w CRM PL")
    gold = mk("Dzwonić Złoty Mail", "Mail kontakt@")
    ad = mk("Dzwonić Z Reklamy", "ClickUp")
    html = client.get("/crm/do-obdzwonienia?zrodlo=poczta").get_data(as_text=True)
    assert f'id="lead-{gold.id}"' in html and f'id="lead-{ad.id}"' not in html
    assert "cl-direct" in html and 'name="zrodlo" value="poczta"' in html, "złota karta, filtr zostaje po kliknięciu wyniku"
    html = client.get("/crm/do-obdzwonienia").get_data(as_text=True)
    assert f'id="lead-{gold.id}"' in html and f'id="lead-{ad.id}"' in html and "Z poczty" in html
