"""Przełącznik języka PL / LT dla interfejsu CRM.

Szablony są te same co w polskim CRM (łatwo przenosić poprawki) — tłumaczymy gotową stronę:
teksty między znacznikami, podpowiedzi (title, placeholder…), komunikaty w JS i odpowiedzi JSON
(błędy, ostrzeżenia). Słownik PL → LT jest w lt_strings.py; czego w nim nie ma, zostaje po polsku.

Zasady bezpieczeństwa:
- treść <textarea> i wartości pól formularza nie są ruszane (zapis nie może zmienić danych),
- <option> bez value dostaje value z oryginalnym (polskim) tekstem, więc formularze wysyłają to samo,
- w JS tłumaczymy tylko napisy z co najmniej dwoma słowami (pojedyncze słowa bywają kluczami, np. 'Nowy'),
- pojedyncze słowa tłumaczymy tylko, gdy są całym tekstem elementu (np. przycisk „Zapisz”),
  dłuższe frazy także wewnątrz tekstu.
"""
import json
import re

from lt_strings import LT

LANGS = {"pl": "Polski", "lt": "Lietuvių"}
DEFAULT_LANG = "pl"

_LETTER = "A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźżĄČĘĖĮŠŲŪŽąčęėįšųūž"
_EDGE = re.compile(rf"^([^{_LETTER}]*)(.*?)([^{_LETTER}]*)$", re.S)


def _norm(s):
    return " ".join(s.split())


_EXACT = {_norm(k): v for k, v in LT.items() if _norm(k)}
_PHRASES = sorted((k for k in _EXACT if " " in k or len(k) >= 12), key=len, reverse=True)
_PHRASE_RE = re.compile(
    rf"(?<![{_LETTER}])(" + "|".join(re.escape(p).replace(r"\ ", r"\s+") for p in _PHRASES) + rf")(?![{_LETTER}])"
) if _PHRASES else None


def tr(text, phrases=True):
    """Tłumaczy jeden kawałek tekstu (z zachowaniem spacji, emoji i liczb na brzegach)."""
    if not text or not text.strip():
        return text
    lead_ws = text[: len(text) - len(text.lstrip())]
    trail_ws = text[len(text.rstrip()):]
    core = text.strip()
    hit = _EXACT.get(_norm(core))
    if hit is not None:
        return lead_ws + hit + trail_ws
    m = _EDGE.match(core)
    if m and m.group(2):
        hit = _EXACT.get(_norm(m.group(2)))
        if hit is not None:
            return lead_ws + m.group(1) + hit + m.group(3) + trail_ws
    if phrases and _PHRASE_RE is not None:
        return _PHRASE_RE.sub(lambda mm: _EXACT.get(_norm(mm.group(1)), mm.group(1)), text)
    return text


# --- HTML ---

_SKIP_BLOCK = re.compile(r"(<textarea\b.*?</textarea>|<style\b.*?</style>|<script\b.*?</script>)", re.S | re.I)
_TEXT_NODE = re.compile(r">([^<>]+)<")
_ATTR = re.compile(r'(\s(?:title|placeholder|aria-label|alt|data-confirm|data-title|data-label)=")([^"]*)(")', re.I)
_HANDLER = re.compile(r'(\son(?:click|submit|change)=")([^"]*)(")', re.I)
_OPTION = re.compile(r"<option\b([^>]*)>([^<]*)</option>", re.I)
_JS_STR = re.compile(r"""('(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`)""")


def _js_strings(code, quote_entities=False):
    def one(m):
        lit = m.group(1)
        q, body = lit[0], lit[1:-1]
        if " " not in body.strip() or not re.search(f"[{_LETTER}]{{3}}", body):
            return lit
        if q == "`":  # szablon z ${…}: tłumaczymy tylko kawałki tekstu między wstawkami
            parts = re.split(r"(\$\{[^}]*\})", body)
            return "`" + "".join(p if p.startswith("${") else tr(p) for p in parts) + "`"
        new = tr(body)
        if new == body:
            return lit
        new = new.replace("\\", "\\\\").replace(q, "\\" + q)
        return q + new + q
    if quote_entities:
        code = code.replace("&#39;", "'").replace("&#34;", '"').replace("&quot;", '"')
        out = _JS_STR.sub(one, code)
        return out.replace('"', "&#34;")
    return _JS_STR.sub(one, code)


def _option(m):
    attrs, label = m.group(1), m.group(2)
    new = tr(label, phrases=False)
    if new == label:
        return m.group(0)
    if not re.search(r"\svalue\s*=", attrs, re.I):
        attrs += f' value="{label.strip()}"'
    return f"<option{attrs}>{new}</option>"


def _html_chunk(s):
    s = _OPTION.sub(_option, s)
    s = _ATTR.sub(lambda m: m.group(1) + tr(m.group(2)) + m.group(3), s)
    s = _HANDLER.sub(lambda m: m.group(1) + _js_strings(m.group(2), quote_entities=True) + m.group(3), s)
    s = _TEXT_NODE.sub(lambda m: ">" + tr(m.group(1)) + "<", s)
    return s


def translate_html(html):
    out = []
    for part in _SKIP_BLOCK.split(html):
        low = part[:10].lower()
        if low.startswith("<script"):
            head_end = part.find(">") + 1
            if 'type="application/json"' in part[:head_end] or "src=" in part[:head_end]:
                out.append(part)
            else:
                out.append(part[:head_end] + _js_strings(part[head_end:]))
        elif low.startswith("<textarea"):
            head_end = part.find(">") + 1
            out.append(_html_chunk(part[:head_end]) + part[head_end:])
        elif low.startswith("<style"):
            out.append(part)
        else:
            out.append(_html_chunk(part))
    return "".join(out).replace('<html lang="pl">', '<html lang="lt">', 1)


# --- JSON ---

_JSON_KEYS = {"error", "warning", "next", "info", "message", "msg", "status_text", "label", "hint"}


def translate_json(data):
    if isinstance(data, dict):
        return {k: (tr(v) if isinstance(v, str) and k in _JSON_KEYS else translate_json(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [translate_json(v) for v in data]
    return data


def translate_json_bytes(raw):
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return json.dumps(translate_json(data), ensure_ascii=False)
