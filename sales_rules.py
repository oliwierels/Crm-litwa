"""Reguły procesu sprzedaży 33Bots — wspólne dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach).

Tylko czysta logika (bez bazy i Flaska): listy wartości, bramki etapów, podpowiedź typu klienta,
liczenie prób kontaktu i czasu reakcji w godzinach pracy. CRM woła te funkcje przy zmianie etapu,
wysyłce oferty, odhaczaniu zadań i w statystykach — dzięki temu UI, API i MCP mają te same reguły.

Decyzje (Krok 2), które tu są zapisane — zmiana = edycja stałych poniżej:
- powody straty: LOST_REASONS („inne” wymaga komentarza),
- Wygrany: umowa podpisana ALBO pisemne potwierdzenie mailem + co najmniej jeden dzień wynajmu od 2025 r.
  + wartość > 0 + typ klienta; wartość różna od ostatniej wysłanej oferty = ostrzeżenie do potwierdzenia,
- typ klienta wymagany najpóźniej przy wysłaniu oferty (i przy ręcznym przejściu na Oferta),
- telefon potwierdzający: następny dzień roboczy po ofercie, dla firm / agencji / partnerów i każdej oferty
  od 10 000 zł (równowartość w EUR po kursie), dzwonimy pn–pt 9:00–17:00,
- limit prób kontaktu: 3 nieudane (telefon nieodebrany, SMS / mail bez odpowiedzi), u osób prywatnych 2 →
  zadanie „Zdecyduj” dla opiekuna (bez automatycznego przenoszenia na Stracony),
- czas reakcji: cel 30 min w godzinach pracy pn–pt 9:00–18:00 (lead z wieczora liczy się od 9:00 następnego dnia
  roboczego), alert w CRM (okno alarmów) dla wszystkich,
- osoby prywatne: szkic pierwszej odpowiedzi do zatwierdzenia (nic nie wychodzi samo) + telefon (1–2 próby),
- „Czy ktoś oddzwaniał?”: raz dziennie na każdym komputerze od 16:00 — numer, który oddzwonił, zapisuje się
  przy leadzie jako udany kontakt,
- wyjątki: zmiany robione przez automaty CRM (bez osoby) nie są blokowane; awaryjnie STAGE_GATES=0 wyłącza bramki.
"""
import re
import unicodedata
from datetime import date, datetime, time, timedelta

LOST_REASONS = {
    "cena": "Cena lub budżet",
    "konkurencja": "Wybrali innego dostawcę",
    "brak_decyzji": "Brak decyzji (np. klienta agencji)",
    "brak_kontaktu": "Brak kontaktu",
    "odrzucony": "Odrzuciliśmy sami (słaby klient)",
    "termin": "Termin zajęty lub brak robota",
    "inne": "Inne (wymaga komentarza)",
}
CLIENT_TYPES = {
    "firma": "Firma / instytucja",
    "prywatna": "Osoba prywatna",
    "agencja": "Agencja eventowa",
    "partner": "Firma robotyczna / partner",
}
WIN_CONFIRMATIONS = {
    "umowa": "Umowa podpisana",
    "mail": "Pisemne potwierdzenie mailem",
}
OUTCOMES = {
    "odebral": "Odebrał",
    "nie_odebral": "Nie odebrał",
    "odpisal": "Odpisał",
    "bez_odpowiedzi": "Bez odpowiedzi",
}
FAILED_OUTCOMES = ("nie_odebral", "bez_odpowiedzi")
SUCCESS_OUTCOMES = ("odebral", "odpisal")
OUTCOME_KINDS = ("call", "sms", "email")  # zadania, które przy odhaczeniu wymagają wyniku
CONFIRM_QUESTIONS = (
    ("dotarla", "Czy oferta dotarła?"),
    ("decyzja_kiedy", "Kiedy zapadnie decyzja?"),
    ("decyduje", "Kto decyduje?"),
    ("porownanie", "Z kim nas porównują?"),
)
CONFIRM_CALL_PREFIX = "📞 Telefon potwierdzający"
DECIDE_PREFIX = "⚖️ Zdecyduj"
PRIVATE_DRAFT_PREFIX = "✉️ Pierwsza odpowiedź (osoba prywatna)"
REPLY_CALL_PREFIX = "📞 Klient odpisał"

