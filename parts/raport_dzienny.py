# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("raport_dzienny") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: poranny raport mailowy dla zespołu

def send_daily_report():
    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    pwd = os.environ.get("SMTP_PASS", "").strip()
    to = [e.strip() for e in os.environ.get("REPORT_EMAILS", "").split(",") if e.strip()]
    if not to:  # domyślnie maile użytkowników z USERS
        to = [u["email"] for u in USERS if u.get("email")]
    if not (host and user and pwd and to):
        return False, "Brak konfiguracji: ustaw SMTP_HOST, SMTP_USER i SMTP_PASS"
    port = int(os.environ.get("SMTP_PORT", 587))
    subject, body = build_daily_summary()
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(to)
    if port == 465:  # cyber_Folks: SSL na 465
        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            s.login(user, pwd)
            s.sendmail(user, to, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(user, pwd)
            s.sendmail(user, to, msg.as_string())
    return True, f"Wyslano do: {', '.join(to)}"


def _report_scheduler():
    """Codziennie o REPORT_HOUR (czas warszawski, domyślnie 8) wysyła raport."""
    report_hour = int(os.environ.get("REPORT_HOUR", 8))
    last_sent = None
    while True:
        try:
            now = datetime.now(WARSAW_TZ) if WARSAW_TZ else datetime.now()
            if now.hour == report_hour and last_sent != now.date():
                with app.app_context():
                    ok, msg = send_daily_report()
                last_sent = now.date()
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
