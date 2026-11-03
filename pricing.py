"""Kalkulator wyceny wynajmu robota na Litwie: stawka za czas na miejscu + dojazd od granicy PL–LT.

Zasady (cennik 33bots LT, EUR, netto):
- 8 h na miejscu = 2100 € (cena startowa), 4 h = 6/8 tej ceny, 2 h = połowa (mnożniki jak w Polsce),
- dojazd = 0,75 € za km w jedną stronę × 2 (tam i z powrotem), km liczone od granicy (Budzisko–Kalvarija),
- każda godzina po 22:00 (do 6:00 rano) liczy się × 1,5 stawki godzinowej pakietu,
- nocleg jest zawsze — jeden za każdy dzień wynajmu (ekipa zostaje na miejscu, jeden dojazd),
- dojazd (razem z noclegami) nie może być więcej niż 30% ceny — jeśli jest, cena idzie
  w górę tak, żeby stanowił dokładnie 30%,
- kilka dni z rzędu: 2 dni −10%, 3 dni i więcej −15% od całości.
Stawki można zmienić w Railway → Variables (CALC_BASE_8H, CALC_KM_RATE, CALC_LODGING, CALC_ROUND_TO).
"""
import math
import os


def _env_num(name, default):
    try:
        return float(os.environ.get(name, "").replace(",", ".")) if os.environ.get(name, "").strip() else default
    except ValueError:
        return default


# LT CRM: ceny w EUR (zmienisz je w Railway → Variables)
BASE_8H = int(_env_num("CALC_BASE_8H", 2100))
HOUR_FACTORS = {8: 8 / 8, 4: 6 / 8, 2: 4 / 8}
KM_RATE = _env_num("CALC_KM_RATE", 0.75)
TRIPS = 2
TRAVEL_MAX_SHARE = 0.30
ROUND_TO = int(_env_num("CALC_ROUND_TO", 50))  # cena „do oferty” zaokrąglona w górę
NIGHT_FROM, NIGHT_TO = 22, 6   # godziny nocne: od 22:00 do 6:00
NIGHT_MULT = 1.5               # godzina nocna = 1,5 × stawka godzinowa pakietu
LODGING_KM = 0                 # nocleg zawsze (każdy dzień wynajmu)
LODGING_COST = int(_env_num("CALC_LODGING", 70))  # € za nocleg (liczy się do limitu dojazdu 30%)
DAY_DISCOUNTS = {2: 0.10, 3: 0.15}  # rabat od całości; więcej dni = jak za 3
MAX_DAYS = 7

# skąd wyjeżdża ekipa (klucze z logistics.CITIES)
DEPARTURES = {"granica": "Granica PL–LT (Budzisko)"}
DEPARTURES_FROM = {"granica": "od granicy"}


def parse_start(text):
    """'20:30' / '20' / '20.30' -> godzina jako liczba (20.5) albo None."""
    t = str(text or "").strip().replace(".", ":").replace(",", ":")
    if not t:
        return None
    hh, _, mm = t.partition(":")
    try:
        h, m = int(hh), int(mm or 0)
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h + m / 60


def night_hours(start, hours):
    """Ile z `hours` godzin od godziny `start` (np. 20.5) wypada w nocy (22:00–6:00)."""
    if start is None:
        return 0.0
    end = start + hours
    total = 0.0
    # okna nocne: poprzednia noc (do 6:00), ta noc (22:00–6:00 następnego dnia)
    for a, b in ((NIGHT_FROM - 24, NIGHT_TO), (NIGHT_FROM, NIGHT_TO + 24)):
        total += max(0.0, min(end, b) - max(start, a))
    return round(total, 2)


def fmt_hour(value):
    value %= 24
    return f"{int(value):02d}:{int(round((value - int(value)) * 60)):02d}"


def day_discount(days):
    return DAY_DISCOUNTS.get(min(days, max(DAY_DISCOUNTS)), 0.0) if days > 1 else 0.0


