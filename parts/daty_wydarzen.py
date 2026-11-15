# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("daty_wydarzen") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: daty i miejsca dni wynajmu, odczyt dat

def serialize_activity(a):
    lead = a.lead
    urgency = activity_urgency(a)
    color_map = {
        "overdue": "#ef4444", "today": "#f59e0b",
        "soon": "#6366f1", "future": "#10b981", "done": "#6b7280"
    }
    due = a.due_date.date() if isinstance(a.due_date, datetime) else a.due_date
    return {
        "id": a.id,
        "lead_id": a.lead_id,
        "lead_company": lead.company if lead else "",
        "lead_contact": lead.contact_name if lead else "",
        "kind": a.kind,
        "due_date": due.isoformat() if due else None,
        "due_date_display": due.strftime("%d.%m.%Y") if due else "",
        "done": a.done,
        "notes": a.notes or "",
        "assigned_to": a.assigned_to,
        "assigned_name": USERS_BY_ID.get(a.assigned_to, {}).get("name", ""),
        "urgency": urgency,
        "color": color_map[urgency],
    }


def parse_date(s):
    """Parse date input (YYYY-MM-DD) into datetime at midnight."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            continue
    return None


MIN_EVENT_YEAR = 2020  # najstarszy sensowny rok wynajmu (firma nie działała wcześniej)


def sane_event_date(dt):
    """Odrzuca absurdalne daty wynajmu (np. literówkę '0002' zamiast '2026')."""
    if dt is None:
        return None
    if dt.year < MIN_EVENT_YEAR or dt.year > date.today().year + 15:
        return None
    return dt


def parse_dt(s):
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def parse_event_dates(s):
    """Parsuje 'YYYY-MM-DD,YYYY-MM-DD,...' na posortowaną listę unikalnych dat
    wynajmu — niekoniecznie po kolei, np. tylko 1 i 10 września."""
    if not s:
        return []
    days = set()
    for part in s.split(","):
        dt = sane_event_date(parse_dt(part.strip()))
        if dt:
            days.add(dt.date())
    return sorted(days)


# separatory ASCII (rekord/jednostka) — z klawiatury nigdy się nie wpisze, więc bezpiecznie
# rozdzielają pary data+lokalizacja bez ryzyka kolizji z przecinkami w adresie
_EVT_LOC_RS, _EVT_LOC_US = "\x1e", "\x1f"


def parse_event_locations(s):
    """Parsuje '<data><US><lokalizacja><RS><data><US><lokalizacja>...' na dict
    {date: lokalizacja} — nadpisanie lokalizacji tylko na wybrane dni (np. jednego
    dnia Warszawa, drugiego Kraków). Dni bez wpisu dziedziczą lead.location."""
    if not s:
        return {}
    out = {}
    for pair in s.split(_EVT_LOC_RS):
        if _EVT_LOC_US not in pair:
            continue
        raw_date, loc = pair.split(_EVT_LOC_US, 1)
        d = sane_event_date(parse_dt(raw_date.strip()))
        loc = loc.strip()
        if d and loc:
            out[d.date()] = loc[:200]
    return out


def set_lead_event_days(lead, days, locations=None):
    """Nadpisuje zestaw konkretnych dni wynajmu leada (opcjonalnie z osobną
    lokalizacją na dzień) i przelicza pochodne pola event_date/event_end_date
    (najwcześniejszy/najpóźniejszy dzień) oraz rental_days (liczba wybranych dni)
    — używane w kalendarzu i dashboardzie."""
    locations = locations or {}
    existing = [d.event_date for d in lead.event_days] if lead.id else []
    days, _dropped, warnings, _blocked = sales_rules.check_new_event_days(existing, days)
    lead.event_days = [LeadEventDate(event_date=d, location=locations.get(d)) for d in days]
    if days:
        lead.event_date = datetime.combine(days[0], datetime.min.time())
        lead.event_end_date = datetime.combine(days[-1], datetime.min.time()) if len(days) > 1 else None
        lead.rental_days = len(days)
    else:
        lead.event_date = None
        lead.event_end_date = None
    return warnings
