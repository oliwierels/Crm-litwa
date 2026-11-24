# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("nastepny_krok") tuż przed raportem dnia — działa w przestrzeni nazw crm.py.
# Temat: następny krok w leadzie ustawia człowiek; wygrany lead bez zadań z lejka
#
# - CRM nie dodaje już zadań sam (auto_tasks_on w follow_upy.py, domyślnie wyłączone — AUTO_TASKS=1 przywraca),
# - aktywny lead (Nowy / Oferta) bez żadnego otwartego zadania: na karcie leada okno „Ustal następny krok” — człowiek
#   wpisuje, co i kiedy dalej (albo zamyka leada etapem Wygrany / Stracony),
# - lead, który przechodzi na Wygrany / Zrealizowany, traci otwarte zadania z lejka — dalej prowadzi go Customer Journey
#   (umowa, faktura, baza wiedzy). Zrobione zadania i notatki zostają w historii. Raz po wdrożeniu to samo dla leadów,
#   które już są wygrane (WON_TASKS_CLEANUP_KEY); zadania dodane do wygranego leada później przez człowieka zostają.

WON_TASKS_CLEANUP_KEY = "won_tasks_cleanup_v1"


def lead_open_tasks(lead_id):
    """Otwarte zadania leada (bez notatek)."""
    return Activity.query.filter(Activity.lead_id == lead_id, Activity.done.isnot(True), Activity.kind != "note")


def clear_open_tasks(lead, reason="lead wygrany (dalej Customer Journey)"):
    """Zamyka otwarte zadania leada (wygrany / zrealizowany / stracony) przez silnik porządku: automatyczne znikają,
    zadania od człowieka zostają w historii jako komentarz „🧹 Nieaktualne” (parts/silnik_porzadku.py).
    Zwraca liczbę zamkniętych."""
    if lead is None or not lead.id:
        return 0
    return retire_tasks(lead, lead_open_tasks(lead.id).all(), reason)


def lead_needs_first_call(lead):
    """Nowy lead, z którym nikt się jeszcze nie skontaktował: na karcie najpierw „📞 Pierwszy telefon” (numer na wierzchu),
    a nie okno „Ustal następny krok” — następny krok planuje się dopiero po pierwszym kontakcie."""
    try:
        return bool(lead is not None and lead.id and lead.stage == "Nowy" and lead.handled_at is None
                    and not lead.clickup_pending and not is_market_copy(lead))
    except Exception:
        db.session.rollback()
        return False


def lead_needs_next_step(lead):
    """Czy na karcie leada trzeba ustalić następny krok: aktywny etap, lead już w CRM (nie w „Z ClickUp”, nie kopia
    z CRM LT/DE), po pierwszym kontakcie i ani jednego otwartego zadania."""
    try:
        if lead is None or not lead.id or lead.stage not in ACTIVE_STAGES or lead.clickup_pending or is_market_copy(lead):
            return False
        if lead_needs_first_call(lead):
            return False
        return lead_open_tasks(lead.id).first() is None
    except Exception:
        db.session.rollback()
        return False


def first_call_leads(user_id=None):
    """„🔥 Pilne — oddzwoń do nowych leadów” w Moim dniu: nowe leady bez kontaktu (te same co w oknie alarmów),
    najpierw leady tej osoby i bez opiekuna, potem cudze; w każdej grupie najstarsze na górze."""
    try:
        leads = unhandled_leads()
        rows = [dict(_lead_alert_row(l), owner=USERS_BY_ID.get(l.assigned_to, {}).get("name", ""),
                     mine=user_id is None or l.assigned_to in (None, user_id), created=l.created_at or datetime.min)
                for l in leads]
    except Exception:
        db.session.rollback()
        return []
    return sorted(rows, key=lambda r: (not r["mine"], r["created"]))


def cleanup_won_tasks_once():
    """Jednorazowo po wdrożeniu: otwarte zadania wszystkich wygranych i zrealizowanych leadów znikają.
    Raport (ile leadów, ile zadań) w ustawieniu WON_TASKS_CLEANUP_KEY."""
    if get_setting(WON_TASKS_CLEANUP_KEY) or not _claim_once(WON_TASKS_CLEANUP_KEY + "_lock"):
        return None
    leads = all_markets(Lead.query.filter(Lead.stage.in_(WON_STAGES), Lead.id.in_(
        db.session.query(Activity.lead_id).filter(Activity.done.isnot(True), Activity.kind != "note")))).all()
    removed = sum(clear_open_tasks(lead) for lead in leads)
    set_setting(WON_TASKS_CLEANUP_KEY, json.dumps({"at": datetime.utcnow().isoformat(timespec="seconds"),
                                                   "leads": len(leads), "tasks": removed}))
    db.session.commit()
    return removed


app.jinja_env.globals.update(lead_needs_next_step=lead_needs_next_step, auto_tasks_on=auto_tasks_on,
                             lead_needs_first_call=lead_needs_first_call, first_call_leads=first_call_leads)
