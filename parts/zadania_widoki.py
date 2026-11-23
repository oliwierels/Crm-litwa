# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("zadania_widoki") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: zadania i komentarze w leadzie, zmiana etapu z kanbana

@app.route("/crm/tasks/quick-add", methods=["POST"])
def crm_quick_add_task():
    """Zlecenie zadania z FAB-a dostępnego wszędzie — dla leada albo 'własne' (bez firmy)."""
    lead_id = request.form.get("lead_id", type=int)
    lead = db.session.get(Lead, lead_id) if lead_id else None
    if lead_id and not lead:
        abort(404)
    fallback = (lead.assigned_to if lead else None) or get_current_user(request)["id"]
    try:
        assigned = int(request.form.get("assigned_to", fallback))
    except (TypeError, ValueError):
        assigned = fallback
    if assigned not in USERS_BY_ID:
        assigned = fallback
    due = parse_date(request.form.get("due_date", ""))
    if not lead and not due:
        # zadanie własne bez terminu nie miałoby gdzie się pokazać — domyślnie na dziś
        due = datetime.combine(date.today(), datetime.min.time())
    act = Activity(
        lead_id=lead.id if lead else None,
        kind=request.form.get("kind", "call"),
        due_date=due,
        notes=request.form.get("notes", "").strip(),
        assigned_to=assigned,
        created_by=get_current_user(request)["id"],
    )
    db.session.add(act)
    db.session.commit()
    if request.form.get("next") == "zespol":
        return redirect(url_for("crm_team", zlecone=1))
    if lead:
        return redirect(url_for("crm_lead_detail", lead_id=lead.id))
    return redirect(url_for("crm_dashboard"))


@app.route("/crm/leads/<int:lead_id>/activities/add", methods=["POST"])
def crm_add_activity(lead_id):
    lead = db.session.get(Lead, lead_id)
    if not lead:
        abort(404)
    try:
        assigned = int(request.form.get("assigned_to") or get_current_user(request)["id"])
    except (TypeError, ValueError):
        assigned = get_current_user(request)["id"]
    if assigned not in USERS_BY_ID:
        assigned = get_current_user(request)["id"]
    act = Activity(
        lead_id=lead_id,
        kind=request.form.get("kind", "note"),
        due_date=parse_date(request.form.get("due_date", "")),
        notes=request.form.get("notes", "").strip(),
        assigned_to=assigned,
        created_by=get_current_user(request)["id"],
    )
    db.session.add(act)
    db.session.commit()
    return redirect(url_for("crm_lead_detail", lead_id=lead_id))


@app.route("/crm/leads/<int:lead_id>/comments/add", methods=["POST"])
def crm_add_comment(lead_id):
    lead = db.session.get(Lead, lead_id)
    if not lead:
        abort(404)
    text = request.form.get("text", "").strip()
    voice_note = request.form.get("voice_note", "").strip()
    if not voice_note.startswith("data:audio/"):
        voice_note = ""
    if text or voice_note:
        now = datetime.utcnow()
        db.session.add(Activity(
            lead_id=lead_id, kind="note", done=True, done_at=now,
            notes=text, voice_note=voice_note or None, assigned_to=get_current_user(request)["id"],
            created_by=get_current_user(request)["id"],
        ))
        db.session.commit()
    return redirect(url_for("crm_lead_detail", lead_id=lead_id))


@app.route("/crm/activities/<int:aid>/comments/add", methods=["POST"])
def crm_add_activity_comment(aid):
    a = db.session.get(Activity, aid)
    if not a:
        abort(404)
    data = request.get_json(silent=True) or request.form
    text = (data.get("text") or "").strip()
    if text:
        db.session.add(ActivityComment(
            activity_id=aid, author_id=get_current_user(request)["id"], text=text,
        ))
        db.session.commit()
    if request.is_json:
        return jsonify({"ok": True})
    if a.lead_id:
        return redirect(url_for("crm_lead_detail", lead_id=a.lead_id))
    return redirect(url_for("crm_dashboard"))


@app.route("/crm/activities/<int:aid>/delete", methods=["POST"])
def crm_delete_activity(aid):
    a = db.session.get(Activity, aid)
    if not a:
        abort(404)
    lead_id = a.lead_id
    db.session.delete(a)
    db.session.commit()
    if lead_id:
        return redirect(url_for("crm_lead_detail", lead_id=lead_id))
    return redirect(url_for("crm_dashboard"))


@app.route("/crm/api/leads/<int:lead_id>/stage", methods=["POST"])
def api_set_stage(lead_id):
    lead = db.session.get(Lead, lead_id)
    if not lead:
        abort(404)
    data = request.get_json(silent=True) or {}
    gate = gated_stage_change(lead, data.get("stage"), get_current_user(request)["id"], gate_form_data(data))
    if gate.get("changed"):
        db.session.commit()
    else:
        db.session.rollback()
    out = _gate_json(gate)
    if gate.get("changed"):  # do animacji wygranej na tablicy / w nagłówku leada
        out["lead"] = {"id": lead.id, "company": lead.company, "stage": lead.stage, "value": lead.value_pln or 0,
                       "currency": lead.currency or M(lead_market(lead))["currency"]}
    return jsonify(out), (200 if not gate["message"] else 422)
