# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("asystent_pola") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: asystent / konektor: zmiana pól leada

def _assistant_closed_on(lead, inp, user):
    """closed_on z narzędzia -> poprawka daty zamknięcia; zwraca tekst błędu albo None."""
    if not inp.get("closed_on"):
        return None
    d = parse_date(str(inp["closed_on"]).strip())
    db.session.flush()  # nowy wpis historii (zmiana etapu w tym samym wywołaniu) musi mieć już datę
    db.session.expire(lead, ["stage_history"])
    code = set_lead_closed_on(lead, d.date() if d else None, user["id"])
    return CLOSED_ON_ERRORS[code] if code else None


def _assistant_parse_days(inp):
    """event_dates (lista YYYY-MM-DD) + event_locations (dict) z narzędzia -> (days, locations)."""
    days = set()
    for s in inp.get("event_dates") or []:
        dt = sane_event_date(parse_dt(str(s).strip()))
        if dt:
            days.add(dt.date())
    locations = {}
    for k, v in (inp.get("event_locations") or {}).items():
        dt = sane_event_date(parse_dt(str(k).strip()))
        if dt and str(v).strip():
            locations[dt.date()] = str(v).strip()[:200]
    return sorted(days), locations


def _assistant_apply_lead_fields(lead, inp, user):
    """Wspólne dla create/update: nakłada TYLKO podane pola."""
    simple = {
        "company": "company", "contact_name": "contact_name", "contact_phone": "contact_phone",
        "contact_email": "contact_email", "contact_email2": "contact_email2", "location": "location",
        "source": "source", "notes": "notes",
        "nip": "nip",
    }
    for key, attr in simple.items():
        if key in inp and inp[key] is not None:
            setattr(lead, attr, str(inp[key]).strip())
    if inp.get("robot_model") in ROBOT_MODELS:
        lead.robot_model = inp["robot_model"]
    if "value" in inp and inp["value"] is not None:
        lead.value_pln = float(inp["value"])
    if inp.get("currency") in CURRENCIES:
        lead.currency = inp["currency"]
    if inp.get("quality") in QUALITY_LEVELS:
        lead.quality = inp["quality"]
    if "contract_signed" in inp and inp["contract_signed"] is not None:
        lead.contract_signed = bool(inp["contract_signed"])
    if "assigned_to" in inp:
        lead.assigned_to = _assistant_resolve_user(inp.get("assigned_to"), lead.assigned_to or user["id"])
    if inp.get("client_type") in sales_rules.CLIENT_TYPES:
        lead.client_type = inp["client_type"]
    if str(inp.get("country") or "").upper() in sales_rules.COUNTRIES:
        lead.country = str(inp["country"]).upper()
    warnings = []
    if "event_dates" in inp and inp["event_dates"] is not None:  # dni przed etapem — bramka Wygrany ich wymaga
        days, locations = _assistant_parse_days(inp)
        warnings += set_lead_event_days(lead, days, locations) or []
    stage = REMOVED_STAGES.get(inp.get("stage"), inp.get("stage"))  # stara nazwa z zapamiętanego schematu
    if stage in STAGES and stage != lead.stage:
        if lead.id:
            gate = gated_stage_change(lead, stage, user["id"], gate_form_data(inp))
            if gate["message"]:
                hint = gate.get("hint", {})
                extra = []
                if "lost_reason" in gate["need"]:
                    extra.append("lost_reason: " + ", ".join(sales_rules.LOST_REASONS))
                if "win_confirmation" in gate["need"]:
                    extra.append("win_confirmation: umowa | mail")
                if "client_type" in gate["need"]:
                    extra.append(f"client_type: {', '.join(sales_rules.CLIENT_TYPES)} (podpowiedź: {hint.get('client_type')})")
                if gate["warnings"]:
                    extra.append("po sprawdzeniu kwoty podaj confirm_warning=true")
                return gate["message"] + (" [" + "; ".join(extra) + "]" if extra else "")
        else:  # nowy lead od razu na dalszym etapie (np. wygrany wpisany po fakcie) — te same bramki
            data = gate_form_data(inp)
            gate = stage_gate_check(lead, stage, data)
            if gate["message"]:
                return gate["message"] + (f" [lost_reason: {', '.join(sales_rules.LOST_REASONS)}; win_confirmation: umowa | mail; "
                                          f"client_type: {', '.join(sales_rules.CLIENT_TYPES)}]")
            if data.get("client_type") in sales_rules.CLIENT_TYPES:
                lead.client_type = data["client_type"]
            if stage == "Stracony":
                lead.lost_reason, lead.lost_comment = data["lost_reason"], data.get("lost_comment") or None
            if stage in WON_STAGES:
                lead.win_confirmation = data.get("win_confirmation") or ("umowa" if lead.contract_signed else None)
                lead.contract_signed = bool(lead.contract_signed) or lead.win_confirmation == "umowa"
            lead.stage = stage
    lead._mcp_warnings = warnings
    return None
