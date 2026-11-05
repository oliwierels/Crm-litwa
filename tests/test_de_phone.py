"""Maile DE bez polskiego numeru telefonu (telefon tylko w PDF); Litwa bez zmian."""
from datetime import date

import pytest

import crm


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


def test_strip_pl_phone_only_for_de_mailbox():
    body = "Mit freundlichen Grüßen\nOliwier Latek\n33Bots\no.latek@33bots.de\n+48 531 408 004"
    de = crm.strip_pl_phone(body, {"email": "o.latek@33bots.de", "phone": "+48 531 408 004"})
    assert "+48" not in de and "o.latek@33bots.de" in de
    assert crm.strip_pl_phone("Tel.: +48 601 499 947", {"email": "kontakt@33bots.de"}).strip() == ""
    lt = crm.strip_pl_phone(body, {"email": "o.latek@33bots.lt", "phone": "+48 531 408 004"})
    assert "+48 531 408 004" in lt


def test_signatures_without_phone_in_de(app):
    assert crm.mail_signature({"login": "o.latek@33bots.de", "market": "de", "label": "o.latek@", "user_id": 1}).count("+48") == 0
    assert "+48" in crm.mail_signature({"login": "o.latek@33bots.lt", "market": "lt", "label": "o.latek@", "user_id": 1})
    assert crm.mail_phone("+48 531 408 004", "de") == "" and crm.mail_phone("+48 531 408 004", "lt") == "+48 531 408 004"


def test_quote_mail_de_has_no_polish_phone(app):
    lead = crm.Lead(company="Berlin Phone GmbH", stage="Nowy", assigned_to=1, currency="EUR", market="de",
                    contact_email="a@phone.de", location="Berlin", notes="von 18 bis 22 Uhr")
    crm.db.session.add(lead)
    crm.db.session.commit()
    crm.set_lead_event_days(lead, [date(2027, 1, 23)], {})
    crm.db.session.commit()
    with app.test_request_context():
        q = crm.lt_quote(lead)
        _subj, body = crm.lt_quote_mail_draft(lead, q, {"full_name": "Oliwier Latek", "email": "o.latek@33bots.de",
                                                        "phone": "+48 531 408 004"})
    assert "+48" not in body and "o.latek@33bots.de" in body
