# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("logowanie") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: logowanie do CRM (hasło, blokada po nieudanych próbach)

LOGIN_MAX_FAILS = 8
LOGIN_WINDOW_S = 15 * 60
_login_fails = {}
_login_lock = threading.Lock()


def crm_password():
    return os.environ.get("CRM_PASSWORD", "").strip()


def _auth_marker(pwd):
    # zmiana hasła w Railway wylogowuje wszystkich
    return hmac.new(app.config["SECRET_KEY"].encode(), b"crm-login:" + pwd.encode(), hashlib.sha256).hexdigest()


def _client_ip():
    return (request.remote_addr or "")[:64]


def _safe_next(target):
    return target if target and target.startswith("/") and not target.startswith("//") else url_for("crm_dashboard")


@app.before_request
def _require_login():
    pwd = crm_password()
    if not pwd or request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
        return None
    if hmac.compare_digest(str(session.get("auth", "")), _auth_marker(pwd)):
        return None
    if (request.headers.get("X-Requested-With") == "fetch" or request.is_json
            or request.path.startswith("/crm/api/") or "application/json" in request.headers.get("Accept", "")):
        return jsonify({"error": "Sesja wygasła — zaloguj się ponownie"}), 401
    nxt = request.full_path.rstrip("?") if request.method == "GET" else url_for("crm_dashboard")
    return redirect(url_for("crm_login", next=nxt))


@app.route("/crm/login", methods=["GET", "POST"])
def crm_login():
    pwd = crm_password()
    nxt = _safe_next(request.values.get("next", ""))
    if not pwd:
        return redirect(nxt)
    error, status = None, 200
    if request.method == "POST":
        ip, now = _client_ip(), time.time()
        with _login_lock:
            fails = [t for t in _login_fails.get(ip, []) if now - t < LOGIN_WINDOW_S]
            _login_fails[ip] = fails
        if len(fails) >= LOGIN_MAX_FAILS:
            error, status = "Za dużo nieudanych prób — spróbuj ponownie za kwadrans.", 429
        elif hmac.compare_digest(request.form.get("password", "").encode(), pwd.encode()):
            session.clear()
            session.permanent = True
            session["auth"] = _auth_marker(pwd)
            with _login_lock:
                _login_fails.pop(ip, None)
            resp = make_response(redirect(nxt))
            try:
                uid = int(request.form.get("user_id", ""))
            except ValueError:
                uid = None
            if uid in USERS_BY_ID:
                resp.set_cookie("crm_user", str(uid), max_age=60 * 60 * 24 * 365)
            return resp
        else:
            with _login_lock:
                _login_fails.setdefault(ip, []).append(now)
            error, status = "Nieprawidłowe hasło", 401
    return render_template("crm/login.html", error=error, next=nxt, current=get_current_user(request)), status


@app.route("/crm/logout")
def crm_logout():
    session.clear()
    return redirect(url_for("crm_login") if crm_password() else url_for("crm_dashboard"))
