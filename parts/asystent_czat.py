# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("asystent_czat") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: asystent ✨ w CRM (czat)

@app.route("/crm/assistant/chat", methods=["POST"])
def crm_assistant_chat():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({
            "reply": "Asystent nie jest jeszcze skonfigurowany. Dodaj w Railway (web → Variables) zmienną "
                     "ANTHROPIC_API_KEY z kluczem z console.anthropic.com → API Keys.",
            "actions": [],
        })
    import anthropic

    data = request.get_json(silent=True) or {}
    msgs = []
    for m in (data.get("messages") or [])[-20:]:
        role = m.get("role")
        content = str(m.get("content") or "")[:4000]
        if role in ("user", "assistant") and content.strip():
            msgs.append({"role": role, "content": content})
    if not msgs or msgs[-1]["role"] != "user":
        abort(400)

    user = get_current_user(request)
    client = anthropic.Anthropic()
    actions = []
    try:
        response = None
        for _ in range(ASSISTANT_MAX_TURNS):
            response = client.messages.create(
                model=ASSISTANT_MODEL,
                max_tokens=4096,
                system=_assistant_system_prompt(user),
                tools=ASSISTANT_TOOLS,
                messages=msgs,
            )
            if response.stop_reason != "tool_use":
                break
            msgs.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type == "tool_use":
                    try:
                        out, action = _assistant_execute(block.name, dict(block.input or {}), user)
                    except Exception as e:  # błąd jednego narzędzia nie ubija całej rozmowy
                        db.session.rollback()
                        out, action = f'{{"error": "wyjątek: {type(e).__name__}"}}', None
                    if action:
                        actions.append(action)
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
            msgs.append({"role": "user", "content": results})
        reply = "".join(b.text for b in (response.content if response else []) if b.type == "text").strip()
        if not reply:
            reply = "Zrobione." if actions else "Nie mam odpowiedzi — spróbuj sformułować inaczej."
        return jsonify({"reply": reply, "actions": actions})
    except anthropic.AuthenticationError:
        return jsonify({"reply": "Klucz ANTHROPIC_API_KEY jest nieprawidłowy — sprawdź go w Railway.", "actions": actions})
    except anthropic.RateLimitError:
        return jsonify({"reply": "Limit zapytań do Claude'a chwilowo wyczerpany — spróbuj za minutę.", "actions": actions})
    except anthropic.APIStatusError as e:
        return jsonify({"reply": f"Błąd API Claude'a ({e.status_code}) — spróbuj ponownie.", "actions": actions})
    except anthropic.APIConnectionError:
        return jsonify({"reply": "Brak połączenia z API Claude'a — spróbuj ponownie.", "actions": actions})


# ---------- AUTOMATYZACJA: zapytania z kontakt@ → leady, oferty pisane przez Claude z poziomu CRM ----------
# Obie funkcje używają API Claude'a (ANTHROPIC_API_KEY, płatne za użycie, osobno od abonamentu claude.ai).
# Długie wywołania idą w tle (zadania), żeby nie blokować jedynego workera gunicorna.

INBOX_AI_MODEL = os.environ.get("INBOX_AI_MODEL", "claude-haiku-4-5-20251001")
OFFER_AI_MODEL = os.environ.get("OFFER_AI_MODEL", "claude-sonnet-5-5")
