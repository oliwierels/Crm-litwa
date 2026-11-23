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
    return db.session.query(StageHistory.id).filter(StageHistory.lead_id == lead_id,
                                                    StageHistory.changed_by == uid).first() is not None


def owner_cleanup_once():
    """Jednorazowe porządki przypisań (opis na górze pliku). Zwraca raport albo None, gdy już zrobione."""
    if get_setting(OWNER_CLEANUP_KEY) or not _claim_once(OWNER_CLEANUP_KEY + "_lock"):
        return None
    report = {"at": datetime.utcnow().isoformat(timespec="seconds"), "moved": [], "lost_tasks": 0, "auto_tasks": 0}
    for code in markets.MARKETS:
        owner = default_owner(code)
        if owner == _LEGACY_IMPORT_OWNER:
            continue
        for lead in all_markets(Lead.query.filter(Lead.market == code, Lead.assigned_to == _LEGACY_IMPORT_OWNER,
                                                  Lead.stage.in_(ACTIVE_STAGES), own_leads())).all():
            if _owner_worked(lead.id, _LEGACY_IMPORT_OWNER):
                continue  # Oliwier prowadzi tego leada — zostaje u niego
            lead.assigned_to = owner  # otwarte zadania przechodzą razem z leadem (_owner_tasks_follow)
            db.session.add(Activity(
                lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(), assigned_to=owner,
                notes=f"🔁 Opiekun: {USERS_BY_ID[owner]['name']} (domyślny opiekun leadów rynku) — wcześniej przypisany "
                      f"automatycznie przy imporcie do {USERS_BY_ID[_LEGACY_IMPORT_OWNER]['name']}."))
            report["moved"].append(lead.id)
    db.session.flush()
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


app.jinja_env.globals.update(default_owner=default_owner)
