# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("duplikaty") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: duplikaty leadów i uzupełnianie kroków

@app.route("/crm/duplikaty")
def crm_duplicates():
    groups = duplicate_groups()
    return render_template(
        "crm/duplicates.html", groups=groups, untouched=is_untouched, window=DUP_WINDOW_DAYS,
        msg=request.args.get("msg", "")[:200], stage_colors=STAGE_COLORS, to_local=to_local,
    )


@app.route("/crm/duplikaty/scal", methods=["POST"])
def crm_duplicates_merge():
    """Scal wybrane leady z jednej grupy w jeden (wybrany główny)."""
    keeper = db.session.get(Lead, request.form.get("keeper", type=int) or 0)
    ids = {int(x) for x in request.form.getlist("merge") if x.isdigit()}
    if not keeper:
        abort(404)
    group = next((g for g in duplicate_groups() if keeper in g), [])
    dups = [l for l in group if l.id in ids and l.id != keeper.id]  # tylko z tej samej grupy
    for dup in dups:
        merge_leads(keeper, dup, get_current_user(request)["id"])
    db.session.commit()
    return redirect(url_for("crm_duplicates", msg=f"Scalono {len(dups)} w #{keeper.id} {keeper.company}"))


@app.route("/crm/duplikaty/auto", methods=["POST"])
def crm_duplicates_auto():
    merged, review = auto_merge_duplicates(get_current_user(request)["id"])
    return redirect(url_for("crm_duplicates", msg=f"Scalono automatycznie: {merged}. Do przejrzenia ręcznie: {len(review)}"))


@app.route("/crm/kroki/uzupelnij", methods=["POST"])
def crm_backfill_steps():
    """Panel „Bez następnego kroku” na dashboardzie: telefon dla wszystkich aktywnych leadów bez zadania."""
    backfill_next_steps()
    return redirect(url_for("crm_dashboard"))
