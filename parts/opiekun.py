# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("opiekun") zaraz po następnym kroku — działa w przestrzeni nazw crm.py.
# Temat: opiekun leada i jego zadania
#
# - Nowy lead bez wskazanej osoby (ClickUp, skrzynka kontakt@, formularz, konektor, „+ Lead”) dostaje domyślnego
#   opiekuna rynku z market.py (default_owner): CRM PL — Jerzy (obsługuje wszystkie polskie leady), CRM LT/DE —
#   Oliwier. Zmienna INBOX_ASSIGN_TO w Railway nadal może wskazać kogoś innego dla zapytań z kontakt@.
# - Zadania idą za opiekunem: zmiana opiekuna leada przenosi jego otwarte zadania na nową osobę (zadania zlecone
#   komuś trzeciemu zostają u tej osoby).
# - Raz po wdrożeniu (OWNER_CLEANUP_KEY): leady, które import przypisywał na sztywno użytkownikowi nr 1 (Oliwier),
#   a on przy nich nic nie zrobił, przechodzą do domyślnego opiekuna rynku razem z otwartymi zadaniami; otwarte zadania
#   straconych leadów znikają; zadania dodane przez automat (bez autora) trafiają do opiekuna leada. Raport z listą
#   przeniesionych leadów zostaje w ustawieniu — można je odtworzyć.
OWNER_CLEANUP_KEY = "owner_cleanup_v1"
_LEGACY_IMPORT_OWNER = 1  # dawny domyślny opiekun importu (na sztywno)


def default_owner(code=None):
    """Id domyślnego opiekuna nowego leada rynku `code` (domyślnie oglądanego)."""
    uid = markets.get(code or current_market()).get("default_owner")
    return uid if uid in USERS_BY_ID else USERS[0]["id"]


@_sa_event.listens_for(Lead.assigned_to, "set", active_history=True)
def _owner_remember_previous(target, value, oldvalue, _initiator):
    """Zmiana opiekuna: zapamiętaj poprzedniego (raz na zapis), żeby po zapisie przenieść jego zadania."""
    if isinstance(oldvalue, int) and value != oldvalue and "_owner_prev" not in target.__dict__:
        target.__dict__["_owner_prev"] = oldvalue
    return value


@_sa_event.listens_for(_OrmSession, "after_flush")
def _owner_tasks_follow(session, _ctx):
    """Zmiana opiekuna leada: jego otwarte zadania przechodzą na nowego opiekuna."""
    for obj in list(session.dirty) + list(session.new):
        if not isinstance(obj, Lead) or "_owner_prev" not in obj.__dict__:
            continue
        old, new = obj.__dict__.pop("_owner_prev"), obj.assigned_to
        if obj.id and old and new and old != new:
            session.connection().execute(
                db.update(Activity).where(Activity.lead_id == obj.id, Activity.assigned_to == old,
                                          Activity.done.isnot(True), Activity.kind != "note")
                .values(assigned_to=new))


def _owner_worked(lead_id, uid):
    """Czy osoba `uid` coś przy leadzie zrobiła (zadanie, notatka, telefon, oferta, komentarz, zmiana etapu)."""
    acts = db.session.query(Activity.id).filter(Activity.lead_id == lead_id)
    if acts.filter(db.or_(Activity.created_by == uid,
                          db.and_(Activity.done.is_(True), Activity.assigned_to == uid, Activity.kind != "note"))).first():
        return True
    if db.session.query(SentOffer.id).filter(SentOffer.lead_id == lead_id, SentOffer.sender_id == uid,
                                             SentOffer.sent_at.isnot(None)).first():
        return True
    if db.session.query(ActivityComment.id).filter(ActivityComment.author_id == uid,
                                                   ActivityComment.activity_id.in_(acts)).first():
        return True
    # wpis przy tworzeniu leada (from_stage puste) robi automat w imieniu osoby — to nie jest praca przy leadzie
    return db.session.query(StageHistory.id).filter(StageHistory.lead_id == lead_id, StageHistory.changed_by == uid,
                                                    StageHistory.from_stage.isnot(None)).first() is not None


def _owner_acted(lead, uid):
    """Ślady pracy, które osoba `uid` zostawiła przy leadzie SAMA (nie automat w jej imieniu): obsłużenie w panelu
    nowych leadów, wysłany mail z CRM, telefon z zapisanym wynikiem, jej komentarz albo zadanie, komentarz do zadania,
    wysłana oferta, zmiana etapu. Zadanie automatu zamknięte w jej imieniu (np. nieaktualny szkic) się nie liczy."""
    if lead.handled_by == uid:
        return True
    acts = db.session.query(Activity.id).filter(Activity.lead_id == lead.id)
    if acts.filter(db.or_(Activity.created_by == uid,
                          db.and_(Activity.assigned_to == uid, Activity.done.is_(True),
                                  db.or_(Activity.outcome.isnot(None), Activity.notes.like("✉️ Mail z CRM%"))))).first():
        return True
    if db.session.query(SentOffer.id).filter(SentOffer.lead_id == lead.id, SentOffer.sender_id == uid,
                                             SentOffer.sent_at.isnot(None)).first():
        return True
    if db.session.query(ActivityComment.id).filter(ActivityComment.author_id == uid,
                                                   ActivityComment.activity_id.in_(acts)).first():
        return True
    return db.session.query(StageHistory.id).filter(StageHistory.lead_id == lead.id, StageHistory.changed_by == uid,
                                                    StageHistory.from_stage.isnot(None)).first() is not None


