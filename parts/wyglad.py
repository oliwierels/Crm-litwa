# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("wyglad") po opiekunie leada — działa w przestrzeni nazw crm.py.
# Temat: drobne pomocniki wyglądu dla szablonów (wspólny base.html dla obu CRM)
#
# - has_route("nazwa_widoku"): czy dany CRM ma tę stronę (menu pokazuje tylko istniejące, np. „Do obdzwonienia” w PL)
# - av_style(id): awatar osoby w jednym stylu w całym CRM


def has_route(endpoint):
    return endpoint in app.view_functions


def av_style(uid):
    """Styl kółka z inicjałem osoby (jasne tło w kolorze osoby, mocny tekst)."""
    color = USERS_BY_ID.get(uid, {}).get("color", "#94a3b8")
    return f"background:{color}1f;color:{color};box-shadow:inset 0 0 0 1px {color}40"


app.jinja_env.globals.update(has_route=has_route, av_style=av_style)
