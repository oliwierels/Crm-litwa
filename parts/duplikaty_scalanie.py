# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("duplikaty_scalanie") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: scalanie duplikatów i zapytania ze skrzynki kontakt@

def is_untouched(lead):
    """Kopia z automatycznego importu, którą można scalić bez pytania: nikt nad nią nie pracował.
    Leady dodane ręcznie nigdy nie są scalane same — tylko z listy Duplikaty."""
    from_import = bool(lead.clickup_task_id or lead.clickup_pending or lead.source in IMPORT_SOURCES)
    return (from_import and lead.stage == "Nowy" and not lead.value_pln and not lead.sent_offers
            and not lead.contract_requests and not lead.crews)


def merge_leads(keeper, dup, actor_id=None):
    """Przenosi wszystko z `dup` do `keeper` (komentarze, zadania, oferty, umowy, ekipy, maile,
    brakujące dane kontaktowe) i usuwa `dup`. Nie commituje."""
    for f in ("contact_name", "contact_email", "contact_email2", "contact_phone", "location", "robot_model", "rental_days",
              "value_pln", "source", "event_date", "event_end_date"):
        if not getattr(keeper, f) and getattr(dup, f):
            setattr(keeper, f, getattr(dup, f))
    for addr in dup.emails:  # adres duplikatu, którego główny lead jeszcze nie ma, trafia jako drugi
        if addr.lower() not in {e.lower() for e in keeper.emails} and not keeper.contact_email2:
            keeper.contact_email2 = addr
    if dup.notes and (dup.notes.strip() not in (keeper.notes or "")):
        keeper.notes = ((keeper.notes or "").rstrip() + f"\n\n[z duplikatu #{dup.id}] " + dup.notes.strip()).strip()
    if dup.clickup_task_id:
        if not keeper.clickup_task_id:
            keeper.clickup_task_id = dup.clickup_task_id
        else:  # żeby import z ClickUp nie dodał go znowu
            set_setting(f"clickup_dup:{dup.clickup_task_id}"[:64], str(keeper.id))
    moved = [Activity, Crew, ContractRequest, SentOffer, InboxMessage, CampaignRecipient, JourneyDocument, JourneyMail,
             MailTask]
    if not keeper.event_days:
        moved.append(LeadEventDate)
    if dup.journey and not keeper.journey:  # Customer Journey przechodzi razem z leadem
        Journey.query.filter_by(lead_id=dup.id).update({"lead_id": keeper.id}, synchronize_session=False)
    for model in moved:
        model.query.filter_by(lead_id=dup.id).update({"lead_id": keeper.id}, synchronize_session=False)
    added = to_local(dup.created_at)
    db.session.add(Activity(
        lead_id=keeper.id, kind="note", done=True, done_at=datetime.utcnow(), assigned_to=actor_id or keeper.assigned_to or 1,
        notes=f"🔗 Scalono duplikat #{dup.id} „{dup.company}” (źródło: {dup.source or '—'}, etap: {dup.stage}"
              f"{f', dodany {added:%d.%m %H:%M}' if added else ''}) — jego komentarze i zadania są teraz tutaj.",
    ))
    db.session.flush()
    db.session.expire(keeper)
    db.session.expire(dup)
    db.session.delete(dup)  # zostały w nim już tylko jego historia etapów i ewentualnie dni realizacji


def auto_merge_duplicates(actor_id=None):
    """Scala automatycznie tylko nietknięte kopie z importów (Nowy, bez kwoty, ofert, umów, ekip)
    z głównym leadem grupy. Zwraca (liczba scalonych, grupy do ręcznego przejrzenia)."""
    merged, review = 0, []
    for group in duplicate_groups():
        keeper, rest = group[0], group[1:]
        left = []
        for dup in rest:
            if is_untouched(dup):
                merge_leads(keeper, dup, actor_id)
                merged += 1
            else:
                left.append(dup)
        if left:
            review.append([keeper] + left)
    if merged:
        db.session.commit()
    return merged, review


def _inbox_note(rec, header):
    return (f"{header} ({rec.received_at:%d.%m %H:%M} UTC)\nOd: {rec.from_addr}\nTemat: {rec.subject}\n---\n"
            f"{(rec.body or '')[:4000]}")


def _inbox_accept_pending(lead):
    """Klient sam napisał na kontakt@, więc lead czekający w „Z ClickUp” jest prawdziwy — zatwierdzamy go,
    żeby zapytanie nie utknęło w poczekalni niewidocznej na Kanbanie i liście. Zwraca True, gdy zatwierdzono."""
    if not lead.clickup_pending:
        return False
    lead.clickup_pending = False
    db.session.add(StageHistory(lead_id=lead.id, from_stage=None, to_stage=lead.stage, changed_by=None))
    return True


def _inbox_refresh_lead(lead, res, contact_email, received_at=None):
    """Kolejne zapytanie od znanego klienta: uzupełnia leada o nowe dane z maila (nazwa firmy zamiast
    samego imienia, brakujący kontakt/miejsce, nowy termin gdy stary minął). Zwraca listę zmienionych pól."""
    inp, updated = {}, []
    company = str(res.get("company") or "").strip()
    placeholders = {(lead.contact_name or "").strip().lower(), (lead.contact_email or "").strip().lower(),
                    "", "nowe zapytanie"}
    if company and (lead.company or "").strip().lower() in placeholders \
            and company.lower() != (lead.company or "").strip().lower():
        inp["company"] = company[:200]
        updated.append("firma")
    for key, label, value in (("contact_name", "osoba", res.get("contact_name")),
                              ("contact_email", "e-mail", contact_email),
                              ("contact_phone", "telefon", res.get("contact_phone")),
                              ("location", "miejsce", res.get("location"))):
        if value and str(value).strip() and not (getattr(lead, key) or "").strip():
            inp[key] = str(value).strip()[:200]
            updated.append(label)
    if contact_email and EMAIL_RE.match(contact_email) and (lead.contact_email or "").strip() \
            and contact_email.lower() not in {e.lower() for e in lead.emails} and not lead.contact_email2:
        inp["contact_email2"] = contact_email[:200]  # klient napisał z innego adresu — zapamiętaj go jako drugi
        updated.append("drugi e-mail")
    if res.get("robot_model") in ROBOT_MODELS and not lead.robot_model:
        inp["robot_model"] = res["robot_model"]
        updated.append("robot")
    dates = [d for d in (res.get("event_dates") or []) if parse_date(str(d))]
    if dates and lead.stage in ("Nowy", "Oferta") \
            and not any(d.event_date >= date.today() for d in lead.event_days):
        inp["event_dates"] = dates
        updated.append("termin")
    summary = str(res.get("summary") or "").strip()
    if summary and summary not in (lead.notes or ""):
        when = f" {received_at:%d.%m}" if received_at else ""
        inp["notes"] = ((lead.notes or "").rstrip() + f"\n\n[zapytanie{when}] {summary}").strip()
        updated.append("notatka")
    if inp:
        _assistant_apply_lead_fields(lead, inp, USERS_BY_ID.get(lead.assigned_to) or USERS[0])
    return updated
