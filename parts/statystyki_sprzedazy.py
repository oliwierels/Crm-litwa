# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("statystyki_sprzedazy") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: statystyki i panel skuteczności: cele, kursy, data zamknięcia, lejek

SALES_TARGETS_KEY = "sales_targets"
DEFAULT_SALES_TARGETS = {
    "PL": {"cur": "PLN", "realny": 150000, "ambitny": 200000},
    "DE": {"cur": "EUR", "realny": 30000},
    "LT": {"cur": "EUR", "realny": 15000},
}
PANEL_COUNTRIES = ("PL", "DE", "LT", "RO")
BIG_DEAL_PLN = 10000


def sales_targets():
    try:
        t = json.loads(get_setting(SALES_TARGETS_KEY) or "{}")
    except ValueError:
        t = {}
    out = {k: dict(v) for k, v in DEFAULT_SALES_TARGETS.items()}
    for k, v in t.items():
        if isinstance(v, dict):
            out.setdefault(k, {}).update(v)
    return out


def refresh_eur_rate():
    """Kurs EUR z NBP (tabela A) raz dziennie — tylko odczyt z api.nbp.pl. Ręcznie wpisany kurs ma pierwszeństwo."""
    if get_setting("fx_EUR_manual") == "1" or get_setting("fx_EUR_date") == date.today().isoformat():
        return
    try:
        import urllib.request
        with urllib.request.urlopen("https://api.nbp.pl/api/exchangerates/rates/a/eur/?format=json", timeout=6) as r:
            rate = float(json.loads(r.read().decode())["rates"][0]["mid"])
        if 3.0 < rate < 7.0:
            set_setting("fx_EUR", f"{rate:.4f}")
            set_setting("fx_EUR_date", date.today().isoformat())
            db.session.commit()
    except Exception:
        db.session.rollback()


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else round((xs[m - 1] + xs[m]) / 2)


