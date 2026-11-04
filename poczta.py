"""Poczta w CRM: czytanie skrzynek IMAP (Odebrane i Wysłane, także archiwum), parsowanie maili,
załączniki na żądanie i proste rozpoznawanie języka. Czyste funkcje — bez Flaska i bazy; crm.py
zapisuje wyniki w tabeli mail_messages i pokazuje je w zakładce „Poczta”.

Skrzynki otwieramy tylko do odczytu (select readonly + BODY.PEEK), więc CRM niczego nie oznacza
jako przeczytane, nie usuwa i nie przenosi.
"""
import email
import email.policy
import html as _html
import imaplib
import re
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

BIG_MESSAGE = 4 * 1024 * 1024   # większe maile: nagłówki + początek treści (bez ciężkich załączników)
BODY_LIMIT = 60000              # tyle znaków treści trzymamy w bazie


class MailboxError(Exception):
    pass


def connect(host, port, login, pwd, timeout=30):
    try:
        im = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    except OSError as e:
        raise MailboxError(f"brak połączenia z {host}: {type(e).__name__}") from e
    try:
        im.login(login, pwd)
    except imaplib.IMAP4.error as e:
        try:
            im.logout()
        except Exception:
            pass
        raise MailboxError(f"logowanie do {login} nie powiodło się (złe hasło?)") from e
    return im


def close(im):
    try:
        im.logout()
    except Exception:
        pass


def sent_folder(im, forced=""):
    """Folder Wysłane: IMAP_SENT_FOLDER, flaga \\Sent albo typowe nazwy (cyber_Folks: INBOX.Sent)."""
    if forced:
        return forced
    typ, boxes = im.list()
    names = []
    for raw in boxes or []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        m = re.match(r'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$', line)
        if not m:
            continue
        name = m.group("name").strip()
        if "\\Sent" in m.group("flags"):
            return name
        names.append(name.strip('"'))
    for cand in ("INBOX.Sent", "Sent", "Sent Items", "Sent Messages", "INBOX.Sent Items", "Wysłane", "INBOX.Wysłane",
                 "Gesendet", "INBOX.Gesendet", "Išsiųsti", "INBOX.Išsiųsti"):
        if cand in names:
            return f'"{cand}"' if " " in cand else cand
    return None


def select(im, folder):
    """Otwiera folder tylko do odczytu. Zwraca UIDVALIDITY (str) albo rzuca MailboxError."""
    typ, _ = im.select(folder, readonly=True)
    if typ != "OK":
        raise MailboxError(f"nie mogę otworzyć folderu {folder}")
    resp = im.response("UIDVALIDITY")[1]
    val = resp[0] if resp and resp[0] else b"0"
    return val.decode() if isinstance(val, bytes) else str(val)


def uids_after(im, last_uid):
    """UID-y maili nowszych niż last_uid (rosnąco)."""
    typ, data = im.uid("SEARCH", None, f"UID {int(last_uid) + 1}:*")
    if typ != "OK" or not data or not data[0]:
        return []
    out = sorted(int(x) for x in data[0].split() if x.isdigit())
    return [u for u in out if u > int(last_uid)]


def _sizes(im, uids):
    sizes = {}
    if not uids:
        return sizes
    typ, data = im.uid("FETCH", ",".join(str(u) for u in uids), "(RFC822.SIZE)")
    for item in data or []:
        line = item.decode("utf-8", "replace") if isinstance(item, bytes) else (
            item[0].decode("utf-8", "replace") if isinstance(item, tuple) else "")
        m = re.search(r"UID (\d+).*RFC822\.SIZE (\d+)|RFC822\.SIZE (\d+).*UID (\d+)", line)
        if m:
            uid = int(m.group(1) or m.group(4))
            sizes[uid] = int(m.group(2) or m.group(3))
    return sizes


def fetch_raw(im, uid, big=False):
    """Surowy mail (bajty). Duży: nagłówki + pierwsze 300 kB treści."""
    if big:
        typ, data = im.uid("FETCH", str(uid), "(BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.300000>)")
        parts = [p[1] for p in (data or []) if isinstance(p, tuple) and p[1]]
        return b"\r\n".join(parts) if parts else None
    typ, data = im.uid("FETCH", str(uid), "(BODY.PEEK[])")
    return next((p[1] for p in (data or []) if isinstance(p, tuple) and p[1]), None)


def fetch_batch(im, uids):
    """[(uid, surowe bajty, czy_obcięty)] dla listy UID-ów."""
    sizes = _sizes(im, uids)
    out = []
    for uid in uids:
        big = sizes.get(uid, 0) > BIG_MESSAGE
        try:
            raw = fetch_raw(im, uid, big=big)
        except imaplib.IMAP4.error:
            raw = None
        if raw:
            out.append((uid, raw, big))
    return out


# --- parsowanie ---

def _hdr(msg, name):
    try:
        return str(msg.get(name) or "").strip()
    except Exception:
        return ""


def html_to_text(text):
    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>|</li>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "• ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return _html.unescape(text)


