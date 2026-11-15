# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("poczta_wyslane") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: skrzynka osoby i zapis wysłanych maili w IMAP

def _mailbox_for(user):
    """Skrzynka, z której wychodzi mail danej osoby -> (login, hasło) albo (None, None).

    Najpierw osobna skrzynka osoby: SMTP_PASS_<IMIĘ> (np. SMTP_PASS_OLIWIER, SMTP_PASS_BARTEK),
    login = jej adres z USERS albo SMTP_USER_<IMIĘ>. Bez niej — wspólna SMTP_USER / SMTP_PASS."""
    if (user or {}).get("login") and (user or {}).get("pwd"):
        return user["login"], user["pwd"]
    key = re.sub(r"[^A-Z0-9]", "_", (user or {}).get("name", "").upper())
    pwd = os.environ.get(f"SMTP_PASS_{key}", "").strip() if key else ""
    if pwd:
        login = os.environ.get(f"SMTP_USER_{key}", "").strip() or (user or {}).get("email", "")
        if login:
            return login, pwd
    login = os.environ.get("SMTP_USER", "").strip()
    pwd = os.environ.get("SMTP_PASS", "").strip()
    return (login, pwd) if login and pwd else (None, None)


def _imap_sent_folder(im):
    """Nazwa folderu Wysłane na zalogowanej skrzynce IMAP (IMAP_SENT_FOLDER albo flaga \\Sent) albo None."""
    folder = os.environ.get("IMAP_SENT_FOLDER", "").strip()
    if folder:
        return folder
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
        names.append(name)
    for cand in ('"INBOX.Sent"', "INBOX.Sent", '"Sent"', "Sent", '"Sent Items"'):
        if cand in names:
            return cand
    return None


def _imap_save_sent(raw_msg, login, pwd, host=None):
    """Kopia wysłanego maila w folderze Wysłane skrzynki (żeby był widoczny w poczcie cyber_Folks).
    Nie przerywa wysyłki — zwraca None albo opis problemu."""
    if os.environ.get("IMAP_SAVE_SENT", "1").strip() in ("0", "false", "nie", ""):
        return None
    import imaplib
    host = host or os.environ.get("IMAP_HOST", "").strip() or os.environ.get("SMTP_HOST", "").strip()
    port = int(os.environ.get("IMAP_PORT", 993))
    try:
        im = imaplib.IMAP4_SSL(host, port, timeout=20)
        try:
            im.login(login, pwd)
            folder = _imap_sent_folder(im)
            if not folder:
                return "nie znalazłem folderu Wysłane (ustaw IMAP_SENT_FOLDER)"
            im.append(folder, "\\Seen", imaplib.Time2Internaldate(time.time()), raw_msg)
        finally:
            try:
                im.logout()
            except Exception:
                pass
    except Exception as e:
        return f"kopia w Wysłanych nie zapisała się: {type(e).__name__}"
    return None