def quote(hours, km_one_way, night=0.0, days=1):
    """Wycena dla liczby godzin dziennie (2/4/8), odległości w jedną stronę (km), godzin po 22:00
    dziennie (`night`, np. z night_hours()) i liczby dni. Zwraca dict z rozbiciem
    (base, night_extra — za jeden dzień; onsite, travel, lodging — za całość)."""
    if hours not in HOUR_FACTORS:
        raise ValueError(f"czas: {', '.join(str(h) for h in sorted(HOUR_FACTORS))} h")
    days = max(1, min(int(days or 1), MAX_DAYS))
    km = int(round(max(0.0, float(km_one_way or 0))))
    night = min(max(0.0, float(night or 0)), float(hours))
    base = round(BASE_8H * HOUR_FACTORS[hours])
    hourly = base / hours
    night_extra = round(night * hourly * (NIGHT_MULT - 1))
    onsite = (base + night_extra) * days
    stays = True  # LT: nocleg zawsze — ekipa zostaje na miejscu, jeden dojazd
    round_trips = 1 if stays else days
    travel = round(km * KM_RATE * TRIPS) * round_trips
    nights = days if stays else 0
    lodging = LODGING_COST * nights
    trip = travel + lodging  # koszty wyjazdu — to one podlegają limitowi 30%
    subtotal = onsite + trip
    raise_by = 0
    if trip > TRAVEL_MAX_SHARE * subtotal:
        lifted = math.ceil(trip / TRAVEL_MAX_SHARE)
        raise_by, subtotal = lifted - subtotal, lifted
    pct = day_discount(days)
    discount = round(subtotal * pct)
    total = subtotal - discount
    return {
        "hours": hours, "days": days, "km": km, "base": base, "hourly": hourly, "night": night,
        "night_extra": night_extra, "onsite": onsite, "round_trips": round_trips, "travel": travel,
        "nights": nights, "lodging": lodging, "raise_by": raise_by, "subtotal": subtotal,
        "discount_pct": pct, "discount": discount, "total": total,
        "travel_share": trip / subtotal if subtotal else 0.0,
        "rounded": int(math.ceil(total / ROUND_TO) * ROUND_TO),
    }


def fmt_num(value):
    return f"{value:g}".replace(".", ",")


def fmt_zl(value):
    return f"{int(round(value)):,}".replace(",", " ") + " €"


def days_label(n):
    return "1 dzień" if n == 1 else f"{n} dni"


def describe(q, departure_label="", place=""):
    """Rozbicie wyceny jednym tekstem (do notatki w leadzie i dla asystenta)."""
    route = f"{departure_label} → {place}, " if departure_label and place else ""
    rate = f"{KM_RATE:.2f}".replace(".", ",")  # €/km
    days = q.get("days", 1)
    per_day = " / dzień" if days > 1 else ""
    lines = [f"Wycena: {q['hours']} h na miejscu = {fmt_zl(q['base'])}{per_day}"]
    if q.get("night_extra"):
        lines.append(f"Dopłata nocna: {fmt_num(q['night'])} h po {NIGHT_FROM}:00 (× {fmt_num(NIGHT_MULT)}) "
                     f"= +{fmt_zl(q['night_extra'])}{per_day}")
    if days > 1:
        lines.append(f"Na miejscu razem: {days_label(days)} = {fmt_zl(q['onsite'])}")
    one_trip = q["travel"] // max(1, q.get("round_trips", 1))
    trips = f" × {q['round_trips']} (codzienny powrót)" if q.get("round_trips", 1) > 1 else ""
    lines.append(f"Dojazd: {route}≈{q['km']} km × 2 × {rate} € = {fmt_zl(one_trip)}{trips}"
                 + (f" = {fmt_zl(q['travel'])}" if trips else ""))
    if q.get("lodging"):
        nights = q.get("nights", 1)
        lines.append(f"Nocleg: {nights} × {fmt_zl(LODGING_COST)} = +{fmt_zl(q['lodging'])}"
                     if nights > 1 else f"Nocleg: +{fmt_zl(q['lodging'])}")
    if q["raise_by"]:
        lines.append(f"Dojazd{' z noclegiem' if q.get('lodging') else ''} > {int(TRAVEL_MAX_SHARE * 100)}% ceny "
                     f"→ cena podniesiona o {fmt_zl(q['raise_by'])}")
    if q.get("discount"):
        lines.append(f"Rabat za {days_label(days)} (−{q['discount_pct'] * 100:.0f}% od całości "
                     f"{fmt_zl(q['subtotal'])}): −{fmt_zl(q['discount'])}")
    lines.append(f"Razem: {fmt_zl(q['total'])} netto (dojazd{' z noclegiem' if q.get('lodging') else ''} "
                 f"{q['travel_share'] * 100:.0f}% ceny przed rabatem), " if q.get("discount") else
                 f"Razem: {fmt_zl(q['total'])} netto (dojazd{' z noclegiem' if q.get('lodging') else ''} "
                 f"{q['travel_share'] * 100:.0f}% ceny), ")
    lines[-1] += f"do oferty: {fmt_zl(q['rounded'])}"
    return "\n".join(lines)
