# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("dostarczalnosc") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: dostarczalność maili (SPF / DKIM / DMARC) i porządki w danych

DELIVERABILITY_DOMAINS = ("33bots.pl", "33bots.de", "33bots.lt")
DKIM_SELECTORS = ("default", "google", "selector1", "selector2", "k1", "mail", "dkim", "s1", "s2", "mx", "smtp", "x")
_dns_cache = {}


def dns_txt(name, rtype="TXT"):
    """Rekordy DNS przez DNS-over-HTTPS (dns.google) — tylko odczyt; wynik z pamięci na godzinę."""
    hit = _dns_cache.get((name, rtype))
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    import urllib.parse
    import urllib.request
    try:
        url = "https://dns.google/resolve?" + urllib.parse.urlencode({"name": name, "type": rtype})
        with urllib.request.urlopen(url, timeout=6) as r:
            data = json.loads(r.read().decode())
        out = [a.get("data", "").strip('"').replace('" "', "") for a in data.get("Answer", []) or []]
    except Exception as e:
        out = None if not isinstance(e, ValueError) else []
    _dns_cache[(name, rtype)] = (time.time(), out)
    return out


def deliverability_report():
    rows = []
    for d in DELIVERABILITY_DOMAINS:
        txt = dns_txt(d)
        spf = [t for t in (txt or []) if t.lower().startswith("v=spf1")]
        dmarc = [t for t in (dns_txt(f"_dmarc.{d}") or []) if t.lower().startswith("v=dmarc1")]
        dkim = []
        for sel in DKIM_SELECTORS:
            recs = dns_txt(f"{sel}._domainkey.{d}") or []
            if any("v=dkim1" in r.lower() or "p=" in r for r in recs):
                dkim.append(sel)
        mx = dns_txt(d, "MX") or []
        tips = []
        if txt is None:
            tips.append("Nie udało się odpytać DNS — spróbuj później.")
        else:
            if not spf:
                tips.append(f"Brak SPF: dodaj TXT dla {d}: „v=spf1 include:<serwer poczty> ~all” (dostawca poczty poda include).")
            elif len(spf) > 1:
                tips.append("Są dwa rekordy SPF — musi być jeden (połącz je), inaczej SPF nie działa.")
            elif "+all" in spf[0]:
                tips.append("SPF z „+all” pozwala każdemu wysyłać jako Wy — zmień na „~all” albo „-all”.")
            if not dmarc:
                tips.append(f"Brak DMARC: dodaj TXT dla _dmarc.{d}: „v=DMARC1; p=none; rua=mailto:kontakt@{d}” "
                            "(po 2–4 tygodniach zaostrz do p=quarantine).")
            elif "p=none" in dmarc[0].lower():
                tips.append("DMARC jest w trybie p=none (tylko raporty) — gdy raporty są czyste, przejdź na p=quarantine.")
            if not dkim:
                tips.append("Nie znaleziono DKIM pod typowymi selektorami — włącz podpis DKIM u dostawcy poczty i dodaj "
                            "rekord TXT <selektor>._domainkey." + d + " (bez DKIM duzi odbiorcy, np. Orange, częściej wrzucają do spamu).")
        rows.append({"domain": d, "spf": spf, "dmarc": dmarc, "dkim": dkim, "mx": mx, "tips": tips, "ok": txt is not None and not tips})
    return {"rows": rows, "smtp_host": os.environ.get("SMTP_HOST", "").strip() or "—"}


@app.route("/crm/dostarczalnosc")
def crm_deliverability():
    return render_template("crm/dostarczalnosc.html", r=deliverability_report())


