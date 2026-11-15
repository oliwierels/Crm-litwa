"""Strażnik CRM — wspólny dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach, jak sales_rules.py).

Jak działa:
- czujniki: każdy pracownik w tle (poczta, ClickUp, Customer Journey, przegląd poczty…) po każdym obiegu zapisuje
  krótką notkę: „OK o 12:04” albo „błąd: …” (beat_update);
- strażnik co kwadrans ocenia notki (job_state) i ciągłość pracy (quiet_alarm — np. od 4 dni ani jednego leada,
  a zwykle przychodzi kilka dziennie) i aktualizuje listę alarmów (merge_alarms). Alarm widać w CRM i znika sam,
  gdy wszystko wróci do normy;
- każdy alarm ma właściciela (classify): „auto” — CRM naprawia sam (chwilowy błąd mija, stojący wątek
  uruchamia ponownie, osobowość robota tworzy od razu, listę ClickUp szuka od nowa, błąd w kodzie naprawia
  Claude) albo „human” — tylko to, czego bez człowieka zrobić się nie da (nowy klucz, hasło, decyzja). Ludzie
  widzą tylko alarmy „human”; naprawa automatyczna, która nie pomaga, po czasie przechodzi do człowieka;
- strażnik niczego nie wysyła do klientów. Claude dopisuje diagnozę po ludzku przy alarmie dla człowieka.

Tu tylko czysta logika (bez bazy i Flaska) — CRM woła te funkcje i trzyma notki w ustawieniach (AppSetting).
Czasy w danych są w UTC; do opisów CRM podaje funkcję czasu lokalnego (set_local_time)."""
import re
from datetime import datetime, timedelta

FAIL_STREAK = 3  # tyle błędów z rzędu = alarm (pojedynczy timeout skrzynki to jeszcze nie awaria)
CHECK_MINUTES = 15  # co ile minut strażnik ocenia stan
QUIET_MIN_DAYS = 3  # cisza krótsza niż tyle dni nigdy nie jest alarmem (weekend, święta)
QUIET_FACTOR = 4  # dłuższa — gdy jest tyle razy dłuższa niż zwykła przerwa
QUIET_MIN_SAMPLES = 8  # za mało danych (np. nowa skrzynka) — bez oceny ciszy
LEVELS = ("red", "yellow")  # red: coś nie działa; yellow: podejrzana cisza / ostrzeżenie

_local = lambda dt: dt  # noqa: E731


def set_local_time(fn):
    """CRM podaje zamianę UTC → czas lokalny (do opisów „ostatnio OK 12:04”)."""
    global _local
    _local = fn


def iso(dt):
    return dt.isoformat(timespec="seconds") if dt else None


def parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts)[:19])
    except ValueError:
        return None


def fmt(dt):
    return f"{_local(dt):%d.%m %H:%M}" if dt else "nigdy"


def human(td):
    """timedelta → „15 min”, „3 h”, „2 dni”."""
    s = td.total_seconds() if isinstance(td, timedelta) else float(td)
    if s < 3600:
        return f"{max(1, round(s / 60))} min"
    if s < 2 * 86400:
        return f"{round(s / 3600)} h"
    return f"{round(s / 86400)} dni"


def beat_update(beat, ok, info="", error="", now=None):
    """Nowa notka czujnika na podstawie poprzedniej: ok → „OK o …”, błąd → licznik błędów z rzędu i od kiedy."""
    now = now or datetime.utcnow()
    b = dict(beat or {})
    b["at"] = iso(now)
    if ok:
        b.update(ok_at=iso(now), info=str(info or "")[:300], fails=0)
        b.pop("fail_since", None)
    else:
        fails = int(b.get("fails") or 0)
        if not fails:
            b["fail_since"] = iso(now)
        b.update(err_at=iso(now), error=str(error or "nieznany błąd")[:500], fails=fails + 1)
    return b


