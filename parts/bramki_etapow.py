# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("bramki_etapow") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: bramki etapów (powód straty, typ klienta, wygrana)

def gate_form_data(src):
    """Pola bramek etapów z formularza / JSON / narzędzia MCP."""
    keys = ("lost_reason", "lost_comment", "win_confirmation", "client_type", "confirm_warning")
    return {k: (str(src.get(k)).strip() if src.get(k) is not None else "") for k in keys}


def stage_gate_check(lead, new_stage, data):
    gate = {"need": [], "errors": [], "warnings": [], "message": ""}
    if sales_rules.gates_enabled(os.environ) and new_stage in STAGES and new_stage != lead.stage:
        gate = sales_rules.stage_gate(lead, new_stage, data, event_dates=[d.event_date for d in lead.event_days],
                                      last_offer_value=last_sent_offer_value(lead),
                                      contract_signed=bool(lead.contract_signed))
    key, label, why = client_type_hint(lead)
    gate["hint"] = {"client_type": key, "client_type_label": label, "why": why}
    return gate


def gated_stage_change(lead, new_stage, actor_id, data=None):
    """Zmiana etapu przez człowieka (UI, API, MCP) — z bramkami procesu sprzedaży. Zwraca dict bramki;
    changed=True gdy etap zmieniony, message = czytelny powód odmowy po polsku."""
    new_stage = REMOVED_STAGES.get(new_stage, new_stage)
    data = data or {}
    if new_stage not in STAGES or new_stage == lead.stage:
        return {"need": [], "errors": [], "warnings": [], "message": "", "changed": False}
    gate = stage_gate_check(lead, new_stage, data)
    if gate["message"]:
        gate["changed"] = False
        return gate
    ct = data.get("client_type")
    if ct in sales_rules.CLIENT_TYPES:
        lead.client_type = ct
    reason, note = None, None
    if new_stage == "Stracony":
        lead.lost_reason = data.get("lost_reason") or None
        lead.lost_comment = (data.get("lost_comment") or "").strip() or None
        reason, note = lead.lost_reason, lead.lost_comment
    conf = None
    if new_stage in WON_STAGES and lead.stage not in WON_STAGES:
        conf = data.get("win_confirmation") if data.get("win_confirmation") in sales_rules.WIN_CONFIRMATIONS else (
            "umowa" if lead.contract_signed else None)
        lead.win_confirmation = conf
        reason = conf
    was_won = lead.stage in WON_STAGES
    record_stage_change(lead, new_stage, actor_id, reason=reason, note=note)
    if new_stage == "Wygrany" and not was_won and actor_id is not None:
        notify_team_win(lead, actor_id)  # zespół dostaje powiadomienie z pokazem 🏆 (silnik_porzadku.py)
    if conf == "umowa" and lead.stage in WON_STAGES:  # umowa już podpisana — Customer Journey tego nie zgubi
        j = ensure_journey(lead) if lead.stage in JOURNEY_STAGES else None
        if j is not None:
            journey_set_contract(j, lead, "podpisana", via="przy wygranej")
        else:
            lead.contract_signed = True
    gate["changed"] = True
    return gate
