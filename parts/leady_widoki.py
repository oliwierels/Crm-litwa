# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("leady_widoki") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: lead: dodawanie, karta, edycja, zmiana etapu, pola sprzedaży, oddzwonienia

@app.route("/crm/leads/add", methods=["POST"])
def crm_add_lead():
    user = get_current_user(request)
    company = request.form.get("company", "").strip()
    if not company:
        return redirect(url_for("crm_leads"))
    lead = Lead(company=company, assigned_to=default_owner())  # opiekun z formularza; bez niego — domyślny rynku
    msgs = _save_lead_fields(lead, request.form, actor_id=user["id"])
    if lead.stage not in STAGES:
        lead.stage = "Nowy"
    db.session.add(lead)
    db.session.flush()
    db.session.add(StageHistory(
        lead_id=lead.id, from_stage=None, to_stage=lead.stage, changed_by=user["id"],
    ))
    db.session.commit()

    kind = request.form.get("first_activity_kind", "").strip()
    due = parse_date(request.form.get("first_activity_due", ""))
    if kind and due:
        db.session.add(Activity(lead_id=lead.id, kind=kind, due_date=due, assigned_to=lead.assigned_to,
                                created_by=user["id"]))
        db.session.commit()
    if lead.stage == "Oferta":
        ensure_followup_call(lead)
        db.session.commit()

    return redirect(url_for("crm_lead_detail", lead_id=lead.id, **({"uwaga": " • ".join(msgs)[:600]} if msgs else {})))


@app.route("/crm/leads/<int:lead_id>")
def crm_lead_detail(lead_id):
    user = get_current_user(request)
    lead = db.session.get(Lead, lead_id)
    if not lead:
        abort(404)
    inactive_days = lead_inactivity(lead)
    if lead.stage in JOURNEY_STAGES and not lead.journey and not lead.clickup_pending:
        ensure_journey(lead)
        db.session.commit()
    return render_template(
        "crm/lead_detail.html",
        user=user, users=USERS, USERS_BY_ID=USERS_BY_ID,
        lead=lead, stages=STAGES, stage_colors=STAGE_COLORS,
        jv=journey_view(lead.journey, lead) if lead.journey and lead.stage in JOURNEY_STAGES else None,
        jfiles=journey_files([lead.id]).get(lead.id, {}),
        pending_mails=JourneyMail.query.filter_by(lead_id=lead.id, pending=True).order_by(JourneyMail.received_at).all(),
        robot_models=ROBOT_MODELS,
        urgency_fn=activity_urgency,
        pending_fn=pending_activities,
        inactive_days=inactive_days,
        inactive_color=lead_inactivity_color(inactive_days),
        closed_on=lead_closed_on(lead),
        closed_msg=("ok" if request.args.get("zamkniecie") == "ok"
                    else CLOSED_ON_ERRORS.get(request.args.get("zamkniecie", ""), "")),
        today=date.today(),
    )


@app.route("/crm/leads/<int:lead_id>/closed-on", methods=["POST"])
def crm_lead_closed_on(lead_id):
    lead = db.session.get(Lead, lead_id)
    if not lead:
        abort(404)
    day = parse_date(request.form.get("closed_on"))
    err = set_lead_closed_on(lead, day.date() if day else None, get_current_user(request)["id"])
    if err:
        db.session.rollback()
    else:
        db.session.commit()
    return redirect(url_for("crm_lead_detail", lead_id=lead.id, zamkniecie=err or "ok") + "#historia")


@app.route("/crm/leads/<int:lead_id>/edit", methods=["POST"])
def crm_edit_lead(lead_id):
    lead = db.session.get(Lead, lead_id)
    if not lead:
        abort(404)
    msgs = _save_lead_fields(lead, request.form, actor_id=get_current_user(request)["id"])
    db.session.commit()
    return redirect(url_for("crm_lead_detail", lead_id=lead_id, **({"uwaga": " • ".join(msgs)[:600]} if msgs else {})))


