"""Customer Journey LT/DE: „Przypomnij” o umowie / bazie wiedzy dostępne od razu po wysłaniu (szkic do akceptu)."""
from datetime import datetime, timedelta, date

import pytest

import crm


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


def test_early_reminder_button_and_draft(app, monkeypatch):
    monkeypatch.setattr(crm, "_smtp_send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("bez wysyłki")))
    lead = crm.Lead(company="Reminder DE GmbH", stage="Wygrany", assigned_to=1, currency="EUR", market="de",
                    contact_email="kunde@reminder-de.de")
    crm.db.session.add(lead)
    crm.db.session.commit()
    crm.db.session.add(crm.LeadEventDate(lead_id=lead.id, event_date=date.today() + timedelta(days=20)))
    j = crm.ensure_journey(lead)
    j.contract_status, j.contract_sent_at = "wyslana", datetime.utcnow() - timedelta(hours=5)  # termin jeszcze nie minął
    j.kb_status, j.kb_sent_at = "wyslana", datetime.utcnow() - timedelta(hours=5)
    crm.db.session.commit()
    c = app.test_client()
    c.set_cookie("crm_market", "de")
    html = c.get("/crm/journey?tylko=wszyscy").get_data(as_text=True)
    assert f"openJourneyMail({lead.id}, 'umowa_przypomnienie')" in html
    assert f"openJourneyMail({lead.id}, 'baza_przypomnienie')" in html
    r = c.get(f"/crm/journey/{lead.id}/szkic?rodzaj=umowa_przypomnienie")
    assert r.status_code == 200 and r.get_json().get("subject")
