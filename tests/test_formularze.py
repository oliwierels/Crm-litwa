"""Formularz ze strony (Formspree) i adresy automatów (parts/formularze.py, ten sam plik w CRM PL i LT/DE):
„Odpowiedz” na powiadomienie pisze DO KLIENTA (adres z leada / z treści formularza), z normalnym tematem, bez cytatu
(w cytacie jest link „wypisz się” z naszego formularza) i bez wątku powiadomienia; CRM nigdy nie wysyła maila na adres
automatu ani z cytatem powiadomienia."""
from datetime import datetime

import pytest

import crm

CODE = crm.markets.DEFAULT
DOMAIN = crm.M(CODE)["domain"]
BOX = f"kontakt@{DOMAIN}"
FORM_BODY = (f"Hey there,\n\nSomeone just submitted your form on {DOMAIN}/. Here's what they had to say:\n\n"
             "name:\nAneta Formularzowa\n\ncompany:\n—\n\nemail:\nAneta.Form@wp-test.pl\n\nphone:\n500249002\n\n"
             "location:\nWłoszczowa\n\nSubmitted 09:39 PM - 23 November 2026\n---\n\nYou are receiving this because you "
             "confirmed this email address on Formspree. If you don't remember doing that, please visit "
             "//formspree.io/unsubscribe/abc123?email=kontakt to unsubscribe from this endpoint.")
MADE = []


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


@pytest.fixture(autouse=True)
def cleanup(app):
    yield
    crm.db.session.rollback()
    for lead_id in MADE:
        lead = crm.db.session.get(crm.Lead, lead_id)
        if lead is not None:
            lead.stage = "Stracony"
        # wysłany mail zapisuje się jako zrobione zadanie Jerzego — nie może zawyżać jego raportu dnia w innych testach
        crm.Activity.query.filter(crm.Activity.lead_id == lead_id).delete()
    MADE.clear()
    crm.db.session.commit()


@pytest.fixture
def client(app, monkeypatch):
    monkeypatch.setattr(crm, "mail_boxes", lambda *a, **k: [{"login": BOX, "pwd": "x", "key": "KONTAKT", "label": "kontakt@",
                                                            "host": "h", "port": 993, "user_id": 3, "market": CODE}])
    c = crm.app.test_client()
    c.set_cookie("crm_user", "3")
    if len(crm.markets.MARKETS) > 1:
        c.set_cookie("crm_market", CODE)
    return c


@pytest.fixture
def form_mail(app):
    lead = crm.Lead(company="Aneta Formularzowa", stage="Nowy", assigned_to=3, market=CODE,
                    contact_email="aneta.form@wp-test.pl", source="ClickUp")
    crm.db.session.add(lead)
    m = crm.MailMessage(mailbox=BOX, market=CODE, folder="in", from_addr="noreply@formspree.io", from_name="Formspree",
                        to_addrs=BOX, addrs=f" noreply@formspree.io {BOX} ", subject="New submission from A New Form",
                        body_text=FORM_BODY, date=datetime.utcnow(), message_id="<form-1@formspree.io>",
                        thread_key="<form-1@formspree.io>")
    crm.db.session.add(m)
    crm.db.session.commit()
    MADE.append(lead.id)
    return lead, m


def test_machine_addresses():
    for a in ("noreply@formspree.io", "no-reply@firma.pl", "do-not-reply@bank.pl", "mailer-daemon@x.pl",
              "bounce+123@mail.pl", "anything@formspree.io"):
        assert crm.is_machine_address(a), a
    for a in ("kontakt@firma.pl", "marketing@agencja.pl", "a.jaszewska@wp.pl", "info@hotel.pl", ""):
        assert not crm.is_machine_address(a), a


def test_form_notification_points_to_client(form_mail):
    lead, m = form_mail
    assert crm.is_form_notification(m) and crm.is_machine_mail(m)
    assert crm.form_client_email(m) == "aneta.form@wp-test.pl"
    assert crm.mail_lead_for(m).id == lead.id, "powiadomienie łączy się z leadem po adresie z treści formularza"