def job_state(beat, max_age, now=None, started_at=None):
    """Stan pracownika w tle: ('ok' | 'failing' | 'stale' | 'unknown', opis po polsku).
    failing — FAIL_STREAK błędów z rzędu; stale — nie zgłosił się dłużej niż max_age (wątek stanął)."""
    now = now or datetime.utcnow()
    if not beat:
        if started_at and now - started_at > max_age:
            return "stale", "nie zgłosił się ani razu od startu CRM"
        return "unknown", "jeszcze się nie zgłosił (chwilę po wdrożeniu to normalne)"
    fails = int(beat.get("fails") or 0)
    if fails >= FAIL_STREAK:
        return "failing", (f"nie działa od {fmt(parse(beat.get('fail_since')))} ({fails}× z rzędu): "
                           f"{beat.get('error') or 'nieznany błąd'}")
    last = parse(beat.get("at"))
    if last is None or now - last > max_age:
        return "stale", f"stoi: ostatni obieg {fmt(last)}, a powinien co najmniej co {human(max_age)}"
    if fails:
        return "ok", f"chwilowy błąd ({fails}×, ponowi sam): {beat.get('error')}"
    info = beat.get("info")
    return "ok", f"OK {fmt(parse(beat.get('ok_at')))}" + (f" · {info}" if info else "")