def data_cleanup_report():
    """Porządki w danych — lista do RĘCZNEJ poprawy (nic nie zmienia): wygrane bez daty, absurdalne daty,
    wartość inna niż wysłana oferta, leady bez wartości, bardzo długie zakresy dni."""
    out = {"won_no_date": [], "bad_dates": [], "value_vs_offer": [], "no_value": [], "long_range": [], "won_no_type": []}
    for lead in Lead.query.filter(Lead.clickup_pending.isnot(True), own_leads()).all():
        days = [d.event_date for d in lead.event_days]
        bad = [d for d in days if not sane_event_date(datetime.combine(d, datetime.min.time()))]
        if lead.event_date and not sane_event_date(lead.event_date):
            bad.append(lead.event_date.date())
        if bad:
            out["bad_dates"].append({"lead": lead, "info": ", ".join(f"{d:%d.%m.%Y}" for d in sorted(set(bad))[:5])
                                     + (f" (+{len(set(bad)) - 5})" if len(set(bad)) > 5 else "")})
        good = sorted(d for d in days if d not in bad)
        if lead.stage in WON_STAGES and not good:
            out["won_no_date"].append({"lead": lead, "info": lead.stage})
        if lead.stage in WON_STAGES and not lead.client_type:
            out["won_no_type"].append({"lead": lead, "info": lead.stage})
        if good and (good[-1] - good[0]).days + 1 > sales_rules.LONG_RANGE_DAYS:
            out["long_range"].append({"lead": lead, "info": f"{good[0]:%d.%m.%Y} – {good[-1]:%d.%m.%Y} ({len(good)} dni)"})
        if lead.stage in ("Oferta", "Wygrany", "Zrealizowany") and not lead.value_pln:
            out["no_value"].append({"lead": lead, "info": lead.stage})
        if lead.stage in ("Oferta", "Wygrany", "Zrealizowany") and lead.value_pln:
            ov = last_sent_offer_value(lead)
            if ov and abs(float(lead.value_pln) - ov) > max(1.0, 0.01 * ov):
                out["value_vs_offer"].append({"lead": lead, "info": f"w leadzie {lead.value_pln:,.0f}, w ofercie {ov:,.0f}".replace(",", " ")})
            elif lead.notes and lead.value_pln:  # kwota z notatki (np. VULCAN: 6 000 przy ofercie 68 600 w notatce)
                amounts = [float(a.replace(" ", "").replace("\xa0", "").replace(",", ".")) for a in
                           re.findall(r"(\d{1,3}(?:[ \xa0]\d{3})+|\d{4,6})(?:[,.]\d{2})?\s*(?:zł|zl|pln|PLN|netto)", lead.notes)]
                if amounts and max(amounts) > 2 * float(lead.value_pln):
                    out["value_vs_offer"].append({"lead": lead, "info": f"w leadzie {lead.value_pln:,.0f}, w notatce {max(amounts):,.0f}".replace(",", " ")})
    return out


@app.route("/crm/porzadki")
def crm_data_cleanup():
    return render_template("crm/porzadki.html", r=data_cleanup_report(), labels={
        "won_no_date": "Wygrane — termin do ustalenia (wpisz datę, gdy klient ją poda)","bad_dates": "Daty spoza 2020–2041 (np. rok 0002)",
        "value_vs_offer": "Wartość inna niż w ofercie / notatce", "no_value": "Oferta / wygrana bez wartości",
        "long_range": "Zakres dni dłuższy niż 14 dni", "won_no_type": "Wygrane bez typu klienta"})


@app.route("/crm/leads/<int:lead_id>/contract/toggle", methods=["POST"])
def crm_toggle_contract(lead_id):
    lead = db.session.get(Lead, lead_id)
    if not lead:
        abort(404)
    signed = not lead.contract_signed
    if lead.stage in JOURNEY_STAGES:  # ten sam status co w Customer Journey
        j = ensure_journey(lead)
        journey_set_contract(j, lead, "podpisana" if signed else ("wyslana" if j.contract_sent_at else "do_wyslania"),
                             via="recznie")
        j.needs_review = False
    else:
        lead.contract_signed = signed
    db.session.commit()
    if request.is_json:
        return jsonify({"ok": True, "signed": lead.contract_signed})
    return redirect(url_for("crm_lead_detail", lead_id=lead_id))