def _move_legacy_leads(report, worked):
    """Leady rynku przypisane na sztywno przy imporcie użytkownikowi nr 1, przy których `worked(lead)` mówi „nic nie
    robił” → domyślny opiekun rynku (z otwartymi zadaniami i komentarzem, skąd zmiana)."""
    for code in markets.MARKETS:
        owner = default_owner(code)
        if owner == _LEGACY_IMPORT_OWNER:
            continue
        for lead in all_markets(Lead.query.filter(Lead.market == code, Lead.assigned_to == _LEGACY_IMPORT_OWNER,
                                                  Lead.stage.in_(ACTIVE_STAGES), own_leads())).all():
            if worked(lead):
                continue  # Oliwier prowadzi tego leada — zostaje u niego
            lead.assigned_to = owner  # otwarte zadania przechodzą razem z leadem (_owner_tasks_follow)
            db.session.add(Activity(
                lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(), assigned_to=owner,
                notes=f"🔁 Opiekun: {USERS_BY_ID[owner]['name']} (domyślny opiekun leadów rynku) — wcześniej przypisany "
                      f"automatycznie przy imporcie do {USERS_BY_ID[_LEGACY_IMPORT_OWNER]['name']}."))
            report["moved"].append(lead.id)
    db.session.flush()


OWNER_CLEANUP_V2_KEY = "owner_cleanup_v2"


def owner_cleanup_v2_once():
    """Druga, dokładniejsza tura porządków: pierwsza liczyła jako pracę Oliwiera także zadania automatu zamknięte
    w jego imieniu, więc część nieruszanych leadów została u niego. Teraz liczą się tylko jego własne działania
    (_owner_acted). Raz; raport z listą przeniesionych leadów zostaje w ustawieniu."""
    if get_setting(OWNER_CLEANUP_V2_KEY) or not _claim_once(OWNER_CLEANUP_V2_KEY + "_lock"):
        return None
    report = {"at": datetime.utcnow().isoformat(timespec="seconds"), "moved": []}
    _move_legacy_leads(report, lambda lead: _owner_acted(lead, _LEGACY_IMPORT_OWNER))
    set_setting(OWNER_CLEANUP_V2_KEY, json.dumps(report))
    db.session.commit()
    return report


def owner_cleanup_once():
    """Jednorazowe porządki przypisań (opis na górze pliku). Zwraca raport albo None, gdy już zrobione."""
    if get_setting(OWNER_CLEANUP_KEY) or not _claim_once(OWNER_CLEANUP_KEY + "_lock"):
        return None
    report = {"at": datetime.utcnow().isoformat(timespec="seconds"), "moved": [], "lost_tasks": 0, "auto_tasks": 0}
    _move_legacy_leads(report, lambda lead: _owner_worked(lead.id, _LEGACY_IMPORT_OWNER))
    for lead in all_markets(Lead.query.filter(Lead.stage == "Stracony")).all():
        report["lost_tasks"] += clear_open_tasks(lead)
    for act in all_markets(Activity.query.join(Lead, Activity.lead_id == Lead.id).filter(
            Activity.done.isnot(True), Activity.kind != "note", Activity.created_by.is_(None),
            Lead.assigned_to.isnot(None), Activity.assigned_to != Lead.assigned_to)).all():
        act.assigned_to = act.lead.assigned_to
        report["auto_tasks"] += 1
    set_setting(OWNER_CLEANUP_KEY, json.dumps(report))
    db.session.commit()
    return report


_DRAFT_CLOSING = "\nPozdrawiam serdecznie\n"


def draft_signed_by(body, user, lead=None):
    """Szkic pierwszej odpowiedzi (sales_rules.private_draft) podpisany osobą, która go otwiera i wysyła — szkic mógł
    powstać, gdy lead miał jeszcze innego opiekuna (np. przed przeniesieniem do Jerzego)."""
    if _DRAFT_CLOSING not in (body or ""):
        return body
    code = lead_market(lead) if lead is not None else DEFAULT_MARKET
    phone = mail_phone(user.get("phone", ""), code)
    return (body.rsplit(_DRAFT_CLOSING, 1)[0] + _DRAFT_CLOSING + (user.get("full_name") or user["name"])
            + (f"\n{phone}" if phone else ""))


@app.route("/crm/leads/<int:lead_id>/opiekun", methods=["POST"])
def crm_lead_owner(lead_id):
    """Zmiana opiekuna z nagłówka karty leada. Otwarte zadania poprzedniego opiekuna przechodzą na nowego,
    a w komentarzach zostaje ślad, kto zmienił."""
    lead = db.session.get(Lead, lead_id) or abort(404)
    actor = get_current_user(request)
    try:
        uid = int(request.form.get("assigned_to", ""))
    except ValueError:
        uid = None
    if uid in USERS_BY_ID and uid != lead.assigned_to:
        old = USERS_BY_ID.get(lead.assigned_to, {}).get("name", "—")
        lead.assigned_to = uid
        db.session.add(Activity(lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(),
                                assigned_to=actor["id"], created_by=actor["id"],
                                notes=f"🔁 Opiekun: {old} → {USERS_BY_ID[uid]['name']}"))
        db.session.commit()
    return redirect(url_for("crm_lead_detail", lead_id=lead.id))


app.jinja_env.globals.update(default_owner=default_owner)