@app.route("/crm/leads/<int:lead_id>/stage", methods=["POST"])
def crm_set_stage(lead_id):
    lead = db.session.get(Lead, lead_id)
    if not lead:
        abort(404)
    src = request.get_json(silent=True) or {} if request.is_json else request.form
    stage = src.get("stage")
    gate = gated_stage_change(lead, stage, get_current_user(request)["id"], gate_form_data(src))
    if gate.get("changed"):
        db.session.commit()
    else:
        db.session.rollback()
    if request.is_json:
        return jsonify(_gate_json(gate)), (200 if not gate["message"] else 422)
    return redirect(url_for("crm_lead_detail", lead_id=lead_id, **({"uwaga": gate["message"]} if gate["message"] else {})))


def _gate_json(gate):
    return {"ok": not gate["message"], "changed": bool(gate.get("changed")), "error": gate["message"],
            "need": gate["need"], "errors": gate["errors"], "warnings": gate["warnings"], "hint": gate.get("hint", {}),
            "lost_reasons": sales_rules.LOST_REASONS, "client_types": sales_rules.CLIENT_TYPES,
            "win_confirmations": sales_rules.WIN_CONFIRMATIONS}


@app.route("/crm/api/leads/<int:lead_id>/stage-check", methods=["POST"])
def api_stage_check(lead_id):
    """Sprawdza bramki etapu bez zmiany (formularz edycji leada przed zapisem). Można podać value_pln i event_dates
    z formularza, żeby liczyły się wartości jeszcze niezapisane."""
    lead = db.session.get(Lead, lead_id) or abort(404)
    data = request.get_json(silent=True) or {}
    with db.session.no_autoflush:
        if data.get("value_pln") not in (None, ""):
            try:
                lead.value_pln = float(data.get("value_pln"))
            except (TypeError, ValueError):
                pass
        if data.get("event_dates") is not None:
            days = parse_event_dates(str(data.get("event_dates")))
            gate = stage_gate_check(_GateLead(lead, days), data.get("stage"), gate_form_data(data))
        else:
            gate = stage_gate_check(lead, data.get("stage"), gate_form_data(data))
    db.session.rollback()
    return jsonify(_gate_json(gate))


class _GateLead:
    """Lead z dniami wynajmu z formularza (jeszcze niezapisanymi) — tylko do sprawdzenia bramek."""
    def __init__(self, lead, days):
        self._lead, self.event_days = lead, [type("D", (), {"event_date": d, "location": None})() for d in days]

    def __getattr__(self, name):
        return getattr(self._lead, name)


@app.route("/crm/leads/<int:lead_id>/sprzedaz", methods=["POST"])
def crm_lead_sales_fields(lead_id):
    """Typ klienta i kraj z panelu „Proces sprzedaży” na stronie leada."""
    lead = db.session.get(Lead, lead_id) or abort(404)
    if request.form.get("client_type") in sales_rules.CLIENT_TYPES:
        lead.client_type = request.form["client_type"]
    if "country" in request.form:
        c = (request.form.get("country") or "").strip().upper()
        lead.country = c if c in sales_rules.COUNTRIES else None
    db.session.commit()
    return redirect(url_for("crm_lead_detail", lead_id=lead.id) + "#sprzedaz")


def _fromjson(raw):
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


app.jinja_env.filters["fromjson"] = _fromjson
app.jinja_env.globals.update(client_type_hint=client_type_hint, lead_country=lead_country,
                             confirm_questions=sales_rules.CONFIRM_QUESTIONS,
                             lead_attempt_limit=lead_attempt_limit,
                             response_target=sales_rules.RESPONSE_TARGET_MIN)


CALLBACK_COOKIE, CALLBACK_LATER_COOKIE = "cb_asked", "cb_later"


def callback_ask_due():
    """Pytanie „Czy ktoś do Ciebie dziś oddzwaniał?” — raz dziennie na każdym komputerze (ciasteczko przeglądarki,
    więc trzy komputery w jednym biurze dostają je osobno), w dni robocze od 16:00."""
    from flask import has_request_context
    if not has_request_context() or request.path.startswith(("/f/", "/static", "/mcp")) or \
            (request.endpoint or "") in ("crm_login", "crm_logout", "lead_form", "static"):
        return False
    now = to_local(datetime.utcnow())
    if now.weekday() not in sales_rules.WORK_DAYS or now.hour < sales_rules.CALLBACK_ASK_FROM:
        return False
    return request.cookies.get(CALLBACK_COOKIE) != now.date().isoformat() and not request.cookies.get(CALLBACK_LATER_COOKIE)
