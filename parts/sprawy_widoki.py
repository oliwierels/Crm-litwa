# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("sprawy_widoki") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: sprawy z poczty: akcje i lead sprawy

@app.route("/crm/sprawy/<int:tid>/<action>", methods=["POST"])
def crm_mail_task_action(tid, action):
    t = db.session.get(MailTask, tid) or abort(404)
    if action == "reopen":  # agent się pomylił — sprawa wraca na listę i nie jest już sprawdzana automatycznie
        t.status, t.done_at, t.done_by, t.done_how = "open", None, None, MAIL_TASK_REOPENED
        db.session.commit()
        return redirect(request.referrer or url_for("crm_mail", sprawy=1))
    if action not in ("done", "dismiss"):
        abort(404)
    t.status = "done" if action == "done" else "dismissed"
    t.done_at, t.done_by = datetime.utcnow(), get_current_user(request)["id"]
    t.done_how = "załatwione" if action == "done" else "nie dotyczy"
    if t.lead_id:
        db.session.add(Activity(lead_id=t.lead_id, kind="note", done=True, done_at=datetime.utcnow(),
                                assigned_to=t.done_by, notes=f"📋 Sprawa z poczty {t.done_how}: {t.title}"))
    db.session.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True})
    return redirect(request.referrer or url_for("crm_mail"))


@app.route("/crm/sprawy/<int:tid>/lead")
def crm_mail_task_lead(tid):
    """„Otwórz lead” przy sprawie: lead zapisany przy sprawie, a gdy go już nie ma (przeniesiony, scalony) albo jest
    z innego rynku — szukany po adresie klienta. Bez leada — mail w Poczcie (tam „➕ Nowy lead z maila”)."""
    t = all_markets(MailTask.query.filter(MailTask.id == tid)).first() or abort(404)
    lead = _task_lead(t, fix=True)
    db.session.commit()
    if lead is None:
        if t.mail_id:
            return redirect(url_for("crm_mail", f="all", m=t.mail_id, brak_leada=1))
        abort(404)
    return _market_switch(redirect(url_for("crm_lead_detail", lead_id=lead.id)), lead)


@app.route("/crm/sprawy/sprawdz", methods=["POST"])
def crm_mail_tasks_verify():
    """Ręcznie: agent sprawdza teraz w poczcie wszystkie otwarte sprawy (w tle, z paskiem postępu)."""
    if _ai_client() is None:
        return redirect(url_for("crm_mail", sprawy=1))
    return redirect(url_for("crm_job", jid=_start_job("Claude sprawdza w poczcie, co już załatwione", verify_mail_tasks, None, True)))
