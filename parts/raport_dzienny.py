# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("raport_dzienny") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: poranny raport mailowy dla zespołu

REPORT_SENT_KEY = "daily_report_sent"  # dzień ostatniej wysyłki — restart serwera o 8 rano nie wyśle raportu drugi raz


def report_wanted():
    """Czy poranny raport jest zamówiony: podani adresaci (REPORT_EMAILS) albo — jak dawniej — wspólna skrzynka
    SMTP_USER / SMTP_PASS (wtedy idzie do wszystkich osób z CRM). Bez tego raport jest wyłączony: nic nie wysyła,
    a strażnik go nie pilnuje (brak ustawień opcjonalnej funkcji to nie awaria)."""
    env = os.environ.get
    return bool(env("SMTP_HOST", "").strip() and (env("REPORT_EMAILS", "").strip()
                                                  or (env("SMTP_USER", "").strip() and env("SMTP_PASS", "").strip())))


def report_sender():
    """(login, hasło, host, port) skrzynki, z której wychodzi raport: osoba z REPORT_FROM (imię z CRM, jej hasło
    SMTP_PASS_<IMIĘ> — to samo co przy zwykłych mailach z CRM), inaczej wspólna SMTP_USER / SMTP_PASS, a gdy jej nie
    ma — pierwsza osoba z CRM, która ma skonfigurowaną skrzynkę. (None, …), gdy żadnej nie ma."""
    env = os.environ.get
    who = env("REPORT_FROM", "").strip().lower()
    people = [u for u in USERS if u["name"].lower() == who] if who else []
    if not people and not (env("SMTP_USER", "").strip() and env("SMTP_PASS", "").strip()):
        people = list(USERS) if not who else []
    for person in people or [None]:
        key = re.sub(r"[^A-Z0-9]", "_", (person or {}).get("name", "").upper())
        login, pwd = _mailbox_for(person)
        if login and pwd:
            host = (env(f"SMTP_HOST_{key}", "").strip() if key else "") or env("SMTP_HOST", "").strip()
            port = int((env(f"SMTP_PORT_{key}", "").strip() if key else "") or env("SMTP_PORT", 587))
            return login, pwd, host, port
    return None, None, env("SMTP_HOST", "").strip(), int(env("SMTP_PORT", 587))


def _report_smtp(host, port, login, pwd):
    if port == 465:  # cyber_Folks: SSL na 465
        s = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        s = smtplib.SMTP(host, port, timeout=30)
        s.starttls()
    s.login(login, pwd)
    return s


def send_daily_report(check_only=False):
    """Wysyła poranny raport. check_only — tylko sprawdza ustawienia i logowanie do skrzynki (nic nie wysyła)."""
    to = [e.strip() for e in os.environ.get("REPORT_EMAILS", "").split(",") if e.strip()]
    if not to:  # dawne ustawienie (wspólna skrzynka bez listy adresatów): maile użytkowników z USERS
        to = [u["email"] for u in USERS if u.get("email")]
    login, pwd, host, port = report_sender()
    if not (host and login and pwd and to):
        who = os.environ.get("REPORT_FROM", "").strip()
        return False, ("Brak konfiguracji: " + (f"osoba z REPORT_FROM („{who}”) nie ma skrzynki z hasłem (SMTP_PASS_<IMIĘ>)" if who
                                                else "ustaw REPORT_EMAILS (adresaci) i REPORT_FROM (imię osoby, z której skrzynki "
                                                     "ma wyjść raport) albo wspólną skrzynkę SMTP_USER i SMTP_PASS"))
    if check_only:
        with _report_smtp(host, port, login, pwd):
            pass
        return True, f"gotowy: {login} → {', '.join(to)}"
    subject, body = build_daily_summary()
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = login
    msg["To"] = ", ".join(to)
    with _report_smtp(host, port, login, pwd) as s:
        s.sendmail(login, to, msg.as_string())
    return True, f"Wyslano do: {', '.join(to)}"


def _report_scheduler():
    """Codziennie o REPORT_HOUR (czas warszawski, domyślnie 8) wysyła raport — tylko gdy jest zamówiony."""
    report_hour = int(os.environ.get("REPORT_HOUR", 8))
    checked = False
    while True:
        try:
            if report_wanted():
                now = datetime.now(WARSAW_TZ) if WARSAW_TZ else datetime.now()
                with app.app_context():
                    if not checked:  # po starcie: czy ustawienia i hasło działają (bez wysyłki) — strażnik wie od razu
                        checked = True
                        ok, msg = send_daily_report(check_only=True)
                        health_beat("raport", ok, info=msg, error=msg)
                    if now.hour == report_hour and get_setting(REPORT_SENT_KEY) != now.date().isoformat():
                        set_setting(REPORT_SENT_KEY, now.date().isoformat())  # najpierw znacznik — nigdy dwa razy
                        db.session.commit()
                        ok, msg = send_daily_report()
                        health_beat("raport", ok, info="raport wysłany", error=msg)
        except Exception as e:
            health_fail("raport", e)
        time.sleep(60)


if os.environ.get("SMTP_HOST"):
    _start_worker("report", _report_scheduler)


@app.route("/crm/report/test")
def crm_report_test():
    try:
        ok, msg = send_daily_report()
    except Exception as e:
        ok, msg = False, f"Blad wysylki: {e}"
    return jsonify({"ok": ok, "info": msg})
