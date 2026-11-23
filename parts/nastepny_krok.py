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


def lead_needs_next_step(lead):
    """Czy na karcie leada trzeba ustalić następny krok: aktywny etap, lead już w CRM (nie w „Z ClickUp”, nie kopia
    z CRM LT/DE) i ani jednego otwartego zadania."""
    try:
        if lead is None or not lead.id or lead.stage not in ACTIVE_STAGES or lead.clickup_pending or is_market_copy(lead):
            return False
        return lead_open_tasks(lead.id).first() is None
    except Exception:
        db.session.rollback()
        return False


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


app.jinja_env.globals.update(lead_needs_next_step=lead_needs_next_step, auto_tasks_on=auto_tasks_on)
