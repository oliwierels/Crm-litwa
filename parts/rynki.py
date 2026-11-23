# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("rynki") zaraz po utworzeniu bazy (db) — działa w przestrzeni nazw crm.py.
# Temat: rynki (Polska / Litwa / Niemcy): rynek oglądany, rynek leada, teksty i ustawienia rynku (market.py)
#
# Każdy lead ma rynek (Lead.market). Serwis włącza swoje rynki zmienną CRM_MARKETS (CRM PL: pl, CRM LT/DE: lt,de).
# Przy kilku rynkach przełącznik w menu wybiera, który oglądamy — widoki pokazują tylko leady (i ich zadania,
# oferty, umowy, maile) tego rynku (parts/rynki_filtr.py). Wszystko do klienta idzie w języku rynku leada.
import market as markets
import poczta
from flask import g, has_request_context

DEFAULT_MARKET = os.environ.get("DEFAULT_MARKET", "").strip().lower() if os.environ.get(
    "DEFAULT_MARKET", "").strip().lower() in markets.MARKETS else markets.DEFAULT
MARKET_COOKIE = "crm_market"


def current_market():
    """Rynek oglądany w CRM (przełącznik w menu); poza zapytaniem przeglądarki — domyślny."""
    if has_request_context():
        forced = getattr(g, "market_force", None)
        if forced:
            return forced
        code = request.cookies.get(MARKET_COOKIE, "")
        if code in markets.MARKETS:
            return code
    return DEFAULT_MARKET


def M(code=None):
    """Teksty i ustawienia rynku (market.py)."""
    return markets.get(code or current_market())


def lead_market(lead):
    return (getattr(lead, "market", None) or DEFAULT_MARKET) if lead is not None else current_market()


def wrong_language(code, *texts):
    """Kod języka, gdy tekst do klienta wyraźnie NIE jest w języku rynku `code` (np. 'en', 'pl'), inaczej None."""
    lang = poczta.detect_lang("\n".join(str(t or "") for t in texts))
    return lang if lang and lang != markets.get(code)["lang"] else None


def _default_market():
    if has_request_context():
        return getattr(g, "market_force", None) or current_market()
    return DEFAULT_MARKET


def mail_phone(phone, code):
    """Telefon do podpisu maila na rynku `code`. Niemcy: bez polskiego numeru (telefon jest tylko w PDF oferty);
    niemiecki numer można ustawić w Railway: MAIL_PHONE_DE."""
    code = code if code in markets.MARKETS else markets.DEFAULT
    if markets.get(code).get("mail_phone", True):
        return phone or ""
    return os.environ.get(f"MAIL_PHONE_{code.upper()}", "").strip()


_PL_PHONE_LINE = re.compile(r"^[ \t]*(?:(?:tel|telefon|phone|mobil|handy|mob)\.?[ \t]*:?[ \t]*)?(?:\+|00)48[\d \t-]{9,14}[ \t]*$",
                            re.I | re.M)


def strip_pl_phone(body, sender):
    """Zabezpieczenie: z maili wysyłanych ze skrzynek rynku bez telefonu (33bots.de) znikają linie z samym polskim
    numerem (+48 …) — np. z podpisu dopisanego przez Claude albo z szablonu. Na rynkach z telefonem — bez zmian."""
    email = ((sender or {}).get("email") or (sender or {}).get("login") or "").lower()
    code = next((c for c in markets.MARKETS if email.endswith("@" + markets.get(c)["domain"])), None)
    if not code or markets.get(code).get("mail_phone", True) or not body:
        return body
    out = _PL_PHONE_LINE.sub("", body)
    own = re.sub(r"\D", "", (sender or {}).get("phone") or "")
    if len(own) >= 9:
        out = "\n".join(l for l in out.split("\n") if re.sub(r"\D", "", l) != own or not l.strip())
    return re.sub(r"\n{3,}", "\n\n", out)
