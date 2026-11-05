"""Sprawy z poczty: agent sprawdza w poczcie i CRM, czy sprawa nie jest już załatwiona (np. oferta poszła osobnym
mailem), zamyka ją z dowodem; „Otwórz lead” znajduje leada po adresie klienta. Bez prawdziwego Claude'a i bez wysyłki."""
import json
from datetime import datetime, timedelta

import pytest

import crm

LT = hasattr(crm.MailMessage, "market")
MK = {"market": "lt"} if LT else {}
DOM = "lt" if LT else "pl"


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


@pytest.fixture
def client(app):
    c = app.test_client()
    if LT:
        c.set_cookie("crm_market", "lt")
    return c


def mail(folder, frm, to, subject, when, thread, body="", att=None):
    m = crm.MailMessage(mailbox=f"o.latek@33bots.{DOM}", folder=folder, from_addr=frm, to_addrs=to,
                        addrs=f" {frm} {to} ", subject=subject, date=when, thread_key=thread, body_text=body,
                        attachments=json.dumps([{"n": 1, "name": n} for n in (att or [])]), **MK)
    crm.db.session.add(m)
    crm.db.session.commit()
    return m


def task(m, title, action, **k):
    t = crm.MailTask(mail_id=m.id, thread_key=m.thread_key, mailbox=m.mailbox, counterpart=f"Klient <{m.from_addr}>",
                     subject=m.subject, title=title, reason="Klient prosi", action=action, **MK, **k)
    crm.db.session.add(t)
    crm.db.session.commit()
    return t


