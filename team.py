"""Zespół: rangi, obowiązkowy raport dnia i zestawienia pracy — wspólne reguły CRM PL i CRM LT/DE
(ten sam plik w obu repozytoriach, jak sales_rules.py). Tylko czysta logika, bez bazy i Flaska.

Rangi (zmiana = edycja ROLES poniżej; rangę osoby ustawia Zarząd w CRM: Zespół → Rangi):
- Zarząd      — pełny dostęp, raporty i zadania całego zespołu, nadawanie rang; bez obowiązku raportu,
- Kierownik   — pełny dostęp do danych, zleca zadania; OBOWIĄZKOWY raport dnia; widzi raporty osób niżej,
- Pracownik   — pełny dostęp do danych; obowiązkowy raport dnia; widzi tylko swoje raporty,
- Wykonawca   — bez danych klientów: tylko zlecone mu zadania i własny raport dnia (obowiązkowy).

Raport dnia: w każdy dzień roboczy (pn–pt bez świąt) — co zrobiłem, co mnie zablokowało, plan na jutro; przy każdym
zadaniu zleconym przez inną osobę z terminem do dziś trzeba zaznaczyć „zrobione” albo „niezrobione” z powodem.
Zamiast raportu można zgłosić dzień wolny (urlop, choroba). Raport złożony po dniu, którego dotyczy = spóźniony;
brak raportu z dnia roboczego blokuje CRM tej osobie, aż go uzupełni (ostatnie GATE_DAYS dni roboczych), a w
historii zostaje jako spóźniony albo brak. Liczby dnia (telefony, maile, oferty…) CRM zbiera sam."""
from datetime import date, timedelta

import sales_rules

ROLES = {
    "zarzad": {"name": "Zarząd", "rank": 3, "data": True, "report": False, "sees": "all", "manage": True,
               "about": "Pełny dostęp do wszystkiego: dane, raporty i zadania całego zespołu, nadawanie rang."},
    "kierownik": {"name": "Kierownik", "rank": 2, "data": True, "report": True, "sees": "below", "manage": False,
                  "about": "Pełny dostęp do danych i zlecanie zadań. Obowiązkowy raport dnia. Widzi raporty osób niżej."},
    "pracownik": {"name": "Pracownik", "rank": 1, "data": True, "report": True, "sees": "own", "manage": False,
                  "about": "Pełny dostęp do danych. Obowiązkowy raport dnia. Widzi tylko swoje raporty."},
    "wykonawca": {"name": "Wykonawca", "rank": 0, "data": False, "report": True, "sees": "own", "manage": False,
                  "about": "Bez danych klientów: widzi tylko zlecone mu zadania i swój raport dnia (obowiązkowy)."},
}
DEFAULT_ROLE = "pracownik"  # nowa osoba w CRM, dopóki Zarząd nie nada rangi
DEFAULT_BY_NAME = {"oliwier": "zarzad", "bartek": "zarzad", "jerzy": "kierownik"}

MIN_SUMMARY = 20  # „co zrobiłem” — co najmniej tyle znaków
MIN_REASON = 5  # powód niezrobionego zadania
GATE_DAYS = 10  # brak raportu z ostatnich tylu dni roboczych blokuje CRM; starsze zostają w historii jako „brak”
REMIND_HOUR = 15  # od tej godziny w dzień roboczy przypomnienie o dzisiejszym raporcie (okno w CRM + jeden mail)
BLOCK_HOUR = 17  # od tej godziny brak dzisiejszego raportu blokuje CRM (zmienna DAY_REPORT_BLOCK_HOUR)
REMIND_SNOOZE_MIN = 30  # okno przypomnienia można odłożyć najwyżej na tyle minut
OFF_REASONS = {"urlop": "Urlop", "choroba": "Choroba", "wolne": "Dzień wolny / święto", "inne": "Inne"}
WEEKDAYS = ("poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela")
WEEKDAYS_SHORT = ("pn", "wt", "śr", "cz", "pt", "sb", "nd")

# liczby dnia zbierane przez CRM (klucz, nazwa) — w tej kolejności w tabelach, eksporcie i konektorze
METRICS = (
    ("calls_ok", "Telefony odebrane"), ("calls_fail", "Telefony nieodebrane"), ("mails_sent", "Maile wysłane"),
    ("offers", "Oferty wysłane"), ("leads_handled", "Nowe leady obsłużone"), ("tasks_done", "Zadania odhaczone"),
    ("notes", "Komentarze w leadach"), ("stage_offer", "Leady → Oferta"), ("wins", "Wygrane"),
    ("wins_pln", "Wygrane (zł)"), ("lost", "Stracone"), ("active_h", "Aktywność w CRM (h)"),
)
METRIC_KEYS = tuple(k for k, _ in METRICS)
STATES = {"ok": "✅ w terminie", "late": "🕐 spóźniony", "off": "🌴 dzień wolny", "missing": "❌ brak raportu",
          "today": "⏳ dziś — jeszcze nie złożony", "free": "— dzień bez obowiązku"}


def role_key(user, saved=None):
    """Ranga osoby: zapisana w CRM (saved: {id jako tekst: klucz}) albo domyślna po imieniu, inaczej DEFAULT_ROLE."""
    key = (saved or {}).get(str(user.get("id")))
    if key in ROLES:
        return key
    return DEFAULT_BY_NAME.get((user.get("name") or "").strip().lower(), DEFAULT_ROLE)


def role(user, saved=None):
    key = role_key(user, saved)
    return dict(ROLES[key], key=key)


def can_view(viewer_key, viewer_id, target_key, target_id):
    """Czy `viewer` widzi raporty i zestawienia `target`: swoje zawsze, Zarząd wszystkie, Kierownik — osób niżej."""
    if viewer_id == target_id:
        return True
    sees = ROLES[viewer_key]["sees"]
    return sees == "all" or (sees == "below" and ROLES[target_key]["rank"] < ROLES[viewer_key]["rank"])


