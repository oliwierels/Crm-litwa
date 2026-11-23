"""Każdy mail i każda sprawa z poczty ma rynek swojej skrzynki — przy kilku rynkach (CRM LT/DE) filtr rynku pokazuje
je we właściwej zakładce (mail z kontakt@33bots.de i sprawa z niego są w „Niemcy”, nie w „Litwa”).
Ten sam plik w CRM PL i LT/DE. Bez prawdziwego Claude'a, IMAP i wysyłki."""
from datetime import datetime, timedelta

import pytest

import crm
import poczta


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


def raw_mail(frm, to, subject, body, msg_id, when):
    return (f"From: Kunde <{frm}>\r\nTo: {to}\r\nSubject: {subject}\r\nMessage-ID: {msg_id}\r\n"
            f"Date: {when:%a, %d %b %Y %H:%M:%S} +0000\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n"
            ).encode()


def test_stored_mail_and_review_task_get_the_mailbox_market(app, monkeypatch):
    monkeypatch.setattr(crm, "_ai_client", lambda: object())
    box = {"login": "kontakt@33bots.de", "market": "de"}
    when = datetime.utcnow() - timedelta(hours=5)
    data = poczta.parse(raw_mail("kunde@firma-markt.de", "kontakt@33bots.de", "Anfrage Roboter Messe",
                                 "Guten Tag, wir möchten einen Roboter für unsere Messe mieten. Was kostet das?",
                                 "<markt-1@firma-markt.de>", when))
    row = crm._mail_store(box, "in", "1", 101, data)
    crm.db.session.commit()
    assert row.market == "de" and row.lang == "de"

    def fake(model, system, text, tool, max_tokens=4096):
        if tool["name"] == "mail_review":
            idx = next(i for i, part in enumerate(text.split("### WĄTEK ")[1:]) if "Anfrage Roboter Messe" in part)
            return {"items": [{"thread": idx, "title": "Angebot senden", "reason": "fragt nach Preis",
                               "action": "Angebot senden", "reply_hint": "-", "priority": "normalne"}]}
        return {"items": []}

    monkeypatch.setattr(crm, "_ai_forced_tool", fake)
    crm.mail_review("de")  # w tle (bez zapytania przeglądarki) — rynek sprawy z rynku przeglądanej poczty
    t = crm.all_markets(crm.MailTask.query.filter(crm.MailTask.thread_key == row.thread_key)).one()
    assert t.market == "de" and t.mailbox == "kontakt@33bots.de"


def test_mail_without_market_gets_the_service_default(app):
    m = crm.MailMessage(mailbox="ktos@33bots.pl", folder="in", subject="Bez rynku", date=datetime.utcnow())
    crm.db.session.add(m)
    crm.db.session.commit()
    assert m.market == crm.markets.DEFAULT