CONFIRM_CALL_TYPES = ("firma", "agencja", "partner")
CONFIRM_CALL_MIN_PLN = 10000
CONTACT_ATTEMPT_LIMIT = 3
PRIVATE_ATTEMPT_LIMIT = 2  # osoby prywatne: dzwonimy raz, najwyżej dwa razy
CALLBACK_ASK_FROM = 16  # od tej godziny raz dziennie pytanie „czy ktoś oddzwaniał?”
CALLBACK_PREFIX = "📲 Klient oddzwonił"
RESPONSE_TARGET_MIN = 30
WORK_DAYS = (0, 1, 2, 3, 4)  # pn–pt
WORK_START, WORK_END = 9, 18  # godziny pracy (czas reakcji): lead po 18:00 liczy się od 9:00 kolejnego dnia roboczego
CALL_HOURS = "pn–pt 9:00–17:00"
MIN_NEW_EVENT_YEAR = 2025  # nowych dni wynajmu sprzed 2025 r. nie da się zapisać (literówka „0002”)
LONG_RANGE_DAYS = 14  # dłuższy zakres = ostrzeżenie
MAX_EVENT_DAYS = 62  # więcej dni naraz = na pewno pomyłka (np. rozpisany rok)
PRIVATE_PACKAGE_HOURS = 2
PRIVATE_PACKAGE_TEXT = "2 h od 2 750 zł netto + dojazd"
WON = ("Wygrany", "Zrealizowany")

COUNTRIES = {"PL": "Polska", "LT": "Litwa", "DE": "Niemcy", "RO": "Rumunia", "AT": "Austria", "CH": "Szwajcaria",
             "CZ": "Czechy", "SK": "Słowacja", "LV": "Łotwa", "EE": "Estonia", "UA": "Ukraina", "INNY": "Inny"}

_FREE_MAIL = ("gmail.", "googlemail.", "wp.pl", "o2.pl", "onet.", "op.pl", "interia.", "poczta.", "yahoo.",
              "outlook.", "hotmail.", "live.", "icloud.", "me.com", "gmx.", "web.de", "t-online.", "proton",
              "inbox.lt", "one.lt", "zoho.", "aol.", "tlen.pl", "vp.pl", "int.pl", "spoko.pl", "buziaczek.pl")
_PARTNER_RE = re.compile(r"robot|unitree|humanoid|automati|\bai\b|mechatroni", re.I)
_AGENCY_RE = re.compile(r"event|agencj|agency|agentur|eventow|\bproduction|produkcj|incentive|\bmice\b|"
                        r"marketing|promo|\bshow\b|rozrywk|entertainment|wedding|ślub|wesel", re.I)
_COMPANY_RE = re.compile(r"sp\.?\s*z\s*o\.?\s*o|\bs\.?\s?a\.?$|\bs\.?k\.?a?\b|spółk|gmbh|\bag\b|\buab\b|\bltd\b|"
                         r"\binc\b|\bllc\b|\bsrl\b|\bs\.r\.l|fundacj|stowarzysz|urząd|urzad|gmina|powiat|szkoł|"
                         r"przedszkol|uniwersyt|politechni|akademi|hotel|bank|muzeum|centrum|instytut|ministerst|"
                         r"starostw|\bmiasto\b|biblioteka|kościół|parafia|klub|zespół|zespol|grupa|group|holding|"
                         r"\bkg\b|\bo\.o\.|\bbv\b|\bab\b|\boy\b|firma|company|corp", re.I)


def _norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return re.sub(r"[^a-z0-9 ]", "", "".join(c for c in s if not unicodedata.combining(c))).strip()


def suggest_client_type(company, contact_name="", emails=(), nip=None, notes=""):
    """Podpowiedź typu klienta: (klucz z CLIENT_TYPES, uzasadnienie). Tylko podpowiedź — wybiera człowiek."""
    comp = (company or "").strip()
    text = f"{comp} {' '.join(emails or ())}"
    if _PARTNER_RE.search(text):
        return "partner", "w nazwie / adresie jest robotyka"
    if _AGENCY_RE.search(text):
        return "agencja", "w nazwie / adresie jest event / agencja"
    if (nip or "").strip() or _COMPANY_RE.search(comp):
        return "firma", "nazwa firmy albo NIP"
    domains = [e.split("@")[-1].lower() for e in emails or () if "@" in e]
    free = bool(domains) and all(d.startswith(_FREE_MAIL) or any(f in d for f in _FREE_MAIL) for d in domains)
    same = bool(comp) and _norm(comp) == _norm(contact_name)
    looks_person = bool(re.fullmatch(r"[A-ZĄĆĘŁŃÓŚŹŻČĖĮŠŲŪŽÄÖÜ][\wąćęłńóśźżčėįšųūžäöüß'-]+"
                                     r"(\s+[A-ZĄĆĘŁŃÓŚŹŻČĖĮŠŲŪŽÄÖÜ][\wąćęłńóśźżčėįšųūžäöüß'-]+){1,2}", comp))
    if same or (free and (looks_person or not comp)) or re.search(r"urodzin|wesel|komuni|chrzcin|18-?stk|jubileusz",
                                                                   f"{comp} {notes or ''}", re.I) and free:
        return "prywatna", "nazwa = imię i nazwisko / prywatny e-mail"
    return "firma", "domyślnie"


