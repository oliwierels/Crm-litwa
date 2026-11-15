# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("journey_widoki_dokumenty") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: Customer Journey: szukanie, dokumenty, skanowanie skrzynek

@app.route("/crm/journey/<int:lead_id>/szukaj", methods=["POST"])
def crm_journey_search(lead_id):
    _journey_or_404(lead_id)
    jid = _start_job("CRM szuka umowy i bazy wiedzy w skrzynkach", journey_search_lead_mail, lead_id)
    return jsonify({"jid": jid, "poll": url_for("crm_job", jid=jid, format="json")})


@app.route("/crm/journey/<int:lead_id>/pomin", methods=["POST"])
def crm_journey_skip(lead_id):
    j, lead = _journey_or_404(lead_id)
    if not journey_skip(j, request.form.get("rodzaj", "")):
        return jsonify({"error": "tego nie da się pominąć"}), 400
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/crm/journey/<int:lead_id>/odhacz", methods=["POST"])
def crm_journey_check(lead_id):
    """Znaczniki: baza wiedzy jest / nie ma i checklista po wydarzeniu."""
    j, lead = _journey_or_404(lead_id)
    data = request.get_json(silent=True) or request.form
    group, item, value = data.get("grupa"), data.get("pozycja"), str(data.get("wartosc")).lower() in ("1", "true", "on")
    if group == "baza":
        journey_set_kb(j, "otrzymana" if value else ("wyslana" if j.kb_sent_at else "brak"))
    elif group == "po" and item in dict(journey.AFTER_ITEMS):
        items = _jjson(j.after)
        items[item] = value
        j.after = json.dumps(items)
        _journey_maybe_close(j)
    else:
        return jsonify({"error": "nieznana pozycja"}), 400
    j.updated_at = datetime.utcnow()
    _journey_badge_reset()
    db.session.commit()
    return jsonify({"ok": True, "col": journey_view(j, lead)["col"]})


@app.route("/crm/journey/mail/<int:mid>/<action>", methods=["POST"])
def crm_journey_mail_action(mid, action):
    """Niepewne rozpoznanie maila: potwierdź (odhacza krok) albo odrzuć (dokumenty zostają jako „inne”)."""
    rec = db.session.get(JourneyMail, mid)
    if not rec or not rec.pending:
        abort(404)
    if action == "potwierdz":
        journey_apply_mail(rec, confirm=True)
        rec.confidence = "potwierdzone"
    elif action == "odrzuc":
        for doc in JourneyDocument.query.filter_by(mail_id=rec.id).all():
            doc.kind, doc.status = "inny", "ok"
        rec.pending, rec.confidence = False, "odrzucone"
    else:
        abort(404)
    _journey_badge_reset()
    db.session.commit()
    return redirect(_journey_back(rec.lead) if rec.lead else url_for("crm_journey"))


@app.route("/crm/journey/<int:lead_id>/plik", methods=["POST"])
def crm_journey_upload(lead_id):
    """Dokument dodany ręcznie (np. skan umowy podpisanej na miejscu) — umowa podpisana / baza odhacza krok."""
    j, lead = _journey_or_404(lead_id)
    user = get_current_user(request)
    up = request.files.get("plik")
    kind = request.form.get("rodzaj", "inny")
    if not up or not up.filename:
        return redirect(_journey_back(lead))
    data = up.read(JOURNEY_MAX_FILE + 1)
    if len(data) > JOURNEY_MAX_FILE or not data:
        return redirect(_journey_back(lead))
    doc = journey_add_document(lead, kind, up.filename, data, up.mimetype, f"dodany ręcznie ({user['name']})",
                               user_id=user["id"])
    if doc.kind == "umowa_podpisana" and j.contract_status != "podpisana":
        journey_set_contract(j, lead, "podpisana", via="recznie")
    elif doc.kind == "baza_wiedzy" and j.kb_status != "otrzymana":
        journey_set_kb(j, "otrzymana")
    elif doc.kind == "umowa_wyslana" and j.contract_status == "do_wyslania":
        journey_set_contract(j, lead, "wyslana")
    db.session.commit()
    _journey_convert_async(doc.id)
    return redirect(_journey_back(lead))


def _journey_doc_or_404(doc_id):
    doc = db.session.get(JourneyDocument, doc_id)
    if not doc:
        abort(404)
    return doc


def _doc_headers(resp, filename, inline):
    safe = journey.safe_filename(filename).replace('"', "")
    ascii_name = safe.encode("ascii", "replace").decode().replace("?", "_")
    resp.headers["Content-Disposition"] = (f'{"inline" if inline else "attachment"}; filename="{ascii_name}"; '
                                           f"filename*=UTF-8''{quote(safe)}")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "sandbox"  # pliki od klientów nie wykonają skryptów w CRM
    return resp


@app.route("/crm/journey/dokument/<int:doc_id>.pdf")
def crm_journey_doc_pdf(doc_id):
    doc = _journey_doc_or_404(doc_id)
    if not doc.pdf:
        return redirect(url_for("crm_journey_doc_file", doc_id=doc.id))
    if not doc.seen:
        doc.seen = True
        db.session.commit()
        _journey_badge_reset()
    resp = make_response(doc.pdf)
    resp.headers["Content-Type"] = "application/pdf"
    base = os.path.splitext(doc.filename or "dokument")[0]
    return _doc_headers(resp, base + ".pdf", inline=True)


@app.route("/crm/journey/dokument/<int:doc_id>/plik")
def crm_journey_doc_file(doc_id):
    doc = _journey_doc_or_404(doc_id)
    if not doc.seen:
        doc.seen = True
        db.session.commit()
        _journey_badge_reset()
    resp = make_response(doc.data or b"")
    resp.headers["Content-Type"] = "application/octet-stream"
    return _doc_headers(resp, doc.filename or "dokument", inline=False)


@app.route("/crm/journey/dokument/<int:doc_id>/<action>", methods=["POST"])
def crm_journey_doc_action(doc_id, action):
    doc = _journey_doc_or_404(doc_id)
    lead = doc.lead
    if action == "obejrzane":
        doc.seen = True
    elif action == "rodzaj" and request.form.get("rodzaj") in DOC_KINDS:
        doc.kind = request.form["rodzaj"]
    elif action == "usun":
        db.session.delete(doc)
    else:
        abort(404)
    _journey_badge_reset()
    db.session.commit()
    if request.is_json:
        return jsonify({"ok": True})
    return redirect(_journey_back(lead))


@app.route("/crm/journey/dokumenty")
def crm_journey_docs():
    kind = request.args.get("rodzaj", "")
    q = JourneyDocument.query
    if kind in DOC_KINDS:
        q = q.filter(JourneyDocument.kind == kind)
    docs = q.order_by(JourneyDocument.received_at.desc().nulls_last(), JourneyDocument.id.desc()).limit(500).all()
    return render_template("crm/journey_docs.html", docs=docs, kind=kind, doc_kinds=DOC_KINDS)


@app.route("/crm/journey/skanuj", methods=["POST"])
def crm_journey_scan():
    jid = _start_job("CRM przegląda skrzynki", scan_journey_mail)
    return redirect(url_for("crm_job", jid=jid))