def build_sales_panel(period_days=90, today=None):
    """Panel skuteczności: wygrane wg miesiąca zamknięcia i kraju na tle celów (z prognozą), skuteczność wg typu
    klienta i opiekuna, powody strat, mediana czasu reakcji, anulacje po wygranej, duże otwarte leady.
    Liczy z tych samych funkcji co Statystyki (closed_lead_events) — wspólne dla strony i MCP."""
    today = today or to_local(datetime.utcnow()).date()
    rate = currency_rate("EUR")
    targets = sales_targets()
    events = closed_lead_events(include_copies=True)
    months = []
    y, m = today.year, today.month
    for _ in range(6):
        months.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()

    won = {}
    for lead, stage, when in events:
        if stage != "Wygrany":
            continue
        c = lead_country(lead)
        c = c if c in PANEL_COUNTRIES else "INNE"
        k = (when.year, when.month)
        won.setdefault((c, k), {"pln": 0.0, "count": 0, "leads": []})
        won[(c, k)]["pln"] += to_pln(lead.value_pln, lead.currency)
        won[(c, k)]["count"] += 1
        won[(c, k)]["leads"].append(lead)
    import calendar as _cal
    dim = _cal.monthrange(today.year, today.month)[1]
    countries = []
    for c in PANEL_COUNTRIES + ("INNE",):
        t = targets.get(c, {})
        cur = t.get("cur") or ("PLN" if c in ("PL", "INNE") else "EUR")
        rows = []
        for k in months:
            w = won.get((c, k), {"pln": 0.0, "count": 0, "leads": []})
            val = w["pln"] if cur == "PLN" else w["pln"] / rate
            rows.append({"month": f"{k[0]}-{k[1]:02d}", "value": round(val), "count": w["count"],
                         "pct": round(100 * val / t["realny"]) if t.get("realny") else None})
        cur_val = rows[-1]["value"]
        forecast = round(cur_val * dim / max(1, today.day))
        if not any(r["count"] for r in rows) and c not in targets:
            continue
        countries.append({"code": c, "name": sales_rules.COUNTRIES.get(c, "Inne"), "cur": cur, "rows": rows,
                          "target": t.get("realny"), "ambitious": t.get("ambitny"), "current": cur_val,
                          "forecast": forecast,
                          "forecast_pct": round(100 * forecast / t["realny"]) if t.get("realny") else None})
    total_pln = sum(w["pln"] for (c, k), w in won.items() if k == months[-1])

    since = today - timedelta(days=period_days)
    closed = [(l, st, w) for l, st, w in events if w.date() >= since]

    def group(keyf, labels):
        out = {}
        for lead, st, _w in closed:
            g = out.setdefault(keyf(lead), {"won": 0, "lost": 0, "won_pln": 0.0})
            if st == "Wygrany":
                g["won"] += 1
                g["won_pln"] += to_pln(lead.value_pln, lead.currency)
            else:
                g["lost"] += 1
        return [{"key": k, "label": labels.get(k, k or "nie ustawiono"), "won": v["won"], "lost": v["lost"],
                 "rate": round(100 * v["won"] / (v["won"] + v["lost"])) if v["won"] + v["lost"] else None,
                 "won_pln": round(v["won_pln"])} for k, v in sorted(out.items(), key=lambda kv: -(kv[1]["won"] + kv[1]["lost"]))]

    by_type = group(lambda l: l.client_type or "", sales_rules.CLIENT_TYPES)
    by_owner = group(lambda l: str(l.assigned_to or ""), {str(u["id"]): u["name"] for u in USERS})
    reasons = {}
    for lead, st, _w in closed:
        if st == "Stracony":
            k = lead.lost_reason or ""
            reasons[k] = reasons.get(k, 0) + 1
    reasons = [{"key": k, "label": sales_rules.LOST_REASONS.get(k, "brak powodu (sprzed obowiązkowego pola)"), "count": n}
               for k, n in sorted(reasons.items(), key=lambda kv: -kv[1])]
    created = [l for l in Lead.query.filter(Lead.created_at >= datetime.combine(since, datetime.min.time()),
                                            Lead.handled_at.isnot(None), Lead.clickup_pending.isnot(True), own_leads()).all()]
    resp = [lead_response_minutes(l) for l in created]
    resp = [r for r in resp if r is not None]
    cancels = []
    for h in StageHistory.query.filter(StageHistory.to_stage == "Stracony", StageHistory.from_stage.in_(WON_STAGES),
                                       StageHistory.changed_at >= datetime.combine(since, datetime.min.time())).all():
        if h.lead is not None:
            cancels.append({"lead": h.lead, "when": to_local(h.changed_at), "reason": sales_rules.LOST_REASONS.get(h.reason or "", ""),
                            "pln": round(to_pln(h.lead.value_pln, h.lead.currency))})
    big = []
    for lead in Lead.query.filter(Lead.stage.in_(ACTIVE_STAGES), Lead.clickup_pending.isnot(True), own_leads()).all():
        pln = to_pln(lead.value_pln, lead.currency)
        if pln >= BIG_DEAL_PLN:
            nxt = min((a.due_date for a in lead.activities if not a.done and a.kind != "note" and a.due_date), default=None)
            big.append({"lead": lead, "pln": round(pln), "next": nxt})
    big.sort(key=lambda x: (x["next"] is not None, x["next"] or datetime.min, -x["pln"]))
    return {"months": [f"{y}-{m:02d}" for y, m in months], "countries": countries, "total_pln_current": round(total_pln),
            "rate": rate, "rate_date": get_setting("fx_EUR_date") or "", "period_days": period_days,
            "by_type": by_type, "by_owner": by_owner, "reasons": reasons,
            "response": {"median": _median(resp), "count": len(resp), "target": sales_rules.RESPONSE_TARGET_MIN,
                         "within": round(100 * sum(1 for r in resp if r <= sales_rules.RESPONSE_TARGET_MIN) / len(resp)) if resp else None},
            "cancels": cancels, "cancel_pln": sum(c["pln"] for c in cancels), "big_open": big,
            "day": today.day, "days_in_month": dim}


def lead_closed_on(lead):
    """Data zamknięcia leada (czas warszawski) albo None, gdy nie jest zamknięty."""
    if lead.stage not in CLOSED_STAGES:
        return None
    entry = _closing_entry(lead, [h for h in lead.stage_history if h.changed_at])
    return to_local(entry.changed_at) if entry else None


CLOSED_ON_ERRORS = {
    "stage": "datę zamknięcia ustawia się tylko leadom wygranym, zrealizowanym albo straconym",
    "future": "data zamknięcia nie może być w przyszłości",
    "invalid": "nieprawidłowa data zamknięcia",
}


