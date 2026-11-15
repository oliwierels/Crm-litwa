# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("sprawy_z_poczty") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: sprawy z poczty: sprawdzanie w poczcie i CRM, czy już załatwione

MAIL_TASK_DONE_HOW = "zrobione — widać w poczcie"
MAIL_TASK_REOPENED = "przywrócona ręcznie"
MAIL_TASK_LOOKBACK_DAYS = 10  # maile z klientem sprzed jego prośby — dla kontekstu
MAIL_TASK_CHECK_BATCH = 10
MAIL_TASK_RECHECK_HOURS = 24  # bez nowych maili / wpisów — ponowne sprawdzenie raz na dobę
MAIL_TASK_CHECK_TOOL = {
    "name": "mail_task_check",
    "description": "Czy sprawy z poczty są już załatwione.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "items": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "task": {"type": "integer", "description": "Numer sprawy z listy"},
                    "done": {"type": "boolean", "description": "true tylko gdy w mailach / CRM widać, że to już zrobione"},
                    "evidence": {"type": "string", "description": "Po polsku, krótko: co to potwierdza (data, skrzynka, "
                                                                  "temat maila albo wpis CRM) albo czego brakuje"},
                },
                "required": ["task", "done", "evidence"]}},
        },
        "required": ["items"],
    },
}
MAIL_TASK_CHECK_SYSTEM = (
    "Jesteś asystentem firmy 33Bots (wynajem humanoidalnego robota Unitree G1 na eventy). Dostajesz listę spraw "
    "z przeglądu poczty i przy każdej wszystkie maile z tym klientem (wszystkie skrzynki i wątki, także osobne maile "
    "z ofertą) oraz wpisy z CRM. Oceń, czy sprawa jest JUŻ ZAŁATWIONA. Załatwiona = po prośbie klienta widać, że "
    "zrobiliśmy to, czego dotyczy: „wyślij ofertę / wycenę” — jest nasz mail z ofertą lub ceną (temat, treść albo "
    "załącznik PDF z ofertą) albo wpis CRM o wysłanej ofercie; „wyślij umowę” — nasz mail z umową albo CRM: umowa "
    "wysłana / podpisana; „odpowiedz / odpisz” — wysłaliśmy odpowiedź na to, o co pytał (także w innym wątku); "
    "„umów spotkanie / zadzwoń” — termin ustalony albo rozmowa odnotowana w CRM. Prośba mogła być we wcześniejszym "
    "mailu klienta w tym wątku, a ostatni mail klienta to np. podziękowanie, potwierdzenie albo doprecyzowanie: "
    "nasz ruch po prośbie, choć przed ostatnim mailem klienta, też ją załatwia, jeśli ostatni mail klienta nie "
    "prosi o nic nowego. Liczy się też nasz mail z innej naszej skrzynki, w innym wątku albo do innej osoby z firmy "
    "klienta, a wpis CRM „📨 Umowa … wysłane do …” albo „umowa wysłana” to wysłana umowa. NIE jest załatwiona, gdy "
    "nie ma śladu, nasz mail nie odpowiada na prośbę albo klient po naszym ruchu poprosił o coś nowego, na co nie "
    "odpisaliśmy. Gdy nie masz pewności — done=false. Pisz po polsku."
)


def _dtl(value):
    return f"{to_local(value):%d.%m %H:%M}" if value else "bez daty"


