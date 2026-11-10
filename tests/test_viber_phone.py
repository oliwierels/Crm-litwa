"""Viber z własnego konta: przycisk w leadzie (Viber Desktop) + kod QR → strona na telefon z gotowym tekstem."""
import re
from datetime import date, datetime, timedelta

import pytest

import crm


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


def mk(company, **k):
    lead = crm.Lead(company=company, stage="Nowy", assigned_to=1, currency="EUR", market="lt", location="Vilnius", **k)
    crm.db.session.add(lead)
    crm.db.session.commit()
    crm.set_lead_event_days(lead, [date(2027, 2, 6)], {})
    crm.db.session.commit()
    return lead


def test_lead_has_viber_desktop_and_phone_buttons(app):
    lead = mk("UAB Viberis", contact_email="v@x.lt", contact_phone="+370 612 34567")
    c = app.test_client()
    c.set_cookie("crm_market", "lt")
    html = c.get(f"/crm/leads/{lead.id}").get_data(as_text=True)
    assert "onclick=\"ltMsg('viber')\"" in html and 'onclick="ltViberQr()"' in html
    assert 'id="lt-viber-help" hidden' in html
    # zapis „wysłane na Viber” dopiero, gdy Viber faktycznie się otworzy (okno traci fokus)
    assert "if (kind === 'viber') return;" in html and "window.addEventListener('blur', leave)" in html
    assert 'const LT_VIBER_DIGITS = "37061234567"' in html and "viberUrl(ltViberStyle(), LT_VIBER_DIGITS, text)" in html
    assert "return 'viber://chat?number=%2B' + d + t;" in html and 'onclick="ltViberPick(4)"' in html


def test_qr_phone_page_opens_viber_and_logs_once(app, monkeypatch):
    lead = mk("UAB Telefonas", contact_email="t@x.lt", contact_phone="861234567")
    c = app.test_client()
    c.set_cookie("crm_market", "lt")
    text = "Sveiki! Roboto nuoma 2027-02-06 — 900 € + PVM."
    d = c.post(f"/crm/leads/{lead.id}/viber-qr", data={"text": text}).get_json()
    assert d["ok"] and d["svg"].startswith("<svg") and 'fill="#fff"' in d["svg"]
    path = re.sub(r"^https?://[^/]+", "", d["url"])
    assert re.fullmatch(r"/f/v/[A-Za-z0-9_-]{16}", path), d["url"]

    # telefon bez logowania (hasło CRM ustawione) — strona z tekstem i linkiem do czatu z numerem klienta
    monkeypatch.setenv("CRM_PASSWORD", "tajne")
    phone = app.test_client()
    page = phone.get(path)
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "Sveiki! Roboto nuoma" in html and "+37061234567" in html
    assert 'href="viber://chat?number=%2B37061234567&amp;draft=Sveiki%21%20Roboto' in html
    assert page.headers["Cache-Control"] == "no-store" and "noindex" in page.headers["X-Robots-Tag"]
    assert phone.get(f"/crm/leads/{lead.id}").status_code == 302, "reszta CRM nadal za hasłem"

    # klik „Otwórz Viber” → jeden wpis w leadzie, nawet przy kilku kliknięciach
    assert phone.post(path).get_json()["ok"] and phone.post(path).get_json()["ok"]
    notes = [a.notes for a in crm.Activity.query.filter_by(lead_id=lead.id).all() if "Viber" in (a.notes or "")]
    assert notes == [f"💬 Wiadomość wysłana na Viber (z telefonu)\n---\n{text}"]

    # po 30 min link wygasa; zły / zgadywany kod — nic
    key = crm.VIBER_QR_PREFIX + path.rsplit("/", 1)[1]
    import json
    row = crm.db.session.get(crm.AppSetting, key)
    v = json.loads(row.value)
    v["ts"] = (datetime.utcnow() - timedelta(minutes=31)).isoformat()
    row.value = json.dumps(v)
    crm.db.session.commit()
    assert phone.get(path).status_code == 410 and phone.post(path).status_code == 410
    assert phone.get("/f/v/krotki").status_code == 410
    # nowy kod sprząta wygasłe
    monkeypatch.delenv("CRM_PASSWORD")
    assert c.post(f"/crm/leads/{lead.id}/viber-qr", data={"text": "x"}).get_json()["ok"]
    assert crm.db.session.get(crm.AppSetting, key) is None


