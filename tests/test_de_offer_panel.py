"""CRM DE: nadawca .de w podglądzie oferty, ręczna cena / czas w automatycznej ofercie, bez WhatsApp/Viber."""
from datetime import date

import pytest

import crm


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


@pytest.fixture()
def boxes(monkeypatch):
    def fake(code=None):
        allb = [{"login": "o.latek@33bots.de", "pwd": "x", "key": "OLDE", "label": "o.latek@", "host": "h", "port": 993,
                 "user_id": 1, "market": "de"},
                {"login": "o.latek@33bots.lt", "pwd": "x", "key": "OLLT", "label": "o.latek@", "host": "h", "port": 993,
                 "user_id": 1, "market": "lt"}]
        return [b for b in allb if code in (None, b["market"])]
    monkeypatch.setattr(crm, "mail_boxes", fake)


def mk(company, market, **k):
    lead = crm.Lead(company=company, stage="Nowy", assigned_to=1, currency="EUR", market=market, **k)
    crm.db.session.add(lead)
    crm.db.session.commit()
    crm.set_lead_event_days(lead, [date(2027, 1, 23)], {})
    crm.db.session.commit()
    return lead


def test_offer_review_shows_market_mailbox(app, boxes):
    lead = mk("Adams Russian Night", "de", contact_email="mail@adamsrussiannight.de")
    offer = crm.SentOffer(lead_id=lead.id, filename="o.pdf", pdf=b"%PDF", to_addr="mail@adamsrussiannight.de",
                          subject="Angebot", body="Hallo", price="2 200 €", sender_id=1)
    crm.db.session.add(offer)
    crm.db.session.commit()
    c = app.test_client()
    c.set_cookie("crm_market", "de")
    html = c.get(f"/crm/oferty/{offer.id}").get_data(as_text=True)
    assert "o.latek@33bots.de" in html and "o.latek@33bots.lt" not in html


def test_quote_override_and_no_messengers_in_de(app, boxes):
    lead = mk("Berlin Gala GmbH", "de", contact_email="a@gala.de", contact_phone="+49 151 2345678", location="Berlin")
    c = app.test_client()
    c.set_cookie("crm_market", "de")
    with app.test_request_context():
        q = crm.lt_quote(lead)
    assert q.get("price"), q
    html = c.get(f"/crm/leads/{lead.id}").get_data(as_text=True)
    assert "Edytuj ofertę" in html
    assert "Wyślij przez WhatsApp" not in html and ">Viber<" not in html and "id=\"lt-msg\"" not in html
    c.post(f"/crm/leads/{lead.id}/oferta-edycja", data={"price": "3 300", "time": "18:00–22:00"})
    with app.test_request_context():
        q = crm.lt_quote(crm.db.session.get(crm.Lead, lead.id))
        qm = crm.quote_mail_preview(crm.db.session.get(crm.Lead, lead.id), q)
    assert q["price"] == 3300 and q["time"] == "18:00–22:00" and q["override"]
    assert "3 300" in qm["body"] and "18:00" in qm["body"] and qm["from"] == "o.latek@33bots.de"
    r = c.get(f"/crm/leads/{lead.id}/oferta-auto.pdf")
    assert r.status_code == 200 and r.data[:4] == b"%PDF"
    c.post(f"/crm/leads/{lead.id}/oferta-edycja", data={"price": "", "time": ""})
    with app.test_request_context():
        assert not crm.lt_quote(crm.db.session.get(crm.Lead, lead.id)).get("override")


def test_lt_keeps_messengers(app, boxes):
    lead = mk("UAB Renginiai", "lt", contact_email="r@x.lt", contact_phone="+37061234567", location="Kaunas")
    c = app.test_client()
    c.set_cookie("crm_market", "lt")
    html = c.get(f"/crm/leads/{lead.id}").get_data(as_text=True)
    assert "WhatsApp" in html and "Viber" in html
