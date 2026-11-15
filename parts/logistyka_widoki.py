# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("logistyka_widoki") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: logistyka: strony, operatorzy, zlecenia operatora

@app.route("/crm/logistics")
def crm_logistics():
    today = date.today()
    d_from, d_to = _logistics_range()
    tentative = request.args.get("niepewne") == "1"
    plan = build_logistics(d_from, d_to, tentative)
    month_start = today.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    presets = [
        ("7 dni", today, today + timedelta(days=6)),
        ("14 dni", today, today + timedelta(days=13)),
        ("30 dni", today, today + timedelta(days=30)),
        ("Ten miesiąc", month_start, next_month - timedelta(days=1)),
        ("Następny miesiąc", next_month, (next_month + timedelta(days=32)).replace(day=1) - timedelta(days=1)),
    ]
    return render_template(
        "crm/logistics.html", plan=plan, d_from=d_from, d_to=d_to, tentative=tentative, presets=presets,
        robot_kinds=ROBOT_KINDS, default_base=LOGISTICS_DEFAULT_BASE, today=today,
        fmt_km=logistics.fmt_km, fmt_hours=logistics.fmt_hours,
    )


def _logistics_back(anchor=None, **payload):
    # strona Logistyka zapisuje w tle (fetch) i sama przelicza panele — wtedy bez przekierowania
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True, **payload})
    back = request.form.get("back") or ""
    if not back.startswith("/crm/logistics"):
        back = url_for("crm_logistics")
    return redirect(back + (f"#{anchor}" if anchor else ""))


