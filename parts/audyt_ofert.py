# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("audyt_ofert") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: audyt ofert: drugi krok po ofercie, kontakt po ofercie, porządki dat z formularza

def last_offer_at(lead):
    """Kiedy poszła ostatnia oferta: oferta wysłana z CRM, notatka o ofercie (📄 / 💶) albo wejście na etap Oferta."""
    times = [o.sent_at for o in SentOffer.query.filter(SentOffer.lead_id == lead.id, SentOffer.sent_at.isnot(None)).all()]
    times += [a.done_at or a.created_at for a in lead.activities
              if a.done and (a.notes or "").startswith(("📄", "💶"))]
    times += [h.changed_at for h in lead.stage_history if h.to_stage == "Oferta" and h.changed_at]
    times = [t for t in times if t]
    return max(times) if times else None


def add_offer_second_step(lead, offer_at, actor_id=None):
    """Brakujący drugi krok po ofercie: w CRM PL telefon, w CRM LT/DE follow-up mailem. Zwraca zadanie albo None.
    Bez automatów (auto_tasks_on) nic nie dodaje."""
    if not auto_tasks_on():
        return None
    sent_on = to_local(offer_at).date() if offer_at else to_local(datetime.utcnow()).date()
    if offer_second_step_kind(lead) == "call":
        return ensure_followup_call(lead, sent_on=sent_on)
    today = to_local(datetime.utcnow()).date()
    due = max(sales_rules.next_work_day(sent_on, 3), today)
    act = Activity(lead_id=lead.id, kind="email", assigned_to=lead.assigned_to or actor_id or USERS[0]["id"],
                   due_date=datetime.combine(due, datetime.min.time()),
                   notes=f"{FOLLOWUP_STEP_PREFIX} po ofercie z {sent_on:%d.%m} — kliknij „Follow-up”, CRM przygotuje "
                         "mail w języku klienta (sprawdzasz i wysyłasz).")
    db.session.add(act)
    return act


def contact_after_offer(lead, offer_at):
    """Ostatni kontakt z klientem po ofercie: follow-up (z CRM albo z Poczty), telefon (też nieodebrany), SMS,
    WhatsApp, spotkanie, mail do klienta albo jego odpowiedź. Zwraca (kiedy, co) albo None — po ofercie nic się nie działo."""
    if not offer_at:
        return None
    after = offer_at + OFFER_CONTACT_GRACE
    kinds = {"call": "telefon", "sms": "SMS", "online": "spotkanie online", "meeting": "spotkanie", "email": "mail"}
    found = []
    for a in lead.activities:
        notes = a.notes or ""
        for c in getattr(a, "comments", []) or []:
            if (c.text or "").startswith("📵") and c.created_at and c.created_at > after:
                found.append((c.created_at, "telefon (nie odebrał)"))
        when = a.done_at or a.created_at
        if not a.done or not when or when <= after or notes.startswith(("📄", "💶")):
            continue  # zadanie otwarte, sprzed oferty albo sama oferta
        if notes.startswith("📨"):
            label = "follow-up mailem"
        elif notes.startswith("💬"):
            label = "WhatsApp / komunikator"
        elif notes.startswith("✉️ Mail z CRM"):
            label = "mail z CRM"
        elif a.kind in kinds or a.outcome or notes.startswith(("📞", "📵")):
            label = kinds.get(a.kind, "telefon")
        else:
            continue  # zwykła notatka (zmiana etapu, import itd.) to nie kontakt
        found.append((when, label))
    try:  # Poczta: mail do klienta (follow-up wysłany poza CRM) albo odpowiedź klienta
        q = mail_query_for_addrs(lead.emails)
        if q is not None:
            for folder, label in (("sent", "mail do klienta"), ("in", "odpowiedź klienta")):
                m = q.filter(MailMessage.folder == folder, MailMessage.date > after).order_by(MailMessage.date.desc()).first()
                if m is not None and m.date:
                    found.append((m.date, label))
    except Exception:
        db.session.rollback()
    return max(found, key=lambda x: x[0]) if found else None


def _contact_text(contact):
    return f"{contact[1]} {to_local(contact[0]):%d.%m}" if contact else ""


def audit_offer_second_steps(days=OFFER_AUDIT_DAYS, apply=False, actor_id=None):
    """Audyt ofert z ostatnich `days` dni: aktywne leady (Nowy / Oferta) po ofercie bez żadnego otwartego zadania.
    Lead, z którym po ofercie już był kontakt (follow-up, telefon, SMS, WhatsApp, mail z Poczty, odpowiedź klienta),
    drugiego kroku nie dostaje — w raporcie ma „kontakt po ofercie”. apply=True dodaje brakujący drugi krok pozostałym
    (tylko zadanie w CRM — nic nie wysyła). Zwraca listę wierszy raportu."""
    since = datetime.utcnow() - timedelta(days=days)
    rows, todo = [], []
    for lead in _audit_leads_query().filter(Lead.stage.in_(ACTIVE_STAGES), Lead.clickup_pending.isnot(True),
                                            own_leads()).all():
        at = last_offer_at(lead)
        if not at or at < since:
            continue
        open_tasks = [a for a in lead.activities if not a.done and a.kind != "note"]
        contact = None if open_tasks else contact_after_offer(lead, at)
        nxt = min((a.due_date for a in open_tasks if a.due_date), default=None)
        row = {"id": lead.id, "company": lead.company, "stage": lead.stage, "market": getattr(lead, "market", "pl"),
               "offer_at": f"{to_local(at):%Y-%m-%d %H:%M}",
               "owner": USERS_BY_ID.get(lead.assigned_to, {}).get("name", ""),
               "next": f"{nxt:%d.%m}" if nxt else "",
               "had_step": bool(open_tasks), "contact": _contact_text(contact), "added": ""}
        if not open_tasks and not contact:
            todo.append((lead, at, row))
        rows.append(row)
    if apply:  # dopiero po przejrzeniu wszystkich leadów — sprawdzanie Poczty niczego nie cofa
        for lead, at, row in todo:
            act = add_offer_second_step(lead, at, actor_id)
            if act is not None:
                row["added"] = f"{'telefon' if act.kind == 'call' else 'follow-up mailem'} na {act.due_date:%d.%m}"
        db.session.commit()
    rows.sort(key=lambda r: (r["had_step"] or bool(r["contact"]), r["offer_at"]))
    return rows


