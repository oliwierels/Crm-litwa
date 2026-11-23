"""Blitzkrieg po danych (parts/blitzkrieg_dane.py, ten sam plik w CRM PL i LT/DE): Claude zamienia opis grupy na filtr
danych z CRM, który liczy Python (wpadł N dni temu, nieobsłużony, oferta bez odpowiedzi, wycena…), a cechy jakościowe
ocenia tylko na przefiltrowanych leadach. Follow-upy: osobna treść dla każdego, w wątku naszego maila, bez wysyłki
przed startem i bez treści zastępczej."""
from datetime import datetime, timedelta

import pytest

import crm

CODE = crm.markets.DEFAULT
OLIWIER, BARTEK, JERZY = 1, 2, 3
MADE, CAMPS = [], []


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


@pytest.fixture(autouse=True)
def cleanup(app):
    yield
    crm.db.session.rollback()
    for cid in CAMPS:
        c = crm.db.session.get(crm.Campaign, cid)
        if c is not None:
            crm.db.session.delete(c)
    for lead_id in MADE:
        lead = crm.db.session.get(crm.Lead, lead_id)
        if lead is not None:
            lead.stage = "Stracony"
    MADE.clear()
    CAMPS.clear()
    crm.db.session.commit()


def ago(days):
    return datetime.utcnow() - timedelta(days=days)


def mk(company, stage="Nowy", days=1, email=None, **kw):
    lead = crm.Lead(company=company, stage=stage, assigned_to=kw.pop("assigned_to", JERZY), market=CODE,
                    contact_email=email or f"{company.lower().replace(' ', '.')}@bk-test.pl",
                    contact_name=kw.pop("contact_name", "Anna Test"), currency=crm.M(CODE)["currency"], **kw)
    crm.db.session.add(lead)
    crm.db.session.flush()
    lead.created_at = ago(days)
    crm.db.session.commit()
    MADE.append(lead.id)
    return lead


def offer(lead, days, price="6 500 zł"):
    crm.db.session.add(crm.SentOffer(lead_id=lead.id, filename="oferta.pdf", pdf=b"%PDF", price=price,
                                     sent_at=ago(days), to_addr=lead.contact_email, subject="Oferta 33Bots",
                                     body="Dzień dobry, w załączniku oferta."))
    crm.db.session.commit()


def mail(lead, days, folder="in", subject="Re: Oferta 33Bots", msgid=None):
    addr = lead.contact_email.lower()
    crm.db.session.add(crm.MailMessage(
        mailbox="kontakt@33bots.pl", market=CODE, folder=folder, subject=subject, date=ago(days),
        from_addr=addr if folder == "in" else "kontakt@33bots.pl", to_addrs="kontakt@33bots.pl" if folder == "in" else addr,
        addrs=f" {addr} kontakt@33bots.pl ", body_text="Dzień dobry, dziękujemy.", message_id=msgid))
    crm.db.session.commit()


@pytest.fixture
def world(app):
    w = {
        "fresh": mk("Bk Swiezy", days=2),
        "handled": mk("Bk Obsluzony", days=2, handled_at=ago(1)),
        "old_new": mk("Bk Stary", days=10),
        "silent": mk("Bk Oferta Cisza", stage="Oferta", days=20),
        "answered": mk("Bk Oferta Odpisal", stage="Oferta", days=20),
        "quoted": mk("Bk Wycena", days=15),
        "fund": mk("Fundacja Bk Dobro", stage="Oferta", days=20, contact_name="Ewa Nowak"),
    }
    for k in ("silent", "answered", "fund"):
        offer(w[k], 10)
    mail(w["answered"], 5)
    mail(w["silent"], 12)  # klient pisał PRZED ofertą — to nie jest odpowiedź na ofertę
    crm.db.session.add(crm.Activity(lead_id=w["quoted"].id, kind="note", done=True, done_at=ago(14), assigned_to=JERZY,
                                    notes="🧮 Wycena: 8 h, Warszawa"))
    crm.db.session.commit()
    return w


