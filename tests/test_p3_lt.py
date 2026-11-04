"""P3: panel skuteczności, porządki w danych, dostarczalność (CRM LT/DE)."""
import json
from datetime import date, datetime, timedelta

import pytest

import crm


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        crm.set_setting("fx_EUR", "4.30")
        crm.set_setting("fx_EUR_manual", "1")  # bez pytania NBP w testach
        crm.db.session.commit()
        yield crm.app


@pytest.fixture()
def client(app):
    return app.test_client()


def won(company, value, currency="PLN", **k):
    lead = crm.Lead(company=company, stage="Oferta", assigned_to=1, currency=currency, value_pln=value,
                    client_type=k.pop("client_type", "firma"), **k)
    crm.db.session.add(lead)
    crm.db.session.commit()
    crm.set_lead_event_days(lead, [date(2026, 12, 10)], {})
    crm.record_stage_change(lead, "Wygrany", 1, reason="mail")
    crm.db.session.commit()
    return lead


def test_panel_countries_targets_forecast(client):
    won("UAB Vilnius", 4000, currency="EUR", market="lt", contact_email="a@firma.lt")
    won("Berlin GmbH", 5000, currency="EUR", market="de", contact_email="a@firma.de")
    p = crm.build_sales_panel(90)
    lt = next(c for c in p["countries"] if c["code"] == "LT")
    de = next(c for c in p["countries"] if c["code"] == "DE")
    assert lt["current"] >= 4000 and lt["target"] == 15000 and lt["cur"] == "EUR"
    assert de["current"] >= 5000 and de["target"] == 30000
    assert lt["forecast"] >= lt["current"]
    assert any(g["key"] == "firma" and g["won"] >= 1 for g in p["by_type"])


def test_panel_reasons_cancellations_big_open(client):
    lost = crm.Lead(company="Strata", stage="Oferta", assigned_to=2, currency="PLN")
    crm.db.session.add(lost)
    crm.db.session.commit()
    crm.gated_stage_change(lost, "Stracony", 2, {"lost_reason": "cena"})
    cancel = won("Anulacja", 12000)
    crm.gated_stage_change(cancel, "Stracony", 1, {"lost_reason": "brak_decyzji"})
    big = crm.Lead(company="Duży Lead", stage="Oferta", assigned_to=1, currency="EUR", value_pln=3000)  # 12 900 zł
    crm.db.session.add(big)
    crm.db.session.commit()
    p = crm.build_sales_panel(90)
    assert any(r["key"] == "cena" for r in p["reasons"])
    assert any(c["lead"].id == cancel.id and c["pln"] == 12000 for c in p["cancels"])
    b = next(x for x in p["big_open"] if x["lead"].id == big.id)
    assert b["pln"] == 12900 and b["next"] is None
    html = client.get("/crm/skutecznosc").get_data(as_text=True)
    assert "Skuteczność sprzedaży" in html and "Duży Lead" in html and "brak — zaplanuj!" in html
    out = json.loads(crm._assistant_execute("sales_performance", {}, crm.USERS[0])[0])
    assert out["countries"] and "open_leads_10k" in out and out["eur_pln_rate"] == 4.3


def test_targets_editable(client):
    client.post("/crm/skutecznosc", data={"PL_realny": "160000", "PL_ambitny": "210000", "DE_realny": "", "DE_ambitny": ""})
    t = crm.sales_targets()
    assert t["PL"]["realny"] == 160000 and t["PL"]["ambitny"] == 210000


def test_cleanup_report(client):
    v = crm.Lead(company="VULCAN", stage="Oferta", assigned_to=1, currency="PLN", value_pln=6000,
                 notes="Oferta 68 600 zł netto za 3 dni")
    nd = crm.Lead(company="Wygrany Bez Daty", stage="Wygrany", assigned_to=1, currency="PLN", value_pln=5000)
    bad = crm.Lead(company="Rok 0002", stage="Oferta", assigned_to=1, currency="PLN", value_pln=1,
                   event_date=datetime(2, 12, 12))
    crm.db.session.add_all([v, nd, bad])
    crm.db.session.commit()
    r = crm.data_cleanup_report()
    assert any(i["lead"].id == v.id and "68 600" in i["info"] for i in r["value_vs_offer"])
    assert any(i["lead"].id == nd.id for i in r["won_no_date"])
    assert any(i["lead"].id == bad.id for i in r["bad_dates"])
    assert crm.db.session.get(crm.Lead, v.id).value_pln == 6000  # raport niczego nie zmienia
    assert client.get("/crm/porzadki").status_code == 200


def test_deliverability_page(client, monkeypatch):
    fake = {("33bots.pl", "TXT"): ["v=spf1 include:_spf.example ~all"], ("_dmarc.33bots.pl", "TXT"): [],
            ("default._domainkey.33bots.pl", "TXT"): ["v=DKIM1; k=rsa; p=MIGf"]}
    monkeypatch.setattr(crm, "dns_txt", lambda name, rtype="TXT": fake.get((name, rtype), []))
    r = crm.deliverability_report()
    pl = r["rows"][0]
    assert pl["spf"] and pl["dkim"] == ["default"] and any("DMARC" in t for t in pl["tips"])
    de = r["rows"][1]
    assert any("SPF" in t for t in de["tips"])
    assert "Dostarczalność" in client.get("/crm/dostarczalnosc").get_data(as_text=True)
