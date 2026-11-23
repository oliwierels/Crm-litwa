# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("formularze") przy funkcjach poczty — działa w przestrzeni nazw crm.py.
# Temat: POWIADOMIENIA Z FORMULARZY I ADRESY AUTOMATÓW
#
# Formularz na stronie (Formspree) przychodzi jako mail OD noreply@formspree.io — klient jest tylko w treści
# („email: …”). Zasady, których pilnuje cały CRM:
#   1) na adres automatu (no-reply, Formspree…) CRM nigdy nic nie wysyła (_smtp_send odmawia),
#   2) nic, co wychodzi do klienta, nie zawiera cytatu powiadomienia Formspree (jest w nim link „wypisz się”, którym
#      klient mógłby wyłączyć nasz formularz na stronie),
#   3) „Odpowiedz” na powiadomienie = nowy mail DO KLIENTA (adres z leada albo z treści formularza), z normalnym
#      tematem, bez cytatu i bez podpinania pod wątek powiadomienia,
#   4) powiadomienie łączy się z leadem po adresie klienta z treści (korespondencja leada, silnik poczty).

_MACHINE_LOCAL = re.compile(r"(?i)^(no-?reply|do-?not-?reply|mailer-daemon|postmaster|bounces?)([._+-].*)?$")
FORM_DOMAINS = ("formspree.io", "formsubmit.co", "getform.io", "usebasin.com", "formcarry.com")
_FORM_SUBJECT = re.compile(r"(?i)(new (form )?submission|new form entry|nowe zgłoszenie z formularza)")
_FORM_BODY = re.compile(r"(?i)(someone just submitted your form|submitted your form on)")
_FORM_EMAIL = re.compile(r"(?im)^\s*(?:e-?mail|_replyto|email address|adres e-?mail)\s*:\s*\n?\s*<?([^\s<>@]+@[^\s<>@]+\.[a-z]{2,})>?")
FORM_LEAK = re.compile(r"(?i)(formspree\.io/unsubscribe|you are receiving this because you confirmed this email address)")
FORM_REPLY_SUBJECT_PL = "33Bots – wynajem humanoidalnego robota Unitree G1"


def _form_domain(addr):
    dom = (addr or "").strip().lower().rpartition("@")[2]
    return bool(dom) and any(dom == d or dom.endswith("." + d) for d in FORM_DOMAINS)


def is_machine_address(addr):
    """Adres automatu: no-reply, mailer-daemon, bounce… albo serwis formularzy (Formspree). Klient tego nie czyta."""
    addr = (addr or "").strip().lower()
    if "@" not in addr:
        return False
    return bool(_MACHINE_LOCAL.match(addr.rpartition("@")[0])) or _form_domain(addr)


def is_form_notification(row):
    """Mail z powiadomieniem o wypełnionym formularzu na stronie (Formspree, FormSubmit, Getform…) —
    MailMessage z Poczty albo InboxMessage z importu zapytań."""
    if row is None or getattr(row, "folder", "in") != "in":
        return False
    frm = row.from_addr or ""
    text = getattr(row, "body_text", None) or getattr(row, "body", None) or ""  # MailMessage albo InboxMessage
    return _form_domain(frm) or (is_machine_address(frm) and bool(
        _FORM_SUBJECT.search(row.subject or "") or _FORM_BODY.search(text)))


def is_machine_mail(row):
    """Odebrany mail od automatu (powiadomienie z formularza, no-reply…) — nie odpowiadamy na adres nadawcy."""
    return row is not None and getattr(row, "folder", "in") == "in" and (
        is_form_notification(row) or is_machine_address(row.from_addr))


def form_client_email(row):
    """Adres klienta z treści powiadomienia formularza („email: …”) albo pusty."""
    own = _own_addresses()
    text = getattr(row, "body_text", None) or getattr(row, "body", None) or ""  # MailMessage albo InboxMessage
    for m in _FORM_EMAIL.finditer(text):
        addr = m.group(1).strip().strip(".,;").lower()
        if EMAIL_RE.match(addr) and not is_machine_address(addr) and addr not in own:
            return addr
    return ""


def form_reply_target(row):
    """(lead albo None, adres klienta) dla maila od automatu: lead po adresie z treści formularza."""
    addr = form_client_email(row) if is_form_notification(row) else ""
    return (_find_lead_by_contact(addr) if addr else None), addr


def form_reply_subject(lead):
    code = lead_market(lead) if lead is not None else DEFAULT_MARKET
    return M(code).get("ask_subject") or FORM_REPLY_SUBJECT_PL


def outgoing_mail_problem(recipients, body):
    """Powód, dla którego maila nie wolno wysłać (adres automatu, cytat powiadomienia Formspree) albo None."""
    bad = [a for a in recipients if is_machine_address(a)]
    if bad:
        return (f"adres {bad[0]} to automat (formularz / no-reply) — klient tego nie przeczyta. "
                "Wpisz adres klienta (jest w leadzie albo w treści formularza).")
    if FORM_LEAK.search(body or ""):
        return ("w treści jest cytat powiadomienia z formularza (z linkiem do wypisania naszego formularza na stronie) "
                "— usuń go przed wysłaniem.")
    return None