def can_assign(viewer_key, viewer_id, target_id):
    """Czy `viewer` może zlecić zadanie `target`: osoby z dostępem do danych — każdemu; Wykonawca tylko sobie."""
    return viewer_id == target_id or ROLES[viewer_key]["data"]


def _easter(year):
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def holidays(year):
    """Polskie dni ustawowo wolne od pracy (stałe + Poniedziałek Wielkanocny i Boże Ciało)."""
    easter = _easter(year)
    fixed = [(1, 1), (1, 6), (5, 1), (5, 3), (8, 15), (11, 1), (11, 11), (12, 24), (12, 25), (12, 26)]
    return {date(year, m, d) for m, d in fixed} | {easter, easter + timedelta(days=1), easter + timedelta(days=49),
                                                   easter + timedelta(days=60)}


def is_required_day(d):
    """Dzień, z którego raport jest obowiązkowy: pn–pt, nie święto."""
    return d.weekday() in sales_rules.WORK_DAYS and d not in holidays(d.year)


def required_days(start, end):
    """Dni obowiązkowego raportu od `start` do `end` włącznie."""
    out, d = [], start
    while d <= end:
        if is_required_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def day_state(day, report, today, since=None):
    """Stan dnia: ok | late | off | missing | today | free. report: None albo {"status": "work"|"off", "late": bool}.
    since — od kiedy osoba ma obowiązek raportu (wcześniejsze dni bez raportu = free)."""
    if report:
        if report.get("status") == "off":
            return "off"
        return "late" if report.get("late") else "ok"
    if not is_required_day(day) or (since and day < since) or day > today:
        return "free"
    return "today" if day == today else "missing"


def missing_days(report_days, since, today, limit=GATE_DAYS):
    """Dni robocze przed dziś bez raportu — z ostatnich `limit` dni roboczych (to one blokują CRM)."""
    if not since:
        return []
    recent = required_days(max(since, today - timedelta(days=limit * 2 + 7)), today - timedelta(days=1))[-limit:]
    return [d for d in recent if d not in report_days]


def fold_events(events):
    """events: [(chwila lokalna, klucz liczby, ile)] → {dzień: {klucz: suma, active_from, active_to, active_h}}.
    Aktywność = od pierwszej do ostatniej zapisanej czynności tego dnia (przybliżenie czasu pracy w CRM)."""
    days = {}
    for when, key, amount in events:
        if when is None:
            continue
        m = days.setdefault(when.date(), {k: 0 for k in METRIC_KEYS})
        m[key] = m.get(key, 0) + amount
        m["active_from"] = min(m.get("active_from") or when, when)
        m["active_to"] = max(m.get("active_to") or when, when)
    for m in days.values():
        span = (m["active_to"] - m["active_from"]).total_seconds() / 3600
        m["active_h"] = round(span, 1)
        m["active_from"], m["active_to"] = m["active_from"].strftime("%H:%M"), m["active_to"].strftime("%H:%M")
        m["wins_pln"] = round(m.get("wins_pln") or 0)
    return days


def empty_metrics():
    return dict({k: 0 for k in METRIC_KEYS}, active_from="", active_to="")


def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def summarize(rows):
    """rows: dni osoby [{day, state, metrics, ordered_done, ordered_open, active}] → podsumowanie okresu i średnie
    wg dnia tygodnia. Średnie liczone z dni przepracowanych: raport w terminie / spóźniony, brak raportu w dzień
    roboczy albo dzień bez obowiązku, w którym coś zrobiono w CRM. Dzisiejszy dzień bez raportu się nie liczy."""
    worked = [r for r in rows if r["state"] in ("ok", "late", "missing") or (r["state"] == "free" and r.get("active"))]
    required = [r for r in rows if r["state"] in ("ok", "late", "missing")]
    done = sum(r.get("ordered_done") or 0 for r in rows)
    open_ = sum(r.get("ordered_open") or 0 for r in rows)
    total = {
        "days": len(rows), "worked": len(worked), "off": sum(1 for r in rows if r["state"] == "off"),
        "reports_ok": sum(1 for r in rows if r["state"] == "ok"),
        "reports_late": sum(1 for r in rows if r["state"] == "late"),
        "reports_missing": sum(1 for r in rows if r["state"] == "missing"),
        "on_time_pct": round(100 * sum(1 for r in required if r["state"] == "ok") / len(required)) if required else None,
        "ordered_done": done, "ordered_not_done": open_,
        "ordered_done_pct": round(100 * done / (done + open_)) if done + open_ else None,
        "sum": {k: round(sum(r["metrics"].get(k) or 0 for r in rows), 1) for k in METRIC_KEYS},
        "avg_per_worked_day": {k: _avg([r["metrics"].get(k) or 0 for r in worked]) for k in METRIC_KEYS},
    }
    by_weekday = []
    for wd in range(7):
        rs = [r for r in worked if r["day"].weekday() == wd]
        if not rs and wd > 4:
            continue
        d = sum(r.get("ordered_done") or 0 for r in rs)
        o = sum(r.get("ordered_open") or 0 for r in rs)
        by_weekday.append({
            "weekday": WEEKDAYS[wd], "days": len(rs),
            "avg": {k: _avg([r["metrics"].get(k) or 0 for r in rs]) for k in METRIC_KEYS},
            "reports_late": sum(1 for r in rs if r["state"] == "late"),
            "reports_missing": sum(1 for r in rs if r["state"] == "missing"),
            "ordered_done_pct": round(100 * d / (d + o)) if d + o else None,
        })
    return {"total": total, "by_weekday": by_weekday}