def test_facts_are_computed_from_crm_data(world):
    leads = list(world.values())
    f = crm.bk_facts(leads)
    w = {k: f[v.id] for k, v in world.items()}
    assert w["fresh"]["created_days"] == 2 and w["fresh"]["unhandled"] and not w["handled"]["unhandled"]
    assert w["silent"]["offer_days"] == 10 and not w["silent"]["reply_after_offer"]
    assert w["answered"]["reply_after_offer"] and w["answered"]["client_wrote_days"] == 5
    assert w["quoted"]["quoted"] and w["quoted"]["offer_days"] is None and not w["fresh"]["quoted"]
    line = crm.bk_fact_line(world["silent"], w["silent"])
    assert "oferta 10 dni temu" in line and "BEZ odpowiedzi po ofercie" in line and "@bk-test.pl" in line
    assert "bk.oferta.cisza@" not in line, "z adresu e-mail Claude widzi tylko domenę"


def test_filter_new_unhandled_last_4_days(world):
    leads = list(world.values())
    picked, conds = crm.bk_apply_filter(leads, crm.bk_facts(leads), {"created_within_days": 4, "unhandled": True})
    assert [l.id for l in picked] == [world["fresh"].id] and "nieobsłużony" in conds


def test_filter_offer_without_reply(world):
    leads = list(world.values())
    flt = {"stages": ["Nowy", "Oferta"], "offer_sent": True, "no_reply_after_offer": True, "offer_older_than_days": 7}
    picked, _ = crm.bk_apply_filter(leads, crm.bk_facts(leads), flt)
    assert {l.id for l in picked} == {world["silent"].id, world["fund"].id}
    picked, _ = crm.bk_apply_filter(leads, crm.bk_facts(leads), {"quoted": True, "offer_sent": False})
    assert [l.id for l in picked] == [world["quoted"].id], "wyceniony, ale oferta nie poszła"


def test_pick_by_data_then_qualities(world, monkeypatch):
    calls = []

    def fake(model, system, text, tool, max_tokens=0):
        calls.append((tool["name"], text))
        if tool["name"] == "filtr_leadow":
            return {"offer_sent": True, "no_reply_after_offer": True, "qualitative": "fundacje", "summary": "oferta bez odpowiedzi"}
        return {"lead_ids": [world["fund"].id, world["fresh"].id], "summary": "fundacja po nazwie"}
    monkeypatch.setattr(crm, "_ai_forced_tool", fake)
    leads = [l for l in crm._campaign_form_leads() if l.id in {x.id for x in world.values()}]
    out = crm.pick_leads_by_data("fundacje, którym wysłaliśmy ofertę i nie odpisały", leads)
    assert out["lead_ids"] == [world["fund"].id], "Claude wybiera tylko z przefiltrowanych (spoza filtra odrzucone)"
    assert [c[0] for c in calls] == ["filtr_leadow", "wybierz_odbiorcow"]
    assert "Fundacja Bk Dobro" in calls[1][1] and "Bk Swiezy" not in calls[1][1], "do Claude'a idą tylko leady po filtrze"
    assert "Filtr z danych CRM" in out["info"]


