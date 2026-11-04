"""Customer Journey (posprzedaż) — czyste funkcje, bez Flaska i bazy; crm.py podaje im dane.

Umowa DOCX z naszego wzoru (klient sam uzupełnia swoje dane), konwersja odesłanych dokumentów
do PDF, tekst z załączników (dla Claude'a), treści maili do klienta oraz reguły kanbana:
w której kolumnie jest klient, co trzeba zrobić i jak pilne to jest."""
import io
import os
import re
import shutil
import subprocess
import tempfile
import threading
from datetime import date, timedelta

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fonts")
_DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "docs")
KB_INSTRUCTION_PATH = os.path.join(_DOCS_DIR, "Roboto_ziniu_baze_instrukcija.pdf")
KB_INSTRUCTION_NAME = "Roboto_ziniu_baze_instrukcija.pdf"
# instrukcja bazy wiedzy w języku rynku: (ścieżka, nazwa załącznika)
KB_INSTRUCTIONS = {
    "lt": (KB_INSTRUCTION_PATH, KB_INSTRUCTION_NAME),
    "de": (os.path.join(_DOCS_DIR, "Roboter_Wissensdatenbank_Anleitung.pdf"), "Roboter_Wissensdatenbank_Anleitung.pdf"),
}


def kb_instruction(code="lt"):
    return KB_INSTRUCTIONS.get(code) or KB_INSTRUCTIONS["lt"]
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# --- kanban ---

# Tylko umowa i baza wiedzy. Baza to znacznik na karcie podpisanych (czerwono, gdy jej brak), a logistyka żyje
# wyłącznie w swojej zakładce — Customer Journey jej nie dotyka. Zrealizowane (wszystkie, bez wyjątku) = archiwum.
COLUMNS = [
    ("umowa", "Umowa do wysłania",
     "CRM ma gotowego maila z umową (DOCX) — sprawdź i wyślij. Instrukcja bazy wiedzy to osobny mail. Sam nic nie wysyła."),
    ("czekamy", "Czekamy na podpis",
     "Gdy klient odeśle podpisaną umowę, CRM zauważy to w skrzynce i przesunie kartę. "
     "Gdy milczy — pojawi się przypomnienie do akceptu."),
    ("podpisane", "Podpisane — brak bazy wiedzy",
     "Umowa jest, brakuje bazy wiedzy. Ustaw „Baza wiedzy: jest” albo CRM zrobi to sam, gdy klient ją przyśle."),
    ("odhaczone", "Odhaczone", "Umowa i baza wiedzy są — wszystko klepnięte, czekamy na wydarzenie."),
    ("archiwum", "Archiwum", "Zrealizowane — umowy i bazy wiedzy z przeszłości."),
]
COLUMN_KEYS = [k for k, _, _ in COLUMNS]
COLUMN_LABELS = {k: label for k, label, _ in COLUMNS}
BOARD_COLUMNS = COLUMNS[:4]  # archiwum to lista pod tablicą

AFTER_ITEMS = [
    ("faktura", "Faktura wystawiona"),
    ("platnosc", "Faktura opłacona"),
    ("opinia", "Prośba o opinię wysłana"),
]

CONTRACT_STATUSES = {"do_wyslania": "do wysłania", "wyslana": "wysłana", "podpisana": "podpisana"}
KB_STATUSES = {"brak": "nie wysłano prośby", "wyslana": "prośba wysłana", "otrzymana": "otrzymana"}

DEFAULTS = {
    "remind_first": 3,     # pierwsze przypomnienie o umowie: tyle dni po wysłaniu
    "remind_every": 4,     # kolejne co tyle dni
    "kb_deadline": 7,      # baza wiedzy najpóźniej tyle dni przed wydarzeniem
    "kb_remind_from": 21,  # przypomnienia o bazie od tylu dni przed wydarzeniem
    "red": 7,              # umowa niepodpisana, a do wydarzenia ≤ tyle dni -> czerwony
    "yellow": 14,
}


def after_complete(after):
    return all((after or {}).get(k) for k, _ in AFTER_ITEMS)


def column(st, stage):
    """Kolumna kanbana = pierwszy niespełniony krok; zrealizowane zawsze w archiwum (z umową czy bez).
    st: obiekt z polami Journey (statusy, flagi)."""
    if stage == "Zrealizowany":
        return "archiwum"
    if st.contract_status == "do_wyslania":
        return "umowa"
    if st.contract_status != "podpisana":
        return "czekamy"
    if st.kb_status != "otrzymana":
        return "podpisane"
    return "odhaczone"


def next_event_day(days, today):
    """Najbliższy dzień wydarzenia od dziś (albo ostatni miniony, gdy wszystkie już były)."""
    days = sorted(d for d in days if d)
    upcoming = [d for d in days if d >= today]
    if upcoming:
        return upcoming[0]
    return days[-1] if days else None


def urgency(st, stage, days, today, cfg):
    """Kolor karty: red / yellow / ok / nodate / None (spokojnie; archiwum zawsze spokojne).
    Podpisane: bez bazy wiedzy czerwony, z bazą „ok”. Niepodpisane: im bliżej wydarzenia, tym ostrzej
    ('nodate' = brak daty wynajmu)."""
    if stage == "Zrealizowany":
        return None
    if st.contract_status == "podpisana":
        return "ok" if st.kb_status == "otrzymana" else "red"
    event = next_event_day(days, today)
    if event is None:
        return "nodate"
    left = (event - today).days
    if left <= cfg["red"]:
        return "red"
    if left <= cfg["yellow"]:
        return "yellow"
    return None


def _due(start, first, every, reminded, count):
    """Termin kolejnego przypomnienia: start + first dni, potem co every dni od ostatniego."""
    if not start:
        return None
    if reminded and count:
        return reminded + timedelta(days=every)
    return start + timedelta(days=first)


def actions(st, stage, days, today, cfg, unseen_docs=0, pending_suggestions=0):
    """Co trzeba zrobić przy tym kliencie — lista (klucz, etykieta, rodzaj maila albo None, pilne?).
    Rodzaj maila = szkic do akceptu (CRM sam nic nie wysyła)."""
    out = []
    event = next_event_day(days, today)
    if stage == "Zrealizowany":
        # archiwum: nic tu nie pali się na czerwono ani nie wisi w liczniku; faktura i opinia — przez miesiąc
        after = st.after or {}
        if pending_suggestions:
            out.append(("sugestia", "Potwierdź, co CRM znalazł w skrzynce", None, False))
        if event and (today - event).days <= 30:
            if not after.get("faktura"):
                out.append(("faktura", "Wystaw fakturę", None, False))
            if not after.get("opinia"):
                out.append(("opinia", "Poproś o opinię", "opinia", False))
        return out
    if st.needs_review:
        out.append(("sprawdz", "Potwierdź, co już poszło (lead sprzed Customer Journey)", None, True))
    if pending_suggestions:
        out.append(("sugestia", "Potwierdź, co CRM znalazł w skrzynce", None, True))
    if unseen_docs:
        out.append(("dokument", "Nowy dokument od klienta", None, True))
    # lead sprzed Customer Journey też ma przyciski maili — wysłanie umowy/instrukcji samo odpowiada na pytanie
    if st.contract_status == "do_wyslania":
        out.append(("wyslij_umowe", "Wyślij umowę", "umowa", True))
    if st.contract_status == "wyslana":
        due = _due(st.contract_sent_at, cfg["remind_first"], cfg["remind_every"],
                   st.contract_reminded_at, st.contract_reminders)
        if due and today >= due:
            out.append(("przypomnij_umowe", "Przypomnij o umowie", "umowa_przypomnienie", True))
    if st.contract_status == "do_wyslania" and st.kb_status == "brak":
        # do wyboru: umowa i instrukcja bazy w jednym mailu (osobne przyciski zostają)
        out.append(("wyslij_umowe_i_baze", "Wyślij umowę + instrukcję bazy w jednym mailu", "umowa_baza", False))
    if st.kb_status != "otrzymana":  # osobny mail z instrukcją — niezależnie od umowy
        if st.kb_status == "brak":
            out.append(("wyslij_baze", "Wyślij instrukcję bazy wiedzy", "baza", True))
        elif event:
            window = event - timedelta(days=cfg["kb_remind_from"])
            last = st.kb_reminded_at if st.kb_reminders else st.kb_sent_at
            due = max(window, (last + timedelta(days=cfg["remind_every"])) if last else window)
            if today >= due:
                out.append(("przypomnij_baze", "Przypomnij o bazie wiedzy", "baza_przypomnienie", True))
    return out


# --- kwoty słownie (polska odmiana) ---

_UNITS = ["", "jeden", "dwa", "trzy", "cztery", "pięć", "sześć", "siedem", "osiem", "dziewięć"]
_TEENS = ["dziesięć", "jedenaście", "dwanaście", "trzynaście", "czternaście", "piętnaście",
          "szesnaście", "siedemnaście", "osiemnaście", "dziewiętnaście"]