def quiet_alarm(times, now=None, min_days=QUIET_MIN_DAYS, factor=QUIET_FACTOR, min_samples=QUIET_MIN_SAMPLES):
    """Cisza dłuższa niż zwykle. times — chwile zdarzeń z ostatnich tygodni (np. wpłynięcia leadów, maile do
    skrzynki). Zwraca opis („ostatnio 10.11 14:02, a zwykle co 5 h”) albo None, gdy wszystko normalnie."""
    now = now or datetime.utcnow()
    ts = sorted(t for t in times if t)
    if len(ts) < min_samples:
        return None
    gaps = sorted((b - a).total_seconds() for a, b in zip(ts, ts[1:]))
    median = gaps[len(gaps) // 2]
    limit = max(timedelta(days=min_days), timedelta(seconds=median * factor))
    if now - ts[-1] <= limit:
        return None
    return f"ostatnio {fmt(ts[-1])} (cisza od {human(now - ts[-1])}), a zwykle co {human(max(median, 60))}"


def job_alarms(beats, jobs, now=None, started_at=None):
    """Alarmy z czujników. jobs: {klucz: (nazwa, max_age)}, beats: {klucz: notka}.
    Zwraca ({klucz alarmu: alarm}, {klucz: (stan, opis)}) — drugie do pokazania wszystkich pracowników."""
    now = now or datetime.utcnow()
    alarms, states = {}, {}
    for key, (name, max_age) in jobs.items():
        st, text = job_state(beats.get(key), max_age, now, started_at)
        states[key] = (st, text)
        if st in ("failing", "stale"):
            alarms[f"job:{key}"] = {"level": "red", "title": f"{name}: {'nie działa' if st == 'failing' else 'stoi'}",
                                    "detail": text, "job": key}
    return alarms, states


def merge_alarms(prev, found, now=None):
    """Łączy nowy obraz z poprzednim: alarm, który trwa, zachowuje „od kiedy”, diagnozę i próby naprawy.
    Zwraca (aktualne, nowe_klucze, zamknięte_klucze)."""
    now = now or datetime.utcnow()
    prev = prev or {}
    out, new = {}, []
    for key, alarm in found.items():
        old = prev.get(key) or {}
        cur = dict(alarm)
        cur["since"] = old.get("since") or iso(now)
        for k in ("auto_tries", "auto_at", "auto_note", "diag_tries"):
            if old.get(k) is not None:
                cur[k] = old[k]
        if old.get("diag") and old.get("title") == cur.get("title"):
            cur["diag"] = old["diag"]
        if not old:
            new.append(key)
        out[key] = cur
    closed = [k for k in prev if k not in found]
    return out, new, closed


# --- kto naprawia: CRM / Claude sam („auto”) czy człowiek („human”) ---
_HUMAN_RE = re.compile(
    r"\b40[13]\b|unauthori[sz]ed|forbidden|invalid[ _-]?(?:api[ _-]?)?key|api[ _-]?key|\btoken\b|hasł|password|"
    r"authenticat|login failed|\[AUTHENTICATIONFAILED\]|credential|brak konfiguracji|brak klucza|niepodłączon|"
    r"nie jest podłączon|ustaw [A-Z][A-Z0-9_]{3,}|quota|insufficient|credit balance|billing|payment required|\b402\b",
    re.I)
_TRANSIENT_RE = re.compile(
    r"timeout|timed out|temporar|\b5\d\d\b|connection (?:reset|refused|aborted)|ConnectionError|RemoteDisconnected|"
    r"EOF occurred|rate limit|\b429\b|overloaded|database is locked|SSLError|\bssl\b|name resolution|"
    r"Max retries exceeded|socket", re.I)
_CODE_RE = re.compile(r"\b(?:TypeError|AttributeError|KeyError|NameError|ValueError|IndexError|UnboundLocalError|"
                      r"ZeroDivisionError|AssertionError|ProgrammingError|InvalidRequestError|DataError|"
                      r"UndefinedColumn|NotNullViolation|StatementError|JSONDecodeError|UnicodeError)\b")
# po ilu próbach / godzinach naprawa automatyczna oddaje alarm człowiekowi
AUTO_LIMIT = {"restart": ("tries", 3), "robot": ("tries", 2), "rediscover": ("tries", 2),
              "wait": ("hours", 6), "code": ("hours", 24)}
HOW_TEXT = {
    "wait": "chwilowy błąd zewnętrznej usługi — CRM ponawia sam",
    "restart": "wątek stanął — CRM uruchamia go ponownie",
    "robot": "CRM od razu tworzy i wgrywa osobowość robota",
    "rediscover": "CRM szuka listy ClickUp od nowa",
    "code": "błąd w kodzie — naprawia go Claude",
    "info": "informacja — nic nie trzeba robić",
}


def classify(key, alarm, now=None):
    """Kto zajmuje się alarmem: ("auto", sposób z HOW_TEXT) albo ("human", powód). Człowiek dostaje tylko to, czego
    bez niego zrobić się nie da (klucz, hasło, konfiguracja, cisza w leadach), i to, czego naprawa automatyczna
    nie usunęła w czasie z AUTO_LIMIT."""
    now = now or datetime.utcnow()
    text = f"{alarm.get('title') or ''} {alarm.get('detail') or ''}"
    if key.startswith("quiet:mail"):
        return "auto", "info"  # skrzynka działa (pilnuje jej czujnik), po prostu mniej maili — nic do zrobienia
    if key.startswith("quiet:"):
        return "human", "cisza w zgłoszeniach — sprawdź formularz, reklamy i ClickUp"
    if _HUMAN_RE.search(text):
        return "human", "potrzebny klucz, hasło albo ustawienie w Railway"
    if key.startswith("robot:"):
        how = "robot"
    elif "stoi" in (alarm.get("title") or ""):
        how = "restart"
    elif "nie znalazłem" in text or "nie widzi listy" in text:
        how = "rediscover"
    elif _CODE_RE.search(text):
        how = "code"
    elif _TRANSIENT_RE.search(text):
        how = "wait"
    else:
        how = "code"
    kind, limit = AUTO_LIMIT[how]
    if kind == "tries" and int(alarm.get("auto_tries") or 0) >= limit:
        return "human", f"naprawa automatyczna („{HOW_TEXT[how]}”) nie pomogła po {limit} próbach"
    since = parse(alarm.get("since"))
    if kind == "hours" and since and now - since >= timedelta(hours=limit):
        return "human", f"naprawa automatyczna („{HOW_TEXT[how]}”) nie pomogła od {human(now - since)}"
    return "auto", how


def human_alarms(alarms):
    """Tylko alarmy, które wymagają człowieka (pasek na górze CRM, licznik w menu)."""
    return [a for a in sorted_alarms(alarms) if a.get("who") == "human"]


def sorted_alarms(alarms):
    """Lista do pokazania: najpierw czerwone, potem najstarsze."""
    return sorted(({"key": k, **v} for k, v in (alarms or {}).items()),
                  key=lambda a: (LEVELS.index(a.get("level")) if a.get("level") in LEVELS else 9, a.get("since") or ""))
