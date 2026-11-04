"""Oferty handlowe 33bots: 2-stronicowy PDF w firmowym layoucie (ciemny, Poppins).

render_offer_pdf(data, contact) -> bytes

data (dict) — to, co przychodzi z narzędzia send_offer:
  client         krótka, oficjalna nazwa klienta (stopka strony 2, nagłówek)
  headline       pierwsza linia tytułu okładki, np. "Robot humanoidalny"
  subtitle       zdanie pod tytułem okładki
  cover          lista 3-4 par {"label", "value"} w ramce okładki (kontakt dopisywany sam, jeśli brak)
  intro          akapit na stronie 2 (rola robota + doświadczenie)
  cards          2 karty {"label", "title", "items": [3 punkty]}
  price_title    np. "Cały dzień obecności robota"
  price_sub      np. "Częstochowa · 6.11.2026"
  price          np. "6 100 zł"
  included       zdanie "W cenie: ..."
  note           tekst w ramce kontaktu (opcjonalnie)
  section_title  nagłówek strony 2 (opcjonalnie)
  language       język rynku leada: "lt" (Litwa) albo "de" (Niemcy); "pl"/"en" tylko dla zgodności
contact: (imię i nazwisko, e-mail, telefon)
"""
import io
import os

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fonts")
_FONTS = {
    "Poppins-Bold": "Poppins-Bold.ttf",
    "Poppins-Light": "Poppins-Light.ttf",
    "Poppins-Medium": "Poppins-Medium.ttf",
    "Poppins-Regular": "Poppins-Regular.ttf",
    "Mono": "DejaVuSansMono.ttf",
    "Mono-Bold": "DejaVuSansMono-Bold.ttf",
}
_registered = False


def _register_fonts():
    global _registered
    if _registered:
        return
    for name, fn in _FONTS.items():
        pdfmetrics.registerFont(TTFont(name, os.path.join(FONTS_DIR, fn)))
    _registered = True


W, H = A4
BG = HexColor("#0A0C10")
PANEL = HexColor("#15171C")
LINE = HexColor("#2A2D34")
ACC = HexColor("#2F6FE0")
ACC2 = HexColor("#7FA8F5")
ACCBG = HexColor("#0F1B33")
WHITE = HexColor("#FFFFFF")
MUT = HexColor("#8A8F98")
MUT2 = HexColor("#5E636C")
ML = 52
MR = W - 52
CW = MR - ML

DEFAULT_INCLUDED = {
    "pl": "W cenie: dojazd, dedykowany operator, personalizacja osobowości i wiedzy robota oraz "
          "wsparcie techniczne na miejscu. Bez ukrytych opłat.",
    "lt": "Į kainą įskaičiuota: atvykimas, asmeninis operatorius, roboto asmenybės ir žinių pritaikymas bei "
          "techninė pagalba vietoje. Jokių paslėptų mokesčių.",
    "en": "Included: travel, a dedicated operator, personalisation of the robot's personality and "
          "knowledge, and on-site technical support. No hidden fees.",
    "de": "Im Preis enthalten: Anreise, ein eigener Operator, Anpassung der Persönlichkeit und des Wissens des "
          "Roboters sowie technischer Support vor Ort. Keine versteckten Kosten.",
}
DEFAULT_NOTE = {
    "pl": "Chętnie odpowiem na pytania i ustalę szczegóły scenariusza. Oferta ważna 14 dni.",
    "en": "I am happy to answer any questions and agree on the scenario. This offer is valid for 14 days.",
    "lt": "Mielai atsakysiu į klausimus ir suderinsiu scenarijaus detales. Pasiūlymas galioja 14 dienų.",
    "de": "Gerne beantworte ich Ihre Fragen und stimme das Szenario mit Ihnen ab. Das Angebot ist 14 Tage gültig.",
}
LABELS = {
    "pl": {"offer": "Oferta", "label": "OFERTA HANDLOWA", "scope": "ZAKRES I WYCENA", "price": "WYCENA",
           "contact": "KONTAKT", "net": "netto", "section": "Robot na Państwa wydarzeniu"},
    "lt": {"offer": "Pasiūlymas", "label": "KOMERCINIS PASIŪLYMAS", "scope": "APIMTIS IR KAINA", "price": "KAINA",
           "contact": "KONTAKTAI", "net": "suma be PVM", "section": "Robotas jūsų renginyje"},
    "en": {"offer": "Offer", "label": "COMMERCIAL OFFER", "scope": "SCOPE & PRICING", "price": "PRICE",
           "contact": "CONTACT", "net": "net, excl. VAT", "section": "The robot at your event"},
    "de": {"offer": "Angebot", "label": "ANGEBOT", "scope": "LEISTUNGSUMFANG UND PREIS", "price": "PREIS",
           "contact": "KONTAKT", "net": "netto, zzgl. USt.", "section": "Der Roboter auf Ihrer Veranstaltung"},
}
HEADLINE = {"en": "Humanoid robot", "lt": "Humanoidinis robotas", "de": "Humanoider Roboter", "pl": "Robot humanoidalny"}
SITE = {"lt": "33bots.lt", "de": "33bots.de", "pl": "33bots.pl", "en": "33bots.lt"}