def test_followups_are_personal_threaded_and_safe(world, client, monkeypatch):
    mail(world["silent"], 10, folder="sent", subject="Oferta 33Bots — wynajem robota", msgid="<oferta-1@33bots.pl>")
    jobs = []
    monkeypatch.setattr(crm, "_start_job", lambda label, fn, *a: jobs.append(fn(*a)) or "job")
    monkeypatch.setattr(crm, "_ai_client", lambda: object())
    seen = []

    def fake(model, system, text, tool, max_tokens=0):
        seen.append(text)
        return {"subject": "Przypomnienie", "body": f"Dzień dobry,\nczy oferta jest aktualna? ({len(seen)})"}
    monkeypatch.setattr(crm, "_ai_forced_tool", fake)
    r = client.post("/crm/blitzkrieg/nowa", data={"action": "zapisz", "mode": "personal", "name": "Follow-up test",
                                                  "sender": "Bartek", "rate": "5", "hour_from": "8", "hour_to": "20",
                                                  "lead_ids": [str(world["silent"].id), str(world["fund"].id)],
                                                  "followup_instruction": "zapytaj o termin"})
    assert r.status_code == 302, r.get_data(as_text=True)[:500]
    c = crm.Campaign.query.order_by(crm.Campaign.id.desc()).first()
    CAMPS.append(c.id)
    assert c.personal and c.status == "szkic" and len(c.recipients) == 2 and jobs
    assert all("zapytaj o termin" in t for t in seen) and any("6 500 zł" in t for t in seen), "kontekst oferty w prośbie"
    rec = next(x for x in c.recipients if x.lead_id == world["silent"].id)
    assert rec.in_reply_to == "<oferta-1@33bots.pl>" and rec.subject == "Re: Oferta 33Bots — wynajem robota"
    other = next(x for x in c.recipients if x.lead_id == world["fund"].id)
    assert other.in_reply_to is None and other.subject == "Przypomnienie"
    page = client.get(f"/crm/blitzkrieg/{c.id}").get_data(as_text=True)
    assert "Osobne follow-upy" in page and "czy oferta jest aktualna" in page and "Wszystkie follow-upy gotowe" in page
    # poprawka treści przed wysyłką
    client.post(f"/crm/blitzkrieg/{c.id}/odbiorca/{rec.id}", data={"subject": rec.subject, "body": "Dzień dobry,\nmoja poprawka"})
    assert crm.db.session.get(crm.CampaignRecipient, rec.id).body == "Dzień dobry,\nmoja poprawka"
    # wysyłka: w wątku, bez nagłówka masowej wysyłki; odbiorca bez treści nie dostaje nic
    sent = []
    monkeypatch.setattr(crm, "_smtp_send", lambda to, subject, body, name, **kw: sent.append((to, subject, body, kw)) or None)
    assert crm._campaign_send_one(c, rec)
    to, subject, body, kw = sent[0]
    assert subject.startswith("Re: ") and body == "Dzień dobry,\nmoja poprawka"
    assert kw["headers"]["In-Reply-To"] == "<oferta-1@33bots.pl>" and "List-Unsubscribe" not in kw["headers"]
    other.body = ""
    assert not crm._campaign_send_one(c, other) and other.status == "pominiety" and len(sent) == 1
    crm.db.session.commit()


def test_start_waits_for_all_drafts(world, client, monkeypatch):
    monkeypatch.setattr(crm, "_start_job", lambda *a: "job")  # Claude „jeszcze pisze”
    monkeypatch.setattr(crm, "_ai_client", lambda: object())
    client.post("/crm/blitzkrieg/nowa", data={"action": "zapisz", "mode": "personal", "name": "Czeka na Claude'a",
                                              "sender": "Bartek", "rate": "5", "hour_from": "8", "hour_to": "20",
                                              "lead_ids": [str(world["fresh"].id)]})
    c = crm.Campaign.query.order_by(crm.Campaign.id.desc()).first()
    CAMPS.append(c.id)
    page = client.get(f"/crm/blitzkrieg/{c.id}").get_data(as_text=True)
    assert "Claude pisze follow-upy" in page and 'value="start"' not in page
    r = client.post(f"/crm/blitzkrieg/{c.id}", data={"action": "start"})
    assert "jeszcze nie napisał" in r.get_data(as_text=True)
    assert crm.db.session.get(crm.Campaign, c.id).status == "szkic", "nic nie wychodzi bez napisanych follow-upów"


def test_one_message_campaign_unchanged(world, client, monkeypatch):
    client.post("/crm/blitzkrieg/nowa", data={"action": "zapisz", "mode": "one", "name": "Życzenia", "subject": "Wesołych Świąt",
                                              "body": "Dzień dobry {imie},\nwesołych świąt!", "sender": "Bartek", "rate": "5",
                                              "hour_from": "8", "hour_to": "20", "lead_ids": [str(world["fresh"].id)], "footer": "1"})
    c = crm.Campaign.query.order_by(crm.Campaign.id.desc()).first()
    CAMPS.append(c.id)
    assert not c.personal
    sent = []
    monkeypatch.setattr(crm, "_smtp_send", lambda to, subject, body, name, **kw: sent.append((subject, body, kw)) or None)
    assert crm._campaign_send_one(c, c.recipients[0])
    subject, body, kw = sent[0]
    assert subject == "Wesołych Świąt" and body.startswith("Dzień dobry Anna,") and "List-Unsubscribe" in kw["headers"]
    crm.db.session.commit()
