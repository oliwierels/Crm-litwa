# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("clickup_mapowanie") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: ClickUp: pola formularzy, telefony, błędy API

CLICKUP_API = "https://api.clickup.com/api/v2"

# fragmenty nazw custom fields w ClickUp (małe litery) -> pole na Lead.
# Dopasowanie jest częściowe (alias jako fragment nazwy pola), więc "Numer telefonu klienta"
# złapie się na aliasie "telefon", "Podaj swój adres e-mail" na "e-mail" itd. — nie trzeba
# znać dokładnej nazwy pola w formularzu ClickUp.
CLICKUP_FIELD_ALIASES = {
    "company": ["nazwa firmy", "firma", "company"],
    "contact_name": [
        "imię i nazwisko", "imie i nazwisko", "osoba kontaktowa", "osoba do kontaktu",
        "nazwisko", "imię", "imie", "contact name", "twoje dane",
    ],
    "contact_phone": ["telefon", "phone", "tel"],
    "contact_email": ["e-mail", "email", "mail"],
    "robot_model": ["robot", "model robota"],
    "value_pln": ["wartość", "wartosc", "budżet", "budzet", "kwota", "cena"],
    "location": ["lokalizacja", "miejsce", "miasto", "location"],
    "currency": ["waluta", "currency"],
}

# awaryjne wyciąganie numeru telefonu z nazwy/opisu taska, gdy nie ma pasującego custom field
_PHONE_PATTERNS = [
    re.compile(r"\+48[\s-]?\d{3}[\s-]?\d{3}[\s-]?\d{3}"),
    re.compile(r"\b\d{3}[\s-]\d{3}[\s-]\d{3}\b"),
    re.compile(r"\b\d{9}\b"),
]

# tytuł taska w stylu "Jan Kowalski" (dwa-trzy wyrazy z wielkiej litery) to zwykle
# imię i nazwisko zgłaszającego, nie nazwa firmy
_PERSON_NAME_RE = re.compile(
    r"^[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+(?:[\s-][A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+){1,2}$"
)


def _extract_phone(text):
    for pat in _PHONE_PATTERNS:
        m = pat.search(text or "")
        if m:
            return m.group(0).strip()
    return None


def _looks_like_person_name(text):
    return bool(_PERSON_NAME_RE.match((text or "").strip()))


class ClickUpAPIError(Exception):
    pass