class OfferError(ValueError):
    pass


def _y(top):
    return H - top


def _text(c, x, y, s, font="Poppins-Regular", size=10, color=WHITE, right=False):
    c.setFont(font, size)
    c.setFillColor(color)
    (c.drawRightString if right else c.drawString)(x, y, s)


def _spaced(c, x, y, s, size=7, color=ACC2, cs=2.6, font="Mono-Bold"):
    t = c.beginText(x, y)
    t.setFont(font, size)
    t.setFillColor(color)
    t.setCharSpace(cs)
    t.textLine(s)
    t.setCharSpace(0)  # bez resetu odstęp "przecieka" na kolejne napisy
    c.drawText(t)


def _wrap(s, font, size, width):
    out = []
    for para in str(s).split("\n"):
        line = ""
        for word in para.split():
            test = (line + " " + word).strip()
            if stringWidth(test, font, size) <= width:
                line = test
            else:
                if line:
                    out.append(line)
                line = word
        out.append(line)
    return out


def _para(c, x, top, s, width, font="Poppins-Light", size=10, lead=15, color=MUT):
    lines = _wrap(s, font, size, width)
    for i, line in enumerate(lines):
        _text(c, x, _y(top + i * lead), line, font, size, color)
    return top + len(lines) * lead


def _box(c, x, top, w, h, fill=PANEL, r=10, stroke=None):
    c.setFillColor(fill)
    if stroke:
        c.setStrokeColor(stroke)
        c.setLineWidth(1)
    c.roundRect(x, _y(top + h), w, h, r, fill=1, stroke=1 if stroke else 0)


def _check(c, x, y):
    c.setStrokeColor(ACC2)
    c.setLineWidth(1.3)
    p = c.beginPath()
    p.moveTo(x, y + 3.5)
    p.lineTo(x + 3, y + 0.5)
    p.lineTo(x + 8, y + 6.5)
    c.drawPath(p, stroke=1, fill=0)


def _bullets(c, x, top, items, width, size=9.2, lead=13.5, gap=9):
    for it in items:
        lines = _wrap(it, "Poppins-Regular", size, width - 15)
        _check(c, x, _y(top) - 1)
        for i, line in enumerate(lines):
            _text(c, x + 15, _y(top + i * lead), line, "Poppins-Regular", size, WHITE)
        top += len(lines) * lead + gap
    return top


def _bg(c):
    c.setFillColor(BG)
    c.rect(0, 0, W, H, fill=1, stroke=0)


def _s(v, field, maxlen=600):
    v = "" if v is None else str(v).strip()
    if len(v) > maxlen:
        raise OfferError(f"pole {field} jest za długie (max {maxlen} znaków)")
    return v


