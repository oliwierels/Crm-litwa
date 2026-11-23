# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("clickup_import") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: ClickUp: task → lead, pobieranie listy

def _clickup_task_to_lead(task):
    """Mapuje task z ClickUp na pola Lead. Custom fields dopasowane po nazwie (aliasy)."""
    fields = {
        "company": (task.get("name") or "Lead z ClickUp").strip(),
        "notes": (task.get("text_content") or "").strip(),
        "source": "ClickUp",
    }
    for cf in task.get("custom_fields", []) or []:
        cf_name = (cf.get("name") or "").strip().lower()
        cf_value = cf.get("value")
        if cf_value in (None, ""):
            continue
        for lead_field, aliases in CLICKUP_FIELD_ALIASES.items():
            if any(alias in cf_name for alias in aliases):
                if lead_field == "value_pln":
                    try:
                        fields[lead_field] = float(cf_value)
                    except (TypeError, ValueError):
                        pass
                else:
                    fields[lead_field] = str(cf_value).strip()
                break

    # awaryjnie: numer telefonu z nazwy/opisu taska, jeśli nie znaleziono w custom fields
    if "contact_phone" not in fields:
        phone = _extract_phone(f"{task.get('name','')} {task.get('text_content','')}")
        if phone:
            fields["contact_phone"] = phone

    # tytuł taska bywa imieniem i nazwiskiem zgłaszającego (np. formularz ClickUp ustawia
    # tytuł na dane zgłaszającego) — jeśli tak wygląda i nie mamy jeszcze osoby kontaktowej,
    # użyj go też jako contact_name (firma zostaje bez zmian, do poprawienia przy akceptacji)
    if "contact_name" not in fields and _looks_like_person_name(fields["company"]):
        fields["contact_name"] = fields["company"]

    # przypisanie po e-mailu asygnowanej osoby w ClickUp -> konto w CRM; bez niej lead dostaje domyślnego
    # opiekuna rynku (default_owner w market.py — CRM PL: Jerzy) przy tworzeniu leada
    assignee_emails = {(a.get("email") or "").lower() for a in task.get("assignees", []) or []}
    assigned_to = next((u["id"] for u in USERS if (u.get("email") or "").lower() in assignee_emails), None)
    if assigned_to:
        fields["assigned_to"] = assigned_to

    created = task.get("date_created")
    if created:
        try:
            fields["created_at"] = datetime.utcfromtimestamp(int(created) / 1000)
        except (TypeError, ValueError):
            pass

    # termin taska ustawiony na dzień zgłoszenia (formularz / automat w ClickUp) to nie data wydarzenia — zostaje puste
    due = task.get("due_date")
    if due and "event_date" not in fields:
        try:
            due_dt = datetime.utcfromtimestamp(int(due) / 1000)
            if not fields.get("created_at") or to_local(due_dt).date() != to_local(fields["created_at"]).date():
                fields["event_date"] = due_dt
        except (TypeError, ValueError):
            pass

    return fields


def _clickup_fetch_all_tasks(list_id, token=None):
    """Pobiera wszystkie taski z listy, stronicując (API ClickUp zwraca max 100 na stronę)."""
    tasks = []
    page = 0
    while True:
        data = _clickup_get(f"/list/{list_id}/task", params={"archived": "false", "page": page}, token=token)
        page_tasks = data.get("tasks", [])
        tasks.extend(page_tasks)
        if data.get("last_page") or not page_tasks:
            break
        page += 1
        if page > 20:  # zabezpieczenie przed nieskonczona petla
            break
    return tasks


_CLICKUP_REFRESH_FIELDS = (
    "company", "contact_name", "contact_phone", "contact_email", "robot_model",
    "value_pln", "location", "event_date", "created_at", "notes",
)
