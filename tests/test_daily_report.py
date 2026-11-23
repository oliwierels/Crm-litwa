"""Poranny raport mailowy (parts/raport_dzienny.py, ten sam plik w CRM PL i LT/DE): wyłączony, dopóki nikt go nie
zamówi (brak ustawień opcjonalnej funkcji to nie alarm strażnika), nadawca ze skrzynki osoby, bez podwójnej wysyłki."""
import re

import pytest

import crm


class FakeSMTP:
    log = []

    def __init__(self, host, port, timeout=None):
        FakeSMTP.log.append(("connect", host, port))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        FakeSMTP.log.append(("tls",))

    def login(self, user, pwd):
        FakeSMTP.log.append(("login", user, pwd))

    def sendmail(self, sender, to, raw):
        FakeSMTP.log.append(("send", sender, list(to)))


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


@pytest.fixture(autouse=True)
def env(app, monkeypatch):
    for name in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "REPORT_EMAILS", "REPORT_FROM"):
        monkeypatch.delenv(name, raising=False)
    FakeSMTP.log = []
    monkeypatch.setattr(crm.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(crm.smtplib, "SMTP_SSL", FakeSMTP)
    return monkeypatch


def person():
    p = next(u for u in crm.USERS if u.get("email"))
    return p, re.sub(r"[^A-Z0-9]", "_", p["name"].upper())


def test_report_is_off_and_silent_until_someone_orders_it(env):
    env.setenv("SMTP_HOST", "mail.example.pl")  # sama poczta wychodząca (oferty, maile z CRM) to jeszcze nie raport
    p, key = person()
    env.setenv(f"SMTP_PASS_{key}", "haslo")
    assert not crm.report_wanted() and "raport" not in crm.health_jobs(), "niezamówiony raport nie jest pilnowany"
    env.setenv("REPORT_EMAILS", "szef@example.pl")
    assert crm.report_wanted() and "raport" in crm.health_jobs()
    assert not FakeSMTP.log, "samo sprawdzanie ustawień niczego nie wysyła"


def test_report_goes_from_persons_mailbox_only_to_listed_addresses(env):
    p, key = person()
    env.setenv("SMTP_HOST", "mail.example.pl")
    env.setenv("SMTP_PORT", "465")
    env.setenv(f"SMTP_PASS_{key}", "haslo-osoby")
    env.delenv(f"SMTP_USER_{key}", raising=False)
    env.setenv("REPORT_EMAILS", "szef@example.pl, drugi@example.pl")
    env.setenv("REPORT_FROM", p["name"].lower())
    ok, msg = crm.send_daily_report(check_only=True)
    assert ok and "szef@example.pl" in msg and ("login", p["email"], "haslo-osoby") in FakeSMTP.log
    assert not [e for e in FakeSMTP.log if e[0] == "send"], "sprawdzenie loguje się do skrzynki, ale nic nie wysyła"
    ok, msg = crm.send_daily_report()
    assert ok and ("send", p["email"], ["szef@example.pl", "drugi@example.pl"]) in FakeSMTP.log, msg


def test_unknown_sender_or_missing_password_is_a_clear_error(env):
    env.setenv("SMTP_HOST", "mail.example.pl")
    env.setenv("REPORT_EMAILS", "szef@example.pl")
    env.setenv("REPORT_FROM", "Nieistniejący")
    ok, msg = crm.send_daily_report()
    assert not ok and "REPORT_FROM" in msg and not FakeSMTP.log


def test_old_shared_mailbox_setup_still_works(env):
    env.setenv("SMTP_HOST", "mail.example.pl")
    env.setenv("SMTP_USER", "crm@example.pl")
    env.setenv("SMTP_PASS", "wspolne")
    assert crm.report_wanted()
    ok, msg = crm.send_daily_report()
    team = [u["email"] for u in crm.USERS if u.get("email")]
    assert ok and ("tls",) in FakeSMTP.log and ("send", "crm@example.pl", team) in FakeSMTP.log, msg
