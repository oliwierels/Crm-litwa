"""Audyt ofert w CRM LT/DE: brakujący drugi krok = follow-up mailem (oba rynki)."""
from datetime import datetime, timedelta

import pytest

import crm


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


def mk(company, market, stage="Oferta", days_ago=5):
    lead = crm.Lead(company=company, stage=stage, assigned_to=1, currency="EUR", market=market,
                    contact_email=f"a@{company.lower().replace(' ', '')}.{market}")
    crm.db.session.add(lead)
    crm.db.session.commit()
    crm.db.session.add(crm.Activity(lead_id=lead.id, kind="email", done=True, assigned_to=1,
                                    done_at=datetime.utcnow() - timedelta(days=days_ago),
                                    notes="💶 Oferta 2400 € (mail + PDF) wysłana"))
    crm.db.session.commit()
    return lead


def test_audit_adds_followup_in_lt_and_de(app, monkeypatch):
    monkeypatch.setattr(crm, "_smtp_send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("bez wysyłki")))
    lt = mk("Audit LT", "lt")
    de = mk("Audit DE", "de")
    planned = mk("Audit Planned", "de")
    crm.db.session.add(crm.Activity(lead_id=planned.id, kind="call", assigned_to=1, notes="Telefon"))
    crm.db.session.commit()
    rows = crm.run_offer_audit_once()
    added = {r["id"]: r for r in rows if r["added"]}
    assert set(added) == {lt.id, de.id}
    for lead in (lt, de):
        t = crm.Activity.query.filter_by(lead_id=lead.id, done=False).one()
        assert t.kind == "email" and t.notes.startswith(crm.FOLLOWUP_STEP_PREFIX)
        assert crm.followup_lead(t) is not None  # okienko „Follow-up” przygotuje mail w języku klienta
    assert crm.run_offer_audit_once() is None
    c = app.test_client()
    c.set_cookie("crm_market", "de")
    html = c.get("/crm/audyt-ofert").get_data(as_text=True)
    assert "Audit DE" in html and "Audit LT" not in html  # strona pokazuje oglądany rynek


def test_contact_after_offer_no_second_step_and_cleanup(app, monkeypatch):
    """Lead po ofercie z follow-upem / mailem z Poczty nie dostaje drugiego kroku; pierwszy audyt jest cofany."""
    monkeypatch.setattr(crm, "_smtp_send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("bez wysyłki")))
    now = datetime.utcnow()
    fu = mk("Contact Followup", "lt")
    crm.db.session.add(crm.Activity(lead_id=fu.id, kind="note", done=True, assigned_to=1, done_at=now - timedelta(days=2),
                                    notes="📨 Follow-up (litewski) wysłany do a@b.lt"))
    reply = mk("Contact Reply", "de")
    crm.db.session.add(crm.MailMessage(mailbox="o.latek@33bots.de", folder="in", subject="Re: Angebot",
                                       addrs=f" {reply.contact_email} o.latek@33bots.de ", date=now - timedelta(days=1)))
    bare = mk("Contact None", "lt")
    crm.db.session.add(crm.MailMessage(mailbox="o.latek@33bots.lt", folder="sent", subject="Pasiūlymas",  # sam mail z ofertą
                                       addrs=f" {bare.contact_email} ", date=crm.last_offer_at(bare) + timedelta(minutes=1)))
    crm.db.session.commit()
    rows = {r["id"]: r for r in crm.audit_offer_second_steps(apply=True)}
    assert rows[fu.id]["contact"].startswith("follow-up mailem") and not rows[fu.id]["added"]
    assert rows[reply.id]["contact"].startswith("odpowiedź klienta") and not rows[reply.id]["added"]
    assert rows[bare.id]["added"].startswith("follow-up mailem") and not rows[bare.id]["contact"]

    ran = now - timedelta(hours=3)
    old = mk("Contact Old Audit", "de")
    crm.db.session.add(crm.Activity(lead_id=old.id, kind="email", done=True, assigned_to=1, done_at=now - timedelta(days=1),
                                    notes="✉️ Mail z CRM do x@y.de (od: o.latek@33bots.de)\nTemat: Nachfrage"))
    crm.db.session.add(crm.Activity(lead_id=old.id, kind="email", assigned_to=1, created_at=ran - timedelta(seconds=5),
                                    notes=f"{crm.FOLLOWUP_STEP_PREFIX} po ofercie z 31.10 — kliknij „Follow-up”"))
    crm.db.session.commit()
    crm.set_setting(crm.OFFER_AUDIT_KEY, ran.isoformat(timespec="seconds"))
    crm.set_setting(crm.OFFER_AUDIT_REPORT_KEY, crm.json.dumps({"at": "2026-11-05 10:00", "rows": [
        {"id": old.id, "company": old.company, "market": "de", "offer_at": "2026-10-31 10:00", "added": "follow-up mailem na 05.11"}]}))
    crm.db.session.commit()
    assert crm.cleanup_offer_audit_steps() == [old.id]
    assert crm.Activity.query.filter_by(lead_id=old.id, done=False).count() == 0
    assert crm.cleanup_offer_audit_steps() is None
    c = app.test_client()
    c.set_cookie("crm_market", "de")
    html = c.get("/crm/audyt-ofert").get_data(as_text=True)
    assert "Cofnięte (1)" in html and "mail z CRM" in html and "odpowiedź klienta" in html