def test_reply_to_form_writes_to_client_without_quote(client, form_mail):
    lead, m = form_mail
    html = client.get(f"/crm/poczta/nowa?reply={m.id}&auto=1").get_data(as_text=True)
    assert 'value="aneta.form@wp-test.pl"' in html, "adresat = klient, nie Formspree"
    assert 'value="noreply@formspree.io"' not in html
    assert "New submission" not in html.split('name="subject"')[1][:200], "temat nie jest „Re: New submission…”"
    assert "formspree.io/unsubscribe" not in html, "cytat powiadomienia (z linkiem wypisania) nie trafia do maila"
    assert "To było powiadomienie z formularza" in html


def test_send_reply_to_form_has_no_quote_and_no_thread(client, form_mail, monkeypatch):
    lead, m = form_mail
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def sendmail(self, frm, to, raw):
            sent.append((to, raw.decode("utf-8", "replace")))
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setattr(crm.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(crm.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(crm, "_imap_save_sent", lambda *a, **k: None)
    data = {"from": BOX, "to": "aneta.form@wp-test.pl", "subject": "33Bots – wynajem robota", "subject_out": "33Bots – wynajem robota",
            "body": "Dzień dobry, dziękujemy za zapytanie.", "body_out": "Dzień dobry, dziękujemy za zapytanie.",
            "reply": str(m.id), "lead": str(lead.id), "quoted": "\n\n> " + FORM_BODY.replace("\n", "\n> ")}
    r = client.post("/crm/poczta/nowa", data=data)
    assert r.status_code == 200 and r.get_json().get("ok"), r.get_data(as_text=True)[:300]
    to, raw = sent[0]
    assert to == ["aneta.form@wp-test.pl"]
    assert "formspree" not in raw.lower(), "ani cytatu, ani wątku powiadomienia"
    assert "In-Reply-To" not in raw


def test_nothing_goes_to_machine_or_with_form_quote(app):
    err = crm._smtp_send("noreply@formspree.io", "Re: New submission", "Dzień dobry", "Jerzy")
    assert err and "automat" in err
    err = crm._smtp_send("klient@firma-test.pl", "Oferta", "Dzień dobry\n> " + FORM_BODY, "Jerzy")
    assert err and "cytat powiadomienia" in err


def test_inbox_form_never_stores_machine_address(app, monkeypatch):
    rec = crm.InboxMessage(message_key="<inbox-form-1>", from_addr="noreply@formspree.io",
                           subject="New submission from A New Form", received_at=datetime.utcnow(), body=FORM_BODY)
    crm.db.session.add(rec)
    crm.db.session.commit()
    monkeypatch.setattr(crm, "_ai_forced_tool", lambda *a, **k: {
        "is_inquiry": True, "contact_email": "noreply@formspree.io", "contact_name": "Aneta Formularzowa",
        "company": "Aneta Formularzowa", "summary": "Zapytanie z formularza", "quality": "Średni", "reason": "formularz"})
    monkeypatch.setattr(crm, "_ai_client", lambda: object())
    crm.process_inbox_message(rec)
    crm.db.session.commit()
    lead = crm.db.session.get(crm.Lead, rec.lead_id) if rec.lead_id else None
    assert lead is not None, rec.reason
    MADE.append(lead.id)
    assert (lead.contact_email or "").lower() == "aneta.form@wp-test.pl", "e-mail klienta z treści, nie Formspree"


def test_merge_keeps_duplicate_dates_as_note(app):
    """Scalenie kopii z importu: gdy główny lead ma już dni, inne daty z kopii nie przepadają (komentarz przy leadzie)."""
    from datetime import date, timedelta
    keeper = crm.Lead(company="Scalanie Główny", stage="Oferta", assigned_to=3, market=CODE, contact_phone="600111222")
    dup = crm.Lead(company="Scalanie Kopia", stage="Nowy", assigned_to=3, market=CODE, contact_phone="600111222",
                   source="ClickUp", clickup_task_id="cu-scal-1")
    crm.db.session.add_all([keeper, dup])
    crm.db.session.commit()
    MADE.append(keeper.id)
    d1, d2 = date.today() + timedelta(days=30), date.today() + timedelta(days=60)
    crm.set_lead_event_days(keeper, [d1], {})
    crm.set_lead_event_days(dup, [d2], {})
    crm.db.session.commit()
    crm.merge_leads(keeper, dup)
    crm.db.session.commit()
    notes = " ".join(a.notes for a in crm.Activity.query.filter_by(lead_id=keeper.id, kind="note").all())
    assert "Scalono duplikat" in notes and f"{d2:%d.%m.%Y}" in notes, "nowy termin z kopii widoczny przy leadzie"