def suggest_country(location="", currency="", emails=(), phone="", billing_country="", lang=""):
    """Podpowiedź kraju leada (PL, LT, DE, RO…): kraj z faktury, język klienta, domena maila, waluta, miejsce."""
    bc = (billing_country or "").upper()
    if bc in COUNTRIES:
        return bc
    if lang == "lt":
        return "LT"
    if lang == "de":
        return "DE"
    tlds = {e.lower().rsplit(".", 1)[-1] for e in emails or () if "." in e}
    for tld, code in (("lt", "LT"), ("de", "DE"), ("ro", "RO"), ("at", "AT"), ("ch", "CH"), ("cz", "CZ"), ("sk", "SK")):
        if tld in tlds:
            return code
    digits = re.sub(r"\D", "", phone or "")
    if (phone or "").strip().startswith(("+40", "0040")) or digits.startswith("0040"):
        return "RO"
    loc = (location or "").lower()
    if re.search(r"rumuni|romania|bucure|bukareszt|cluj|timi[sș]oara|ia[sș]i\b|constan", loc):
        return "RO"
    if re.search(r"niemcy|deutschland|germany|berlin|münchen|munchen|hamburg|frankfurt|köln|koln|düsseldorf|leipzig|dresden", loc):
        return "DE"
    if re.search(r"litw|lithuan|lietuv|vilni|wilno|kaun|kowno|klaip", loc):
        return "LT"
    if (currency or "").upper() == "EUR":
        return "DE" if "de" in tlds else "INNY"
    return "PL"


def gates_enabled(env):
    return (env.get("STAGE_GATES", "1") or "1").strip() != "0"


def stage_gate(lead, new_stage, data, *, event_dates=(), last_offer_value=None, contract_signed=False):
    """Sprawdza przejście `lead` na `new_stage`. data = pola z formularza / API / MCP (lost_reason, lost_comment,
    win_confirmation, client_type, confirm_warning). Zwraca dict:
      need     — brakujące pola do uzupełnienia w okienku (lost_reason, lost_comment, win_confirmation, client_type),
      errors   — braki, których okienko nie załatwi (data wydarzenia, wartość) — trzeba poprawić leada,
      warnings — ostrzeżenia do potwierdzenia (confirm_warning=1),
      message  — czytelny komunikat po polsku (pusty = można zmieniać)."""
    data = data or {}
    need, errors, warnings = [], [], []
    old = lead.stage
    client_type = (data.get("client_type") or getattr(lead, "client_type", None) or "").strip()
    if new_stage == "Stracony" and old != "Stracony":
        reason = (data.get("lost_reason") or "").strip()
        if reason not in LOST_REASONS:
            need.append("lost_reason")
        elif reason == "inne" and not (data.get("lost_comment") or "").strip():
            need.append("lost_comment")
    if new_stage in WON and old not in WON:
        conf = (data.get("win_confirmation") or "").strip()
        if conf not in WIN_CONFIRMATIONS and not contract_signed:
            need.append("win_confirmation")
        if client_type not in CLIENT_TYPES:
            need.append("client_type")
        good_days = [d for d in event_dates if d and d.year >= MIN_NEW_EVENT_YEAR]
        if not good_days:
            errors.append("brak daty wydarzenia — dodaj w leadzie co najmniej jeden dzień wynajmu (od 2025 r.)")
        value = getattr(lead, "value_pln", None)
        if not value or value <= 0:
            errors.append("brak wartości zlecenia — wpisz w leadzie kwotę netto większą od zera")
        elif last_offer_value and abs(float(value) - float(last_offer_value)) > max(1.0, 0.01 * float(last_offer_value)):
            if not str(data.get("confirm_warning") or "").strip() in ("1", "true", "True", "tak"):
                warnings.append(f"wartość leada {value:,.0f} różni się od ostatniej wysłanej oferty "
                                f"{float(last_offer_value):,.0f} — sprawdź kwotę".replace(",", " "))
    if new_stage == "Oferta" and old == "Nowy" and client_type not in CLIENT_TYPES:
        need.append("client_type")
    parts = []
    labels = {"lost_reason": "powód straty", "lost_comment": "komentarz do powodu „inne”",
              "win_confirmation": "potwierdzenie (umowa podpisana albo pisemne potwierdzenie mailem)",
              "client_type": "typ klienta"}
    if need:
        parts.append("uzupełnij: " + ", ".join(labels[n] for n in need))
    parts += errors
    parts += warnings
    msg = f"Nie można zmienić etapu na {new_stage}: " + "; ".join(parts) if parts else ""
    return {"need": need, "errors": errors, "warnings": warnings, "message": msg}


def needs_confirm_call(client_type, value_pln_equiv):
    """Telefon potwierdzający po ofercie: firma / agencja / partner albo oferta od 10 000 zł."""
    return client_type in CONFIRM_CALL_TYPES or (value_pln_equiv or 0) >= CONFIRM_CALL_MIN_PLN