def test_qr_needs_client_phone(app):
    lead = mk("UAB Be Telefono", contact_email="b@x.lt")
    c = app.test_client()
    c.set_cookie("crm_market", "lt")
    r = c.post(f"/crm/leads/{lead.id}/viber-qr", data={"text": "x"})
    assert r.status_code == 400 and "telefonu" in r.get_json()["error"]


def test_first_message_is_welcome_without_price(app):
    lt = mk("UAB Pasisveikinimas", contact_email="p@x.lt", contact_phone="+37061234567")
    with app.test_request_context():
        q = crm.lt_quote(lt)
    msg = q["message"]
    assert msg.startswith("Laba diena!") and "Čia Oliwier iš 33bots" in msg and "renginiui 2027-02-06" in msg
    assert "€" not in msg and "kain" not in msg.lower() and "rezervuojame" not in msg
    assert msg.rstrip().endswith("Oliwier Latek, 33bots") and "?" in msg
    assert "€" in q["message_price"] and "rezervuojame" in q["message_price"]
    assert crm.quote(msg, safe="")[:20] in q["whatsapp"] or "Laba%20diena" in q["whatsapp"]
    de = crm.Lead(company="Feier GmbH", stage="Nowy", assigned_to=2, currency="EUR", market="de",
                  contact_phone="+491512345678", location="Berlin")
    crm.db.session.add(de)
    crm.db.session.commit()
    crm.set_lead_event_days(de, [date(2027, 3, 5)], {})
    crm.db.session.commit()
    w = crm.lt_welcome_message(de, crm._lt_dates_text(de), "de")
    assert w.startswith("Guten Tag!") and "Hier ist Bartosz von 33bots" in w and "Veranstaltung am 05.03.2027" in w
    assert "€" not in w and w.rstrip().endswith("Bartosz Wysocki, 33bots")
    html = app.test_client().get(f"/crm/leads/{lt.id}").get_data(as_text=True)
    assert "👋 Powitanie" in html and "💶 Z ceną" in html and "LT_VERSIONS" in html


