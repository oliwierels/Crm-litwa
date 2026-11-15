# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("sprawy_z_poczty_sprawdzanie") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: sprawy z poczty: agent sprawdzający (Claude)

def verify_mail_tasks(tasks=None, force=False):
    """Agent przeszukuje pocztę (wszystkie skrzynki i wątki z klientem) i CRM przy każdej otwartej sprawie: gdy
    to już zrobione — zamyka ją z dowodem („zrobione — widać w poczcie”). Nic nie wysyła. Zwraca dict."""
    if _ai_client() is None:
        return {"info": "Brak klucza Claude'a — sprawdzanie spraw wyłączone", "closed": 0, "url": "/crm/poczta?sprawy=1"}
    if tasks is None:
        tasks = all_markets(MailTask.query.filter(MailTask.status == "open")).order_by(MailTask.created_at).all()
    now = datetime.utcnow()
    todo, closed = [], 0
    for t in tasks:
        if t.status != "open" or (t.done_how or "") == MAIL_TASK_REOPENED:
            continue
        proof = _task_done_proof(t)
        if proof:  # umowa / oferta już poszła po ostatnim mailu klienta — bez pytania Claude'a
            t.status, t.done_at, t.done_how, t.checked_at, t.check_note = "done", now, MAIL_TASK_DONE_HOW, now, proof
            closed += 1
            continue
        if not (force or _task_needs_check(t)):
            continue
        ev, after = mail_task_evidence(t)
        if not after:  # po prośbie klienta nic od nas nie wyszło i nic nie ma w CRM — na pewno otwarta
            t.checked_at, t.check_note = now, "Sprawdzone w poczcie i CRM: po prośbie klienta nic od nas nie wyszło."
            continue
        todo.append((t, ev))
    db.session.commit()
    checked, error = len(todo) + closed, None
    system = MAIL_TASK_CHECK_SYSTEM + f" Dziś jest {to_local(now):%Y-%m-%d %H:%M} (czas polski)."
    for start in range(0, len(todo), MAIL_TASK_CHECK_BATCH):
        chunk = todo[start:start + MAIL_TASK_CHECK_BATCH]
        text = "\n\n".join(
            f"### SPRAWA {i}: {t.title}\nCo trzeba zrobić: {t.action or '-'}\nDlaczego: {t.reason or '-'}\n"
            f"Klient: {t.counterpart} · temat „{t.subject or ''}” · jego prośba z {_dtl(_task_asked_at(t))}\n"
            f"MAILE Z KLIENTEM I CRM:\n{ev}" for i, (t, ev) in enumerate(chunk))
        try:
            res = _ai_forced_tool(MAIL_REVIEW_AI_MODEL, system, text, MAIL_TASK_CHECK_TOOL, max_tokens=4000)
        except Exception as e:
            db.session.rollback()
            app.logger.exception("verify_mail_tasks")
            error = f"{type(e).__name__}: {e}"[:300]
            break  # reszta przy następnym obiegu
        for t, _ev in chunk:
            t.checked_at = now
        for item in res.get("items") or []:
            try:
                idx = int(item.get("task"))
            except (TypeError, ValueError):
                continue
            if not 0 <= idx < len(chunk):
                continue
            t = chunk[idx][0]
            t.check_note = str(item.get("evidence") or "")[:2000]
            if item.get("done") is True and t.status == "open":
                t.status, t.done_at, t.done_how = "done", now, MAIL_TASK_DONE_HOW
                closed += 1
        db.session.commit()
    out = {"info": f"Sprawdzono w poczcie {checked} spraw — zamknięte jako już załatwione: {closed}",
           "closed": closed, "url": "/crm/poczta?sprawy=1"}
    if error:
        out["error"] = error  # dla strażnika CRM (Claude nie odpowiedział — reszta spraw przy następnym obiegu)
    return out


def mail_tasks_autoclosed(days=7):
    """Sprawy zamknięte przez agenta po sprawdzeniu poczty (z dowodem) — do podglądu i ewentualnego przywrócenia."""
    q = MailTask.query.filter(MailTask.status == "done", MailTask.done_how == MAIL_TASK_DONE_HOW,
                              MailTask.done_at >= datetime.utcnow() - timedelta(days=days))
    if hasattr(MailTask, "market"):
        q = all_markets(q.filter(MailTask.market == current_market()))
    return q.order_by(MailTask.done_at.desc()).all()
