# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("straznik_czujniki") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: strażnik CRM: czujniki pracowników w tle i rejestr wątków

HEALTH_BEAT_PREFIX = "hb:"
HEALTH_STARTED_AT = datetime.utcnow()


def health_beat(name, ok=True, info="", error=""):
    """Czujnik: pracownik w tle po obiegu zapisuje notkę „OK” albo błąd. Własna sesja bazy (nie rusza pracy
    pracownika) i nigdy nie wywraca pracownika — najwyżej notka się nie zapisze."""
    try:
        from sqlalchemy.orm import Session as _BeatSession
        with app.app_context(), _BeatSession(db.engine) as s:
            key = (HEALTH_BEAT_PREFIX + name)[:64]
            row = s.get(AppSetting, key)
            try:
                cur = json.loads(row.value) if row is not None and row.value else {}
            except ValueError:
                cur = {}
            value = json.dumps(health.beat_update(cur, ok, info, error), ensure_ascii=False)
            if row is None:
                s.add(AppSetting(key=key, value=value))
            else:
                row.value = value
            s.commit()
    except Exception:
        app.logger.exception("health_beat %s", name)


def health_fail(name, exc):
    health_beat(name, False, error=f"{type(exc).__name__}: {exc}"[:500])


_WORKERS = {}  # pracownicy w tle: nazwa → (funkcja, wątek) — strażnik uruchamia ponownie ten, który stanął


def _start_worker(name, target):
    t = threading.Thread(target=target, daemon=True, name=f"crm-{name}")
    t.start()
    _WORKERS[name] = (target, t)
    return t