def test_generate_message_from_context(app, monkeypatch):
    """✨ Generuj z kontekstu: Claude dostaje maile, notatki i komentarze leada; wynik zapamiętany i domyślny w okienku."""
    lead = mk("UAB Konferencija", contact_email="ruta@konf.lt", contact_phone="+37061234567")
    crm.db.session.add_all([
        crm.MailMessage(mailbox="o.latek@33bots.lt", market="lt", folder="in", uid=None, uidvalidity=None,
                        addrs=" ruta@konf.lt o.latek@33bots.lt ", subject="Re: Pasiūlymas", lang="lt",
                        date=datetime(2026, 11, 2, 14, 11), body_text="Ačiū, bet mums per brangu."),
        crm.MailMessage(mailbox="o.latek@33bots.lt", market="lt", folder="in", uid=None, uidvalidity=None,
                        addrs=" ruta@konf.lt o.latek@33bots.lt ", subject="Re: Pasiūlymas", lang="lt",
                        date=datetime(2026, 11, 4, 17, 55),
                        body_text="Idėja: robotas tik per pristatymą, kaip pranešėjas, 30–35 min."),
        crm.Activity(lead_id=lead.id, kind="note", notes="Klientka ma budżet ok. 1200 €", done=True)])
    crm.db.session.commit()
    seen = {}

    def fake_tool(model, system, text, tool, max_tokens=4096):
        seen.update(model=model, system=system, text=text, tool=tool["name"])
        return {"language": "lt", "message_pl": "Dzień dobry! …", "hook": "Nawiązuje do pomysłu robota-prelegenta.",
                "message": "Laba diena! Čia Oliwier iš 33bots. Ar dar aktuali idėja, kad robotas būtų pranešėjas 30–35 min?"}
    monkeypatch.setattr(crm, "_ai_forced_tool", fake_tool)
    res = crm.lt_msg_ai(lead.id, 1, "zapytaj o budżet")
    assert res["draft"]["message"].endswith("Oliwier Latek, 33bots"), "podpis doklejony"
    assert "per brangu" in seen["text"] and "pranešėjas, 30–35 min" in seen["text"]
    assert "budżet ok. 1200" in seen["text"] and "WSKAZÓWKA UŻYTKOWNIKA: zapytaj o budżet" in seen["text"]
    assert seen["text"].index("per brangu") < seen["text"].index("pranešėjas"), "maile od najstarszego"
    assert "języki jego maili: lt" in seen["system"] and "nigdy jej sam nie obniżaj" in seen["system"]
    assert seen["tool"] == "write_message" and seen["model"] == crm.LT_MSG_AI_MODEL

    c = app.test_client()
    c.set_cookie("crm_market", "lt")
    html = c.get(f"/crm/leads/{lead.id}").get_data(as_text=True)
    assert "✨ Generuj z kontekstu" in html and "Nawiązuje do pomysłu robota-prelegenta." in html
    box = html.split('<textarea id="lt-msg"', 1)[1].split("</textarea>", 1)[0]
    assert "robotas būtų pranešėjas" in box, "zapamiętana wiadomość z kontekstu jest domyślna"

    # trasa: zadanie w tle, wynik przez /crm/job (klucz draft); bez klucza API — czytelny błąd
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(crm, "_ai_client", lambda: object())
    start = c.post(f"/crm/leads/{lead.id}/lt-wiadomosc/generuj", data={"hint": ""}).get_json()
    import time
    for _ in range(50):
        job = c.get(start["poll"]).get_json()
        if job["status"] != "pending":
            break
        time.sleep(0.05)
    assert job["status"] == "done" and "pranešėjas" in job["draft"]["message"]
    monkeypatch.setattr(crm, "_ai_client", lambda: None)
    r = c.post(f"/crm/leads/{lead.id}/lt-wiadomosc/generuj")
    assert r.status_code == 400 and "ANTHROPIC_API_KEY" in r.get_json()["error"]


def test_messenger_panel_without_price(app):
    """Lead bez miejsca (brak ceny): okienko wiadomości, ✨ z kontekstu i przyciski WhatsApp / Viber / SMS i tak są."""
    lead = crm.Lead(company="MB Frendas", stage="Nowy", assigned_to=1, currency="EUR", market="lt",
                    contact_phone="+37061009614", contact_email="v@frendas.lt")
    crm.db.session.add(lead)
    crm.db.session.commit()
    with app.test_request_context():
        q = crm.lt_quote(lead)
    assert q["missing"] == ["miejsca"] and not q.get("price")
    assert q["message"].startswith("Laba diena!") and q["viber"] == "viber://chat?number=%2B37061009614"
    c = app.test_client()
    c.set_cookie("crm_market", "lt")
    html = c.get(f"/crm/leads/{lead.id}").get_data(as_text=True)
    assert "Cena pojawi się, gdy będą znane: miejsca" in html and '<textarea id="lt-msg"' in html
    assert "onclick=\"ltMsg('viber')\"" in html and "onclick=\"ltMsg('whatsapp')\"" in html and "✨ Generuj z kontekstu" in html
    assert 'id="lt-v-price" hidden' in html and "jako wartość leada" not in html and 'id="lt-qmail"' not in html