def normalize(data):
    """Sprawdza i uzupełnia dane oferty. Rzuca OfferError z opisem po polsku."""
    if not isinstance(data, dict):
        raise OfferError("offer musi być obiektem")
    raw_lang = str(data.get("language") or "lt").lower()[:2]
    lang = raw_lang if raw_lang in LABELS else "lt"
    d = {"language": lang}
    for f, maxlen in (("client", 80), ("headline", 40), ("subtitle", 220), ("intro", 600),
                      ("price_title", 60), ("price_sub", 90), ("price", 20), ("included", 300),
                      ("note", 160), ("section_title", 40)):
        d[f] = _s(data.get(f), f, maxlen)
    for f in ("client", "price", "price_title", "intro"):
        if not d[f]:
            raise OfferError(f"brak wymaganego pola offer.{f}")
    d["headline"] = d["headline"] or HEADLINE[lang]
    d["included"] = d["included"] or DEFAULT_INCLUDED[lang]
    d["note"] = d["note"] or DEFAULT_NOTE[lang]
    d["section_title"] = d["section_title"] or LABELS[lang]["section"]
    cover = data.get("cover") or []
    if not isinstance(cover, list) or not 1 <= len(cover) <= 4:
        raise OfferError("offer.cover: od 1 do 4 pozycji {label, value}")
    d["cover"] = [(_s(x.get("label"), "cover.label", 22).upper(), _s(x.get("value"), "cover.value", 34))
                  for x in cover if isinstance(x, dict)]
    cards = data.get("cards") or []
    if not isinstance(cards, list) or len(cards) != 2:
        raise OfferError("offer.cards: dokładnie 2 karty {label, title, items}")
    d["cards"] = []
    for k in cards:
        items = [_s(i, "cards.items", 90) for i in (k.get("items") or []) if str(i).strip()]
        if not 1 <= len(items) <= 4:
            raise OfferError("każda karta potrzebuje od 1 do 4 punktów (items)")
        d["cards"].append((_s(k.get("label"), "cards.label", 24).upper(), _s(k.get("title"), "cards.title", 32), items))
    return d


def _site(d, email):
    """Strona w stopce: domena skrzynki nadawcy (33bots.lt / 33bots.de), inaczej domena rynku."""
    dom = (email or "").rsplit("@", 1)[-1].lower()
    return dom if dom.startswith("33bots.") else SITE.get(d["language"], "33bots.lt")


