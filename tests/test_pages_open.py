"""Siatka bezpieczeństwa porządków: każda strona CRM (GET bez parametrów) i karta leada w różnych etapach się
otwiera — bez błędu serwera, bez wysyłki i bez internetu (ClickUp, DNS, ElevenLabs „padły”). Ten sam plik w CRM PL
i LT/DE; w LT/DE każda strona na obu rynkach."""
from datetime import date, timedelta

import pytest
import requests

import crm

LT = "lt" in crm.markets.MARKETS
MARKETS = ["lt", "de"] if LT else [None]
# te adresy przy samym wejściu łączą się z ClickUp albo wysyłają raport — nie klikamy ich w teście
SKIP = {"/crm/integrations/clickup/debug", "/crm/integrations/clickup/sync", "/crm/report/test", "/crm/logout"}


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


@pytest.fixture
def offline(monkeypatch):
    def no_net(*a, **k):
        raise requests.ConnectionError("test: bez internetu")
    monkeypatch.setattr(requests, "get", no_net)
    monkeypatch.setattr(requests, "post", no_net)
    monkeypatch.setattr(requests, "request", no_net)
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: no_net())
    monkeypatch.setattr(crm, "_smtp_send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("bez wysyłki")))
    monkeypatch.setattr(crm, "_ai_client", lambda: None)


def client(market):
    c = crm.app.test_client()
    if market:
        c.set_cookie("crm_market", market)
    return c


def leads(market):
    mk = {"market": market} if market else {}
    out = []
    for i, stage in enumerate(("Nowy", "Oferta", "Wygrany", "Stracony")):
        lead = crm.Lead(company=f"Strona {stage} {market or 'pl'}", stage=stage, assigned_to=1, contact_email=f"s{i}@strony.pl",
                        contact_phone="600100200", location="Warszawa", currency="EUR" if market else "PLN",
                        value_pln=5000, **mk)
        crm.db.session.add(lead)
        crm.db.session.commit()
        crm.set_lead_event_days(lead, [date.today() + timedelta(days=10)], {})
        crm.db.session.commit()
        if stage == "Wygrany":
            crm.ensure_journey(lead)
            crm.db.session.commit()
        out.append(lead)
    return out


def test_every_page_opens(app, offline):
    rules = sorted(r.rule for r in crm.app.url_map.iter_rules()
                   if "GET" in r.methods and not r.arguments and r.rule not in SKIP and not r.rule.startswith("/static"))
    assert len(rules) > 30
    for market in MARKETS:
        made = leads(market)
        c = client(market)
        bad = []
        for rule in rules:
            r = c.get(rule)
            if r.status_code >= 500:
                bad.append(f"{rule} → {r.status_code}")
        for lead in made:
            r = c.get(f"/crm/leads/{lead.id}")
            if r.status_code != 200:
                bad.append(f"/crm/leads/{lead.id} ({lead.stage}) → {r.status_code}")
        assert not bad, f"rynek {market or 'pl'}: " + "; ".join(bad)
