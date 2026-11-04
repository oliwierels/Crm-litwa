"""„Czy ktoś oddzwaniał?”, limit prób dla osób prywatnych, czas reakcji od 9:00."""
from datetime import datetime

import pytest

import crm


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


@pytest.fixture()
def client(app):
    return app.test_client()


def mk(company, **k):
    lead = crm.Lead(company=company, stage=k.pop("stage", "Oferta"), assigned_to=1, currency="PLN", **k)
    crm.db.session.add(lead)
    crm.db.session.commit()
    return lead


def test_callback_phone_records_contact_and_resets_attempts(client):
    lead = mk("Oddzwonił", contact_phone="+48 601-222-333", client_type="firma")
    for _ in range(2):
        crm.db.session.add(crm.Activity(lead_id=lead.id, kind="call", done=True, done_at=datetime.utcnow(),
                                        outcome="nie_odebral", assigned_to=1))
    crm.db.session.commit()
    assert crm.lead_failed_attempts(crm.db.session.get(crm.Lead, lead.id)) == 2
    r = client.post("/crm/oddzwonil", json={"action": "phone", "phone": "601 222 333"})
    assert r.json["ok"] and r.json["lead"]["id"] == lead.id
    lead = crm.db.session.get(crm.Lead, lead.id)
    assert crm.lead_failed_attempts(lead) == 0
    assert any((a.notes or "").startswith(crm.sales_rules.CALLBACK_PREFIX) for a in lead.activities)


def test_callback_not_found_and_none(client):
    r = client.post("/crm/oddzwonil", json={"action": "phone", "phone": "999 888 777"})
    assert r.json.get("not_found")
    r = client.post("/crm/oddzwonil", json={"action": "none"})
    assert r.json["ok"] and "cb_asked=" in r.headers.get("Set-Cookie", "")
    r = client.post("/crm/oddzwonil", json={"action": "later"})
    assert "cb_later=" in r.headers.get("Set-Cookie", "")


def test_callback_card_renders_when_due(client, monkeypatch):
    monkeypatch.setattr(crm, "callback_ask_due", lambda: True)
    html = client.get("/crm/").get_data(as_text=True)
    assert "Czy ktoś dziś do Ciebie oddzwaniał" in html
    monkeypatch.setattr(crm, "callback_ask_due", lambda: False)
    assert "Czy ktoś dziś do Ciebie oddzwaniał" not in client.get("/crm/").get_data(as_text=True)


def test_private_two_attempts_then_decide(client):
    lead = mk("Jan Kowalski", contact_name="Jan Kowalski", contact_email="jan.k@gmail.com", contact_phone="500600700")
    assert crm.lead_attempt_limit(lead) == 2
    t = crm.Activity(lead_id=lead.id, kind="call", assigned_to=1, notes="Zadzwonić")
    crm.db.session.add(t)
    crm.db.session.commit()
    for _ in range(2):
        call = crm.Activity.query.filter_by(lead_id=lead.id, kind="call", done=False).first()
        assert client.post(f"/crm/api/activity/{call.id}/done", json={"outcome": "nie_odebral"}).json["ok"]
    assert crm.Activity.query.filter_by(lead_id=lead.id, kind="decision").count() == 1


def test_private_new_lead_gets_call_and_draft(monkeypatch):
    monkeypatch.setattr(crm, "_smtp_send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("bez wysyłki")))
    lead = mk("Anna Nowak", stage="Nowy", contact_name="Anna Nowak", contact_email="anna.n@wp.pl", contact_phone="510520530")
    crm.sales_automation_pass()
    acts = crm.Activity.query.filter_by(lead_id=lead.id, done=False).all()
    assert any(a.kind == "call" for a in acts) and any(a.kind == "email" for a in acts)


def test_evening_lead_counts_from_nine():
    lead = mk("Wieczorny", stage="Nowy")
    # 19:00 czasu polskiego w środę (UTC+1) → odpowiedź 9:20 w czwartek = 20 min pracy
    lead.created_at, lead.handled_at = datetime(2026, 11, 4, 18, 0), datetime(2026, 11, 5, 8, 20)
    crm.db.session.commit()
    assert crm.lead_response_minutes(lead) == 20
