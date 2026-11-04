"""Bramki etapów, typ klienta przy ofercie i telefon potwierdzający — UI, API i MCP (CRM LT/DE)."""
import json
from datetime import date, datetime

import pytest

import crm


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


@pytest.fixture()
def client(app):
    return app.test_client()


def mk(company="Firma Test", stage="Oferta", **k):
    lead = crm.Lead(company=company, stage=stage, assigned_to=1, currency=k.pop("currency", "PLN"), **k)
    crm.db.session.add(lead)
    crm.db.session.commit()
    return lead


def days(lead, *ds):
    crm.set_lead_event_days(lead, list(ds), {})
    crm.db.session.commit()


def post_stage(client, lead, stage, **extra):
    return client.post(f"/crm/api/leads/{lead.id}/stage", json=dict(stage=stage, **extra))


def test_lost_requires_reason_ui_api(client):
    lead = mk("Stracony Bez Powodu")
    r = post_stage(client, lead, "Stracony")
    assert r.status_code == 422 and r.json["need"] == ["lost_reason"] and "powód straty" in r.json["error"]
    assert crm.db.session.get(crm.Lead, lead.id).stage == "Oferta"
    r = post_stage(client, lead, "Stracony", lost_reason="cena")
    assert r.status_code == 200 and r.json["ok"]
    lead = crm.db.session.get(crm.Lead, lead.id)
    assert lead.stage == "Stracony" and lead.lost_reason == "cena"
    assert lead.stage_history[-1].reason == "cena"


def test_win_gate_dates_value_confirmation(client):
    lead = mk("Wygrana Bez Danych", client_type="firma")
    r = post_stage(client, lead, "Wygrany", win_confirmation="mail")
    assert r.status_code == 422 and len(r.json["errors"]) == 2
    lead.value_pln = 8000
    crm.db.session.commit()
    days(lead, date(2026, 12, 5))
    r = post_stage(client, lead, "Wygrany")
    assert r.json["need"] == ["win_confirmation"]
    r = post_stage(client, lead, "Wygrany", win_confirmation="umowa")
    assert r.json["ok"]
    lead = crm.db.session.get(crm.Lead, lead.id)
    assert lead.stage == "Wygrany" and lead.win_confirmation == "umowa" and lead.contract_signed
    assert lead.journey is not None and lead.journey.contract_status == "podpisana"


def test_won_to_realized_and_auto_realize_not_blocked(client):
    lead = mk("Stary Wygrany", stage="Wygrany")  # stary lead bez nowych pól
    days(lead, date(2025, 3, 1))
    assert crm.auto_realize_leads() >= 1
    assert crm.db.session.get(crm.Lead, lead.id).stage == "Zrealizowany"
    assert client.get(f"/crm/leads/{lead.id}").status_code == 200


def test_offer_needs_client_type_and_confirm_call(client, monkeypatch):
    sent = []

    def fake_send(to, subject, body, name, attachments=None, sender=None, headers=None, cc=None):
        sent.append(to)
        return None
    fake_send.last_warning = None
    monkeypatch.setattr(crm, "_smtp_send", fake_send)
    lead = mk("Oferta Firma", stage="Nowy", contact_email="k@firma.pl")
    offer = crm.SentOffer(lead_id=lead.id, filename="o.pdf", pdf=b"%PDF", to_addr="k@firma.pl", subject="Oferta",
                          body="Oferta", price="12 000 zł", sender_id=1)
    crm.db.session.add(offer)
    crm.db.session.commit()
    err, _w, _c = crm.deliver_offer(offer, crm.USERS[0])
    assert err and "typ klienta" in err and not sent
    lead.client_type = "firma"
    crm.db.session.commit()
    err, _w, changes = crm.deliver_offer(offer, crm.USERS[0])
    crm.db.session.commit()
    assert err is None and sent == ["k@firma.pl"]
    calls = crm.Activity.query.filter_by(lead_id=lead.id, kind="call", done=False).all()
    assert len(calls) == 1, "dokładnie jedno zadanie telefonu po ofercie"
    assert crm.sales_rules.CONFIRM_CALL_PREFIX in calls[0].notes and "Kto decyduje?" in calls[0].notes
    today = crm.to_local(datetime.utcnow()).date()
    assert calls[0].due_date.date() == crm.sales_rules.next_work_day(today)
    lead = crm.db.session.get(crm.Lead, lead.id)
    assert lead.value_pln == 12000 and lead.stage == "Oferta"


def test_private_small_offer_keeps_weekly_followup():
    lead = mk("Jan Prywatny", stage="Nowy", client_type="prywatna", value_pln=2750)
    crm.record_stage_change(lead, "Oferta", 1)
    crm.db.session.commit()
    call = crm.Activity.query.filter_by(lead_id=lead.id, kind="call", done=False).one()
    assert crm.sales_rules.CONFIRM_CALL_PREFIX not in call.notes


def test_vulcan_value_mismatch_warning(client):
    lead = mk("VULCAN", client_type="firma", value_pln=6000)
    days(lead, date(2026, 11, 20))
    crm.db.session.add(crm.SentOffer(lead_id=lead.id, filename="v.pdf", pdf=b"%PDF", price="68 600 zł",
                                     sent_at=datetime.utcnow()))
    crm.db.session.commit()
    r = post_stage(client, lead, "Wygrany", win_confirmation="mail")
    assert r.status_code == 422 and r.json["warnings"] and "68 600" in r.json["warnings"][0]
    r = post_stage(client, lead, "Wygrany", win_confirmation="mail", confirm_warning="1")
    assert r.json["ok"]