def _task_addrs(t):
    """Adresy klienta sprawy (z pola rozmówcy i z maila, na który odpowiadamy) — bez naszych skrzynek."""
    own = _own_addresses()
    raw = f"{t.counterpart or ''} {t.mail.from_addr if t.mail is not None and t.mail.from_addr else ''}".lower()
    return {a for a in re.findall(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+", raw)
            if a not in own and a.rsplit("@", 1)[-1] not in OWN_DOMAINS}


def _task_lead(t, fix=False):
    """Lead sprawy: zapisany przy sprawie, jeśli nadal istnieje (i jest z rynku sprawy), inaczej szukany po adresie
    klienta. fix=True poprawia powiązanie w bazie (np. lead przeniesiony albo scalony)."""
    market = getattr(t, "market", None)
    same = lambda lead: market is None or getattr(lead, "market", market) == market  # noqa: E731
    stored = all_markets(Lead.query.filter(Lead.id == t.lead_id)).first() if t.lead_id else None
    if stored is not None and same(stored):
        return stored
    addrs = sorted(_task_addrs(t))
    cands = all_markets(Lead.query.filter(db.or_(db.func.lower(Lead.contact_email).in_(addrs),
                                                 db.func.lower(Lead.contact_email2).in_(addrs)))
                        ).order_by(Lead.created_at.desc()).all() if addrs else []
    lead = next((c for c in cands if same(c)), None) or stored or (cands[0] if cands else None)
    if fix and lead is not None and lead.id != t.lead_id:
        t.lead_id = lead.id
    return lead


def _mail_text_pl(m, limit=500):
    fresh = poczta.split_quote(m.body_text or "")[0] or ""
    pl = getattr(m, "body_pl", None)
    text = pl if pl and m.lang not in ("pl", None) else fresh
    return " ".join((text or "").split())[:limit]


def _task_asked_at(t):
    """Ostatni mail klienta w wątku sprawy (z niego przegląd zrobił sprawę)."""
    return (t.mail.date if t.mail is not None and t.mail.date else t.created_at) or datetime.utcnow()


# sprawa wyłącznie o umowę / ofertę — wtedy wysłana umowa / oferta zamyka ją bez pytania Claude'a
_TASK_CONTRACT_RE = re.compile(r"umow|kontrakt", re.I)
_TASK_OFFER_RE = re.compile(r"ofert|wycen", re.I)
_TASK_OTHER_RE = re.compile(r"faktur|zdję|zdjec|film|baz[aęy] wiedzy|instrukc|spotkan|zadzwo|telefon|\bpytan|"
                            r"\bodpowied|\bodpis|\bdopyt|potwierdz|rabat|zniżk|znizk|negocj", re.I)  # „do podpisu” ≠ „odpisz”
_ATT_CONTRACT_RE = re.compile(r"umow|contract|sutart|vertrag", re.I)
_ATT_OFFER_RE = re.compile(r"ofert|offer|pasiul|pasiūl|angebot|wycen", re.I)


def _att_names(m):
    try:
        return [a.get("name") for a in json.loads(m.attachments or "[]") if isinstance(a, dict) and a.get("name")]
    except (ValueError, TypeError):
        return []


def _task_domains(addrs):
    """Firmowe domeny klienta (bez gmaili itp.) — umowa mogła pójść do kogoś innego z tej samej firmy."""
    return {a.rsplit("@", 1)[-1] for a in addrs if "@" in a and not sales_rules.is_free_mail(a)
            and a.rsplit("@", 1)[-1] not in OWN_DOMAINS}


def _task_client_addrs(t, lead=None):
    lead = lead if lead is not None else _task_lead(t)
    return set(_task_addrs(t)) | ({e.lower() for e in lead.emails} if lead is not None else set())


def _task_sent_since(t, lead, since, company=True):
    """Nasze maile do klienta wysłane po `since` — z każdej naszej skrzynki; company=True także do innych osób
    z jego firmy (ta sama domena, bez gmaili itp.)."""
    addrs = _task_client_addrs(t, lead)
    conds = [MailMessage.addrs.like(f"% {a} %") for a in sorted(addrs)]
    if company:
        conds += [MailMessage.addrs.like(f"%@{d} %") for d in sorted(_task_domains(addrs))]
    if not conds:
        return []
    return all_markets(MailMessage.query.filter(MailMessage.folder == "sent", MailMessage.date > since,
                                                db.or_(*conds))).order_by(MailMessage.date).all()


def _task_done_proof(t, lead=None):
    """Twardy dowód bez Claude'a: sprawa jest wyłącznie o wysłanie umowy albo oferty, a PO ostatnim mailu klienta
    umowa / oferta poszła (z CRM albo mailem z załącznikiem na adres klienta, z dowolnej naszej skrzynki).
    Opis dowodu albo None."""
    what = f"{t.title or ''} {t.action or ''}"
    contract, offer = bool(_TASK_CONTRACT_RE.search(what)), bool(_TASK_OFFER_RE.search(what))
    if contract == offer or _TASK_OTHER_RE.search(what):
        return None  # nie wiadomo, o co chodzi, albo sprawa ma więcej części — decyduje Claude
    asked = _task_asked_at(t)
    lead = lead if lead is not None else _task_lead(t)
    if lead is not None:
        if contract:
            j = lead.journey
            if j is not None and j.contract_sent_at and j.contract_sent_at > asked:
                return f"CRM: umowa wysłana {_dtl(j.contract_sent_at)} (Customer Journey)."
            doc = (all_markets(JourneyDocument.query.filter(
                JourneyDocument.lead_id == lead.id, JourneyDocument.kind == "umowa_wyslana",
                JourneyDocument.received_at > asked)).order_by(JourneyDocument.received_at).first())
            if doc is not None:
                return f"CRM: umowa wysłana {_dtl(doc.received_at)} — {doc.filename} ({doc.source or 'dokumenty leada'})."
        else:
            o = (all_markets(SentOffer.query.filter(SentOffer.lead_id == lead.id, SentOffer.sent_at > asked))
                 .order_by(SentOffer.sent_at).first())
            if o is not None:
                return f"CRM: oferta wysłana z CRM {_dtl(o.sent_at)} do {o.to_addr or 'klienta'}."
    att_re = _ATT_CONTRACT_RE if contract else _ATT_OFFER_RE
    # tylko na adres klienta: mail do kogoś innego z jego firmy (np. agencji z kilkoma eventami) ocenia Claude
    for m in _task_sent_since(t, lead, asked, company=False):
        hit = next((n for n in _att_names(m) if att_re.search(n)), None)
        if hit:
            return f"Mail {_dtl(m.date)} z {m.mailbox} „{m.subject or ''}” — załącznik {hit}."
    return None


def mail_task_evidence(t):
    """Materiał do sprawdzenia sprawy: maile z klientem we wszystkich skrzynkach i wątkach (od kilku dni przed jego
    prośbą do dziś; także nasze maile do innych osób z jego firmy) i wpisy w CRM leada.
    Zwraca (tekst, czy po prośbie klienta coś od nas wyszło / jest wpis). Prośba mogła być we wcześniejszym mailu
    wątku niż ostatni mail klienta (np. potem tylko „dziękuję”) — liczymy od pierwszego maila klienta w wątku."""
    own = _own_addresses()
    asked = _task_asked_at(t)
    lead = _task_lead(t)
    addrs = _task_client_addrs(t, lead)
    window = asked - timedelta(days=MAIL_TASK_LOOKBACK_DAYS)
    msgs = {}
    q = mail_query_for_addrs(sorted(addrs)) if addrs else None
    if q is not None:
        for m in all_markets(q.filter(MailMessage.date >= window)).all():
            msgs[m.id] = m
    if t.thread_key:
        for m in all_markets(MailMessage.query.filter(MailMessage.thread_key == t.thread_key)).all():
            msgs[m.id] = m
    for m in _task_sent_since(t, lead, window):
        msgs[m.id] = m
    is_ours = lambda m: m.folder == "sent" or (m.from_addr or "").lower() in own  # noqa: E731
    first_ask = [m.date for m in msgs.values() if t.thread_key and m.thread_key == t.thread_key and not is_ours(m)
                 and m.date and window <= m.date <= asked]
    since = min(first_ask + [asked])
    lines, after = [], False
    for m in sorted(msgs.values(), key=lambda m: m.date or datetime.min)[-16:]:
        ours = is_ours(m)
        when = "PO prośbie" if m.date and m.date > asked else (
            "po pierwszej prośbie w wątku, przed ostatnim mailem klienta" if m.date and m.date > since else "przed prośbą")
        if m.id == t.mail_id:
            when = "OSTATNI MAIL KLIENTA — z niego sprawa"
        after = after or bool(ours and m.date and m.date > since)
        att = _att_names(m)
        lines.append(f"- [{'MY → klient' if ours else 'klient → my'} · {_dtl(m.date)} · {m.mailbox} · {when}] "
                     f"„{m.subject or ''}”" + (f" · załączniki: {', '.join(att)[:200]}" if att else "")
                     + f"\n  {_mail_text_pl(m)}")
    if not lines:
        lines.append("- brak maili z tym klientem w archiwum Poczty")
    if lead is not None:
        lines.append(f"CRM: lead „{lead.company}” · etap {lead.stage}")
        for o in all_markets(SentOffer.query.filter(SentOffer.lead_id == lead.id, SentOffer.sent_at.isnot(None))).all():
            lines.append(f"- CRM: oferta wysłana z CRM {_dtl(o.sent_at)} ({o.price or ''}) do {o.to_addr or ''}")
            after = after or o.sent_at > since
        j = lead.journey
        if j is not None:
            lines.append(f"- CRM: umowa {j.contract_status}"
                         + (f" (wysłana {_dtl(j.contract_sent_at)})" if j.contract_sent_at else "")
                         + f", baza wiedzy {j.kb_status}")
            after = after or bool(j.contract_sent_at and j.contract_sent_at > since)
        for d in all_markets(JourneyDocument.query.filter(JourneyDocument.lead_id == lead.id,
                                                          JourneyDocument.kind == "umowa_wyslana",
                                                          JourneyDocument.received_at > since)).all():
            lines.append(f"- CRM: umowa wysłana {_dtl(d.received_at)} — {d.filename} ({d.source or ''})")
            after = True
        acts = [a for a in lead.activities if a.done and (a.done_at or a.created_at)
                and (a.done_at or a.created_at) > since - timedelta(days=1)]
        for a in sorted(acts, key=lambda a: a.done_at or a.created_at)[-10:]:
            when = a.done_at or a.created_at
            first = " ".join((a.notes or "").split("\n")[0].split())[:160]
            lines.append(f"- CRM {_dtl(when)}: [{a.kind}{'/' + a.outcome if a.outcome else ''}] {first}")
            after = after or when > since
    return "\n".join(lines), after


def _task_needs_check(t):
    """Sprawdzamy: nową sprawę, sprawę z nowymi mailami / wpisami od ostatniego sprawdzenia i raz na dobę."""
    if (t.done_how or "") == MAIL_TASK_REOPENED:
        return False  # przywrócona ręcznie — człowiek zdecydował
    if not t.checked_at or t.checked_at < datetime.utcnow() - timedelta(hours=MAIL_TASK_RECHECK_HOURS):
        return True
    lead = _task_lead(t)
    addrs = sorted(_task_client_addrs(t, lead))
    q = mail_query_for_addrs(addrs) if addrs else None
    if q is not None and all_markets(q.filter(MailMessage.created_at > t.checked_at)).first() is not None:
        return True
    recent = _task_sent_since(t, lead, t.checked_at - timedelta(days=2))
    if any((m.created_at or m.date or datetime.min) > t.checked_at for m in recent):
        return True  # nasz mail do kogoś innego z firmy klienta (pobrany ze skrzynki po ostatnim sprawdzeniu)
    if lead is None:
        return False
    if all_markets(SentOffer.query.filter(SentOffer.lead_id == lead.id, SentOffer.sent_at > t.checked_at)).first():
        return True
    if lead.journey is not None and lead.journey.updated_at and lead.journey.updated_at > t.checked_at:
        return True  # zmiana w Customer Journey (np. umowa wysłana)
    if all_markets(JourneyDocument.query.filter(JourneyDocument.lead_id == lead.id,
                                                JourneyDocument.received_at > t.checked_at)).first() is not None:
        return True
    return any(a.done and (a.done_at or a.created_at or datetime.min) > t.checked_at for a in lead.activities)
