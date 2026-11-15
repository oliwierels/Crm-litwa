# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("logistyka_plan") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: logistyka: plan ekip, tras i robotów

class _Locator:
    """Opis miejsca -> (współrzędne, nazwa). Kolejno: wbudowana lista miast, zapamiętane
    wyniki, OpenStreetMap (z limitem zapytań na jedno wyświetlenie, żeby strona nie mulila)."""

    def __init__(self, remote_budget=3):
        self.remote_budget = remote_budget
        self.pending = set()
        self._memo = {}

    def __call__(self, text):
        text = (text or "").strip()
        if not text:
            return None, ""
        if text not in self._memo:
            self._memo[text] = self._resolve(text)
        return self._memo[text]

    def _resolve(self, text):
        m = logistics.match_city(text)
        if m:
            return m[1], logistics.city_label(m[0])
        q = logistics.geocode_query(text)
        key = logistics.normalize(q)[:200]
        if not key:
            return None, ""
        row = GeoCache.query.filter_by(place_key=key).first()
        if row and row.lat is not None:
            return (row.lat, row.lon), row.label or q
        if row and row.checked_at and datetime.utcnow() - row.checked_at < timedelta(days=GEO_RETRY_DAYS):
            return None, ""
        if self.remote_budget <= 0:
            self.pending.add(text)
            return None, ""
        self.remote_budget -= 1
        status, hit = _nominatim_lookup(q)
        if status == "error":
            return None, ""  # awaria sieci — nie zapamiętuj, spróbujemy przy następnym wejściu
        try:
            if row is None:
                row = GeoCache(place_key=key)
                db.session.add(row)
            row.checked_at = datetime.utcnow()
            row.lat, row.lon, row.label = hit if hit else (None, None, None)
            db.session.commit()
        except Exception:
            db.session.rollback()
        return ((hit[0], hit[1]), hit[2]) if hit else (None, "")


def _fmt_d(d):
    return d.strftime("%d.%m")