def test_verify_closes_done_task_and_keeps_open_one(app, client, monkeypatch):
    monkeypatch.setattr(crm, "_smtp_send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("bez wysyłki")))
    monkeypatch.setattr(crm, "_ai_client", lambda: object())
    now = datetime.utcnow()
    lead = crm.Lead(company="Sprawdz Oferta", stage="Oferta", assigned_to=1, contact_email=f"klient@sprawdz.{DOM}", **MK)
    crm.db.session.add(lead)
    crm.db.session.commit()
    ask = mail("in", f"klient@sprawdz.{DOM}", f"o.latek@33bots.{DOM}", "Zapytanie", now - timedelta(days=2), "t-ask",
               "Prosimy o ofertę na 20.11")
    mail("sent", f"o.latek@33bots.{DOM}", f"klient@sprawdz.{DOM}", "Oferta: robot Unitree G1 20.11",
         now - timedelta(days=1), "t-offer", "W załączniku oferta", att=["Oferta_33Bots.pdf"])
    done_task = task(ask, "Wysłać ofertę — Sprawdz Oferta", "Wyślij ofertę", lead_id=lead.id)
    other = mail("in", f"inny@nic.{DOM}", f"o.latek@33bots.{DOM}", "Umowa?", now - timedelta(days=1), "t-other",
                 "Proszę o umowę")
    open_task = task(other, "Wysłać umowę — Nic", "Wyślij umowę")
    calls = []

    def fake(model, system, text, tool, max_tokens=4096):
        calls.append(text)
        assert tool["name"] == "mail_task_check"
        assert "Oferta: robot Unitree G1 20.11" in text and "Oferta_33Bots.pdf" in text and "PO prośbie" in text
        assert "Wysłać umowę" not in text  # bez naszego ruchu po prośbie — bez pytania Claude'a
        return {"items": [{"task": 0, "done": True, "evidence": "Oferta wysłana osobnym mailem z PDF"}]}

    monkeypatch.setattr(crm, "_ai_forced_tool", fake)
    res = crm.verify_mail_tasks()
    assert res["closed"] == 1 and len(calls) == 1
    crm.db.session.refresh(done_task)
    crm.db.session.refresh(open_task)
    assert done_task.status == "done" and done_task.done_how == crm.MAIL_TASK_DONE_HOW
    assert "osobnym mailem" in done_task.check_note
    assert open_task.status == "open" and "nic od nas nie wyszło" in open_task.check_note
    assert crm.verify_mail_tasks()["closed"] == 0 and len(calls) == 1  # bez zmian w poczcie — bez ponownego pytania

    html = client.get("/crm/poczta?sprawy=1").get_data(as_text=True)
    assert "Zamknięte po sprawdzeniu poczty" in html and "Oferta wysłana osobnym mailem" in html
    assert "Sprawdź w poczcie, co już zrobione" in html
    # agent się pomylił — „Przywróć”: sprawa wraca i nie jest już zamykana automatycznie
    client.post(f"/crm/sprawy/{done_task.id}/reopen")
    crm.db.session.refresh(done_task)
    assert done_task.status == "open" and done_task.done_how == crm.MAIL_TASK_REOPENED
    assert crm.verify_mail_tasks(force=True)["closed"] == 0 and len(calls) == 1


def test_review_new_task_checked_before_list(app, monkeypatch):
    monkeypatch.setattr(crm, "_ai_client", lambda: object())
    now = datetime.utcnow()
    ask = mail("in", f"nowy@przeglad.{DOM}", f"kontakt@33bots.{DOM}", "Prośba o wycenę", now - timedelta(hours=30),
               "t-rev", "Ile kosztuje robot?")
    mail("sent", f"kontakt@33bots.{DOM}", f"nowy@przeglad.{DOM}", "Wycena robota", now - timedelta(hours=20), "t-rev2",
         "Cena 5000 zł netto")

    def fake(model, system, text, tool, max_tokens=4096):
        if tool["name"] == "mail_review":
            idx = next(i for i, part in enumerate(text.split("### WĄTEK ")[1:]) if "Prośba o wycenę" in part)
            return {"items": [{"thread": idx, "title": "Wysłać wycenę", "reason": "pyta o cenę", "action": "Wyślij wycenę",
                               "reply_hint": "-", "priority": "normalne"}]}
        assert "Wycena robota" in text
        return {"items": [{"task": 0, "done": True, "evidence": "Wycena poszła osobnym mailem"}]}

    monkeypatch.setattr(crm, "_ai_forced_tool", fake)
    res = crm.mail_review(*(["lt"] if LT else []))
    assert "już załatwionych" in res["info"]
    t = crm.all_markets(crm.MailTask.query.filter(crm.MailTask.thread_key == "t-rev")).one()
    assert t.status == "done" and t.done_how == crm.MAIL_TASK_DONE_HOW
    assert ask.id == t.mail_id


def test_open_lead_link_finds_lead_by_address(app, client):
    now = datetime.utcnow()
    lead = crm.Lead(company="Link Po Adresie", stage="Nowy", assigned_to=1, contact_email=f"adres@link.{DOM}", **MK)
    crm.db.session.add(lead)
    crm.db.session.commit()
    m = mail("in", f"adres@link.{DOM}", f"kontakt@33bots.{DOM}", "Pytanie", now, "t-link")
    t = task(m, "Odpisać", "Odpisz", lead_id=None)
    r = client.get(f"/crm/sprawy/{t.id}/lead")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/crm/leads/{lead.id}")
    crm.db.session.refresh(t)
    assert t.lead_id == lead.id
    assert client.get(r.headers["Location"]).status_code == 200
    m2 = mail("in", f"bez@leada.{DOM}", f"kontakt@33bots.{DOM}", "Inne", now, "t-nolead")
    t2 = task(m2, "Odpisać", "Odpisz")
    r = client.get(f"/crm/sprawy/{t2.id}/lead")
    assert r.status_code == 302 and "brak_leada=1" in r.headers["Location"]
    assert "nie ma leada w CRM" in client.get(r.headers["Location"]).get_data(as_text=True)


@pytest.mark.skipif(not LT, reason="tylko CRM LT/DE")
def test_open_lead_from_other_market_switches_market(app, client):
    lead = crm.Lead(company="Kunde GmbH", stage="Nowy", assigned_to=1, contact_email="kunde@markt.de", market="de")
    crm.db.session.add(lead)
    crm.db.session.commit()
    m = mail("in", "kunde@markt.de", "kontakt@33bots.lt", "Frage", datetime.utcnow(), "t-de")
    t = task(m, "Odpisać", "Odpisz", lead_id=lead.id)
    r = client.get(f"/crm/sprawy/{t.id}/lead")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/crm/leads/{lead.id}")
    assert "crm_market=de" in r.headers.get("Set-Cookie", "")
