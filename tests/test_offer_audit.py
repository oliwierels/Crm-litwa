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
