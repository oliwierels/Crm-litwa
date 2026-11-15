# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("proby_kontaktu") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: próby kontaktu i ich wynik, limit prób

def is_confirm_call(a):
    return a is not None and a.kind == "call" and sales_rules.CONFIRM_CALL_PREFIX in (a.notes or "")


def complete_activity(a, user_id, outcome=None, answers=None):
    """Odhacza zadanie (UI, API, MCP). Telefon / SMS / mail przy leadzie wymaga wyniku kontaktu, a telefon
    potwierdzający po ofercie (odebrany) — odpowiedzi na 4 pytania; zapisują się w leadzie.
    Zwraca None albo dict błędu {"error", "need_outcome", ...} (nic nie zmienione)."""
    outcome = (outcome or "").strip() or None
    answers = {k: str(v).strip() for k, v in (answers or {}).items() if str(v or "").strip()}
    confirm = is_confirm_call(a)
    if a.lead_id and a.kind in sales_rules.OUTCOME_KINDS and sales_rules.gates_enabled(os.environ):
        allowed = ("odebral", "nie_odebral") if a.kind == "call" else ("odpisal", "bez_odpowiedzi")
        if outcome not in allowed:
            return {"error": "Podaj wynik kontaktu: " + " / ".join(sales_rules.OUTCOMES[o] for o in allowed),
                    "need_outcome": True, "kind": a.kind, "confirm": confirm,
                    "outcomes": {o: sales_rules.OUTCOMES[o] for o in allowed},
                    "questions": dict(sales_rules.CONFIRM_QUESTIONS) if confirm else {}}
        if confirm and outcome == "odebral":
            missing = [q for k, q in sales_rules.CONFIRM_QUESTIONS if not answers.get(k)]
            if missing:
                return {"error": "Telefon potwierdzający: wpisz odpowiedzi — " + "; ".join(missing),
                        "need_outcome": True, "kind": a.kind, "confirm": True, "outcomes": {"odebral": "Odebrał", "nie_odebral": "Nie odebrał"},
                        "questions": dict(sales_rules.CONFIRM_QUESTIONS)}
    a.done, a.done_at, a.outcome = True, datetime.utcnow(), outcome
    if confirm and outcome == "odebral" and a.lead:
        who = USERS_BY_ID.get(user_id, {}).get("name", "")
        a.lead.call_answers = json.dumps(dict(answers, at=f"{to_local(datetime.utcnow()):%d.%m.%Y}", by=who), ensure_ascii=False)
        db.session.add(Activity(
            lead_id=a.lead_id, kind="note", done=True, done_at=datetime.utcnow(), assigned_to=user_id,
            notes="📞 Telefon potwierdzający — " + "; ".join(f"{q} {answers.get(k, '—')}" for k, q in sales_rules.CONFIRM_QUESTIONS)))
    after_contact_outcome(a, user_id)
    return None


def lead_contact_events(lead):
    """Próby kontaktu leada: [(kiedy, "fail"/"success")]. Nieudane: telefon nieodebrany (wynik albo 📵),
    SMS / mail bez odpowiedzi. Udane: odebrany telefon, spotkanie, odpowiedź klienta (też mail od klienta w Poczcie)."""
    ev = []
    for a in lead.activities:
        for c in getattr(a, "comments", []) or []:
            if (c.text or "").startswith("📵 Nie odebrał"):
                ev.append((c.created_at, "fail"))
        if not a.done:
            continue
        when = a.done_at or a.created_at
        notes = a.notes or ""
        if a.outcome in sales_rules.FAILED_OUTCOMES or notes.startswith("📵"):
            ev.append((when, "fail"))
        elif a.outcome in sales_rules.SUCCESS_OUTCOMES or (a.kind in ("call", "meeting", "online") and not a.outcome):
            ev.append((when, "success"))
    try:
        q = mail_query_for_addrs(lead.emails)
        last_in = q.filter(MailMessage.folder == "in").order_by(MailMessage.date.desc()).first() if q is not None else None
        if last_in is not None and last_in.date:
            ev.append((last_in.date, "success"))
    except Exception:
        db.session.rollback()
    return ev


def lead_attempt_limit(lead):
    """Limit nieudanych prób dla leada: osoba prywatna (wpisana albo z podpowiedzi) 2, reszta 3."""
    return sales_rules.attempt_limit(lead.client_type or client_type_hint(lead)[0])


def lead_failed_attempts(lead):
    """Nieudane próby kontaktu z rzędu (od ostatniego udanego kontaktu)."""
    return sales_rules.contact_attempts(lead_contact_events(lead)) if lead and lead.id else 0


def check_contact_limit(lead, actor_id=None):
    """Po limicie nieudanych prób (osoby prywatne 2, reszta 3): zadanie „Zdecyduj” dla opiekuna (bez automatycznej straty
    i bez żadnej wysyłki). Jedno na serię prób. Zwraca zadanie albo None."""
    if not lead or lead.stage not in ACTIVE_STAGES or is_market_copy(lead):
        return None
    ev = lead_contact_events(lead)
    n = sales_rules.contact_attempts(ev)
    if n < lead_attempt_limit(lead):
        return None
    last_fail = max((w for w, k in ev if k == "fail" and w), default=None)
    exists = Activity.query.filter(Activity.lead_id == lead.id, Activity.kind == "decision").filter(
        db.or_(Activity.done.isnot(True), Activity.created_at >= (last_fail or datetime.min))).first()
    if exists:
        return None
    act = Activity(lead_id=lead.id, kind="decision", assigned_to=lead.assigned_to or actor_id or USERS[0]["id"],
                   due_date=datetime.combine(to_local(datetime.utcnow()).date(), datetime.min.time()),
                   notes=f"{sales_rules.DECIDE_PREFIX}: {n} nieudane próby kontaktu z rzędu. Zamknij jako Stracony "
                         f"(powód: brak kontaktu) albo zaplanuj inny kanał / termin.")
    db.session.add(act)
    return act


def after_contact_outcome(a, user_id):
    """Po wyniku kontaktu: nieodebrany telefon → kolejna próba jutro (jeśli nie ma zaplanowanej),
    a po limicie prób — zadanie „Zdecyduj”."""
    if not a.lead_id or a.outcome not in sales_rules.FAILED_OUTCOMES:
        return None
    db.session.flush()
    lead = db.session.get(Lead, a.lead_id)
    db.session.expire(lead, ["activities"])
    decide = check_contact_limit(lead, user_id)
    if decide is None and a.kind == "call":
        has_call = Activity.query.filter(Activity.lead_id == lead.id, Activity.kind == "call",
                                         Activity.done.isnot(True)).first()
        if not has_call:
            n = lead_failed_attempts(lead)
            db.session.add(Activity(lead_id=lead.id, kind="call", assigned_to=lead.assigned_to or user_id,
                                    due_date=datetime.combine(sales_rules.next_work_day(date.today()), datetime.min.time()),
                                    notes=f"Zadzwonić ponownie ({n + 1}. próba, nie odebrał)"))
    return decide
