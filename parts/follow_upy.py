# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("follow_upy") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: telefon / follow-up po ofercie i mailu

FOLLOWUP_CALL_DAYS = 7
FOLLOWUP_AFTER_EMAIL_DAYS = 5  # telefon po wysłanym follow-upie mailem
AUTO_CALL_PREFIX = "📞 Drugi krok: telefon"


def ensure_followup_call(lead, sent_on=None, days=FOLLOWUP_CALL_DAYS, after="ofercie wysłanej"):
    """Drugi krok po ofercie: telefon tydzień po jej wysłaniu (nie wcześniej niż dziś), przypisany
    do opiekuna leada. Ręcznie zaplanowany otwarty telefon zostaje bez zmian, automatyczny przesuwa się
    na nowy termin (np. po kolejnej wersji oferty albo po follow-upie mailem). Zwraca zadanie albo None."""
    today = to_local(datetime.utcnow()).date()
    sent_on = sent_on or today
    due = datetime.combine(max(sent_on + timedelta(days=days), today), datetime.min.time())
    # telefon potwierdzający: firma / agencja / partner albo oferta od 10 000 zł → następny dzień roboczy
    confirm = after == "ofercie wysłanej" and sales_rules.needs_confirm_call(lead.client_type,
                                                                             to_pln(lead.value_pln, lead.currency))
    if confirm:
        due = datetime.combine(max(sales_rules.next_work_day(sent_on), today), datetime.min.time())
    # zapytanie, nie lead.activities — widzi też zadania dodane przed chwilą w tej samej sesji
    calls = Activity.query.filter(
        Activity.lead_id == lead.id, Activity.kind == "call", Activity.done.isnot(True)).all()
    if any(not (a.notes or "").startswith(AUTO_CALL_PREFIX) for a in calls):
        return None
    act = calls[0] if calls else Activity(lead_id=lead.id, kind="call")
    act.due_date = due
    act.assigned_to = lead.assigned_to or USERS[0]["id"]
    if confirm:
        questions = " ".join(f"{i}) {q}" for i, (_k, q) in enumerate(sales_rules.CONFIRM_QUESTIONS, 1))
        act.notes = (f"{AUTO_CALL_PREFIX} — {sales_rules.CONFIRM_CALL_PREFIX} po ofercie z {sent_on:%d.%m} "
                     f"(dzwonimy {sales_rules.CALL_HOURS}). Zapytaj: {questions}")
    else:
        act.notes = f"{AUTO_CALL_PREFIX} po {after} {sent_on:%d.%m} — zapytaj o decyzję i pytania do oferty."
    if not calls:
        db.session.add(act)
    return act