_TENS = ["", "", "dwadzieścia", "trzydzieści", "czterdzieści", "pięćdziesiąt", "sześćdziesiąt",
         "siedemdziesiąt", "osiemdziesiąt", "dziewięćdziesiąt"]
_HUNDREDS = ["", "sto", "dwieście", "trzysta", "czterysta", "pięćset", "sześćset", "siedemset",
             "osiemset", "dziewięćset"]
_GROUPS = [None, ("tysiąc", "tysiące", "tysięcy"), ("milion", "miliony", "milionów"),
           ("miliard", "miliardy", "miliardów")]


def plural(n, forms):
    """forms = (1, 2-4, 5+): 1 złoty, 2 złote, 5 złotych, 22 złote, 12 złotych."""
    if n == 1:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def _below_thousand(n):
    words = [_HUNDREDS[n // 100]]
    rest = n % 100
    if 10 <= rest <= 19:
        words.append(_TEENS[rest - 10])
    else:
        words += [_TENS[rest // 10], _UNITS[rest % 10]]
    return " ".join(w for w in words if w)


def number_words(n):
    """4900 -> 'cztery tysiące dziewięćset'."""
    n = int(n)
    if n == 0:
        return "zero"
    parts = []
    for g in range(len(_GROUPS) - 1, -1, -1):
        chunk = (n // 1000 ** g) % 1000
        if not chunk:
            continue
        if g == 0:
            parts.append(_below_thousand(chunk))
        elif chunk == 1:
            parts.append(_GROUPS[g][0])
        else:
            parts.append(f"{_below_thousand(chunk)} {plural(chunk, _GROUPS[g])}")
    return " ".join(parts)


CURRENCY_WORDS = {
    "PLN": ("złoty", "złote", "złotych"),
    "EUR": ("euro", "euro", "euro"),
    "USD": ("dolar", "dolary", "dolarów"),
    "GBP": ("funt", "funty", "funtów"),
}
CURRENCY_SYMBOL = {"PLN": "PLN", "EUR": "EUR", "USD": "USD", "GBP": "GBP"}


def amount_words(amount, currency="PLN"):
    """6027 -> 'sześć tysięcy dwadzieścia siedem złotych 00/100'."""
    whole, cents = divmod(int(round(float(amount) * 100)), 100)
    forms = CURRENCY_WORDS.get(currency, (currency, currency, currency))
    return f"{number_words(whole)} {plural(whole, forms)} {cents:02d}/100"


_LT_UNITS = ["", "vienas", "du", "trys", "keturi", "penki", "šeši", "septyni", "aštuoni", "devyni"]
_LT_TEENS = ["dešimt", "vienuolika", "dvylika", "trylika", "keturiolika", "penkiolika", "šešiolika",
             "septyniolika", "aštuoniolika", "devyniolika"]
_LT_TENS = ["", "", "dvidešimt", "trisdešimt", "keturiasdešimt", "penkiasdešimt", "šešiasdešimt",
            "septyniasdešimt", "aštuoniasdešimt", "devyniasdešimt"]
_LT_GROUPS = [None, ("tūkstantis", "tūkstančiai", "tūkstančių"), ("milijonas", "milijonai", "milijonų"),
              ("milijardas", "milijardai", "milijardų")]
LT_CURRENCY_WORDS = {"EUR": ("euras", "eurai", "eurų"), "PLN": ("zlotas", "zlotai", "zlotų"),
                     "USD": ("doleris", "doleriai", "dolerių"), "GBP": ("svaras", "svarai", "svarų")}


def lt_plural(n, forms):
    """forms = (1, 2-9, 10-20 i dziesiątki): 1 euras, 2 eurai, 10 eurų, 21 euras, 11 eurų."""
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 9 and not 11 <= n % 100 <= 19:
        return forms[1]
    return forms[2]


def _lt_below_thousand(n):
    words = []
    h, rest = divmod(n, 100)
    if h:
        words += ["šimtas"] if h == 1 else [_LT_UNITS[h], "šimtai"]
    if 10 <= rest <= 19:
        words.append(_LT_TEENS[rest - 10])
    else:
        words += [_LT_TENS[rest // 10], _LT_UNITS[rest % 10]]
    return " ".join(w for w in words if w)


def lt_number_words(n):
    """1300 -> 'vienas tūkstantis trys šimtai'."""
    n = int(n)
    if n == 0:
        return "nulis"
    parts = []
    for g in range(len(_LT_GROUPS) - 1, -1, -1):
        chunk = (n // 1000 ** g) % 1000
        if not chunk:
            continue
        parts.append(_lt_below_thousand(chunk) if g == 0 else f"{_lt_below_thousand(chunk)} {lt_plural(chunk, _LT_GROUPS[g])}")
    return " ".join(parts)


def lt_amount_words(amount, currency="EUR"):
    """1300.5 -> 'vienas tūkstantis trys šimtai eurų 50 ct'."""
    whole, cents = divmod(int(round(float(amount) * 100)), 100)
    forms = LT_CURRENCY_WORDS.get(currency, (currency, currency, currency))
    return f"{lt_number_words(whole)} {lt_plural(whole, forms)} {cents:02d} ct"


_DE_UNITS = ["", "eins", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun"]
_DE_TEENS = ["zehn", "elf", "zwölf", "dreizehn", "vierzehn", "fünfzehn", "sechzehn", "siebzehn", "achtzehn",
             "neunzehn"]
_DE_TENS = ["", "", "zwanzig", "dreißig", "vierzig", "fünfzig", "sechzig", "siebzig", "achtzig", "neunzig"]
DE_CURRENCY_WORDS = {"EUR": "Euro", "PLN": "Złoty", "USD": "US-Dollar", "GBP": "Pfund Sterling"}


def _de_below_hundred(n, final=True):
    if n == 0:
        return ""
    if n == 1:
        return "eins" if final else "ein"
    if n < 10:
        return _DE_UNITS[n]
    if n < 20:
        return _DE_TEENS[n - 10]
    t, u = divmod(n, 10)
    if not u:
        return _DE_TENS[t]
    return ("ein" if u == 1 else _DE_UNITS[u]) + "und" + _DE_TENS[t]


def _de_below_thousand(n, final=True):
    h, rest = divmod(n, 100)
    head = ("einhundert" if h == 1 else _DE_UNITS[h] + "hundert") if h else ""
    return head + _de_below_hundred(rest, final)


def de_number_words(n):
    """2125 -> 'zweitausendeinhundertfünfundzwanzig', 1000000 -> 'eine Million'."""
    n = int(n)
    if n == 0:
        return "null"
    parts = []
    mil, rest = divmod(n, 1_000_000)
    if mil:
        parts.append("eine Million" if mil == 1 else f"{_de_below_thousand(mil, final=False)} Millionen")
    th, rest = divmod(rest, 1000)
    word = ""
    if th:
        word += ("eintausend" if th == 1 else _de_below_thousand(th, final=False) + "tausend")
    word += _de_below_thousand(rest, final=True)
    if word:
        parts.append(word)
    return " ".join(parts)


def de_amount_words(amount, currency="EUR"):
    """2100 -> 'zweitausendeinhundert Euro 00/100'."""
    whole, cents = divmod(int(round(float(amount) * 100)), 100)
    return f"{de_number_words(whole)} {DE_CURRENCY_WORDS.get(currency, currency)} {cents:02d}/100"


def amount_words_for(code, amount, currency="EUR"):
    """Kwota słownie w języku rynku (umowa)."""
    return de_amount_words(amount, currency) if code == "de" else lt_amount_words(amount, currency)


def money(amount):
    """4900 -> '4 900,00'."""
    return f"{float(amount):,.2f}".replace(",", " ").replace(".", ",")


# --- wzór umowy (klient uzupełnia swoje dane w DOCX) ---

DOTS = "…………………………"
CLIENT_BLANK = (
    "………………………………………… (įmonės / įstaigos pavadinimas arba vardas ir pavardė), buveinės / gyvenamosios vietos "
    "adresas: ………………………………………………, įmonės kodas: ……………………, PVM mokėtojo kodas: ……………………, "
    "atstovaujama: ………………………………… (vardas, pavardė, pareigos)"
)

CLIENT_BLANKS = {
    "lt": CLIENT_BLANK,
    "de": ("………………………………………… (Firma / Name der Einrichtung bzw. Vor- und Nachname), Sitz / Anschrift: "
           "………………………………………………, Handelsregisternummer: ……………………, USt-IdNr.: ……………………, "
           "vertreten durch: ………………………………… (Vor- und Nachname, Funktion)"),
}

# pola dostępne we wzorze ({{ nazwa }}) — także we wgranym własnym DOCX
FIELDS_DOC = [
    ("klient_opis", "Opis Zamawiającego — w wysyłanej umowie miejsce do uzupełnienia przez klienta"),
    ("nazwa_wydarzenia", "Nazwa wydarzenia"),
    ("daty_wydarzenia", "Dni wydarzenia, np. 17.10.2026"),
    ("data_wydarzenia", "Pierwszy dzień wydarzenia"),
    ("liczba_dni", "Liczba dni"),
    ("godziny", "Godziny realizacji"),
    ("miejsce_wydarzenia", "Adres miejsca wydarzenia"),
    ("charakter_wydarzenia", "Charakter wydarzenia"),
    ("rodzaj_przestrzeni", "Gdzie będzie pracował robot"),
    ("robot", "Model robota"),
    ("cena_netto", "Cena netto, np. 6 500,00"),
    ("cena_netto_slownie", "Cena netto słownie (w języku rynku: litewski / niemiecki)"),
    ("vat_stawka", "Stawka VAT, np. 0% (odwrotne obciążenie)"),
    ("vat_kwota", "Kwota VAT"),
    ("cena_brutto", "Cena brutto"),
    ("cena_brutto_slownie", "Cena brutto słownie (w języku rynku: litewski / niemiecki)"),
    ("waluta", "Waluta, np. EUR"),
    ("numer_umowy", "Numer umowy (nr leada/rok)"),
    ("data_umowy", "Data zawarcia — do uzupełnienia"),
    ("miasto", "Miejscowość zawarcia — do uzupełnienia"),
    ("email", "E-mail klienta z leada"),
    ("telefon", "Telefon klienta z leada"),
    ("nazwa", "Nazwa klienta — do uzupełnienia"),
    ("reprezentant", "Osoba podpisująca — do uzupełnienia"),
    ("reprezentant_funkcja", "Funkcja osoby podpisującej — do uzupełnienia"),
    ("adres", "Adres klienta — do uzupełnienia"),
    ("nip", "NIP — do uzupełnienia"),
    ("regon", "REGON — do uzupełnienia"),
    ("krs", "KRS — do uzupełnienia"),
    ("typ_klienta", "Typ klienta — do uzupełnienia"),
    ("email_faktury", "E-mail do faktury — do uzupełnienia"),
    ("osoba_na_miejscu", "Osoba kontaktowa na miejscu — do uzupełnienia"),
    ("telefon_na_miejscu", "Telefon na miejscu — do uzupełnienia"),
    ("data_wypelnienia", "(nieużywane — zostaje puste)"),
    ("uwagi", "(nieużywane — zostaje puste)"),
]
KNOWN_FIELDS = {k for k, _ in FIELDS_DOC}

# pola, które człowiek może poprawić w okienku przed wysyłką umowy
EDITABLE_FIELDS = [
    ("nazwa_wydarzenia", "Nazwa wydarzenia"),
    ("daty_wydarzenia", "Dni wydarzenia"),
    ("godziny", "Godziny realizacji"),
    ("miejsce_wydarzenia", "Miejsce realizacji (adres)"),
    ("charakter_wydarzenia", "Charakter wydarzenia"),
    ("rodzaj_przestrzeni", "Gdzie będzie pracował robot"),
    ("robot", "Robot"),
    ("cena_netto", "Cena netto"),
    ("waluta", "Waluta"),
    ("vat", "VAT %"),
]

# treść umowy 33Bots — ta sama co w dawnym generatorze PDF (draft 4)
DEFAULT_TEMPLATE = """# HUMANOIDINIO ROBOTO SUTEIKIMO PASLAUGŲ SUTARTIS Nr. {{ numer_umowy }}
**Sudaryta {{ data_umowy }}, {{ miasto }} (toliau – „Sutartis“), tarp:**
**Užsakovo:** {{ klient_opis }}
ir
**Vykdytojo:** Bartosz Wysocki, vykdantis individualią veiklą pavadinimu 33Bots Bartosz Wysocki, korespondencijos adresas: Plac Jana Henryka Dąbrowskiego 12, lok. 120, 00-055 Varšuva, Lenkija, įregistruotas Lenkijos Respublikos ūkinės veiklos centriniame registre (CEIDG), mokesčių mokėtojo kodas (NIP): PL5253090645, REGON: 544792095, toliau kartu – „Šalys“.

## 1 straipsnis. Sutarties dalykas
1. **Sutarties dalykas – Vykdytojo atlygintinai teikiama paslauga: laikinas 1 humanoidinio roboto ({{ robot }}) suteikimas kartu su operatoriaus aptarnavimu Užsakovo organizuojamo renginio („Renginys“) metu:**
– **Renginio pavadinimas: {{ nazwa_wydarzenia }}**
– **Renginio data: {{ daty_wydarzenia }}**
– **Paslaugos teikimo valandos: {{ godziny }}**
– **Vieta: {{ miejsce_wydarzenia }}**
– **Renginio pobūdis: {{ charakter_wydarzenia }}**
1. Paslauga visų pirma apima: a) savalaikį humanoidinio roboto suteikimą; b) roboto transportavimą į Renginio vietą (nurodytą šio straipsnio 1 dalyje) ir jo paruošimą vietoje; c) visapusišką roboto aptarnavimą ir priežiūrą, kurią visą Renginio laiką atlieka Vykdytojo sertifikuotas operatorius; d) roboto pasisakymų pritaikymą ir užprogramavimą pagal Renginio tikslus; e) Užsakovo prekių ženklų pritvirtinimą ant roboto Renginio metu; f) bendravimą su Renginio dalyviais.
1. Vykdytojas turi teisę Sutarčiai vykdyti pasitelkti subrangovus, įskaitant operatorių, tačiau visiškai atsako už jų veiksmus ir neveikimą.
1. **Vykdytojas patvirtina žinantis, kad paslauga bus teikiama šioje erdvėje: {{ rodzaj_przestrzeni }}.**

## 2 straipsnis. Paslaugos pobūdis ir kokybė
1. Vykdytojas įsipareigoja paslaugą teikti rūpestingai ir garantuoja, kad suteiktas robotas yra techniškai tvarkingas ir gali įgyvendinti scenarijaus tikslus.
1. Vykdytojas garantuoja, kad robotas bus prieinamas bendrauti su Renginio dalyviais ne mažiau kaip 80 % 1 straipsnio 1 dalyje nurodytų paslaugos teikimo valandų. Šią ribą viršijančios techninės pertraukos laikomos netinkamu Sutarties vykdymu.
1. Vykdytojas užtikrina, kad roboto dirbtinio intelekto sistema bus sukonfigūruota pagal Užsakovo pateiktą žinių bazę.

## 3 straipsnis. Vykdymo ir saugos taisyklės
1. Robotą valdyti gali tik Vykdytojo operatorius.
1. Vykdytojo operatorius prižiūri įrenginį ir atsako už roboto saugumą bei trečiųjų asmenų veiksmų, galinčių sugadinti įrangą, prevenciją.
1. Operatorius turi teisę sustabdyti bendravimą, kai kyla reali grėsmė žmonių ar įrangos saugumui, tačiau negali savavališkai atsisakyti vykdyti scenarijaus be techninio ar teisinio pagrindo.

## 4 straipsnis. Techninės ir organizacinės sąlygos
1. Tinkamam roboto veikimui Užsakovas užtikrina:
a) saugią erdvę roboto pristatymui ir lygų paviršių judėjimui,
a) prieigą prie standartinio 230 V elektros lizdo užkulisiuose akumuliatoriams įkrauti.
1. Vykdytojas sutinka, kad Užsakovas neatsako už roboto sugadinimą dėl nuo Užsakovo nepriklausančių priežasčių, tačiau Užsakovas įsipareigoja užtikrinti sąlygas be neįprastų grėsmių elektroninei įrangai.

## 5 straipsnis. Atlygis
1. Vykdytojo atlygis už visapusišką paslaugos suteikimą yra {{ cena_netto }} {{ waluta }} be PVM (žodžiais: {{ cena_netto_slownie }}).
1. {% if vat_kwota and vat_stawka not in ("0%", "0") %}Prie šios sumos pridedamas PVM pagal galiojantį {{ vat_stawka }} tarifą. Bendra Sutarties vertė – {{ cena_brutto }} {{ waluta }} su PVM.{% else %}Sąskaita išrašoma be PVM – PVM apskaičiuoja ir sumoka Užsakovas pagal atvirkštinio apmokestinimo tvarką (Direktyvos 2006/112/EB 196 str.).{% endif %}
1. Mokėjimo terminas – 14 dienų nuo PVM sąskaitos faktūros gavimo dienos.
1. Sąskaita faktūra siunčiama Užsakovui elektroniniu paštu. Ji laikoma gauta jos išsiuntimo dieną.
1. Mokama pavedimu į sąskaitoje faktūroje nurodytą Vykdytojo banko sąskaitą. Mokėjimo diena laikoma Užsakovo banko sąskaitos nuskaitymo diena.

## 6 straipsnis. Atsakomybė už įrangą
1. Užsakovas atsako už žalą robotui, padarytą tyčia Užsakovo ar jo darbuotojų.
1. Užsakovas neatsako už žalą, kurią robotui padarė tretieji asmenys, jei žala neatsirado dėl šiurkštaus Vykdytojo operatoriaus priežiūros aplaidumo.
1. Jei robotas sugadinamas dėl trečiųjų asmenų kaltės, Vykdytojas pirmiausia reikalauja žalos atlyginimo iš žalą padariusio asmens.

## 7 straipsnis. Reklaminė medžiaga ir atvaizdas
1. Renginio metu robotas gali būti aprūpintas Užsakovo ir Vykdytojo reklamine medžiaga, įskaitant aprangą, suvenyrus ir kitus elementus su Užsakovo logotipu ar ženklais.
1. Užsakovas turi teisę fotografuoti ir filmuoti robotą bei naudoti šią medžiagą informaciniais ir reklaminiais tikslais nemokėdamas Vykdytojui papildomo atlygio.
1. Vykdytojas be išankstinio raštiško Užsakovo sutikimo neturi teisės skelbti ar naudoti nuotraukų, įrašų ar informacijos apie Renginį, Užsakovo buveinę ar Šalių bendradarbiavimą reklamos, rinkodaros ar rekomendacijų tikslais.

## 8 straipsnis. Vykdytojo atsakomybė
1. Vykdytojas visiškai atsako už žalą, kurią robotas ar Vykdytojo operatorius padarė tretiesiems asmenims ar Užsakovo turtui.
1. Vykdytojas patvirtina, kad turi galiojantį įrangos draudimą ir tinkamą veiklos civilinės atsakomybės draudimą, apimantį su humanoidinių robotų suteikimu susijusią riziką.
1. Vykdytojo atsakomybė neapsiribojama pagal šią Sutartį mokamo atlygio dydžiu.

## 9 straipsnis. Atsisakymas ir Sutarties nutraukimas
1. Jei Užsakovas atsisako paslaugos likus mažiau nei 3 dienoms iki Renginio, jis sumoka Vykdytojui 50 % atlygio.
1. Užsakovas turi teisę nedelsiant nutraukti Sutartį, jei Vykdytojas laiku nepristato tvarkingo roboto arba jei atsiranda roboto gedimų, avarijų ar veikimo sutrikimų, dėl kurių neįmanoma suteikti paslaugos ir įgyvendinti Renginio tikslų.
1. Nutraukus Sutartį pagal šio straipsnio 2 dalį, Užsakovas neprivalo mokėti Vykdytojui atlygio ir turi teisę reikalauti netesybų, lygių 5 straipsnyje nurodytam atlygiui. Tai neatima iš Užsakovo teisės reikalauti netesybas viršijančių nuostolių atlyginimo bendra tvarka.

## 10 straipsnis. Asmens duomenys ir konfidencialumas
1. Šalys įsipareigoja tvarkyti asmens duomenis pagal Bendrąjį duomenų apsaugos reglamentą (BDAR).
1. Vykdytojas įsipareigoja negrįžtamai ištrinti visus tekstinius žurnalus ir Žinių bazės duomenis iškart po Renginio.
1. Šalys įsipareigoja saugoti komercinės ir techninės informacijos konfidencialumą 3 metus po Sutarties pabaigos.

## 11 straipsnis. Baigiamosios nuostatos
1. Visi Sutarties pakeitimai galioja tik sudaryti raštu.
1. Sutarčiai taikoma Lietuvos Respublikos teisė.
1. Visi iš šios Sutarties kylantys ginčai sprendžiami Užsakovo buveinės vietos teisme.
1. Sutartis sudaryta dviem vienodą teisinę galią turinčiais egzemplioriais – po vieną kiekvienai Šaliai. Sutartis gali būti pasirašyta elektroniniu parašu arba pasikeičiant pasirašytais skenuotais egzemplioriais.

**Šalių parašai:**
[[podpisy: UŽSAKOVAS | VYKDYTOJAS (33Bots)]]
"""


# treść umowy po niemiecku — ten sam układ i te same zasady co wersja litewska (Niemcy: prawo niemieckie)
DEFAULT_TEMPLATE_DE = """# DIENSTLEISTUNGSVERTRAG ÜBER DIE BEREITSTELLUNG EINES HUMANOIDEN ROBOTERS Nr. {{ numer_umowy }}
**Geschlossen am {{ data_umowy }} in {{ miasto }} (nachfolgend „Vertrag“) zwischen:**
**Auftraggeber:** {{ klient_opis }}
und
**Auftragnehmer:** Bartosz Wysocki, Einzelunternehmer unter der Firma 33Bots Bartosz Wysocki, Korrespondenzanschrift: Plac Jana Henryka Dąbrowskiego 12, lok. 120, 00-055 Warschau, Polen, eingetragen im Zentralen Register und Informationssystem über die Gewerbetätigkeit der Republik Polen (CEIDG), Steuernummer (NIP): PL5253090645, REGON: 544792095, nachfolgend gemeinsam „Parteien“.

## § 1 Vertragsgegenstand
1. **Gegenstand des Vertrags ist die entgeltliche Dienstleistung des Auftragnehmers: die vorübergehende Bereitstellung eines humanoiden Roboters ({{ robot }}) einschließlich Bedienung durch einen Operator während der vom Auftraggeber organisierten Veranstaltung („Veranstaltung“):**
– **Name der Veranstaltung: {{ nazwa_wydarzenia }}**
– **Datum der Veranstaltung: {{ daty_wydarzenia }}**
– **Leistungszeiten: {{ godziny }}**
– **Ort: {{ miejsce_wydarzenia }}**
– **Art der Veranstaltung: {{ charakter_wydarzenia }}**
1. Die Dienstleistung umfasst insbesondere: a) die fristgerechte Bereitstellung des humanoiden Roboters; b) den Transport des Roboters zum Veranstaltungsort (gemäß Abs. 1) und seine Vorbereitung vor Ort; c) die umfassende Bedienung und Betreuung des Roboters durch einen zertifizierten Operator des Auftragnehmers während der gesamten Veranstaltung; d) die Anpassung und Programmierung der Aussagen des Roboters entsprechend den Zielen der Veranstaltung; e) das Anbringen von Markenzeichen des Auftraggebers am Roboter während der Veranstaltung; f) die Interaktion mit den Teilnehmern der Veranstaltung.
1. Der Auftragnehmer ist berechtigt, zur Erfüllung des Vertrags Subunternehmer, einschließlich des Operators, einzusetzen, haftet jedoch in vollem Umfang für deren Handlungen und Unterlassungen.
1. **Der Auftragnehmer bestätigt, dass ihm bekannt ist, dass die Dienstleistung in folgender Umgebung erbracht wird: {{ rodzaj_przestrzeni }}.**

## § 2 Art und Qualität der Dienstleistung
1. Der Auftragnehmer verpflichtet sich, die Dienstleistung mit der gebotenen Sorgfalt zu erbringen, und garantiert, dass der bereitgestellte Roboter technisch einwandfrei ist und die Ziele des Szenarios umsetzen kann.
1. Der Auftragnehmer garantiert, dass der Roboter mindestens 80 % der in § 1 Abs. 1 genannten Leistungszeiten für die Interaktion mit den Teilnehmern der Veranstaltung zur Verfügung steht. Technische Unterbrechungen, die diese Grenze überschreiten, gelten als nicht ordnungsgemäße Vertragserfüllung.
1. Der Auftragnehmer stellt sicher, dass das KI-System des Roboters entsprechend der vom Auftraggeber bereitgestellten Wissensdatenbank konfiguriert wird.

## § 3 Durchführungs- und Sicherheitsregeln
1. Der Roboter darf ausschließlich vom Operator des Auftragnehmers gesteuert werden.
1. Der Operator des Auftragnehmers beaufsichtigt das Gerät und ist für die Sicherheit des Roboters sowie für die Verhinderung von Handlungen Dritter verantwortlich, die die Ausrüstung beschädigen könnten.
1. Der Operator ist berechtigt, die Interaktion zu unterbrechen, wenn eine tatsächliche Gefahr für die Sicherheit von Personen oder der Ausrüstung besteht; er darf die Umsetzung des Szenarios jedoch nicht ohne technischen oder rechtlichen Grund eigenmächtig verweigern.

## § 4 Technische und organisatorische Bedingungen
1. Für den ordnungsgemäßen Betrieb des Roboters stellt der Auftraggeber sicher:
a) einen sicheren Bereich für die Anlieferung des Roboters und einen ebenen Untergrund für seine Bewegung,
a) Zugang zu einer Standardsteckdose (230 V) hinter der Bühne zum Laden der Akkus.
1. Der Auftragnehmer erkennt an, dass der Auftraggeber nicht für Schäden am Roboter haftet, die auf vom Auftraggeber nicht zu vertretenden Ursachen beruhen; der Auftraggeber verpflichtet sich jedoch, Bedingungen ohne ungewöhnliche Gefahren für elektronische Geräte zu gewährleisten.

## § 5 Vergütung
1. Die Vergütung des Auftragnehmers für die vollständige Erbringung der Dienstleistung beträgt {{ cena_netto }} {{ waluta }} netto (in Worten: {{ cena_netto_slownie }}).
1. {% if vat_kwota and vat_stawka not in ("0%", "0") %}Zu diesem Betrag wird die Umsatzsteuer zum geltenden Satz von {{ vat_stawka }} hinzugerechnet. Der Gesamtwert des Vertrags beträgt {{ cena_brutto }} {{ waluta }} brutto.{% else %}Die Rechnung wird ohne Umsatzsteuer ausgestellt – die Steuer wird vom Auftraggeber im Reverse-Charge-Verfahren (Steuerschuldnerschaft des Leistungsempfängers, Art. 196 der Richtlinie 2006/112/EG) berechnet und abgeführt.{% endif %}
1. Die Zahlungsfrist beträgt 14 Tage ab Erhalt der Rechnung.
1. Die Rechnung wird dem Auftraggeber per E-Mail zugesandt. Sie gilt am Tag ihres Versands als zugegangen.
1. Die Zahlung erfolgt per Überweisung auf das in der Rechnung angegebene Bankkonto des Auftragnehmers. Als Zahlungstag gilt der Tag der Belastung des Bankkontos des Auftraggebers.

## § 6 Haftung für die Ausrüstung
1. Der Auftraggeber haftet für Schäden am Roboter, die vorsätzlich vom Auftraggeber oder seinen Mitarbeitern verursacht wurden.
1. Der Auftraggeber haftet nicht für Schäden, die Dritte am Roboter verursacht haben, sofern der Schaden nicht auf grober Fahrlässigkeit des Operators des Auftragnehmers bei der Beaufsichtigung beruht.
1. Wird der Roboter durch Verschulden Dritter beschädigt, macht der Auftragnehmer Schadensersatzansprüche zunächst gegenüber dem Schädiger geltend.

## § 7 Werbematerialien und Bildrechte
1. Während der Veranstaltung kann der Roboter mit Werbematerialien des Auftraggebers und des Auftragnehmers ausgestattet werden, einschließlich Kleidung, Werbeartikeln und anderer Elemente mit dem Logo oder den Kennzeichen des Auftraggebers.
1. Der Auftraggeber ist berechtigt, den Roboter zu fotografieren und zu filmen und dieses Material ohne zusätzliche Vergütung an den Auftragnehmer für Informations- und Werbezwecke zu nutzen.
1. Der Auftragnehmer ist ohne vorherige schriftliche Zustimmung des Auftraggebers nicht berechtigt, Fotos, Aufnahmen oder Informationen über die Veranstaltung, den Sitz des Auftraggebers oder die Zusammenarbeit der Parteien zu Werbe-, Marketing- oder Referenzzwecken zu veröffentlichen oder zu nutzen.

## § 8 Haftung des Auftragnehmers
1. Der Auftragnehmer haftet in vollem Umfang für Schäden, die der Roboter oder der Operator des Auftragnehmers Dritten oder dem Eigentum des Auftraggebers zufügt.
1. Der Auftragnehmer bestätigt, dass er über eine gültige Geräteversicherung und eine angemessene Betriebshaftpflichtversicherung verfügt, die die mit der Bereitstellung humanoider Roboter verbundenen Risiken abdeckt.
1. Die Haftung des Auftragnehmers ist nicht auf die Höhe der nach diesem Vertrag zu zahlenden Vergütung beschränkt.

## § 9 Rücktritt und Kündigung
1. Tritt der Auftraggeber weniger als 3 Tage vor der Veranstaltung von der Dienstleistung zurück, zahlt er dem Auftragnehmer 50 % der Vergütung.
1. Der Auftraggeber ist berechtigt, den Vertrag fristlos zu kündigen, wenn der Auftragnehmer den funktionsfähigen Roboter nicht rechtzeitig bereitstellt oder wenn Ausfälle, Störungen oder Fehlfunktionen des Roboters auftreten, die die Erbringung der Dienstleistung und die Verwirklichung der Ziele der Veranstaltung unmöglich machen.
1. Im Falle einer Kündigung nach Abs. 2 ist der Auftraggeber nicht zur Zahlung der Vergütung verpflichtet und kann eine Vertragsstrafe in Höhe der in § 5 genannten Vergütung verlangen. Dies schließt die Geltendmachung eines darüber hinausgehenden Schadens nach den allgemeinen Vorschriften nicht aus.

## § 10 Personenbezogene Daten und Vertraulichkeit
1. Die Parteien verpflichten sich, personenbezogene Daten gemäß der Datenschutz-Grundverordnung (DSGVO) zu verarbeiten.
1. Der Auftragnehmer verpflichtet sich, sämtliche Textprotokolle und Daten der Wissensdatenbank unmittelbar nach der Veranstaltung unwiderruflich zu löschen.
1. Die Parteien verpflichten sich, geschäftliche und technische Informationen bis 3 Jahre nach Vertragsende vertraulich zu behandeln.

## § 11 Schlussbestimmungen
1. Änderungen dieses Vertrags bedürfen zu ihrer Wirksamkeit der Schriftform.
1. Dieser Vertrag unterliegt dem Recht der Bundesrepublik Deutschland.
1. Für alle Streitigkeiten aus diesem Vertrag ist das Gericht am Sitz des Auftraggebers zuständig.
1. Der Vertrag wird in zwei gleichlautenden Ausfertigungen geschlossen, je eine für jede Partei. Der Vertrag kann mit elektronischer Signatur oder durch den Austausch unterzeichneter, eingescannter Exemplare unterzeichnet werden.

**Unterschriften der Parteien:**
[[podpisy: AUFTRAGGEBER | AUFTRAGNEHMER (33Bots)]]
"""

DEFAULT_TEMPLATES = {"lt": DEFAULT_TEMPLATE, "de": DEFAULT_TEMPLATE_DE}


def default_template(code="lt"):
    return DEFAULT_TEMPLATES.get(code) or DEFAULT_TEMPLATE

def _sandbox():
    # wzór może przyjść od człowieka (wgrany DOCX) — renderujemy w piaskownicy, bez wykonywania kodu
    from jinja2.sandbox import SandboxedEnvironment
    return SandboxedEnvironment(autoescape=False, keep_trailing_newline=True)


def _clean(value):
    # znaczniki pogrubienia w danych nie mogą zmieniać formatowania umowy
    return str(value).replace("**", "*").strip()


def parse_amount(value):
    try:
        v = float(str(value).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def contract_context(fields, lead_id, today, email="", phone="", code="lt"):
    """Pola do wzoru: to, co znamy z leada / okienka, reszta jako miejsce do uzupełnienia przez klienta
    (opis klienta i kwoty słownie w języku rynku)."""
    f = {k: _clean(v) for k, v in (fields or {}).items() if v is not None}
    blank = lambda key: f.get(key) or DOTS  # noqa: E731
    currency = (f.get("waluta") or "EUR").upper()
    netto = parse_amount(f.get("cena_netto"))
    vat_rate = parse_amount(f.get("vat"))
    vat_rate = vat_rate or 0.0  # LT: domyślnie odwrotne obciążenie (bez PVM)
    days_text = f.get("daty_wydarzenia", "")
    days = [x.strip() for x in re.split(r"[,;]", days_text) if x.strip()]
    ctx = {k: DOTS for k in KNOWN_FIELDS}
    ctx.update({
        "klient_opis": CLIENT_BLANKS.get(code, CLIENT_BLANK),
        "nazwa_wydarzenia": blank("nazwa_wydarzenia"),
        "daty_wydarzenia": days_text or DOTS,
        "data_wydarzenia": days[0] if days else DOTS,
        "liczba_dni": str(len(days)) if days else DOTS,
        "godziny": f.get("godziny") or "………… – …………",
        "miejsce_wydarzenia": blank("miejsce_wydarzenia"),
        "charakter_wydarzenia": blank("charakter_wydarzenia"),
        "rodzaj_przestrzeni": blank("rodzaj_przestrzeni"),
        "robot": f.get("robot") or "Unitree G1",
        "waluta": CURRENCY_SYMBOL.get(currency, currency),
        "vat_stawka": f"{vat_rate:g}%",
        "numer_umowy": f"{lead_id}/{today.year}",
        "email": _clean(email) or DOTS,
        "telefon": _clean(phone) or DOTS,
        "data_wypelnienia": "",
        "uwagi": "",
    })
    if netto is not None:
        vat = round(netto * vat_rate / 100, 2)
        brutto = round(netto + vat, 2)
        ctx.update({
            "cena_netto": money(netto), "cena_netto_slownie": amount_words_for(code, netto, currency),
            "vat_kwota": money(vat) if vat else "", "cena_brutto": money(brutto),
            "cena_brutto_slownie": amount_words_for(code, brutto, currency),
        })
    return ctx


def sample_context(today, code="lt"):
    return contract_context({"nazwa_wydarzenia": "Prezentacja", "daty_wydarzenia": today.strftime("%d.%m.%Y"),
                             "cena_netto": "1300", "waluta": "EUR", "vat": "0"}, 77, today, code=code)


# --- DOCX ---

_NUM_RE = re.compile(r"^\d+\.\s+(.*)$")
_LETTER_RE = re.compile(r"^[a-z]\)\s+(.*)$")
_SIGN_RE = re.compile(r"^\[\[podpisy(?::(.*))?\]\]$")
_BOLD_RE = re.compile(r"(\*\*.+?\*\*)")


def render_docx(template_text, ctx):
    """Nasz wzór (tekst z polami {{ … }}, ta sama składnia co dawny PDF) -> bajty DOCX.

    „# ” tytuł, „## ” paragraf, „1. ” ustęp (numeracja liczy się sama w każdym paragrafie),
    „a) ” podpunkt, „– ” myślnik, **pogrubienie**, [[podpisy: LEWY | PRAWY]] miejsca na podpisy."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    text = _sandbox().from_string(template_text).render(**ctx)
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin, sec.bottom_margin = Cm(1.8), Cm(2)
    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(3)
    normal.paragraph_format.line_spacing = 1.15

    def para(t, size=None, bold=False, align=WD_ALIGN_PARAGRAPH.JUSTIFY, indent=0.0, label=None,
             label_w=0.0, before=0):
        p = doc.add_paragraph()
        p.alignment = align
        pf = p.paragraph_format
        if before:
            pf.space_before = Pt(before)
        if label:
            pf.left_indent = Cm(indent + label_w)
            pf.first_line_indent = Cm(-label_w)
            pf.tab_stops.add_tab_stop(Cm(indent + label_w))
            t = f"{label}\t{t}"
        elif indent:
            pf.left_indent = Cm(indent)
        for chunk in _BOLD_RE.split(t):
            if not chunk:
                continue
            strong = chunk.startswith("**") and chunk.endswith("**") and len(chunk) > 4
            run = p.add_run(chunk[2:-2] if strong else chunk)
            run.bold = bold or strong
            if size:
                run.font.size = Pt(size)
        return p

    number = letter = 0
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        m_num, m_let, m_sign = _NUM_RE.match(line), _LETTER_RE.match(line), _SIGN_RE.match(line)
        if line.startswith("# "):
            para(line[2:], size=13, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER).paragraph_format.space_after = Pt(8)
        elif line.startswith("## "):
            number = letter = 0
            para(line[3:], size=11, bold=True, align=WD_ALIGN_PARAGRAPH.LEFT, before=8)
        elif m_num:
            number += 1
            letter = 0
            para(m_num.group(1), label=f"{number}.", label_w=0.6)
        elif m_let:
            letter += 1
            para(m_let.group(1), indent=0.6, label=f"{chr(96 + letter)})", label_w=0.6)
        elif line.startswith(("– ", "- ", "• ")):
            para(line[2:], indent=0.6, label="–", label_w=0.5)
        elif m_sign:
            labels = [x.strip() for x in (m_sign.group(1) or "Zamawiający|Wykonawca").split("|")][:2]
            table = doc.add_table(rows=2, cols=len(labels))
            for i, lab in enumerate(labels):
                top = table.cell(0, i).paragraphs[0]
                top.alignment = WD_ALIGN_PARAGRAPH.CENTER
                top.paragraph_format.space_before = Pt(36)
                top.add_run("……………………………………")
                bottom = table.cell(1, i).paragraphs[0]
                bottom.alignment = WD_ALIGN_PARAGRAPH.CENTER
                r = bottom.add_run(lab)
                r.font.size = Pt(8.5)
        else:
            para(line)
    out = io.BytesIO()
    doc.core_properties.title = "33Bots"
    doc.core_properties.author = "33Bots"
    doc.save(out)
    return out.getvalue()


def _docx_paragraphs(doc):
    """Wszystkie akapity: treść, tabele (także zagnieżdżone), nagłówki i stopki."""
    def walk(container):
        for p in container.paragraphs:
            yield p
        for table in getattr(container, "tables", []):
            for row in table.rows:
                for cell in row.cells:
                    yield from walk(cell)
    yield from walk(doc)
    for sec in doc.sections:
        for part in (sec.header, sec.footer):
            yield from walk(part)


_FIELD_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def fill_docx(docx_bytes, ctx):
    """Wgrany własny wzór DOCX: podstawia pola {{ … }} w akapitach (formatowanie akapitu
    z polem bierze się z jego pierwszego fragmentu)."""
    from docx import Document
    doc = Document(io.BytesIO(docx_bytes))
    env = _sandbox()
    for p in _docx_paragraphs(doc):
        text = p.text
        if "{{" not in text and "{%" not in text:
            continue
        rendered = env.from_string(text).render(**ctx)
        runs = p.runs
        if not runs:
            continue
        runs[0].text = rendered
        for r in runs[1:]:
            r.text = ""
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def check_docx_template(docx_bytes):
    """Sprawdza wgrany wzór. Zwraca (błąd albo None, lista nieznanych pól)."""
    try:
        from docx import Document
        doc = Document(io.BytesIO(docx_bytes))
        used = set()
        for p in _docx_paragraphs(doc):
            used |= set(_FIELD_RE.findall(p.text))
        fill_docx(docx_bytes, sample_context(date.today()))
    except Exception as e:
        return f"To nie jest poprawny plik DOCX albo wzór ma błąd: {type(e).__name__}: {e}"[:300], []
    return None, sorted(used - KNOWN_FIELDS)


# --- konwersja dokumentów do PDF ---

_OFFICE_EXT = {".docx", ".doc", ".odt", ".rtf", ".pages", ".docm", ".dotx", ".wpd"}
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
_TEXT_EXT = {".txt", ".md", ".csv"}
_SOFFICE_LOCK = threading.Lock()


def file_ext(filename):
    return os.path.splitext((filename or "").lower())[1]


def is_pdf(data):
    return bool(data) and data[:5] == b"%PDF-"


def soffice_path():
    return shutil.which("soffice") or shutil.which("libreoffice")


def _soffice_to_pdf(filename, data, timeout):
    exe = soffice_path()
    if not exe:
        return None
    with _SOFFICE_LOCK, tempfile.TemporaryDirectory(prefix="crm_lo_") as tmp:
        ext = file_ext(filename) or ".docx"
        src = os.path.join(tmp, "dokument" + ext)
        with open(src, "wb") as fh:
            fh.write(data)
        cmd = [exe, f"-env:UserInstallation=file://{tmp}/profil", "--headless", "--norestore",
               "--nolockcheck", "--convert-to", "pdf", "--outdir", tmp, src]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout,
                           env={**os.environ, "HOME": tmp}, check=False)
        except (subprocess.TimeoutExpired, OSError):
            return None
        out = os.path.join(tmp, "dokument.pdf")
        if os.path.exists(out):
            with open(out, "rb") as fh:
                pdf = fh.read()
            return pdf if is_pdf(pdf) else None
    return None


def _image_to_pdf(data):
    from PIL import Image, ImageOps
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, "PDF", resolution=150.0)
    return out.getvalue()


def text_pdf(title, text):
    """Prosty PDF z tekstem (baza wiedzy wklejona w maila, zapasowa konwersja DOCX bez LibreOffice)."""
    from fpdf import FPDF
    pdf = FPDF(format="A4")
    pdf.set_margins(20, 18, 20)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_font("Sans", "", os.path.join(FONTS_DIR, "LiberationSans-Regular.ttf"))
    pdf.add_font("Sans", "B", os.path.join(FONTS_DIR, "LiberationSans-Bold.ttf"))
    pdf.add_page()
    if title:
        pdf.set_font("Sans", "B", 13)
        pdf.multi_cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    pdf.set_font("Sans", "", 10)
    clean = re.sub(r"[\U00010000-\U0010FFFF]", "", text or "")
    clean = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", clean)
    for line in clean.split("\n"):
        pdf.multi_cell(0, 5, line or " ", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def to_pdf(filename, data, timeout=90):
    """Dowolny odesłany dokument -> PDF (bajty) albo None, gdy się nie da.
    PDF zostaje, zdjęcia przez Pillow, Word/ODT przez LibreOffice; bez LibreOffice DOCX jako sam tekst."""
    if not data:
        return None
    ext = file_ext(filename)
    try:
        if is_pdf(data):
            return data
        if ext in _IMAGE_EXT:
            return _image_to_pdf(data)
        if ext in _OFFICE_EXT:
            pdf = _soffice_to_pdf(filename, data, timeout)
            if pdf:
                return pdf
            if ext in (".docx", ".docm", ".dotx"):
                return text_pdf(filename, docx_text(data))
            return None
        if ext in _TEXT_EXT:
            return text_pdf(filename, data.decode("utf-8", "replace"))
    except Exception:
        return None
    return None


# --- tekst z dokumentów (żeby Claude ocenił, czym jest załącznik) ---

def docx_text(data):
    from docx import Document
    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in _docx_paragraphs(doc) if p.text.strip())


def extract_text(filename, data, limit=3000):
    try:
        ext = file_ext(filename)
        if is_pdf(data):
            import logging
            from pypdf import PdfReader
            logging.getLogger("pypdf").setLevel(logging.ERROR)  # uszkodzone PDF-y od klientów nie śmiecą w logach
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((page.extract_text() or "") for page in reader.pages[:4])
        elif ext in (".docx", ".docm", ".dotx"):
            text = docx_text(data)
        elif ext in _TEXT_EXT:
            text = data.decode("utf-8", "replace")
        else:
            return ""
    except Exception:
        return ""
    return re.sub(r"[ \t]+", " ", text).strip()[:limit]


def safe_filename(name, default="dokument"):
    base = os.path.basename(str(name or "")).strip().replace("\x00", "")
    base = re.sub(r"[^\w.\- ()ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]", "_", base)[:120].strip(" .")
    return base or default


def slug(text):
    t = re.sub(r"[^\w]+", "_", (text or "").strip(), flags=re.UNICODE).strip("_")
    return t[:40] or "klient"


# --- maile do klienta (CRM przygotowuje, człowiek zatwierdza) ---

EMAIL_KINDS = {
    "umowa": "Umowa do podpisu",
    "umowa_przypomnienie": "Przypomnienie o umowie",
    "baza": "Instrukcja bazy wiedzy",
    "umowa_baza": "Umowa + instrukcja bazy wiedzy",
    "baza_przypomnienie": "Przypomnienie o bazie wiedzy",
    "opinia": "Prośba o opinię",
}


def signature(owner):
    lines = [owner.get("full_name") or owner.get("name") or "", "33Bots", owner.get("email") or "",
             owner.get("phone") or ""]
    return "\n".join(x for x in lines if x)


def _when(days, code="lt"):
    """' jūsų renginiui 2026-11-21', ' jūsų renginiui 2026-11-21–23' (dni po kolei) albo
    ' jūsų renginiams 2026-11-21 ir 2026-12-05' (osobne terminy); po niemiecku
    ' für Ihre Veranstaltung am 21.11.2026', ' … vom 21.–23.11.2026', ' für Ihre Veranstaltungen am … und …'."""
    days = sorted(set(d for d in days if d))
    if not days:
        return ""
    if code == "de":
        if len(days) == 1:
            return f" für Ihre Veranstaltung am {days[0]:%d.%m.%Y}"
        if all((b - a).days == 1 for a, b in zip(days, days[1:])):
            return f" für Ihre Veranstaltung vom {days[0]:%d.%m.}–{days[-1]:%d.%m.%Y}"
        listed = [f"{d:%d.%m.%Y}" for d in days]
        return f" für Ihre Veranstaltungen am {', '.join(listed[:-1])} und {listed[-1]}"
    if len(days) == 1:
        return f" jūsų renginiui {days[0]:%Y-%m-%d}"
    if all((b - a).days == 1 for a, b in zip(days, days[1:])):
        return f" jūsų renginiui {days[0]:%Y-%m-%d}–{days[-1]:%m-%d}"
    listed = [f"{d:%Y-%m-%d}" for d in days]
    return f" jūsų renginiams {', '.join(listed[:-1])} ir {listed[-1]}"


def email_draft(kind, company, robot, days, owner, today, kb_deadline_days, sent_on=None,
                review_link="", prev_subject="", code="lt"):
    """(temat, treść) maila danego rodzaju — w języku rynku (Litwa: litewski, Niemcy: niemiecki). Bez imienia
    w powitaniu. Umowa i baza wiedzy idą zwykle osobnymi mailami; „umowa_baza” to oba w jednym mailu."""
    if code == "de":
        return _email_draft_de(kind, company, robot, days, owner, today, kb_deadline_days, sent_on,
                               review_link, prev_subject)
    when = _when(days)
    robot = robot or "Unitree G1"
    first = min(days) if days else None
    deadline = (first - timedelta(days=kb_deadline_days)) if first else None
    kb_by = (f"ne vėliau kaip iki {max(deadline, today):%Y-%m-%d} (savaitė iki renginio)"
             if deadline else "ne vėliau kaip savaitė iki renginio")
    head = "Laba diena,\n\n"
    tail = "\n\nJei kiltų klausimų, mielai atsakysiu.\n\nPagarbiai,\n" + signature(owner)
    label = f"{first:%Y-%m-%d}" if first else company
    sign = ("1. užpildyti Užsakovo duomenis sutartyje (taškais pažymėtus laukus) ir trūkstamas renginio detales,\n"
            "2. pasirašyti sutartį ir atsiųsti ją atsakydami į šį laišką – skenuotą, nufotografuotą arba PDF "
            "su elektroniniu parašu.\n\n")
    if kind == "umowa":
        subject = f"Sutartis – roboto nuoma {label} | 33Bots"
        body = (head +
                f"dėkojame, kad pasirinkote 33Bots! Prisegame humanoidinio roboto {robot} nuomos sutartį{when}.\n\n"
                "Prašome:\n" + sign +
                "Jei kurią nors nuostatą reikėtų pakeisti, mielai aptarsime." + tail)
    elif kind == "umowa_baza":
        subject = f"Sutartis ir žinių bazė – roboto nuoma {label} | 33Bots"
        body = (head +
                f"dėkojame, kad pasirinkote 33Bots! Prisegame humanoidinio roboto {robot} nuomos sutartį{when} "
                "ir PDF instrukciją, kaip parengti roboto žinių bazę.\n\n"
                "Sutartis – prašome:\n" + sign +
                "Žinių bazė – pagal ją robotas bendrauja su svečiais: pasakoja apie įmonę, atsako į klausimus "
                f"ir laikosi sutarto tono. Prašome ją atsiųsti {kb_by} – kaip failą arba tiesiog atsakydami "
                "į šį laišką. Mielai padėsime ją parengti.\n\n"
                "Jei kurią nors sutarties nuostatą reikėtų pakeisti, mielai aptarsime." + tail)
    elif kind == "umowa_przypomnienie":
        subject = prev_subject if prev_subject.lower().startswith("re:") else (
            f"Re: {prev_subject}" if prev_subject else f"Priminimas: sutartis – roboto nuoma {label} | 33Bots")
        sent = f", kurią išsiuntėme {sent_on:%m-%d}" if sent_on else ""
        body = (head +
                f"norėčiau priminti apie roboto {robot} nuomos sutartį{when}{sent}. Prašome užpildyti duomenis, "
                "pasirašyti ir atsiųsti sutartį atsakant į šį laišką – sutartį prisegu dar kartą. "
                "Jei kurią nors nuostatą reikėtų pakeisti, mielai aptarsime." + tail)
    elif kind == "baza":
        subject = f"Roboto žinių bazė – {label} | 33Bots"
        body = (head +
                f"prisegame PDF instrukciją, kaip parengti roboto žinių bazę{when}. Pagal žinių bazę robotas "
                "bendrauja su svečiais: pasakoja apie įmonę, atsako į klausimus ir laikosi sutarto tono.\n\n"
                f"Prašome atsiųsti žinių bazę {kb_by} – kaip failą arba tiesiog atsakydami į šį laišką. "
                "Mielai padėsime ją parengti." + tail)
    elif kind == "baza_przypomnienie":
        subject = f"Priminimas: roboto žinių bazė – {label} | 33Bots"
        body = (head +
                f"norėčiau priminti apie roboto žinių bazę{when} – prašome ją atsiųsti {kb_by}, kad spėtume "
                "paruošti robotą. Instrukciją prisegu dar kartą; užtenka atsakyti į šį laišką su failu arba tekstu." + tail)
    elif kind == "opinia":
        subject = "Ačiū už bendrą renginį! | 33Bots"
        link = f"\n\n{review_link}" if review_link else ""
        ask = ("Būsime dėkingi už trumpą atsiliepimą – tai užtruks minutę:" if review_link else
               "Būsime dėkingi už kelis atsiliepimo žodžius atsakant į šį laišką.")
        body = (head +
                f"dėkojame, kad pakvietėte robotą {robot}{when}! Tikimės, kad svečiai buvo patenkinti.\n\n"
                f"{ask}{link}\n\nKiekvienas atsiliepimas padeda mums tobulėti." + tail)
    else:
        raise ValueError(kind)
    return subject, body


def _email_draft_de(kind, company, robot, days, owner, today, kb_deadline_days, sent_on=None,
                    review_link="", prev_subject=""):
    when = _when(days, "de")
    robot = robot or "Unitree G1"
    first = min(days) if days else None
    deadline = (first - timedelta(days=kb_deadline_days)) if first else None
    kb_by = (f"spätestens bis zum {max(deadline, today):%d.%m.%Y} (eine Woche vor der Veranstaltung)"
             if deadline else "spätestens eine Woche vor der Veranstaltung")
    head = "Guten Tag,\n\n"
    tail = "\n\nBei Fragen stehe ich Ihnen gerne zur Verfügung.\n\nMit freundlichen Grüßen\n" + signature(owner)
    label = f"{first:%d.%m.%Y}" if first else company
    sign = ("1. die Angaben des Auftraggebers im Vertrag (die gepunkteten Felder) sowie fehlende Details zur "
            "Veranstaltung zu ergänzen,\n"
            "2. den Vertrag zu unterschreiben und als Antwort auf diese E-Mail zurückzusenden – eingescannt, "
            "fotografiert oder als PDF mit elektronischer Signatur.\n\n")
    kb_what = ("Auf Grundlage der Wissensdatenbank unterhält sich der Roboter mit Ihren Gästen: Er erzählt über Ihr "
               "Unternehmen, beantwortet Fragen und hält sich an den vereinbarten Ton.")
    if kind == "umowa":
        subject = f"Vertrag – Robotervermietung {label} | 33Bots"
        body = (head +
                f"vielen Dank, dass Sie sich für 33Bots entschieden haben! Anbei erhalten Sie den Vertrag über die "
                f"Vermietung des humanoiden Roboters {robot}{when}.\n\n"
                "Wir bitten Sie:\n" + sign +
                "Falls eine Bestimmung angepasst werden soll, besprechen wir das gerne." + tail)
    elif kind == "umowa_baza":
        subject = f"Vertrag und Wissensdatenbank – Robotervermietung {label} | 33Bots"
        body = (head +
                f"vielen Dank, dass Sie sich für 33Bots entschieden haben! Anbei erhalten Sie den Vertrag über die "
                f"Vermietung des humanoiden Roboters {robot}{when} sowie eine PDF-Anleitung zur Vorbereitung der "
                "Wissensdatenbank des Roboters.\n\n"
                "Vertrag – wir bitten Sie:\n" + sign +
                f"Wissensdatenbank – {kb_what} Bitte senden Sie sie uns {kb_by} – als Datei oder einfach als "
                "Antwort auf diese E-Mail. Gerne helfen wir Ihnen bei der Vorbereitung.\n\n"
                "Falls eine Vertragsbestimmung angepasst werden soll, besprechen wir das gerne." + tail)
    elif kind == "umowa_przypomnienie":
        subject = prev_subject if prev_subject.lower().startswith("re:") else (
            f"Re: {prev_subject}" if prev_subject else f"Erinnerung: Vertrag – Robotervermietung {label} | 33Bots")
        sent = f", den wir Ihnen am {sent_on:%d.%m.} geschickt haben" if sent_on else ""
        body = (head +
                f"ich möchte Sie kurz an den Vertrag über die Vermietung des Roboters {robot}{when}{sent} erinnern. "
                "Bitte ergänzen Sie die Angaben, unterschreiben Sie den Vertrag und senden Sie ihn als Antwort auf "
                "diese E-Mail zurück – den Vertrag füge ich noch einmal bei. Falls eine Bestimmung angepasst werden "
                "soll, besprechen wir das gerne." + tail)
    elif kind == "baza":
        subject = f"Wissensdatenbank des Roboters – {label} | 33Bots"
        body = (head +
                f"anbei erhalten Sie eine PDF-Anleitung zur Vorbereitung der Wissensdatenbank des Roboters{when}. "
                f"{kb_what}\n\n"
                f"Bitte senden Sie uns die Wissensdatenbank {kb_by} – als Datei oder einfach als Antwort auf diese "
                "E-Mail. Gerne helfen wir Ihnen bei der Vorbereitung." + tail)
    elif kind == "baza_przypomnienie":
        subject = f"Erinnerung: Wissensdatenbank des Roboters – {label} | 33Bots"
        body = (head +
                f"ich möchte Sie kurz an die Wissensdatenbank des Roboters{when} erinnern – bitte senden Sie sie uns "
                f"{kb_by}, damit wir den Roboter rechtzeitig vorbereiten können. Die Anleitung füge ich noch einmal "
                "bei; es genügt, auf diese E-Mail mit einer Datei oder einem Text zu antworten." + tail)
    elif kind == "opinia":
        subject = "Vielen Dank für die gemeinsame Veranstaltung! | 33Bots"
        link = f"\n\n{review_link}" if review_link else ""
        ask = ("Wir würden uns sehr über eine kurze Bewertung freuen – das dauert nur eine Minute:" if review_link else
               "Wir würden uns sehr über ein paar Worte als Antwort auf diese E-Mail freuen.")
        body = (head +
                f"vielen Dank, dass Sie den Roboter {robot}{when} eingeladen haben! Wir hoffen, Ihre Gäste waren "
                f"begeistert.\n\n{ask}{link}\n\nJede Bewertung hilft uns, noch besser zu werden." + tail)
    else:
        raise ValueError(kind)
    return subject, body


# --- faktura po wydarzeniu (inFakt) i dane do faktury z NIP ---

OWN_NIP = "5253090645"  # 33Bots — nie bierzemy go za NIP klienta, gdy czytamy umowę
_NIP_RE = re.compile(r"NIP[\s:.\-]*(?:PL)?[\s]*([0-9][0-9\- ]{8,14}[0-9])", re.IGNORECASE)
_POSTCODE_RE = re.compile(r"(\d{2}-\d{3})\s+(.+)$")


def nip_digits(raw):
    return re.sub(r"\D", "", str(raw or ""))


def nip_valid(raw):
    d = nip_digits(raw)
    if len(d) != 10:
        return False
    s = sum(int(a) * w for a, w in zip(d, (6, 5, 7, 2, 3, 4, 5, 6, 7)))
    return s % 11 == int(d[9]) and s % 11 != 10


def find_nip(text, exclude=OWN_NIP):
    """Pierwszy poprawny NIP klienta w tekście (np. z odesłanej umowy) albo ''."""
    for m in _NIP_RE.finditer(text or ""):
        d = nip_digits(m.group(1))
        if nip_valid(d) and d != exclude:
            return d
    return ""


def parse_mf_address(addr):
    """'UL. PROSTA 1/2, 00-123 WARSZAWA' (Biała lista MF) -> (ulica z numerem, kod, miasto)."""
    addr = (addr or "").strip()
    if "," in addr:
        street, rest = addr.rsplit(",", 1)
    else:
        street, rest = "", addr
    m = _POSTCODE_RE.search(rest.strip())
    if m:
        return street.strip(), m.group(1), m.group(2).strip().title()
    return addr, "", ""


def to_grosze(amount):
    v = parse_amount(amount)
    return int(round(v * 100)) if v is not None else None


_SERVICE_NAME = {
    "lt": "Humanoidinio roboto {robot} nuoma su operatoriaus aptarnavimu",
    "de": "Vermietung des humanoiden Roboters {robot} mit Bedienung durch einen Operator",
}
_SERVICE_NAME_PL = "Wynajem robota humanoidalnego {robot} z obsługą operatora"


def invoice_service_name(robot, days, event_name="", code="lt"):
    """Pozycja na fakturze w języku klienta (litewski / niemiecki) z polskim tłumaczeniem po ukośniku —
    klient czyta swoją część, księgowość w Polsce swoją. Nigdy po angielsku."""
    days = sorted(set(d for d in days if d))
    when = ""
    if days:
        when = (f" — {days[0]:%d.%m.%Y}" if len(days) == 1 else
                f" — {', '.join(f'{d:%d.%m.%Y}' for d in days)}")
    what = f" „{event_name}”" if event_name else ""
    robot = robot or "Unitree G1"
    native = (_SERVICE_NAME.get(code) or _SERVICE_NAME["lt"]).format(robot=robot)
    return f"{native} / {_SERVICE_NAME_PL.format(robot=robot)}{what}{when}"


def invoice_email(number, days, payment_date, owner, code="lt"):
    when = _when(days, code)
    if code == "de":
        subject = f"Rechnung {number} – 33Bots" if number else "Rechnung – 33Bots"
        body = ("Guten Tag,\n\n"
                f"anbei erhalten Sie die Rechnung{(' Nr. ' + number) if number else ''} für die Robotervermietung{when}. "
                f"Zahlungsziel: {payment_date:%d.%m.%Y}.\n\n"
                "Vielen Dank für die Zusammenarbeit – wir hoffen, Ihre Gäste waren begeistert!\n\n"
                "Mit freundlichen Grüßen\n" + signature(owner))
        return subject, body
    subject = f"Sąskaita faktūra {number} – 33Bots" if number else "Sąskaita faktūra – 33Bots"
    body = ("Laba diena,\n\n"
            f"prisegu sąskaitą faktūrą{(' Nr. ' + number) if number else ''} už roboto nuomą{when}. "
            f"Mokėjimo terminas: {payment_date:%Y-%m-%d}.\n\n"
            "Dėkojame už bendradarbiavimą – tikimės, kad svečiai buvo patenkinti!\n\n"
            "Pagarbiai,\n" + signature(owner))
    return subject, body