def body_text(msg):
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
    except Exception:
        part = None
    if part is None:
        return ""
    try:
        text = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", "replace")
    if part.get_content_type() == "text/html":
        text = html_to_text(text)
    text = text.replace("\r\n", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def attachments(msg):
    out = []
    try:
        parts = list(msg.iter_attachments())
    except Exception:
        parts = []
    for i, part in enumerate(parts):
        name = part.get_filename()
        if not name:
            if part.get_content_maintype() in ("text", "multipart"):
                continue
            name = f"zalacznik_{i + 1}"
        try:
            size = len(part.get_payload(decode=True) or b"")
        except Exception:
            size = 0
        out.append({"n": i, "name": str(name)[:200], "type": part.get_content_type(), "size": size})
    return out


def attachment_bytes(raw, n):
    """(nazwa, typ, bajty) n-tego załącznika z surowego maila albo None."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    try:
        parts = list(msg.iter_attachments())
    except Exception:
        return None
    if not 0 <= n < len(parts):
        return None
    part = parts[n]
    return (part.get_filename() or f"zalacznik_{n + 1}"), part.get_content_type(), (part.get_payload(decode=True) or b"")


def _addr_list(value):
    return [(n.strip(), a.strip().lower()) for n, a in getaddresses([value or ""]) if a and "@" in a]


def parse(raw, truncated=False):
    """Słownik pól maila do zapisu w bazie."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    try:
        when = parsedate_to_datetime(_hdr(msg, "Date"))
        when = when.astimezone(timezone.utc).replace(tzinfo=None) if when.tzinfo else when
    except Exception:
        when = None
    frm_name, frm = parseaddr(_hdr(msg, "From"))
    to = _addr_list(_hdr(msg, "To"))
    cc = _addr_list(_hdr(msg, "Cc"))
    text = body_text(msg)
    if truncated:
        text = (text + "\n\n[…] (duży mail — w CRM pokazany początek treści; całość w skrzynce pocztowej)").strip()
    msg_id = _hdr(msg, "Message-ID")[:400]
    refs = _hdr(msg, "References")
    in_reply = _hdr(msg, "In-Reply-To")[:400]
    first_ref = (re.findall(r"<[^>]+>", refs) or [in_reply or msg_id])[0]
    addrs = {frm.lower()} | {a for _, a in to + cc}
    return {
        "message_id": msg_id, "in_reply_to": in_reply, "refs": refs[:4000],
        "thread_key": (first_ref or msg_id)[:400],
        "from_addr": frm.lower()[:300], "from_name": (frm_name or "")[:300],
        "to_addrs": ", ".join(a for _, a in to)[:4000], "cc_addrs": ", ".join(a for _, a in cc)[:4000],
        "to_names": ", ".join(f"{n} <{a}>" if n else a for n, a in to)[:4000],
        "addrs": " " + " ".join(sorted(a for a in addrs if a)) + " ",
        "subject": _hdr(msg, "Subject")[:500], "date": when,
        "body_text": text[:BODY_LIMIT], "snippet": re.sub(r"\s+", " ", text)[:300],
        "attachments": attachments(msg) if not truncated else [],
        "truncated": truncated,
    }


# --- cytowana historia (do tłumaczenia bierzemy tylko nową część) ---

_QUOTE_START = re.compile(
    r"^(>|-{2,} ?(Original|Ursprüngliche|Oryginalna|Pradinis)|_{5,}|"
    r"(Am|On|W dniu|Dnia) .{3,120}(schrieb|wrote|pisze|napisał)|"
    r".{0,60}\d{4}[-./]\d{1,2}[-./]\d{1,2}.{0,60}(rašė|wrote|schrieb|napisał)\s*:?$|"
    r"(Von|From|Od|Nuo|Siuntėjas|Sender)\s*:\s.+@)", re.IGNORECASE)


def split_quote(text):
    """(nowa część, cytowana historia) — historia zaczyna się od pierwszego znacznika cytatu."""
    lines = (text or "").split("\n")
    for i, line in enumerate(lines):
        if i > 0 and _QUOTE_START.match(line.strip()):
            return "\n".join(lines[:i]).strip(), "\n".join(lines[i:]).strip()
    return (text or "").strip(), ""


# --- język ---

_STOP = {
    "pl": "i w nie się na jest że do z to dla oraz jak dzień dobry pozdrawiam proszę dziękuję czy być mamy jesteśmy "
          "państwa pan pani przez już tak tylko robota wydarzenie termin oferta",
    "lt": "ir kad yra su į dėl jūsų laba diena ačiū pagarbiai kaip bet mes prašome renginio renginys robotas būtų "
          "galite norime labai nuo iki apie mūsų jums taip ne",
    "de": "und der die das ist nicht mit für sie wir ich ein eine bitte grüße vielen dank auf zu den dem des sind "
          "haben wird bei uns ihr ihnen veranstaltung roboter freundlichen",
    "en": "the and is are to of for you we please thank regards with our your this that be have will event robot "
          "would could dear hello best kind",
}
_STOP = {k: set(v.split()) for k, v in _STOP.items()}
_CHARS = {"pl": set("ćłńóśźż"), "lt": set("ėįųūčšž"), "de": set("äöüß")}  # ą/ę są w pl i lt


def detect_lang(text):
    """Najbardziej prawdopodobny język tekstu (pl/lt/de/en) albo '' gdy nie wiadomo."""
    t = (text or "").lower()[:4000]
    words = re.findall(r"[a-ząćęłńóśźżėįųūčšäöüß]+", t)
    if not words:
        return ""
    score = {k: sum(1 for w in words if w in s) for k, s in _STOP.items()}
    for k, chars in _CHARS.items():
        score[k] += 2 * sum(1 for c in t if c in chars)
    best = max(score, key=score.get)
    second = sorted(score.values())[-2]
    if score[best] < 2 or score[best] < second * 1.3:
        return ""
    return best
