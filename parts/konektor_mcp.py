# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("konektor_mcp") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: konektor MCP (czat Claude ↔ CRM)

_MCP_PROTOCOL_FALLBACK = "2025-06-18"


def _mcp_tools_payload():
    return [
        {"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]}
        for t in ASSISTANT_TOOLS
    ]


@app.route("/mcp/<secret>/<username>", methods=["POST", "GET", "DELETE"])
def mcp_endpoint(secret, username):
    configured = os.environ.get("MCP_SECRET", "").strip()
    # bez skonfigurowanego sekretu endpoint nie istnieje; porównanie stałoczasowe
    if not configured or not secrets.compare_digest(secret, configured):
        abort(404)
    user = next((u for u in USERS if u["name"].lower() == username.lower()), None)
    if not user:
        abort(404)
    if request.method != "POST":
        return "", 405

    payload = request.get_json(silent=True)
    if payload is None:
        abort(400)

    def handle(req):
        method = req.get("method")
        rid = req.get("id")
        params = req.get("params") or {}
        if rid is None:  # notyfikacja (np. notifications/initialized) — bez odpowiedzi
            return None
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": params.get("protocolVersion") or _MCP_PROTOCOL_FALLBACK,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "roborent-crm", "version": "1.0.0"},
            }}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": rid, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": _mcp_tools_payload()}}
        if method == "tools/call":
            name = str(params.get("name") or "")
            args = params.get("arguments") or {}
            try:
                out, _action = _assistant_execute(name, dict(args), user)
                is_error = False
            except Exception as e:
                db.session.rollback()
                out = f'{{"error": "wyjątek: {type(e).__name__}"}}'
                is_error = True
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": out}], "isError": is_error,
            }}
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"nieznana metoda: {method}"}}

    if isinstance(payload, list):
        replies = [r for r in (handle(x) for x in payload if isinstance(x, dict)) if r]
        return (jsonify(replies), 200) if replies else ("", 202)
    reply = handle(payload)
    if reply is None:
        return "", 202
    return jsonify(reply)