def set_lead_closed_on(lead, day, actor_id=None):
    """Poprawia datę zamknięcia (np. lead wpisany do CRM później niż faktycznie wygrany) —
    od niej zależy miesiąc w Statystykach. Przesuwa w historii ostatnie wejście na obecny etap;
    wcześniejsze wpisy, które wypadłyby po nowej dacie, cofa tuż przed nią, żeby kolejność
    historii została. Zwraca kod błędu z CLOSED_ON_ERRORS albo None."""
    if lead.stage not in CLOSED_STAGES:
        return "stage"
    if day is None or day.year < 2020:
        return "invalid"
    if day > to_local(datetime.utcnow()).date():
        return "future"
    noon = datetime.combine(day, datetime.min.time()).replace(hour=12)  # 12:00 w Polsce
    new_dt = noon.replace(tzinfo=WARSAW_TZ).astimezone(timezone.utc).replace(tzinfo=None) if WARSAW_TZ else noon
    history = sorted(lead.stage_history, key=_history_order)
    entry = _closing_entry(lead, history)
    if entry is None:  # stary lead sprzed historii etapów
        entry = StageHistory(lead_id=lead.id, from_stage=None, to_stage=lead.stage, changed_by=actor_id)
        db.session.add(entry)
        earlier, later = history, []
    else:
        i = history.index(entry)
        earlier, later = history[:i], history[i + 1:]
    entry.changed_at = new_dt
    late = [h for h in earlier if h.changed_at and h.changed_at >= new_dt]
    for i, h in enumerate(late):
        h.changed_at = new_dt - timedelta(minutes=len(late) - i)
    # np. Wygrany → Zrealizowany: późniejsze wpisy nie mogą wypaść przed nową datą
    for i, h in enumerate(later):
        if h.changed_at is None or h.changed_at <= new_dt:
            h.changed_at = new_dt + timedelta(minutes=i + 1)
    return None


def build_stage_flow():
    """Przepływ leadów między etapami (bez cofnięć/cykli) do wykresu Sankey."""
    SOURCE = "Nowy lead"
    col_index = {SOURCE: 0, "Nowy": 1, "Oferta": 2, "Wygrany": 3, "Stracony": 3, "Zrealizowany": 4}

    edge_counts = {}
    for lead in Lead.query.filter(Lead.clickup_pending.isnot(True)).all():
        if not lead.stage_history:
            continue
        pointer, pointer_col = SOURCE, 0
        for h in lead.stage_history:  # relacja już posortowana po changed_at
            dst = h.to_stage
            # zakończenie (Wygrany/Stracony) liczy się wg obecnego etapu — niżej
            if dst not in col_index or dst in CLOSED_STAGES:
                continue
            dst_col = col_index[dst]
            if dst_col > pointer_col:  # liczy się tylko realny postęp — cofnięcia są pomijane
                key = (pointer, dst)
                edge_counts[key] = edge_counts.get(key, 0) + 1
                pointer, pointer_col = dst, dst_col
        if lead.stage in CLOSED_STAGES:
            # zrealizowany przeszedł przez Wygrany: ... → Wygrany → Zrealizowany
            end = "Wygrany" if lead.stage in WON_STAGES else lead.stage
            key = (pointer, end)
            edge_counts[key] = edge_counts.get(key, 0) + 1
            if lead.stage == "Zrealizowany":
                edge_counts[("Wygrany", "Zrealizowany")] = edge_counts.get(("Wygrany", "Zrealizowany"), 0) + 1

    node_ids = [SOURCE] + STAGES
    node_value = {n: 0 for n in node_ids}
    for (s, d), v in edge_counts.items():
        if s == SOURCE:
            node_value[s] += v
        node_value[d] += v

    node_colors = {SOURCE: "#6366f1", **STAGE_COLORS}
    nodes = [
        {"id": n, "label": ("Wszystkie leady" if n == SOURCE else n), "col": col_index[n],
         "value": node_value[n], "color": node_colors.get(n, "#6366f1")}
        for n in node_ids
    ]
    total = node_value[SOURCE] or 1
    links = [
        {"source": s, "target": d, "value": v, "pct": round(100 * v / total, 1)}
        for (s, d), v in edge_counts.items()
    ]
    return {"nodes": nodes, "links": links, "total": node_value[SOURCE]}
