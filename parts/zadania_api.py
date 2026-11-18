# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("zadania_api") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: zadania: odhaczanie, przypisanie, nie odebrał, przełożenie, follow-up

@app.route("/crm/api/activity/<int:aid>/done", methods=["POST"])
def api_mark_done(aid):
    a = db.session.get(Activity, aid)
    if not a:
        abort(404)
    data = request.get_json(silent=True) or {}
    if a.done:  # cofnięcie odhaczenia
        a.done, a.done_at, a.outcome = False, None, None
        db.session.commit()
        return jsonify({"ok": True, "done": False})
    err = complete_activity(a, get_current_user(request)["id"], data.get("outcome"), data.get("answers"))
    if err:
        db.session.rollback()
        return jsonify(dict(err, ok=False)), 422
    db.session.commit()
    return jsonify({"ok": True, "done": True})


@app.route("/crm/api/activity/<int:aid>/assign", methods=["POST"])
def api_assign_activity(aid):
    """Zmiana osoby przypisanej do istniejącego zadania."""
    a = db.session.get(Activity, aid)
    if not a:
        abort(404)
    data = request.get_json(silent=True) or {}
    try:
        user_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        abort(400)
    if user_id not in USERS_BY_ID:
        abort(400)
    a.assigned_to = user_id
    a.created_by = get_current_user(request)["id"]  # przekazanie zadania innej osobie = zlecenie
    db.session.commit()
    return jsonify({"ok": True, "user_id": user_id})


@app.route("/crm/api/activity/<int:aid>/no_answer", methods=["POST"])
def api_no_answer(aid):
    """Jeden klik: zapisz próbę kontaktu (nie odebrał) i przełóż zadanie na jutro."""
    a = db.session.get(Activity, aid)
    if not a:
        abort(404)
    attempts = 1 + sum(1 for c in a.comments if "Nie odebrał" in (c.text or ""))
    db.session.add(ActivityComment(
        activity_id=aid,
        author_id=get_current_user(request)["id"],
        text=f"📵 Nie odebrał ({attempts}. próba)",
    ))
    tomorrow = datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
    a.due_date = tomorrow
    if a.lead_id:
        db.session.flush()
        lead = db.session.get(Lead, a.lead_id)
        db.session.expire(a, ["comments"])
        db.session.expire(lead, ["activities"])
        check_contact_limit(lead, get_current_user(request)["id"])
    db.session.commit()
    return jsonify({
        "ok": True,
        "attempts": attempts,
        "due_display": tomorrow.strftime("%d.%m.%Y"),
    })


@app.route("/crm/api/activity/<int:aid>/postpone", methods=["POST"])
def api_postpone_activity(aid):
    a = db.session.get(Activity, aid)
    if not a:
        abort(404)
    data = request.get_json(silent=True) or {}
    try:
        days = int(data.get("days", 1))
    except (TypeError, ValueError):
        days = 1
    days = max(1, min(days, 90))
    today0 = datetime.combine(date.today(), datetime.min.time())
    base = a.due_date or today0
    if base < today0:  # przeterminowane liczymy od dziś, nie od starego terminu
        base = today0
    a.due_date = base + timedelta(days=days)
    db.session.commit()
    return jsonify({
        "ok": True,
        "due_date": a.due_date.date().isoformat(),
        "display": a.due_date.strftime("%d.%m.%Y"),
    })


# ---------- follow-up mailem jednym kliknięciem z zadania „✉️ mail” ----------

def followup_lead(act):
    """Otwarte zadanie-mail przy leadzie z adresem e-mail -> ten lead (można wysłać follow-up), inaczej None."""
    lead = act.lead if act is not None and act.kind == "email" and not act.done else None
    if lead and lead.emails:
        return lead
    return None