def _int_or_none(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


@app.route("/crm/logistics/crew/save", methods=["POST"])
def crm_logistics_crew_save():
    f = request.form
    crew_id = _int_or_none(f.get("crew_id"))
    if crew_id:
        crew = db.session.get(Crew, crew_id)
        if not crew:
            return _logistics_back()
    else:
        lead = db.session.get(Lead, _int_or_none(f.get("lead_id")) or 0)
        dt = parse_date(f.get("event_date"))
        if not lead or not dt:
            return _logistics_back()
        crew = Crew(lead_id=lead.id, event_date=dt.date())
        db.session.add(crew)
    if "operator_id" in f:
        op_id = _int_or_none(f.get("operator_id"))
        crew.operator_id = op_id if op_id and db.session.get(Operator, op_id) else None
    if "robot_id" in f:
        rid = _int_or_none(f.get("robot_id"))
        crew.robot_id = rid if rid and db.session.get(RobotUnit, rid) else None
    if "robots_count" in f:
        crew.robots_count = 2 if f.get("robots_count") == "2" else 1
    if crew.is_double:
        if "operator2_id" in f:
            op_id = _int_or_none(f.get("operator2_id"))
            crew.operator2_id = op_id if op_id and db.session.get(Operator, op_id) else None
        if "robot2_id" in f:
            rid = _int_or_none(f.get("robot2_id"))
            crew.robot2_id = rid if rid and db.session.get(RobotUnit, rid) else None
    else:
        crew.operator2_id = crew.robot2_id = None
    if "city" in f:
        crew.city = f.get("city", "").strip()[:200] or None
    if "notes" in f:
        crew.notes = f.get("notes", "").strip()[:300] or None
    db.session.commit()
    return _logistics_back(f"d-{crew.event_date.isoformat()}", crew_id=crew.id)


@app.route("/crm/logistics/crew/<int:cid>/delete", methods=["POST"])
def crm_logistics_crew_delete(cid):
    crew = db.session.get(Crew, cid)
    anchor = None
    if crew:
        anchor = f"d-{crew.event_date.isoformat()}"
        db.session.delete(crew)
        db.session.commit()
    return _logistics_back(anchor)


def _crew_from_key(key, create_operator=None):
    """'123' -> ekipa 123; 'new:<lead_id>:<YYYY-MM-DD>' -> nowa ekipa dla dnia bez ekip."""
    key = str(key or "")
    if key.startswith("new:"):
        parts = key.split(":")
        if len(parts) != 3:
            return None
        lead = db.session.get(Lead, _int_or_none(parts[1]) or 0)
        dt = parse_date(parts[2])
        if not lead or not dt or create_operator is None:
            return None
        crew = Crew(lead_id=lead.id, event_date=dt.date(), operator_id=create_operator)
        db.session.add(crew)
        return crew
    cid = _int_or_none(key)
    return db.session.get(Crew, cid) if cid else None


def _crew_slot_from_key(key):
    """'123' -> (ekipa 123, 'operator_id'); '123/2' -> (ekipa 123, 'operator2_id') — 2. operator
    ekipy podwójnej, który jedzie własnym autem."""
    key = str(key or "")
    if key.endswith("/2"):
        crew = _crew_from_key(key[:-2])
        return (crew, "operator2_id") if crew and crew.is_double else (None, None)
    crew = _crew_from_key(key)
    return (crew, "operator_id") if crew else (None, None)


@app.route("/crm/logistics/apply", methods=["POST"])
def crm_logistics_apply():
    """Zastosuj sugestię. Sprawdza, czy plan się w międzyczasie nie zmienił."""
    f = request.form
    kind = f.get("kind")
    op_to = db.session.get(Operator, _int_or_none(f.get("op_to")) or 0)
    op_from_id = _int_or_none(f.get("op_from"))
    if not op_to:
        return _logistics_back()
    if kind == "swap":
        (c1, a1), (c2, a2) = _crew_slot_from_key(f.get("crew_key")), _crew_slot_from_key(f.get("crew_key_2"))
        if c1 and c2 and getattr(c1, a1) == op_from_id and getattr(c2, a2) == op_to.id:
            setattr(c1, a1, op_to.id)
            setattr(c2, a2, op_from_id)
    elif kind == "reassign":
        c, attr = _crew_slot_from_key(f.get("crew_key"))
        # nie wstawiaj operatora, który już jest w tej ekipie na drugim miejscu
        if c and getattr(c, attr) == op_from_id and op_to.id not in c.operator_ids():
            setattr(c, attr, op_to.id)
    elif kind == "assign":
        key = f.get("crew_key", "")
        if key.startswith("new:"):
            _crew_from_key(key, create_operator=op_to.id)
        else:
            c = _crew_from_key(key)
            if c and not c.operator_id:
                c.operator_id = op_to.id
    db.session.commit()
    return _logistics_back()


@app.route("/crm/logistics/operators/save", methods=["POST"])
def crm_logistics_operator_save():
    f = request.form
    name = f.get("name", "").strip()[:100]
    op = db.session.get(Operator, _int_or_none(f.get("id")) or 0) if f.get("id") else None
    if not op:
        if not name:
            return _logistics_back("flota")
        op = Operator(active=True)
        db.session.add(op)
    if name:
        op.name = name
    op.base_city = f.get("base_city", "").strip()[:200] or None
    op.phone = f.get("phone", "").strip()[:50] or None
    if f.get("id"):
        op.active = f.get("active") == "1"
    if "user_id" in f:
        uid = _int_or_none(f.get("user_id"))
        op.user_id = uid if uid in USERS_BY_ID else 0
    db.session.commit()
    return _logistics_back("flota")


@app.route("/crm/logistics/operators/<int:oid>/delete", methods=["POST"])
def crm_logistics_operator_delete(oid):
    op = db.session.get(Operator, oid)
    if op:
        Crew.query.filter_by(operator_id=oid).update({"operator_id": None})
        Crew.query.filter_by(operator2_id=oid).update({"operator2_id": None})
        db.session.delete(op)
        db.session.commit()
    return _logistics_back("flota")


@app.route("/crm/logistics/robots/save", methods=["POST"])
def crm_logistics_robot_save():
    f = request.form
    name = f.get("name", "").strip()[:100]
    robot = db.session.get(RobotUnit, _int_or_none(f.get("id")) or 0) if f.get("id") else None
    if not robot:
        if not name:
            return _logistics_back("flota")
        robot = RobotUnit(active=True)
        db.session.add(robot)
    if name:
        robot.name = name
    if f.get("model") in ROBOT_KINDS:
        robot.model = f.get("model")
    if f.get("id"):
        robot.active = f.get("active") == "1"
    db.session.commit()
    return _logistics_back("flota")


@app.route("/crm/logistics/robots/<int:rid>/delete", methods=["POST"])
def crm_logistics_robot_delete(rid):
    robot = db.session.get(RobotUnit, rid)
    if robot:
        Crew.query.filter_by(robot_id=rid).update({"robot_id": None})
        Crew.query.filter_by(robot2_id=rid).update({"robot2_id": None})
        db.session.delete(robot)
        db.session.commit()
    return _logistics_back("flota")


# pełne formy i zdrobnienia imion kont — operator "Bartłomiej Wysocki" to konto Bartek
USER_NAME_FORMS = {
    "bartek": {"bartlomiej", "bartosz"},
    "jerzy": {"jurek"},
}


def _user_name_keys(u):
    """Słowa, po których operator łączy się z kontem: imię, jego inne formy i nazwisko z maila."""
    name = logistics.normalize(u["name"])
    keys = {name} | USER_NAME_FORMS.get(name, set())
    local = (u.get("email") or "").split("@")[0]
    if "." in local:
        surname = logistics.normalize(local.split(".", 1)[1])
        if len(surname) >= 3:
            keys.add(surname)
    return keys


def operator_user_id(op):
    """Konto CRM operatora: wybrane ręcznie albo (gdy nie wybrano) dopasowane po imieniu/nazwisku."""
    if op.user_id is not None:
        return op.user_id or None  # 0 = celowo bez konta
    words = set(logistics.normalize(op.name).split())
    hits = [u["id"] for u in USERS if words & _user_name_keys(u)]
    return hits[0] if len(hits) == 1 else None


@app.route("/crm/logistics/operators/link-me", methods=["POST"])
def crm_operator_link_me():
    """Z Dashboardu: 'to ja jestem tym operatorem' — łączy operatora z zalogowanym kontem."""
    op = db.session.get(Operator, _int_or_none(request.form.get("operator_id")) or 0)
    if op:
        op.user_id = get_current_user(request)["id"]
        db.session.commit()
    return redirect(url_for("crm_dashboard"))


def operator_jobs(op_ids, limit=None):
    """Nadchodzące zlecenia (od dziś) operatorów z listy — to, co operator musi wiedzieć,
    żeby pojechać: kiedy, gdzie, z czym, do kogo. Bez cen (stronę można wysłać operatorowi)."""
    today = date.today()
    wanted = set(op_ids or [])
    crews = (Crew.query.filter(db.or_(Crew.operator_id.in_(wanted), Crew.operator2_id.in_(wanted)),
                               Crew.event_date >= today)
             .order_by(Crew.event_date, Crew.id).all()) if wanted else []
    jobs = []
    for c in crews:
        lead = c.lead
        if not lead or lead.stage == "Stracony" or lead.clickup_pending:
            continue
        ed = next((d for d in lead.event_days if d.event_date == c.event_date), None)
        if ed is None:
            continue  # dzień usunięty z leada — przypisanie nieaktualne
        same_day = Crew.query.filter_by(lead_id=lead.id, event_date=c.event_date).order_by(Crew.id).all()
        delta = (c.event_date - today).days
        if c.robots_list():
            robot = " + ".join(f"{r.name} ({r.model})" for r in c.robots_list())
            if c.is_double and len(c.robots_list()) < 2:
                robot += " + 2. robot do ustalenia"
        else:
            robot = ("2× " if c.is_double else "") + (lead.robot_model or "Unitree G1")
        for op in {o.id: o for o in c.operators_list()}.values():
            if op.id not in wanted:
                continue
            partners = [o.name for x in same_day for o in x.operators_list() if o.id != op.id]
            if c.is_double and len(set(c.operator_ids())) < 2:
                partners.append("2. operator do ustalenia")
            jobs.append({
                "crew": c, "lead": lead, "op_id": op.id, "date": c.event_date,
                "weekday": PL_WEEKDAYS[c.event_date.weekday()],
                "when": "dziś" if delta == 0 else "jutro" if delta == 1 else f"za {delta} dni",
                "place": c.city or ed.location or lead.location or "",
                "robot": robot, "tentative": lead.stage not in WON_STAGES,
                "partners": list(dict.fromkeys(partners)), "notes": c.notes or "", "operator": op.name,
                "double": c.is_double,
            })
    # skąd operator dojeżdża: zlecenie dzień wcześniej = zwykle jedzie prosto stamtąd
    by_op_day = {(j["op_id"], j["date"]): j for j in jobs}
    for j in jobs:
        prev = by_op_day.get((j["op_id"], j["date"] - timedelta(days=1)))
        j["prev_place"] = prev["place"] if prev else ""
    return jobs[:limit] if limit else jobs


@app.route("/crm/logistics/operator/<int:oid>")
def crm_operator_jobs(oid):
    op = db.session.get(Operator, oid)
    if not op:
        abort(404)
    return render_template("crm/operator_jobs.html", op=op, jobs=operator_jobs([op.id]), today=date.today())