def test_mcp_update_lead_gate_and_closed_leads():
    lead = mk("MCP Lead")
    out = json.loads(crm._assistant_execute("update_lead", {"lead_id": lead.id, "stage": "Stracony"}, crm.USERS[0])[0])
    assert "error" in out and "powód straty" in out["error"] and "lost_reason" in out["error"]
    assert crm.db.session.get(crm.Lead, lead.id).stage == "Oferta"
    out = json.loads(crm._assistant_execute("update_lead", {"lead_id": lead.id, "stage": "Stracony",
                                                            "lost_reason": "konkurencja"}, crm.USERS[0])[0])
    assert out.get("ok") and out["lead"]["lost_reason"] == "konkurencja"
    month = crm.to_local(datetime.utcnow()).strftime("%Y-%m")
    res = json.loads(crm._assistant_execute("closed_leads", {"month": month}, crm.USERS[0])[0])
    mine = [l for l in res["lost"] if l["id"] == lead.id]
    assert mine and mine[0]["lost_reason_label"] == "Wybrali innego dostawcę"


def test_mcp_create_won_lead_needs_data():
    out = json.loads(crm._assistant_execute("create_lead", {"company": "Po Fakcie", "stage": "Wygrany", "value": 5000},
                                            crm.USERS[0])[0])
    assert "error" in out and "NIE został utworzony" in out["error"]
    assert not crm.Lead.query.filter_by(company="Po Fakcie").first()
    out = json.loads(crm._assistant_execute("create_lead", {
        "company": "Po Fakcie", "stage": "Wygrany", "value": 5000, "event_dates": ["2026-12-01"],
        "win_confirmation": "mail", "client_type": "firma"}, crm.USERS[0])[0])
    assert out.get("ok")
    assert crm.db.session.get(crm.Lead, out["lead_id"]).stage == "Wygrany"


def test_complete_task_requires_outcome_and_confirm_answers(client):
    lead = mk("Telefon", client_type="firma", value_pln=15000)
    call = crm.ensure_followup_call(lead)
    crm.db.session.commit()
    r = client.post(f"/crm/api/activity/{call.id}/done", json={})
    assert r.status_code == 422 and r.json["need_outcome"] and r.json["confirm"]
    r = client.post(f"/crm/api/activity/{call.id}/done", json={"outcome": "odebral"})
    assert r.status_code == 422 and "odpowiedzi" in r.json["error"]
    answers = {"dotarla": "tak", "decyzja_kiedy": "piątek", "decyduje": "zarząd", "porownanie": "Robotivo"}
    r = client.post(f"/crm/api/activity/{call.id}/done", json={"outcome": "odebral", "answers": answers})
    assert r.json["ok"]
    lead = crm.db.session.get(crm.Lead, lead.id)
    assert json.loads(lead.call_answers)["porownanie"] == "Robotivo"
    # MCP
    t2 = crm.Activity(lead_id=lead.id, kind="call", assigned_to=1, notes="Zadzwonić")
    crm.db.session.add(t2)
    crm.db.session.commit()
    out = json.loads(crm._assistant_execute("complete_task", {"task_id": t2.id}, crm.USERS[0])[0])
    assert "error" in out and "wynik" in out["error"]
    out = json.loads(crm._assistant_execute("complete_task", {"task_id": t2.id, "outcome": "nie_odebral"}, crm.USERS[0])[0])
    assert out.get("ok") and crm.db.session.get(crm.Activity, t2.id).outcome == "nie_odebral"


def test_edit_form_stage_gate(client):
    lead = mk("Formularz")
    r = client.post(f"/crm/leads/{lead.id}/edit", data={"company": "Formularz", "stage": "Stracony"})
    assert r.status_code == 302 and "uwaga=" in r.headers["Location"]
    assert crm.db.session.get(crm.Lead, lead.id).stage == "Oferta"
    r = client.post(f"/crm/leads/{lead.id}/edit", data={"company": "Formularz", "stage": "Stracony",
                                                        "lost_reason": "inne", "lost_comment": "klient zrezygnował"})
    assert crm.db.session.get(crm.Lead, lead.id).stage == "Stracony"
    page = client.get(f"/crm/leads/{lead.id}").get_data(as_text=True)
    assert "Powód straty" in page and "klient zrezygnował" in page


def test_stage_check_uses_form_values(client):
    lead = mk("Check", client_type="firma")
    r = client.post(f"/crm/api/leads/{lead.id}/stage-check", json={"stage": "Wygrany", "win_confirmation": "mail",
                                                                   "value_pln": "5000", "event_dates": "2026-12-01"})
    assert r.json["ok"], r.json
    assert crm.db.session.get(crm.Lead, lead.id).value_pln is None  # sprawdzenie niczego nie zapisuje


def test_dates_year_0002_rejected():
    lead = mk("Daty")
    warns = crm.set_lead_event_days(lead, [date(2, 12, 12), date(2026, 12, 12)], {})
    crm.db.session.commit()
    assert [d.event_date for d in crm.db.session.get(crm.Lead, lead.id).event_days] == [date(2026, 12, 12)]
    assert warns and "2025" in warns[0]


def test_pages_render(client):
    for path in ("/crm/", "/crm/leads", "/crm/stats"):
        assert client.get(path).status_code == 200
