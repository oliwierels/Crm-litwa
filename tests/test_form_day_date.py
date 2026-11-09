"""Nowy lead z ClickUp: termin taska = dzień zgłoszenia to nie data wydarzenia (zostaje pusta); jednorazowe czyszczenie."""
from datetime import datetime, timedelta

import pytest

import crm

MK = {"market": "lt"} if hasattr(crm.Lead, "market") else {}


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


def ms(dt):
    return str(int(dt.timestamp() * 1000))


def test_due_date_on_form_day_is_not_event_date(app):
    created = datetime(2026, 11, 9, 9, 30)
    same_day = crm._clickup_task_to_lead({"name": "Jan Kowalski", "date_created": ms(created),
                                          "due_date": ms(created.replace(hour=22))})
    assert "event_date" not in same_day
    later = crm._clickup_task_to_lead({"name": "Jan Kowalski", "date_created": ms(created),
                                       "due_date": ms(created + timedelta(days=30))})
    assert later["event_date"].date() == (created + timedelta(days=30)).date()


def test_one_time_cleanup(app):
    now = datetime.utcnow().replace(microsecond=0)
    bad = crm.Lead(company="Data z formularza", stage="Nowy", assigned_to=1, clickup_task_id="t-bad", created_at=now,
                   event_date=now, **MK)
    real = crm.Lead(company="Prawdziwa data", stage="Nowy", assigned_to=1, clickup_task_id="t-ok", created_at=now,
                    event_date=now + timedelta(days=20), **MK)
    offer = crm.Lead(company="Oferta z datą", stage="Oferta", assigned_to=1, clickup_task_id="t-of", created_at=now,
                     event_date=now, **MK)
    crm.db.session.add_all([bad, real, offer])
    crm.db.session.commit()
    crm.db.session.add(crm.LeadEventDate(lead_id=bad.id, event_date=crm.to_local(now).date()))
    crm.db.session.commit()
    assert crm.clear_form_day_event_dates() == [bad.id]
    for l in (bad, real, offer):
        crm.db.session.refresh(l)
    assert bad.event_date is None and not bad.event_days
    assert real.event_date is not None and offer.event_date is not None
    assert crm.clear_form_day_event_dates() is None  # jednorazowo
