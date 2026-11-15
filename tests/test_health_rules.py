"""Strażnik CRM — czysta logika (health.py, ten sam plik w CRM PL i LT/DE)."""
from datetime import datetime, timedelta

import pytest

import health as h

NOW = datetime(2026, 11, 16, 12, 0)


@pytest.fixture(autouse=True)
def utc_as_local():
    """Opisy w czasie UTC (CRM ustawia czas warszawski — tu bez niego, żeby godziny w testach się zgadzały)."""
    prev = h._local
    h.set_local_time(lambda dt: dt)
    yield
    h.set_local_time(prev)


def test_beat_counts_failures_and_resets_on_ok():
    b = h.beat_update({}, True, "3 nowe", now=NOW - timedelta(minutes=30))
    assert b["fails"] == 0 and b["ok_at"] == "2026-11-16T11:30:00" and b["info"] == "3 nowe"
    b = h.beat_update(b, False, error="IMAP timeout", now=NOW - timedelta(minutes=20))
    b = h.beat_update(b, False, error="IMAP timeout", now=NOW - timedelta(minutes=10))
    assert b["fails"] == 2 and b["fail_since"] == "2026-11-16T11:40:00" and b["ok_at"] == "2026-11-16T11:30:00"
    st, text = h.job_state(b, timedelta(minutes=30), NOW)
    assert st == "ok" and "chwilowy błąd (2×" in text, "dwa błędy z rzędu to jeszcze nie alarm"
    b = h.beat_update(b, False, error="401 Unauthorized", now=NOW)
    st, text = h.job_state(b, timedelta(minutes=30), NOW)
    assert st == "failing" and "od 16.11 11:40" in text and "401 Unauthorized" in text
    b = h.beat_update(b, True, now=NOW)
    assert b["fails"] == 0 and "fail_since" not in b and h.job_state(b, timedelta(minutes=30), NOW)[0] == "ok"


def test_stale_and_unknown_jobs():
    old = h.beat_update({}, True, now=NOW - timedelta(hours=2))
    st, text = h.job_state(old, timedelta(minutes=30), NOW)
    assert st == "stale" and "ostatni obieg 16.11 10:00" in text and "30 min" in text
    assert h.job_state(None, timedelta(minutes=30), NOW, started_at=NOW - timedelta(minutes=5))[0] == "unknown"
    assert h.job_state(None, timedelta(minutes=30), NOW, started_at=NOW - timedelta(hours=1))[0] == "stale"
    alarms, states = h.job_alarms({"poczta": old, "clickup": h.beat_update({}, True, now=NOW)},
                                  {"poczta": ("Pobieranie poczty", timedelta(minutes=30)),
                                   "clickup": ("Import z ClickUp", timedelta(minutes=30))}, NOW)
    assert list(alarms) == ["job:poczta"] and alarms["job:poczta"]["level"] == "red"
    assert alarms["job:poczta"]["title"] == "Pobieranie poczty: stoi" and states["clickup"][0] == "ok"


def test_quiet_alarm_compares_with_usual_rhythm():
    every_6h = [NOW - timedelta(days=10) + timedelta(hours=6 * i) for i in range(24)]  # ostatni 4 dni temu
    assert NOW - every_6h[-1] > timedelta(days=4)
    text = h.quiet_alarm(every_6h, NOW)
    assert text and "a zwykle co 6 h" in text, "zwykle co 6 h, a od ~3 dni nic"
    assert h.quiet_alarm(every_6h, every_6h[-1] + timedelta(days=2)) is None, "2 dni ciszy to jeszcze weekend"
    assert h.quiet_alarm(every_6h[:5], NOW) is None, "za mało danych"
    weekly = [NOW - timedelta(days=7 * i) for i in range(1, 10)]
    assert h.quiet_alarm(weekly, NOW) is None, "raz w tygodniu to normalny rytm tej skrzynki"
    assert h.quiet_alarm(weekly, NOW + timedelta(days=30))


def test_merge_keeps_since_and_diagnosis():
    prev = {"job:poczta": {"level": "red", "title": "Poczta: nie działa", "since": "2026-11-15T08:00:00",
                           "diag": {"cause": "hasło"}},
            "job:clickup": {"level": "red", "title": "ClickUp: stoi", "since": "2026-11-15T09:00:00"}}
    found = {"job:poczta": {"level": "red", "title": "Poczta: nie działa", "detail": "x"},
             "quiet:leads": {"level": "yellow", "title": "Cisza w leadach", "detail": "y"}}
    cur, new, closed = h.merge_alarms(prev, found, NOW)
    assert cur["job:poczta"]["since"] == "2026-11-15T08:00:00" and cur["job:poczta"]["diag"] == {"cause": "hasło"}
    assert new == ["quiet:leads"] and closed == ["job:clickup"] and cur["quiet:leads"]["since"] == "2026-11-16T12:00:00"
    assert [a["key"] for a in h.sorted_alarms(cur)] == ["job:poczta", "quiet:leads"]
    assert h.human(timedelta(minutes=15)) == "15 min" and h.human(timedelta(hours=5)) == "5 h"
    assert h.human(timedelta(days=3)) == "3 dni"
