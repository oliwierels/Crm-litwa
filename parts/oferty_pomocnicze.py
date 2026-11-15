# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("oferty_pomocnicze") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: oferty: adresy, ceny, warunki wysyłki

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def email_list(*values):
    """Adresy z pól/tekstu („a@x.pl, b@y.pl” albo osobne wartości) — tylko poprawne, bez powtórzeń."""
    out, seen = [], set()
    for value in values:
        for part in re.split(r"[,;\s]+", str(value or "")):
            part = part.strip().strip("<>")
            if part and EMAIL_RE.match(part) and part.lower() not in seen:
                seen.add(part.lower())
                out.append(part)
    return out


def check_recipients(text):
    """(lista adresów, błąd) dla pola „Do” — kilka adresów po przecinku."""
    parts = [p.strip() for p in re.split(r"[,;]+", text or "") if p.strip()]
    bad = [p for p in parts if not EMAIL_RE.match(p)]
    if bad:
        return [], f"Niepoprawny adres e-mail: {', '.join(bad)}"
    if not parts:
        return [], "Wpisz adres e-mail odbiorcy."
    return email_list(*parts), None


def _offer_url(offer, review=False):
    base = public_base_url()
    path = f"/crm/oferty/{offer.id}" if review else f"/crm/oferty/{offer.id}/{offer.filename}"
    try:
        base = base or request.url_root.rstrip("/")
    except RuntimeError:
        pass
    return f"{base}{path}"


def _offer_options(offer):
    try:
        opts = json.loads(offer.data or "{}")
    except ValueError:
        opts = {}
    return opts if isinstance(opts, dict) else {}


_CURRENCY_MARKS = (("EUR", ("eur", "€")), ("USD", ("usd", "$")), ("GBP", ("gbp", "£")), ("PLN", ("zł", "zl", "pln")))


def parse_price(text):
    """'5 500 zł' -> (5500.0, 'PLN'); '2 300 EUR' -> (2300.0, 'EUR'); bez liczby -> (None, None)."""
    t = str(text or "").replace("\u00a0", " ").replace("\u202f", " ").lower()
    m = re.search(r"\d[\d .,]*", t)
    if not m:
        return None, None
    num = m.group(0).strip(" .,").replace(" ", "")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", num):  # 6,000 / 1,250.50
        num = num.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", num):  # 12.900 / 1.250,50
        num = num.replace(".", "").replace(",", ".")
    else:  # 5500 / 1250,50
        num = num.replace(",", ".")
    try:
        value = float(num)
    except ValueError:
        return None, None
    currency = next((code for code, marks in _CURRENCY_MARKS if any(mk in t for mk in marks)), None)
    return value, currency


def offer_value(offer):
    """Wartość oferty do leada: cena z PDF (to widzi klient); value z przygotowania tylko, gdy ceny nie da się odczytać."""
    price_value, currency = parse_price(offer.price)
    if price_value is not None:
        return price_value, currency
    opts = _offer_options(offer)
    try:
        return (float(opts["value"]), None) if opts.get("value") is not None else (None, None)
    except (TypeError, ValueError):
        return None, None


def _not_compose_offer():
    """Szkic oferty z okna pisania maila (czeka na „Wyślij” razem z mailem) — nie pokazujemy go w „Ofertach”."""
    return db.or_(SentOffer.data.is_(None), db.not_(SentOffer.data.like('%"compose": true%')))


def offer_client_type_error(lead):
    """Oferta nie wychodzi bez typu klienta (bramka procesu sprzedaży). Zwraca komunikat albo None."""
    if lead is None or lead.client_type in sales_rules.CLIENT_TYPES or not sales_rules.gates_enabled(os.environ):
        return None
    _key, label, why = client_type_hint(lead)
    return (f"Najpierw ustaw typ klienta w leadzie „{lead.company}” (podpowiedź: {label} — {why}). "
            "Bez typu klienta oferta nie wychodzi.")
