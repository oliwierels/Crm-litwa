"""Zespół — czysta logika (team.py, ten sam plik w CRM PL i LT/DE): rangi, dni obowiązkowe, stany, zestawienia."""
from datetime import date, datetime

import team

U = {1: {"id": 1, "name": "Oliwier"}, 2: {"id": 2, "name": "Bartek"}, 3: {"id": 3, "name": "Jerzy"},
     4: {"id": 4, "name": "Nowy"}}


def test_roles_defaults_and_overrides():
    assert team.role_key(U[1]) == team.role_key(U[2]) == "zarzad" and team.role_key(U[3]) == "kierownik"
    assert team.role_key(U[4]) == "pracownik", "nowa osoba: pracownik, dopóki Zarząd nie nada rangi"
    assert team.role_key(U[4], {"4": "wykonawca"}) == "wykonawca" and team.role_key(U[3], {"3": "nie-ma"}) == "kierownik"
    r = team.role(U[3])
    assert r["report"] and r["data"] and not r["manage"] and r["name"] == "Kierownik"
    assert not team.role(U[1])["report"] and team.role(U[1])["manage"]
    assert not team.ROLES["wykonawca"]["data"] and team.ROLES["wykonawca"]["report"]


def test_who_sees_whose_reports_and_who_assigns():
    assert team.can_view("zarzad", 1, "kierownik", 3) and team.can_view("zarzad", 2, "zarzad", 1)
    assert team.can_view("kierownik", 3, "pracownik", 4) and not team.can_view("kierownik", 3, "zarzad", 1)
    assert team.can_view("pracownik", 4, "pracownik", 4) and not team.can_view("pracownik", 4, "kierownik", 3)
    assert not team.can_view("kierownik", 3, "kierownik", 5), "równa ranga — nie"
    assert team.can_assign("kierownik", 3, 4) and team.can_assign("pracownik", 4, 3)
    assert team.can_assign("wykonawca", 5, 5) and not team.can_assign("wykonawca", 5, 3)


def test_required_days_skip_weekends_and_holidays():
    assert team.is_required_day(date(2026, 11, 18)) and not team.is_required_day(date(2026, 11, 21))  # sobota
    assert not team.is_required_day(date(2026, 11, 11)), "Święto Niepodległości (środa)"
    assert not team.is_required_day(date(2027, 3, 29)), "Poniedziałek Wielkanocny 2027"
    assert not team.is_required_day(date(2026, 6, 4)), "Boże Ciało 2026"
    days = team.required_days(date(2026, 11, 9), date(2026, 11, 16))
    assert days == [date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 12), date(2026, 11, 13), date(2026, 11, 16)]


def test_day_state_and_missing_days():
    today = date(2026, 11, 18)
    assert team.day_state(date(2026, 11, 17), {"status": "work", "late": False}, today) == "ok"
    assert team.day_state(date(2026, 11, 17), {"status": "work", "late": True}, today) == "late"
    assert team.day_state(date(2026, 11, 17), {"status": "off"}, today) == "off"
    assert team.day_state(date(2026, 11, 17), None, today) == "missing"
    assert team.day_state(today, None, today) == "today" and team.day_state(date(2026, 11, 15), None, today) == "free"
    assert team.day_state(date(2026, 11, 10), None, today, since=date(2026, 11, 16)) == "free", "przed obowiązkiem"
    filed = {date(2026, 11, 16)}
    assert team.missing_days(filed, date(2026, 11, 13), today) == [date(2026, 11, 13), date(2026, 11, 17)]
    assert team.missing_days(filed, None, today) == [] and team.missing_days(set(), today, today) == []
    long_ago = team.missing_days(set(), date(2026, 1, 1), today)
    assert len(long_ago) == team.GATE_DAYS and long_ago[-1] == date(2026, 11, 17), "blokują tylko ostatnie dni"


def test_fold_events_and_weekday_summary():
    ev = [(datetime(2026, 11, 17, 9, 5), "calls_ok", 1), (datetime(2026, 11, 17, 9, 40), "calls_fail", 1),
          (datetime(2026, 11, 17, 15, 35), "offers", 1), (datetime(2026, 11, 17, 15, 35), "wins_pln", 5000.4),
          (datetime(2026, 11, 18, 10, 0), "mails_sent", 3), (None, "offers", 1)]
    days = team.fold_events(ev)
    tue = days[date(2026, 11, 17)]
    assert tue["calls_ok"] == 1 and tue["calls_fail"] == 1 and tue["offers"] == 1 and tue["wins_pln"] == 5000
    assert tue["active_from"] == "09:05" and tue["active_to"] == "15:35" and tue["active_h"] == 6.5
    assert days[date(2026, 11, 18)]["mails_sent"] == 3 and days[date(2026, 11, 18)]["active_h"] == 0
    rows = [
        {"day": date(2026, 11, 10), "state": "ok", "metrics": dict(team.empty_metrics(), calls_ok=2, offers=1),
         "ordered_done": 1, "ordered_open": 1},
        {"day": date(2026, 11, 17), "state": "late", "metrics": dict(team.empty_metrics(), calls_ok=0, offers=0),
         "ordered_done": 0, "ordered_open": 2},
        {"day": date(2026, 11, 16), "state": "ok", "metrics": dict(team.empty_metrics(), calls_ok=10, offers=4),
         "ordered_done": 3, "ordered_open": 0},
        {"day": date(2026, 11, 12), "state": "off", "metrics": team.empty_metrics()},
        {"day": date(2026, 11, 13), "state": "missing", "metrics": dict(team.empty_metrics(), calls_ok=1)},
    ]
    s = team.summarize(rows)
    t = s["total"]
    assert t["worked"] == 4 and t["off"] == 1 and t["reports_ok"] == 2 and t["reports_late"] == 1 and t["reports_missing"] == 1
    assert t["on_time_pct"] == 50 and t["ordered_done"] == 4 and t["ordered_not_done"] == 3 and t["ordered_done_pct"] == 57
    assert t["sum"]["calls_ok"] == 13 and t["avg_per_worked_day"]["calls_ok"] == 3.2
    wd = {w["weekday"]: w for w in s["by_weekday"]}
    assert wd["wtorek"]["days"] == 2 and wd["wtorek"]["avg"]["calls_ok"] == 1.0 and wd["wtorek"]["ordered_done_pct"] == 25
    assert wd["poniedziałek"]["avg"]["calls_ok"] == 10.0 and wd["wtorek"]["reports_late"] == 1
    assert wd["piątek"]["reports_missing"] == 1 and "sobota" not in wd