def render_offer_pdf(data, contact):
    """data: wynik normalize(); contact: (imię i nazwisko, e-mail, telefon)."""
    _register_fonts()
    d = data
    L = LABELS[d["language"]]
    name, email, phone = contact
    cover = list(d["cover"])
    if len(cover) < 4 and not any(lab in ("KONTAKT", "CONTACT") for lab, _ in cover):
        cover.append((L["contact"], name))

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"{L['offer']} 33bots – {d['client']}")
    c.setAuthor("33bots")

    # ---------- strona 1: okładka ----------
    _bg(c)
    cx, cy = 565, _y(175)
    for r, col in ((194, ACC), (155, LINE), (115, ACC), (78, LINE), (44, ACC)):
        c.setStrokeColor(col)
        c.setLineWidth(1)
        c.circle(cx, cy, r, stroke=1, fill=0)
    c.setFillColor(ACC)
    c.circle(cx, cy, 16, stroke=0, fill=1)
    _text(c, ML, _y(68), "33bots", "Mono-Bold", 18)
    _spaced(c, ML, _y(330), L["label"], 8, ACC2, 3)
    _text(c, ML, _y(380), d["headline"], "Poppins-Bold", 36)
    _text(c, ML, _y(424), "Unitree G1", "Poppins-Bold", 36, ACC2)
    if d["subtitle"]:
        _para(c, ML, 467, d["subtitle"], 360, "Poppins-Light", 12, 20, MUT)
    bt, bh = 604, 118
    _box(c, ML, bt, CW, bh)
    widths = [max(stringWidth(v, "Poppins-Medium", 10.5), stringWidth(lab, "Mono-Bold", 6) + 12) + 34
              for lab, v in cover]
    scale = (CW - 40) / sum(widths)
    x = ML + 20
    for i, (lab, val) in enumerate(cover):
        w = widths[i] * scale
        _spaced(c, x, _y(bt + 32), lab, 6, MUT, 1.8)
        size = 10.5
        while size > 7 and stringWidth(val, "Poppins-Medium", size) > w - 16:
            size -= 0.5
        _text(c, x, _y(bt + 54), val, "Poppins-Medium", size)
        if i < len(cover) - 1:
            c.setStrokeColor(LINE)
            c.setLineWidth(0.8)
            c.line(x + w - 12, _y(bt + 20), x + w - 12, _y(bt + 70))
        x += w
    _text(c, ML + 20, _y(bt + 87), f"{email}  ·  {phone}  ·  {_site(d, email)}", "Poppins-Light", 7.5, MUT)
    _text(c, ML, _y(772), "33Bots Bartosz Wysocki  ·  NIP 5253090645", "Poppins-Light", 7, MUT2)
    c.showPage()

    # ---------- strona 2: zakres i wycena ----------
    _bg(c)
    _text(c, ML, _y(46), "33bots", "Mono-Bold", 12)
    _text(c, MR, _y(45), f"{L['offer']}  ·  {d['client']}", "Poppins-Light", 7.5, MUT, right=True)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.7)
    c.line(ML, _y(58), MR, _y(58))
    _spaced(c, ML, _y(99), L["scope"], 7, ACC2, 2.6)
    _text(c, ML, _y(131), d["section_title"], "Poppins-Bold", 23)
    c.setFillColor(ACC)
    c.rect(ML, _y(146), 34, 3, fill=1, stroke=0)
    top = _para(c, ML, 172, d["intro"], CW, "Poppins-Light", 10, 15.5, MUT)

    gap = 13
    cw = (CW - gap) / 2
    ct = max(top + 14, 230)

    def card_h(card):
        h = 92
        for it in card[2]:
            h += len(_wrap(it, "Poppins-Regular", 9.2, cw - 55)) * 13.5 + 9
        return h + 14

    ch = max(card_h(k) for k in d["cards"])
    for i, (lab, title, items) in enumerate(d["cards"]):
        x = ML + i * (cw + gap)
        _box(c, x, ct, cw, ch)
        c.setFillColor(ACC)
        c.rect(x, _y(ct + 3), cw, 3.5, fill=1, stroke=0)
        _spaced(c, x + 20, _y(ct + 30), lab, 6.5, ACC2, 2.2)
        _text(c, x + 20, _y(ct + 55), title, "Poppins-Bold", 13)
        _bullets(c, x + 20, ct + 82, items, cw - 40)

    pt = ct + ch + 20
    incl = _wrap(d["included"], "Poppins-Regular", 9.2, CW - 50)
    ph = 104 + len(incl) * 14 + 12
    _box(c, ML, pt, CW, ph)
    _spaced(c, ML + 26, _y(pt + 29), L["price"], 6.5, ACC2, 2.2)
    _text(c, ML + 26, _y(pt + 56), d["price_title"], "Poppins-Regular", 12.5)
    if d["price_sub"]:
        _text(c, ML + 26, _y(pt + 74), d["price_sub"], "Poppins-Light", 8.5, MUT)
    _text(c, MR - 26, _y(pt + 64), d["price"], "Poppins-Bold", 27, ACC2, right=True)
    _text(c, MR - 26, _y(pt + 81), L["net"], "Poppins-Light", 8, MUT, right=True)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.7)
    c.line(ML + 26, _y(pt + 92), MR - 26, _y(pt + 92))
    for i, line in enumerate(incl):
        _text(c, ML + 26, _y(pt + 110 + i * 14), line, "Poppins-Regular", 9.2)

    kt = pt + ph + 18
    _box(c, ML, kt, CW, 112, fill=ACCBG, stroke=ACC)
    _spaced(c, ML + 26, _y(kt + 28), L["contact"], 6.5, ACC2, 2.2)
    _text(c, ML + 26, _y(kt + 54), name, "Poppins-Bold", 16.5)
    _text(c, ML + 26, _y(kt + 76), email, "Poppins-Regular", 10.5, ACC2)
    _text(c, ML + 26, _y(kt + 93), phone, "Poppins-Regular", 10.5, ACC2)
    _para(c, ML + 245, kt + 40, d["note"], CW - 271, "Poppins-Regular", 9.2, 17, WHITE)
    if kt + 112 > 785:
        raise OfferError("treść nie mieści się na stronie 2 — skróć intro albo punkty kart")
    c.setStrokeColor(LINE)
    c.line(ML, _y(791), MR, _y(791))
    _text(c, ML, _y(805), _site(d, email), "Poppins-Light", 7.5, MUT)
    _text(c, MR, _y(805), "02 / 02", "Mono", 7.5, MUT, right=True)
    c.save()
    return buf.getvalue()


def offer_filename(client):
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", client).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:60] or "klient"
    return f"Oferta_33bots_{s}.pdf"
