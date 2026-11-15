# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("journey_dokumenty") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: Customer Journey: zamykanie, pomijanie, PDF z dokumentów

def _journey_maybe_close(j):
    if journey.after_complete(_jjson(j.after)):
        if not j.closed:
            j.closed, j.closed_at = True, datetime.utcnow()
    else:
        j.closed, j.closed_at = False, None


def journey_skip(j, kind):
    """„Pomiń” — przypomnienie uznane za załatwione (np. zadzwoniliśmy); następne za kilka dni."""
    now = datetime.utcnow()
    if kind == "umowa_przypomnienie":
        j.contract_reminded_at, j.contract_reminders = now, (j.contract_reminders or 0) + 1
    elif kind == "baza_przypomnienie":
        j.kb_reminded_at, j.kb_reminders = now, (j.kb_reminders or 0) + 1
    elif kind == "baza":
        journey_set_kb(j, "wyslana", when=now)
    elif kind == "opinia":
        after = _jjson(j.after)
        after["opinia"] = True
        j.after = json.dumps(after)
        _journey_maybe_close(j)
    else:
        return False
    j.updated_at = now
    _journey_badge_reset()
    return True


def _journey_convert(doc):
    if doc.pdf or not doc.data:
        return
    doc.pdf_tries = (doc.pdf_tries or 0) + 1
    doc.pdf = journey.to_pdf(doc.filename, doc.data)


def _journey_convert_async(doc_id):
    """Konwersja do PDF w tle (LibreOffice potrafi myśleć kilka sekund)."""
    def run():
        with app.app_context():
            try:
                doc = db.session.get(JourneyDocument, doc_id)
                if doc:
                    _journey_convert(doc)
                    db.session.commit()
            except Exception:
                db.session.rollback()
            finally:
                db.session.remove()
    threading.Thread(target=run, daemon=True).start()


def journey_convert_pending(limit=10):
    docs = (JourneyDocument.query.filter(JourneyDocument.pdf.is_(None), JourneyDocument.data.isnot(None),
                                         db.func.coalesce(JourneyDocument.pdf_tries, 0) < 3)
            .order_by(JourneyDocument.id).limit(limit).all())
    for doc in docs:
        _journey_convert(doc)
        db.session.commit()
    return len(docs)


def journey_add_document(lead, kind, filename, data, mime, source, user_id=None, mail=None, status="ok",
                         seen=True, received_at=None, convert_now=False):
    doc = JourneyDocument(lead_id=lead.id, kind=kind if kind in DOC_KINDS else "inny",
                          filename=journey.safe_filename(filename), mime=(mime or "application/octet-stream")[:120],
                          data=data, source=(source or "")[:200], mail=mail, status=status, seen=seen,
                          created_by=user_id, received_at=received_at or datetime.utcnow())
    if convert_now:
        _journey_convert(doc)
    db.session.add(doc)
    _journey_badge_reset()
    return doc