def build_logistics(d_from, d_to, include_tentative=False, remote_budget=3):
    """Cały plan logistyki w zakresie dat: dni realizacji z ekipami, trasy operatorów,
    ostrzeżenia i sugestie. Wspólne dla strony Logistyka i asystenta."""
    today = date.today()
    stages = list(WON_STAGES) + (LOGISTICS_TENTATIVE_STAGES if include_tentative else [])
    locate = _Locator(remote_budget)
    operators = Operator.query.order_by(Operator.active.desc(), Operator.name).all()
    robots = RobotUnit.query.order_by(RobotUnit.model, RobotUnit.name).all()
    ops_by_id = {o.id: o for o in operators}

    # dzień przed i po zakresie też się liczy do tras: jeśli dzień przed pierwszym oglądanym
    # operator jest w Poznaniu, to stamtąd jedzie dalej, a nie z bazy
    ctx_from, ctx_to = d_from - timedelta(days=1), d_to + timedelta(days=1)
    event_rows = (
        LeadEventDate.query.join(Lead, LeadEventDate.lead_id == Lead.id)
        .filter(LeadEventDate.event_date >= ctx_from, LeadEventDate.event_date <= ctx_to,
                Lead.stage.in_(stages), Lead.clickup_pending.isnot(True))
        .order_by(LeadEventDate.event_date, Lead.company).all()
    )
    crews_by_day = {}
    for c in Crew.query.filter(Crew.event_date >= ctx_from, Crew.event_date <= ctx_to).order_by(Crew.id).all():
        crews_by_day.setdefault((c.lead_id, c.event_date), []).append(c)

    # bazy operatorów
    op_info = {}
    for o in operators:
        btext = (o.base_city or "").strip() or LOGISTICS_DEFAULT_BASE
        bpoint, blabel = locate(btext)
        op_info[o.id] = {"point": bpoint, "label": blabel or btext, "user_id": operator_user_id(o)}

    def rides_along(c):
        """Ekipa podwójna: 2. operator jedzie autem 1. operatora, gdy mają bazy blisko siebie.
        Wtedy kilometry liczą się raz (u kierowcy), a 2. operator jest tylko zajęty tego dnia."""
        if not c.is_double or c.operator_id not in ops_by_id or c.operator2_id not in ops_by_id \
                or c.operator_id == c.operator2_id:
            return False
        a, b = op_info[c.operator_id]["point"], op_info[c.operator2_id]["point"]
        return a is not None and b is not None and logistics.road_km(a, b) <= logistics.CARPOOL_KM

    days, crew_views, stops_by_op, unassigned = [], [], {}, []
    rides, passenger_days, busy_by_day = {}, {}, {}
    event_days_in_range = 0
    for ed in event_rows:
        lead = ed.lead
        in_range = d_from <= ed.event_date <= d_to
        place_text = ed.location or lead.location or ""
        point, label = locate(place_text)
        ev = {
            "lead": lead, "date": ed.event_date, "place_text": place_text, "point": point, "label": label,
            "need": robot_need(lead.robot_model), "tentative": lead.stage not in WON_STAGES, "crews": [],
        }
        for c in crews_by_day.get((lead.id, ed.event_date), []):
            if c.city:
                cpoint, clabel = locate(c.city)
                ctext = c.city
            else:
                cpoint, clabel, ctext = point, label, place_text
            place = clabel or ctext or lead.company
            busy_by_day.setdefault(ed.event_date, set()).update(c.operator_ids())
            carpool = rides_along(c)
            # miejsce 1 = kierowca; miejsce 2 (ekipa podwójna) jedzie z nim albo, gdy mieszka
            # daleko, własnym autem — wtedy ma własny stop z kluczem "<id ekipy>/2"
            for slot, op_id in c.slot_operator_ids():
                stop = {"key": c.id if slot == 1 else f"{c.id}/2", "date": ed.event_date, "point": cpoint,
                        "place": place, "title": f"{lead.company} ({place})", "ctx": not in_range}
                if slot == 1 and carpool:
                    stop["with"] = ops_by_id[c.operator2_id].name
                if slot == 2 and carpool:
                    passenger_days.setdefault(op_id, set()).add(ed.event_date)
                    if in_range:
                        rides.setdefault(op_id, []).append(
                            {"date": ed.event_date, "place": place, "company": lead.company,
                             "driver": ops_by_id[c.operator_id].name})
                elif op_id in ops_by_id:
                    stops_by_op.setdefault(op_id, []).append(stop)
                elif slot == 1 and in_range:
                    unassigned.append(stop)
            if in_range:
                cv = {"crew": c, "point": cpoint, "label": clabel, "place_text": ctext, "event": ev,
                      "carpool": carpool}
                ev["crews"].append(cv)
                crew_views.append(cv)
        if not in_range:
            continue
        event_days_in_range += 1
        if not ev["crews"]:
            place = label or place_text or lead.company
            unassigned.append({
                "key": f"new:{lead.id}:{ed.event_date.isoformat()}", "date": ed.event_date, "point": point,
                "place": place, "title": f"{lead.company} ({place})",
            })
        if not days or days[-1]["date"] != ed.event_date:
            days.append({"date": ed.event_date, "weekday": PL_WEEKDAYS[ed.event_date.weekday()], "events": []})
        days[-1]["events"].append(ev)

    warnings = []

    def warn(level, d, text):
        if d is None or d >= today:
            warnings.append({"level": level, "date": d, "text": text})

    for day in days:
        for ev in day["events"]:
            company = ev["lead"].company
            if not ev["crews"] or any(not cv["crew"].operator_id for cv in ev["crews"]):
                warn("danger", ev["date"], f"{_fmt_d(ev['date'])} {company}: brak operatora")
            # nieznane miejsce z leada przeszkadza tylko ekipom bez własnego miasta
            uses_lead_place = not ev["crews"] or any(not cv["crew"].city for cv in ev["crews"])
            if ev["point"] is None and uses_lead_place and ev["place_text"] not in locate.pending:
                if ev["place_text"]:
                    warn("warn", ev["date"], f"{_fmt_d(ev['date'])} {company}: nie umiem znaleźć miejsca "
                         f"„{ev['place_text']}” — wpisz miasto w polu „Miasto” ekipy, żeby policzyć trasę")
                else:
                    warn("warn", ev["date"], f"{_fmt_d(ev['date'])} {company}: brak lokalizacji w leadzie — "
                         "trasa nie policzona (wpisz miasto w polu „Miasto” ekipy)")
            for cv in ev["crews"]:
                c = cv["crew"]
                if c.city and cv["point"] is None and c.city not in locate.pending:
                    warn("warn", ev["date"], f"{_fmt_d(ev['date'])} {company}: nie umiem znaleźć miasta „{c.city}”")
                if c.is_double:
                    if c.operator_id and not c.operator2_id:
                        free = [o.name for o in operators if o.active and o.id not in busy_by_day.get(ev["date"], ())]
                        tail = f" — wolni tego dnia: {', '.join(free[:4])}" if free else " — nikt inny nie jest wolny tego dnia"
                        warn("danger", ev["date"], f"{_fmt_d(ev['date'])} {company}: ekipa na 2 roboty — brak 2. operatora{tail}")
                    if c.operator_id and c.operator_id == c.operator2_id:
                        warn("danger", ev["date"], f"{_fmt_d(ev['date'])} {company}: {c.operator.name} wpisany "
                             "w ekipie dwa razy — wybierz innego 2. operatora")
                    if c.robot_id and c.robot_id == c.robot2_id:
                        warn("danger", ev["date"], f"{_fmt_d(ev['date'])} {company}: robot {c.robot.name} wpisany "
                             "w ekipie dwa razy — wybierz drugiego robota")
                for op in {o.id: o for o in c.operators_list()}.values():
                    if not op.active:
                        warn("warn", ev["date"], f"{_fmt_d(ev['date'])} {company}: operator {op.name} ma status „nieaktywny”")
                for r in {r.id: r for r in c.robots_list()}.values():
                    if r.model not in ev["need"]:
                        warn("warn", ev["date"], f"{_fmt_d(ev['date'])} {company}: przypisany {r.name} "
                             f"({ROBOT_KINDS.get(r.model, r.model)}), a klient chce "
                             f"{ev['lead'].robot_model or 'Unitree G1'}")
                    if not r.active:
                        warn("warn", ev["date"], f"{_fmt_d(ev['date'])} {company}: robot {r.name} jest wyłączony z floty")

    def far_apart(views):
        pts = [v["point"] for v in views]
        if len({v["event"]["lead"].id for v in views}) > 1 and any(p is None for p in pts):
            return None  # różne eventy, nie wiadomo gdzie — traktuj jak konflikt
        best = 0.0
        for i, a in enumerate(pts):
            for b in pts[i + 1:]:
                if a and b:
                    best = max(best, logistics.road_km(a, b))
        return best

    def describe(views):
        return ", ".join(f"{v['event']['lead'].company} ({v['label'] or v['place_text'] or '?'})" for v in views)

    robots_by_id = {r.id: r for r in robots}
    by_op_day, by_robot_day = {}, {}
    for cv in crew_views:
        c = cv["crew"]
        for op_id in set(c.operator_ids()):
            by_op_day.setdefault((op_id, c.event_date), []).append(cv)
        for rid in set(c.robot_ids()):
            by_robot_day.setdefault((rid, c.event_date), []).append(cv)
    for (op_id, d), views in sorted(by_op_day.items(), key=lambda x: x[0][1]):
        if len(views) < 2:
            continue
        dist = far_apart(views)
        if dist is None or dist > logistics.SAME_PLACE_KM:
            extra = f" — ≈{logistics.fmt_km(dist)} km od siebie" if dist else ""
            warn("danger", d, f"{_fmt_d(d)}: {ops_by_id[op_id].name} ma {len(views)} realizacje naraz: {describe(views)}{extra}")
    for (rid, d), views in sorted(by_robot_day.items(), key=lambda x: x[0][1]):
        if len(views) < 2:
            continue
        dist = far_apart(views)
        if dist is None or dist > logistics.SAME_PLACE_KM:
            warn("danger", d, f"{_fmt_d(d)}: robot {robots_by_id[rid].name} jest w {len(views)} miejscach naraz: {describe(views)}")

    # czy starczy robotów we flocie
    fleet = {k: sum(1 for r in robots if r.active and r.model == k) for k in ROBOT_KINDS}
    if any(fleet.values()):
        for day in days:
            need = {k: 0 for k in ROBOT_KINDS}
            tentative_involved = False
            for ev in day["events"]:
                crews = [cv["crew"] for cv in ev["crews"]]
                if len(ev["need"]) == 1:
                    # ekipa podwójna wiezie 2 roboty
                    need[ev["need"][0]] += max(1, sum(2 if c.is_double else 1 for c in crews))
                else:
                    # G1 + piesek: każda ekipa bierze po jednym z każdego
                    for k in ev["need"]:
                        need[k] += max(1, len(crews))
                tentative_involved = tentative_involved or ev["tentative"]
            for k, n in need.items():
                if n > fleet[k]:
                    tail = " (licząc też niepotwierdzone)" if tentative_involved else ""
                    warn("danger", day["date"], f"{_fmt_d(day['date'])}: potrzeba {n}× {ROBOT_KINDS[k]}, "
                         f"a we flocie jest {fleet[k]}{tail}")

    # robot przekazywany z dnia na dzień między operatorami daleko od siebie
    def crew_names(c):
        return " + ".join(o.name for o in c.operators_list()) or "—"

    by_robot = {}
    for cv in crew_views:
        for rid in set(cv["crew"].robot_ids()):
            by_robot.setdefault(rid, []).append(cv)
    for rid, views in by_robot.items():
        views.sort(key=lambda v: v["crew"].event_date)
        for a, b in zip(views, views[1:]):
            ca, cb = a["crew"], b["crew"]
            # ten sam operator jedzie dalej z robotem — nie ma przekazania
            if (cb.event_date - ca.event_date).days != 1 or set(ca.operator_ids()) & set(cb.operator_ids()) \
                    or ca.operator_ids() == cb.operator_ids():
                continue
            if a["point"] and b["point"]:
                dist = logistics.road_km(a["point"], b["point"])
                if dist > logistics.HANDOVER_KM:
                    warn("warn", cb.event_date, f"{_fmt_d(ca.event_date)}→{_fmt_d(cb.event_date)}: robot {robots_by_id[rid].name} "
                         f"zmienia ekipę: {crew_names(ca)} ({a['label']}) → "
                         f"{crew_names(cb)} ({b['label']}), ≈{logistics.fmt_km(dist)} km przewozu")

    # trasy operatorów
    routes = []
    total_km = total_hours = 0.0
    total_nights = 0
    for o in operators:
        stops = stops_by_op.get(o.id, [])
        my_rides = sorted(rides.get(o.id, []), key=lambda r: r["date"])
        count = sum(1 for s in stops if not s["ctx"])
        if not count and not my_rides:
            continue
        info = op_info[o.id]
        if not count:
            # tylko jako 2. operator w cudzym aucie — bez własnych kilometrów
            routes.append({"op": o, "base_label": info["label"], "count": len(my_rides), "trips": [],
                           "km": 0.0, "hours": 0.0, "nights": 0, "rides": my_rides})
            continue
        if info["point"] is None:
            routes.append({"op": o, "base_label": info["label"], "unknown_base": True,
                           "count": count + len(my_rides), "rides": my_rides})
            warn("warn", None, f"{o.name}: nie umiem znaleźć bazy „{info['label']}” — popraw w sekcji Operatorzy")
            continue
        # wyjazdy, które mają choć jeden dzień w oglądanym zakresie (w całości, z dojazdem z dnia przed)
        trips = [t for t in logistics.route_plan(info["point"], info["label"], stops)["trips"]
                 if t["end"] >= d_from and t["start"] <= d_to]
        for t in trips:
            t["with"] = sorted({s["with"] for s in stops if s.get("with") and t["start"] <= s["date"] <= t["end"]})
        km = sum(t["km"] for t in trips)
        nights = sum(t["nights"] for t in trips)
        routes.append({"op": o, "base_label": info["label"], "count": count + len(my_rides), "trips": trips,
                       "km": km, "hours": logistics.drive_hours(km), "nights": nights, "rides": my_rides})
        total_km += km
        total_hours += logistics.drive_hours(km)
        total_nights += nights
        for trip in trips:
            first = trip["legs"][0]
            when = first.get("date", trip["start"])
            if first.get("km", 0) > logistics.LONG_DRIVE_KM and when >= d_from:
                warn("info", when, f"{o.name}: {_fmt_d(when)} dojazd {first['from']} → {first['to']} "
                     f"≈{logistics.fmt_km(first['km'])} km ({logistics.fmt_hours(logistics.drive_hours(first['km']))}) — "
                     "rozważ nocleg dzień wcześniej")
        for hint in logistics.gap_stay_hints(info["point"], stops, o.name):
            if d_from <= hint["date"] <= d_to:
                warn("info", hint["date"], hint["text"])

    active_ops = {o.id: {"name": o.name, "point": op_info[o.id]["point"]}
                  for o in operators if o.active and op_info[o.id]["point"]}
    suggestions = logistics.find_suggestions(
        active_ops, {k: v for k, v in stops_by_op.items() if k in active_ops}, unassigned, today,
        extra_busy=passenger_days,
    ) if active_ops else []

    if locate.pending:
        warn("info", None, f"Ustalam położenie jeszcze {len(locate.pending)} miejsc — odśwież stronę za chwilę")

    order = {"danger": 0, "warn": 1, "info": 2}
    warnings.sort(key=lambda w: (order[w["level"]], w["date"] or date.min))
    return {
        "days": days, "operators": operators, "robots": robots, "op_info": op_info,
        "routes": routes, "warnings": warnings, "suggestions": suggestions, "fleet": fleet,
        "stats": {
            "event_days": event_days_in_range,
            # brakujący operatorzy, łącznie z pustym 2. miejscem w ekipach podwójnych
            "unassigned": sum(1 for s in unassigned if s["date"] >= today) + sum(
                1 for cv in crew_views if cv["crew"].is_double and not cv["crew"].operator2_id
                and cv["crew"].event_date >= today),
            "km": total_km, "hours": total_hours, "nights": total_nights,
        },
    }


def _logistics_range():
    today = date.today()
    d_from = parse_date(request.args.get("od"))
    d_to = parse_date(request.args.get("do"))
    d_from = d_from.date() if d_from else today
    d_to = d_to.date() if d_to else d_from + timedelta(days=30)
    if d_to < d_from:
        d_from, d_to = d_to, d_from
    if (d_to - d_from).days > LOGISTICS_MAX_RANGE_DAYS:
        d_to = d_from + timedelta(days=LOGISTICS_MAX_RANGE_DAYS)
    return d_from, d_to
