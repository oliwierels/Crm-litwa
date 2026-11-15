# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("uzytkownicy") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: użytkownicy i profile (imię, mail, telefon do ofert)

@app.route("/")
def index():
    return redirect(url_for("crm_dashboard"))


@app.route("/crm/set_user/<int:uid>")
def set_user(uid):
    resp = make_response(redirect(request.referrer or url_for("crm_dashboard")))
    resp.set_cookie("crm_user", str(uid), max_age=60 * 60 * 24 * 365)
    return resp


# ---------- PROFIL KONTA (imię i nazwisko, firmowy e-mail, telefon) ----------
# Dane z kodu (USERS) to wartości domyślne; to, co ktoś wpisze w „Profilu”, je nadpisuje.
# Od nich zależy m.in. oferta z Claude'a (kontakt w PDF i nadawca maila).

USER_PROFILE_FIELDS = ("full_name", "email", "phone")
_USER_PROFILE_DEFAULTS = {u["id"]: {k: u.get(k, "") for k in USER_PROFILE_FIELDS} for u in USERS}
PHONE_RE = re.compile(r"^\+?[0-9 ()\-]{6,20}$")


def apply_user_profiles():
    """Nadpisuje dane kont w USERS (w miejscu, więc działa wszędzie) zapisanymi w profilu."""
    saved = {s.key: s.value for s in AppSetting.query.filter(AppSetting.key.like("user_profile_%")).all()}
    for u in USERS:
        for k in USER_PROFILE_FIELDS:
            v = (saved.get(f"user_profile_{u['id']}_{k}") or "").strip()
            u[k] = v or _USER_PROFILE_DEFAULTS[u["id"]][k]


@app.before_request
def _load_user_profiles():
    if request.endpoint and request.endpoint != "static":
        try:
            apply_user_profiles()
        except Exception:
            db.session.rollback()  # np. pierwszy start przed utworzeniem tabel


def user_can_send_offers(u):
    return bool(u.get("email") and u.get("phone"))


@app.route("/crm/profil", methods=["GET", "POST"])
def crm_profile():
    user = get_current_user(request)
    errors, msg = {}, None
    values = {k: user.get(k, "") for k in USER_PROFILE_FIELDS}
    if request.method == "POST":
        values = {k: request.form.get(k, "").strip()[:120] for k in USER_PROFILE_FIELDS}
        if not values["full_name"]:
            errors["full_name"] = "Podaj imię i nazwisko"
        if values["email"] and not EMAIL_RE.match(values["email"]):
            errors["email"] = "Niepoprawny adres e-mail"
        if values["phone"] and not PHONE_RE.match(values["phone"]):
            errors["phone"] = "Niepoprawny numer telefonu"
        if not errors:
            for k, v in values.items():
                set_setting(f"user_profile_{user['id']}_{k}", v)
            db.session.commit()
            apply_user_profiles()
            msg = "Zapisano"
    login, _ = _mailbox_for(user)
    return render_template(
        "crm/profile.html", user=user, users=USERS, values=values, errors=errors, msg=msg,
        defaults=_USER_PROFILE_DEFAULTS[user["id"]], can_send=user_can_send_offers(user), mailbox=login,
        mailbox_key="SMTP_PASS_" + re.sub(r"[^A-Z0-9]", "_", user["name"].upper()),
    )