def attempt_limit(client_type):
    """Ile nieudanych prób z rzędu, zanim CRM każe zdecydować: osoby prywatne 2, reszta 3."""
    return PRIVATE_ATTEMPT_LIMIT if client_type == "prywatna" else CONTACT_ATTEMPT_LIMIT


def phone_key(phone):
    """Ostatnie 9 cyfr numeru — porównanie numerów zapisanych różnie (+48 600-100-200 / 600100200 / 0048…)."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-9:] if len(digits) >= 7 else ""


def next_work_day(d, n=1):
    """n-ty dzień roboczy (pn–pt) po dniu d."""
    out = d
    while n > 0:
        out += timedelta(days=1)
        if out.weekday() in WORK_DAYS:
            n -= 1
    return out


def working_minutes(start_local, end_local):
    """Minuty w godzinach pracy (pn–pt 9–18) między dwoma chwilami czasu lokalnego (naive)."""
    if not start_local or not end_local or end_local <= start_local:
        return 0
    total = 0.0
    day = start_local.date()
    while day <= end_local.date():
        if day.weekday() in WORK_DAYS:
            a = max(start_local, datetime.combine(day, time(WORK_START)))
            b = min(end_local, datetime.combine(day, time(WORK_END)))
            if b > a:
                total += (b - a).total_seconds() / 60
        day += timedelta(days=1)
        if (day - start_local.date()).days > 400:  # zabezpieczenie przed starymi leadami
            break
    return int(round(total))


def contact_attempts(events):
    """events: lista (kiedy, "fail"/"success"). Zwraca liczbę nieudanych prób od ostatniego udanego kontaktu."""
    failed = 0
    for _when, kind in sorted(events, key=lambda e: e[0] or datetime.min):
        if kind == "success":
            failed = 0
        elif kind == "fail":
            failed += 1
    return failed


def check_new_event_days(existing, new_days):
    """Nowe dni wynajmu: odrzuca dni sprzed MIN_NEW_EVENT_YEAR (stare, już zapisane zostają) i dzieli na
    (dni do zapisu, odrzucone, ostrzeżenia). Ostrzeżenie: zakres > LONG_RANGE_DAYS dni; ponad MAX_EVENT_DAYS = błąd."""
    existing = set(existing or ())
    keep, dropped, warnings = [], [], []
    for d in sorted(set(new_days or ())):
        if d.year < MIN_NEW_EVENT_YEAR and d not in existing:
            dropped.append(d)
        else:
            keep.append(d)
    if dropped:
        warnings.append("pominięto daty sprzed 2025 r.: " + ", ".join(f"{d:%d.%m.%Y}" for d in dropped))
    if len(keep) > MAX_EVENT_DAYS and len(keep) > len(existing):
        warnings.append(f"zapisano tylko istniejące dni — {len(keep)} dni naraz to na pewno pomyłka (limit {MAX_EVENT_DAYS})")
        return sorted(existing), dropped, warnings, True
    if keep and (keep[-1] - keep[0]).days + 1 > LONG_RANGE_DAYS:
        warnings.append(f"zakres dni wynajmu ma {(keep[-1] - keep[0]).days + 1} dni (od {keep[0]:%d.%m.%Y} "
                        f"do {keep[-1]:%d.%m.%Y}) — sprawdź, czy to nie pomyłka")
    return keep, dropped, warnings, False


def private_draft(lead_company, contact_name, price_text, location="", event_dates=(), sender_name="", sender_phone=""):
    """Szkic pierwszej odpowiedzi dla osoby prywatnej: krótki pakiet, cena, pytanie o budżet. (temat, treść)"""
    hello = f"Dzień dobry{(' ' + contact_name.split()[0]) if (contact_name or '').strip() else ''},"
    when = ""
    if event_dates:
        when = " na " + ", ".join(f"{d:%d.%m.%Y}" for d in list(event_dates)[:3])
    where = f" ({location})" if location else ""
    body = (f"{hello}\n\n"
            f"dziękuję za zapytanie o robota humanoidalnego{when}{where}.\n\n"
            f"Najczęściej na przyjęcia prywatne wybierany jest krótki pakiet: {price_text}. "
            f"W tym czasie robot wita gości, rozmawia z nimi, tańczy i pozuje do zdjęć, a cały czas jest z nim nasz operator.\n\n"
            f"Jaki budżet Państwo przewidują i ile godzin robot miałby być na miejscu? "
            f"Dopasuję propozycję i od razu potwierdzę dostępność terminu.\n\n"
            f"Pozdrawiam serdecznie\n{sender_name}" + (f"\n{sender_phone}" if sender_phone else ""))
    subject = "Robot humanoidalny na Państwa przyjęcie — propozycja"
    return subject, body
