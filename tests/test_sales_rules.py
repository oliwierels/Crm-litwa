"""Czysta logika reguł sprzedaży (sales_rules.py) — bez bazy."""
from datetime import date, datetime
from types import SimpleNamespace as NS

import sales_rules as sr


def lead(**k):
    base = dict(stage="Oferta", client_type=None, value_pln=None)
    base.update(k)
    return NS(**base)


def test_lost_requires_reason_and_comment_for_other():
    g = sr.stage_gate(lead(), "Stracony", {})
    assert g["need"] == ["lost_reason"] and "powód straty" in g["message"]
    g = sr.stage_gate(lead(), "Stracony", {"lost_reason": "inne"})
    assert g["need"] == ["lost_comment"]
    assert not sr.stage_gate(lead(), "Stracony", {"lost_reason": "inne", "lost_comment": "x"})["message"]
    assert not sr.stage_gate(lead(), "Stracony", {"lost_reason": "cena"})["message"]


def test_win_requires_confirmation_value_type_but_not_date():
    g = sr.stage_gate(lead(), "Wygrany", {})
    assert set(g["need"]) == {"win_confirmation", "client_type"}
    assert len(g["errors"]) == 1 and "wartości" in g["errors"][0]  # tylko wartość — data nie jest wymagana
    ok = sr.stage_gate(lead(value_pln=5000, client_type="firma"), "Wygrany", {"win_confirmation": "mail"},
                       event_dates=[date(2026, 12, 1)])
    assert not ok["message"]
    no_date = sr.stage_gate(lead(value_pln=5000, client_type="firma"), "Wygrany", {"win_confirmation": "umowa"})
    assert not no_date["message"], "umowa podpisana, dzień do ustalenia później — lead jest wygrany"


def test_win_with_signed_contract_needs_no_confirmation():
    g = sr.stage_gate(lead(value_pln=5000, client_type="firma"), "Wygrany", {}, event_dates=[date(2026, 12, 1)],
                      contract_signed=True)
    assert not g["message"]


def test_win_does_not_need_good_date_and_warns_on_offer_mismatch():
    g = sr.stage_gate(lead(value_pln=6000, client_type="firma"), "Wygrany", {"win_confirmation": "umowa"},
                      event_dates=[date(2, 1, 1)])
    assert not g["errors"], "data nie jest wymagana (błędne „0002” odrzuca już zapis dni wynajmu)"
    g = sr.stage_gate(lead(value_pln=6000, client_type="firma"), "Wygrany", {"win_confirmation": "umowa"},
                      event_dates=[date(2026, 12, 1)], last_offer_value=68600)
    assert g["warnings"] and "68 600" in g["warnings"][0]
    g = sr.stage_gate(lead(value_pln=6000, client_type="firma"), "Wygrany",
                      {"win_confirmation": "umowa", "confirm_warning": "1"}, event_dates=[date(2026, 12, 1)],
                      last_offer_value=68600)
    assert not g["message"]


def test_offer_stage_requires_client_type_only_from_new():
    assert sr.stage_gate(lead(stage="Nowy"), "Oferta", {})["need"] == ["client_type"]
    assert not sr.stage_gate(lead(stage="Nowy", client_type="prywatna"), "Oferta", {})["message"]
    assert not sr.stage_gate(lead(stage="Wygrany"), "Zrealizowany", {})["message"]


def test_client_type_suggestion():
    assert sr.suggest_client_type("Jan Kowalski", "Jan Kowalski", ["jan@gmail.com"])[0] == "prywatna"
    assert sr.suggest_client_type("Alina Štura", "Alina Štura", ["stura.alina@gmail.com"])[0] == "prywatna"
    assert sr.suggest_client_type("Eventownia Sp. z o.o.", "Anna", ["a@eventownia.pl"])[0] == "agencja"
    assert sr.suggest_client_type("Robotics Hub", "", ["x@robohub.pl"])[0] == "partner"
    assert sr.suggest_client_type("Orange Polska S.A.", "Ewa", ["e@orange.com"])[0] == "firma"
    assert sr.suggest_client_type("VULCAN", "", ["k@vulcan.edu.pl"])[0] == "firma"


