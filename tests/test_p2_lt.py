"""P2: licznik prób kontaktu, czas reakcji, szkic dla osób prywatnych, walidacja dat (CRM LT/DE)."""
import json
from datetime import date, datetime, timedelta

import pytest

import crm


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def no_mail(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("automat nie może wysyłać maili")
    monkeypatch.setattr(crm, "_smtp_send", boom)


def mk(company, stage="Oferta", **k):
    lead = crm.Lead(company=company, stage=stage, assigned_to=1, currency="PLN", **k)
    crm.db.session.add(lead)
    crm.db.session.commit()
    return lead


def test_three_failed_attempts_create_one_decide_task(client):
    lead = mk("Nie Odbiera", contact_phone="600100200")
    t = crm.Activity(lead_id=lead.id, kind="call", assigned_to=1, notes="Zadzwonić")
    crm.db.session.add(t)
    crm.db.session.commit()
    for i in range(3):
        open_call = crm.Activity.query.filter_by(lead_id=lead.id, kind="call", done=False).first()
        assert open_call is not None, f"po {i} próbach powinien być zaplanowany kolejny telefon"
        r = client.post(f"/crm/api/activity/{open_call.id}/done", json={"outcome": "nie_odebral"})
        assert r.json["ok"]
    lead = crm.db.session.get(crm.Lead, lead.id)
    assert crm.lead_failed_attempts(lead) == 3
    decide = crm.Activity.query.filter_by(lead_id=lead.id, kind="decision").all()
    assert len(decide) == 1 and "3 nieudane próby" in decide[0].notes
    assert not crm.Activity.query.filter_by(lead_id=lead.id, kind="call", done=False).first()
    crm.sales_automation_pass()
    assert crm.Activity.query.filter_by(lead_id=lead.id, kind="decision").count() == 1  # bez dubli
    # udany kontakt zeruje licznik
    ok = crm.Activity(lead_id=lead.id, kind="call", assigned_to=1, done=True, done_at=datetime.utcnow(), outcome="odebral")
    crm.db.session.add(ok)
    crm.db.session.commit()
    assert crm.lead_failed_attempts(crm.db.session.get(crm.Lead, lead.id)) == 0
    page = client.get(f"/crm/leads/{lead.id}").get_data(as_text=True)
    assert "Nieudane próby kontaktu" in page


def test_no_answer_button_counts(client):
    lead = mk("Przycisk Nie Odebrał")
    t = crm.Activity(lead_id=lead.id, kind="call", assigned_to=1, notes="Zadzwonić")
    crm.db.session.add(t)
    crm.db.session.commit()
    for _ in range(3):
        client.post(f"/crm/api/activity/{t.id}/no_answer")
    assert crm.Activity.query.filter_by(lead_id=lead.id, kind="decision").count() == 1


def test_response_time_alert(client, no_mail):
    created = datetime.utcnow() - timedelta(hours=30)
    lead = mk("Czeka Długo", stage="Nowy", created_at=created, contact_email="a@firma.pl")
    crm.LEAD_ALERT_DAYS  # noqa: B018 — alarm patrzy na ostatnie dni
    row = next((r for r in crm.alerts_state()["leads"] if r["id"] == lead.id), None)
    assert row is not None and row["resp_min"] is not None
    crm.sales_automation_pass()
    lead = crm.db.session.get(crm.Lead, lead.id)
    if row["late"]:
        assert lead.response_alert_at is not None
    handled = mk("Obsłużony Szybko", stage="Nowy", contact_email="b@firma.pl")
    handled.created_at = datetime(2026, 11, 4, 9, 0)  # UTC
    handled.handled_at = datetime(2026, 11, 4, 9, 12)
    crm.db.session.commit()
    assert crm.lead_response_minutes(handled) == 12
    out = json.loads(crm._assistant_execute("get_lead", {"lead_id": handled.id}, crm.USERS[0])[0])
    assert out["response_minutes"] == 12 and "failed_contact_attempts" in out


def test_private_lead_gets_draft_not_mail(client, no_mail, monkeypatch):
    monkeypatch.setattr(crm, "mail_boxes", lambda *a, **k: [{"login": "kontakt@33bots.lt", "pwd": "x", "key": "KONTAKT",
                                                    "label": "kontakt@", "host": "h", "port": 993, "user_id": 1}])
    lead = mk("Alina Prywatna", stage="Nowy", contact_name="Alina Prywatna", contact_email="alina.p@gmail.com",
              notes="Urodziny, 50 gości")
    crm.sales_automation_pass()
    crm.sales_automation_pass()
    drafts = crm.Activity.query.filter(crm.Activity.lead_id == lead.id,
                                       crm.Activity.notes.like(crm.sales_rules.PRIVATE_DRAFT_PREFIX + "%")).all()
    assert len(drafts) == 1 and not drafts[0].done
    assert "budżet" in drafts[0].notes and "€ netto" in drafts[0].notes
    html = client.get(f"/crm/poczta/nowa?lead={lead.id}&draft={drafts[0].id}").get_data(as_text=True)
    assert "Szkic pierwszej odpowiedzi" in html and "Jaki budżet" in html
    # brak automatycznego telefonu, dopóki klient nie odpisze
    assert not crm.Activity.query.filter_by(lead_id=lead.id, kind="call").first()
    drafts[0].done, drafts[0].done_at = True, datetime.utcnow() - timedelta(hours=2)
    crm.db.session.add(crm.MailMessage(mailbox="kontakt@33bots.lt", folder="in", uid=999, uidvalidity="1",
                                       message_id="<r1>", thread_key="<r1>", from_addr="alina.p@gmail.com",
                                       subject="Re: robot", date=datetime.utcnow(), body_text="Budżet 3000",
                                       addrs=" alina.p@gmail.com kontakt@33bots.pl "))
    crm.db.session.commit()
    crm.sales_automation_pass()
    calls = crm.Activity.query.filter_by(lead_id=lead.id, kind="call").all()
    assert len(calls) == 1 and calls[0].notes.startswith(crm.sales_rules.REPLY_CALL_PREFIX)


def test_company_lead_gets_no_private_draft(no_mail):
    lead = mk("Orange Polska S.A.", stage="Nowy", contact_name="Ewa", contact_email="ewa@orange.com")
    crm.sales_automation_pass()
    assert not crm.Activity.query.filter(crm.Activity.lead_id == lead.id,
                                         crm.Activity.notes.like(crm.sales_rules.PRIVATE_DRAFT_PREFIX + "%")).first()


def test_long_range_warning_in_form(client):
    lead = mk("Zakres")
    dates = ",".join((date(2026, 12, 1) + timedelta(days=i)).isoformat() for i in range(20))
    r = client.post(f"/crm/leads/{lead.id}/edit", data={"company": "Zakres", "stage": "Oferta", "event_dates": dates})
    assert "uwaga=" in r.headers["Location"] and "zakres" in r.headers["Location"].lower()
    r = client.post(f"/crm/leads/{lead.id}/edit", data={"company": "Zakres", "stage": "Oferta",
                                                        "event_dates": "0002-12-12,2026-12-12"})
    assert [d.event_date for d in crm.db.session.get(crm.Lead, lead.id).event_days] == [date(2026, 12, 12)]
