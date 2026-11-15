# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("journey_widoki") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: Customer Journey: tablica, potwierdzenie, szkic maila

def _journey_or_404(lead_id):
    lead = db.session.get(Lead, lead_id)
    if not lead or lead.stage not in JOURNEY_STAGES:
        abort(404)
    return ensure_journey(lead), lead


@app.route("/crm/journey")
def crm_journey():
    ensure_journeys()
    views = journey_board_views()
    only = request.args.get("tylko") == "akcja"
    cols = {k: [] for k, _, _ in journey.BOARD_COLUMNS}
    for v in views:
        if not only or v["attention"]:
            cols[v["col"]].append(v)
    far = date.max
    for k in cols:
        cols[k].sort(key=lambda v: (not v["attention"], v["event"] or far, v["lead"].company.lower()))
    todo = sum(1 for v in views if v["attention"])
    recent = (JourneyMail.query.filter(JourneyMail.result.notin_(("inne", "crm")))
              .order_by(JourneyMail.received_at.desc()).limit(15).all())
    pending_by_lead, unseen_by_lead = {}, {}
    for m in JourneyMail.query.filter(JourneyMail.pending.is_(True)).order_by(JourneyMail.received_at).all():
        pending_by_lead.setdefault(m.lead_id, []).append(m)
    for did, lid, name, has_pdf in (
            db.session.query(JourneyDocument.id, JourneyDocument.lead_id, JourneyDocument.filename,
                             JourneyDocument.pdf.isnot(None))
            .filter(JourneyDocument.seen.is_(False), JourneyDocument.status == "ok").order_by(JourneyDocument.id)):
        unseen_by_lead.setdefault(lid, []).append({"id": did, "filename": name, "pdf": has_pdf})
    return render_template(
        "crm/journey.html", columns=journey.BOARD_COLUMNS, cols=cols, todo=todo, only=only, recent=recent,
        files_by_lead=journey_files([v["lead"].id for v in views]),
        archive=journey_archive(), scan=_JOURNEY_SCAN, boxes=journey_mailboxes()[1], cfg=journey_cfg(),
        mail_results=JOURNEY_MAIL_RESULTS, doc_kinds=DOC_KINDS, pending_by_lead=pending_by_lead,
        unseen_by_lead=unseen_by_lead, to_local=to_local, archive_scan=get_setting("jr_archive_scan") or "",
    )


def journey_files(lead_ids):
    """Najnowszy plik: umowa od klienta, umowa wysłana i baza wiedzy — dla każdego leada (bez treści plików)."""
    out = {}
    if not lead_ids:
        return out
    key = {"umowa_podpisana": "umowa", "umowa_wyslana": "wyslana", "baza_wiedzy": "baza"}
    q = (db.session.query(JourneyDocument.id, JourneyDocument.lead_id, JourneyDocument.kind, JourneyDocument.filename,
                          JourneyDocument.pdf.isnot(None), JourneyDocument.status, JourneyDocument.seen)
         .filter(JourneyDocument.lead_id.in_(list(lead_ids)), JourneyDocument.kind.in_(list(key)))
         .order_by(db.func.coalesce(JourneyDocument.received_at, JourneyDocument.created_at), JourneyDocument.id))
    for did, lid, kind, name, has_pdf, status, seen in q:
        out.setdefault(lid, {})[key[kind]] = {"id": did, "filename": name, "pdf": has_pdf, "check": status != "ok",
                                              "seen": seen}
    return out


def journey_archive():
    """Zrealizowani klienci (wszyscy, z umową czy bez) z umowami, bazami wiedzy i fakturą w jednym miejscu —
    najnowsze wydarzenia na górze. Dokumenty bez treści plików (tylko to, co potrzebne do linków)."""
    rows = (Journey.query.join(Lead).filter(Lead.stage == "Zrealizowany", Lead.clickup_pending.isnot(True))
            .options(db.contains_eager(Journey.lead).selectinload(Lead.event_days)).all())
    docs = {}
    if rows:
        q = (db.session.query(JourneyDocument.id, JourneyDocument.lead_id, JourneyDocument.kind,
                              JourneyDocument.filename, JourneyDocument.status, JourneyDocument.pdf.isnot(None))
             .filter(JourneyDocument.lead_id.in_([j.lead_id for j in rows]))
             .order_by(db.func.coalesce(JourneyDocument.received_at, JourneyDocument.created_at)))
        for did, lid, kind, name, status, has_pdf in q:
            docs.setdefault(lid, []).append({"id": did, "kind": kind, "filename": name, "check": status != "ok",
                                             "pdf": has_pdf})
    out = []
    for j in rows:
        days = journey_days(j.lead)
        mine = docs.get(j.lead_id, [])
        out.append({
            "j": j, "lead": j.lead, "last": days[-1] if days else None, "first": days[0] if days else None,
            "invoiced": bool(_jjson(j.after).get("faktura")),
            "contracts": [d for d in mine if d["kind"] == "umowa_podpisana"],
            "sent": [d for d in mine if d["kind"] == "umowa_wyslana"],
            "kb": [d for d in mine if d["kind"] == "baza_wiedzy"],
            "other": [d for d in mine if d["kind"] not in ("umowa_podpisana", "umowa_wyslana", "baza_wiedzy")],
        })
    out.sort(key=lambda a: (a["last"] or date.min, a["lead"].company.lower()), reverse=True)
    return out


@app.route("/crm/journey/<int:lead_id>/przenies", methods=["POST"])
def crm_journey_move(lead_id):
    j, lead = _journey_or_404(lead_id)
    col = (request.get_json(silent=True) or {}).get("col") or request.form.get("col")
    if not journey_move(j, lead, col, get_current_user(request)["id"]):
        return jsonify({"error": "nieznana kolumna"}), 400
    db.session.commit()
    return jsonify({"ok": True, "col": journey_view(j, lead)["col"]})


@app.route("/crm/journey/<int:lead_id>/potwierdz", methods=["POST"])
def crm_journey_confirm(lead_id):
    """Szare pole na karcie: człowiek ustawia, co z umową i bazą wiedzy (też: lead sprzed Customer Journey).
    Zmienia tylko to, co faktycznie zmieniono — daty i przypomnienia reszty zostają."""
    j, lead = _journey_or_404(lead_id)
    contract = request.form.get("umowa")
    kb = request.form.get("baza")
    if contract in journey.CONTRACT_STATUSES and contract != j.contract_status:
        journey_set_contract(j, lead, contract, via="recznie")
    if kb in journey.KB_STATUSES and kb != j.kb_status:
        journey_set_kb(j, kb)
    j.needs_review = False
    db.session.commit()
    return redirect(_journey_back(lead))


def _journey_back(lead):
    back = request.form.get("back") or ""
    if back.startswith("/crm/") and not back.startswith("//"):
        return back
    return url_for("crm_journey") + f"#j{lead.id}"


@app.route("/crm/journey/<int:lead_id>/szkic")
def crm_journey_draft(lead_id):
    j, lead = _journey_or_404(lead_id)
    kind = request.args.get("rodzaj", "")
    if kind not in journey.EMAIL_KINDS:
        return jsonify({"error": "nieznany rodzaj maila"}), 400
    return jsonify(journey_draft(j, lead, kind))