def test_confirm_call_rule_and_next_work_day():
    assert sr.needs_confirm_call("firma", 1000)
    assert sr.needs_confirm_call("prywatna", 12000)
    assert not sr.needs_confirm_call("prywatna", 2750)
    assert sr.next_work_day(date(2026, 11, 6)) == date(2026, 11, 9)  # piątek → poniedziałek
    assert sr.next_work_day(date(2026, 11, 4)) == date(2026, 11, 5)


def test_working_minutes():
    assert sr.working_minutes(datetime(2026, 11, 4, 9, 0), datetime(2026, 11, 4, 9, 45)) == 45
    assert sr.working_minutes(datetime(2026, 11, 6, 17, 50), datetime(2026, 11, 9, 9, 20)) == 30  # przez weekend
    assert sr.working_minutes(datetime(2026, 11, 4, 20, 0), datetime(2026, 11, 5, 8, 50)) == 0
    # lead o 19:00 — 30 minut liczy się od 9:00 następnego dnia
    assert sr.working_minutes(datetime(2026, 11, 4, 19, 0), datetime(2026, 11, 5, 9, 25)) == 25


def test_response_time_capped():
    """Czas reakcji: weekend się nie liczy, a licznik staje na 2 h pracy (zamiast 480 albo 1000 minut)."""
    fri_pm, mon = datetime(2026, 11, 13, 15, 0), datetime(2026, 11, 16, 14, 0)
    assert sr.working_minutes(fri_pm, mon) == 480
    assert sr.response_minutes(fri_pm, mon) == sr.RESPONSE_CAP_MIN == 120
    assert sr.response_minutes(datetime(2026, 11, 13, 18, 0), datetime(2026, 11, 16, 9, 20)) == 20  # pt 18:00 → pn
    assert sr.response_label(120) == "ponad 2 h" and sr.response_label(25) == "25 min" and sr.response_label(None) == ""
    assert sr.response_label(17.5) == "17 min"
    assert sr.is_free_mail("jan@gmail.com") and sr.is_free_mail("wp.pl") and not sr.is_free_mail("anna@firma.pl")


def test_attempt_limit_and_phone_key():
    assert sr.attempt_limit("prywatna") == 2 and sr.attempt_limit("firma") == 3 and sr.attempt_limit(None) == 3
    assert sr.phone_key("+48 600-100-200") == sr.phone_key("0048600100200") == sr.phone_key("600 100 200") == "600100200"


def test_contact_attempts_reset_on_success():
    ev = [(datetime(2026, 1, 1), "fail"), (datetime(2026, 1, 2), "fail"), (datetime(2026, 1, 3), "success"),
          (datetime(2026, 1, 4), "fail")]
    assert sr.contact_attempts(ev) == 1
    assert sr.contact_attempts(ev[:2]) == 2


def test_event_days_validation():
    keep, dropped, warns, blocked = sr.check_new_event_days([], [date(2, 12, 12), date(2026, 12, 12)])
    assert keep == [date(2026, 12, 12)] and dropped == [date(2, 12, 12)] and warns
    keep, _d, _w, _b = sr.check_new_event_days([date(2024, 5, 1)], [date(2024, 5, 1)])
    assert keep == [date(2024, 5, 1)]  # stare dni zapisane wcześniej zostają
    keep, _d, warns, _b = sr.check_new_event_days([], [date(2026, 12, 1), date(2026, 12, 20)])
    assert any("zakres" in w for w in warns)
    many = [date(2026, 1, 1).fromordinal(date(2026, 1, 1).toordinal() + i) for i in range(100)]
    keep, _d, warns, blocked = sr.check_new_event_days([], many)
    assert blocked and keep == []


def test_country_suggestion():
    assert sr.suggest_country("Vilniaus rajonas", "EUR", ["a@gmail.com"], "", "", "lt") == "LT"
    assert sr.suggest_country("Berlin", "EUR", ["a@firma.de"]) == "DE"
    assert sr.suggest_country("Bukareszt", "EUR", ["a@x.com"]) == "RO"
    assert sr.suggest_country("Kraków", "PLN", ["a@x.pl"]) == "PL"