def _is_offer_audit_step(a):
    """Zadanie, które dodaje audyt ofert: automatyczny telefon po ofercie (PL) albo follow-up mailem (LT/DE)."""
    notes = a.notes or ""
    if a.kind == "email":
        return notes.startswith(FOLLOWUP_STEP_PREFIX)
    return a.kind == "call" and notes.startswith(AUTO_CALL_PREFIX) and (
        "po ofercie wysłanej" in notes or sales_rules.CONFIRM_CALL_PREFIX in notes)


def cleanup_offer_audit_steps():
    """Jednorazowo: pierwszy audyt ofert dodawał drugi krok także leadom, z którymi po ofercie był już kontakt
    (follow-up, telefon, mail). Takie zadania — dodane przez ten audyt i nieruszone — usuwamy; raport to opisuje."""
    if get_setting(OFFER_AUDIT_CLEANUP_KEY) or not get_setting(OFFER_AUDIT_KEY):
        return None
    if not _claim_once(OFFER_AUDIT_CLEANUP_KEY + "_lock"):
        return None
    try:
        ran = datetime.fromisoformat(get_setting(OFFER_AUDIT_KEY))
    except (TypeError, ValueError):
        ran = None
    try:
        report = json.loads(get_setting(OFFER_AUDIT_REPORT_KEY) or "{}")
    except ValueError:
        report = {}
    found = []  # najpierw tylko sprawdzamy (Poczta), dopiero potem usuwamy
    for row in report.get("rows", []) if ran else []:
        lead = db.session.get(Lead, row.get("id")) if row.get("added") else None
        contact = contact_after_offer(lead, last_offer_at(lead)) if lead is not None else None
        if contact:
            found.append((row, lead, contact))
    removed = []
    for row, lead, contact in found:
        tasks = Activity.query.filter(Activity.lead_id == lead.id, Activity.done.isnot(True),
                                      Activity.created_at >= ran - timedelta(minutes=30),
                                      Activity.created_at <= ran + timedelta(minutes=5)).all()
        tasks = [a for a in tasks if _is_offer_audit_step(a) and not a.outcome and not a.comments]
        for a in tasks:
            db.session.delete(a)
        if tasks:
            row["removed"] = f"usunięty — po ofercie był już kontakt: {_contact_text(contact)}"
            row["added"] = ""
            removed.append(lead.id)
    if removed:
        set_setting(OFFER_AUDIT_REPORT_KEY, json.dumps(report, ensure_ascii=False, default=str)[:200000])
    set_setting(OFFER_AUDIT_CLEANUP_KEY, f"{len(removed)} @ {datetime.utcnow():%Y-%m-%d %H:%M}")
    db.session.commit()
    return removed


FORM_DAY_DATES_KEY = "form_day_event_dates_cleared_v1"


def clear_form_day_event_dates():
    """Jednorazowo: nowe leady z ClickUp dostawały datę wydarzenia = dzień zgłoszenia (termin taska), a nie podaną
    przez klienta. Leady na etapie Nowy z taką datą (ten sam dzień co wpłynięcie) — data wydarzenia czyszczona."""
    if get_setting(FORM_DAY_DATES_KEY) or not _claim_once(FORM_DAY_DATES_KEY + "_lock"):
        return None
    cleared = []
    for lead in Lead.query.filter(Lead.stage == "Nowy", Lead.clickup_task_id.isnot(None), Lead.event_date.isnot(None),
                                  Lead.created_at.isnot(None)).all():
        day = to_local(lead.event_date).date()
        days = {d.event_date for d in lead.event_days}
        if day != to_local(lead.created_at).date() or not days <= {day}:
            continue
        lead.event_date = lead.event_end_date = None
        for d in list(lead.event_days):
            db.session.delete(d)
        cleared.append(lead.id)
    set_setting(FORM_DAY_DATES_KEY, f"{len(cleared)}: {', '.join(map(str, cleared))}"[:500])
    db.session.commit()
    return cleared


def run_offer_audit_once():
    """Jednorazowo po wdrożeniu (w tle): audyt ofert z dodaniem brakujących drugich kroków; raport w ustawieniach."""
    if get_setting(OFFER_AUDIT_KEY) or not _claim_once(OFFER_AUDIT_KEY + "_lock"):
        return None
    rows = audit_offer_second_steps(apply=True)
    set_setting(OFFER_AUDIT_REPORT_KEY, json.dumps({"at": f"{to_local(datetime.utcnow()):%Y-%m-%d %H:%M}", "rows": rows},
                                                   ensure_ascii=False, default=str)[:200000])
    set_setting(OFFER_AUDIT_KEY, datetime.utcnow().isoformat(timespec="seconds"))
    db.session.commit()
    return rows
