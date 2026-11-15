# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("automaty") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: automaty: następne kroki, przenoszenie zrealizowanych

def backfill_next_steps():
    """Aktywne leady bez żadnego otwartego zadania dostają drugi krok — telefon do opiekuna leada:
    tydzień po ostatniej ofercie (nie wcześniej niż dziś), a leady jeszcze bez oferty — na dziś.
    Zwraca liczbę leadów, którym dodano telefon."""
    busy = {lid for (lid,) in db.session.query(Activity.lead_id).filter(
        Activity.lead_id.isnot(None), Activity.done.isnot(True)).distinct()}
    leads = [l for l in Lead.query.filter(Lead.stage.in_(ACTIVE_STAGES), Lead.clickup_pending.isnot(True)).all()
             if l.id not in busy]
    if not leads:
        return 0
    ids = [l.id for l in leads]
    # kiedy poszła ostatnia oferta: wysłana z CRM albo wejście na etap Oferta (oferta mailem poza CRM)
    offered = dict(db.session.query(SentOffer.lead_id, db.func.max(SentOffer.sent_at)).filter(
        SentOffer.lead_id.in_(ids), SentOffer.sent_at.isnot(None)).group_by(SentOffer.lead_id).all())
    for lid, at in db.session.query(StageHistory.lead_id, db.func.max(StageHistory.changed_at)).filter(
            StageHistory.lead_id.in_(ids), StageHistory.to_stage == "Oferta").group_by(StageHistory.lead_id):
        if at and (not offered.get(lid) or at > offered[lid]):
            offered[lid] = at
    today = datetime.combine(to_local(datetime.utcnow()).date(), datetime.min.time())
    for lead in leads:
        sent = offered.get(lead.id)
        if sent:
            ensure_followup_call(lead, to_local(sent).date())
        else:
            db.session.add(Activity(
                lead_id=lead.id, kind="call", due_date=today, assigned_to=lead.assigned_to or USERS[0]["id"],
                notes=f"{AUTO_CALL_PREFIX} — lead nie miał zaplanowanego kroku: zadzwoń i ustal, czego potrzebuje.",
            ))
    db.session.commit()
    return len(leads)


def auto_realize_leads():
    """Wygrane leady, których ostatni dzień realizacji już minął, przechodzą na etap Zrealizowany
    (znikają z kolumny Wygrany i z przypomnień o niepodpisanej umowie). Lead, którego ktoś ręcznie
    cofnął z Zrealizowany na Wygrany, zostaje, dopóki znowu nie zmieni etapu. Zwraca liczbę przeniesionych."""
    today = to_local(datetime.utcnow()).date()
    last_day = db.session.query(LeadEventDate.lead_id, db.func.max(LeadEventDate.event_date).label("last")) \
        .group_by(LeadEventDate.lead_id).subquery()
    leads = Lead.query.join(last_day, last_day.c.lead_id == Lead.id).filter(
        Lead.stage == "Wygrany", Lead.clickup_pending.isnot(True), last_day.c.last < today).all()
    if not leads:
        return 0
    # ostatni wpis historii każdego z nich — jednym zapytaniem, nie osobno dla każdego leada
    last_entry = {}
    for h in StageHistory.query.filter(StageHistory.lead_id.in_([l.id for l in leads])).all():
        if h.lead_id not in last_entry or _history_order(h) > _history_order(last_entry[h.lead_id]):
            last_entry[h.lead_id] = h
    moved = 0
    for lead in leads:
        last = last_entry.get(lead.id)
        if last and last.from_stage == "Zrealizowany" and last.to_stage == "Wygrany":
            continue  # ktoś celowo cofnął na Wygrany — nie przenosimy z powrotem
        if record_stage_change(lead, "Zrealizowany", None):
            moved += 1
    if moved:
        db.session.commit()
    return moved


_AUTO_REALIZE_EVERY = 600  # s


NEXT_STEPS_BACKFILL_KEY = "next_steps_backfill_v1"
