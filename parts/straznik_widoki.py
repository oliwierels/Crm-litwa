# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("straznik_widoki") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: strażnik CRM: wątek i strona „Stan CRM”

def _health_scheduler():
    time.sleep(90)
    while True:
        try:
            with app.app_context():
                if _claim_every(HEALTH_CHECK_KEY + ":lock", health.CHECK_MINUTES * 60 - 30):
                    health_check_pass()
                db.session.remove()
        except Exception:
            app.logger.exception("health_check")
        time.sleep(60)


if os.environ.get("HEALTH_WATCH", "1") != "0":
    _start_worker("health", _health_scheduler)

app.jinja_env.globals.update(health_alarm_list=health_alarm_list,
                             health_fmt=lambda ts: health.fmt(health.parse(ts)))


@app.route("/crm/stan")
def crm_health():
    return render_template("crm/stan.html", r=health_report())


@app.route("/crm/stan/sprawdz", methods=["POST"])
def crm_health_check():
    """„Sprawdź teraz” — obieg strażnika bez diagnozy i bez napraw (te robi obieg w tle, co kwadrans)."""
    health_check_pass(diag=False, fix=False)
    return redirect(url_for("crm_health"))
