# 33bots LT CRM

CRM na Litwę — kopia polskiego CRM (Moja-apka) 1:1, z interfejsem po polsku **i** po litewsku
(przełącznik 🇵🇱 PL / 🇱🇹 LT w lewym dolnym rogu, zapamiętany na urządzeniu).

Co jest inne niż w polskim CRM:
- **EUR** domyślnie (wartości leadów, kalkulator, statystyki, umowy, faktury),
- **maile do klientów po litewsku**: follow-up, oferta z Claude (mail + PDF), umowa (DOCX po litewsku,
  kwota słownie po litewsku), instrukcja bazy wiedzy (PDF po litewsku), przypomnienia, prośba o opinię,
  faktura, kampanie,
- każdy nowy lead dostaje automatycznie litewskiego maila z linkiem do krótkiego formularza
  (z o.latek@33bots.lt — `SMTP_PASS_LT`),
- umowa i faktura: odwrotne obciążenie VAT (0%, faktura „np” w inFakt) — klient z Litwy to firma z UE,
- **przeniesienie z polskiego CRM**: zakładka „Z polskiego CRM” przenosi litewskie leady razem z historią.

Tłumaczenie interfejsu: `i18n.py` (silnik) + `lt_strings.py` (słownik PL → LT). Szablony są takie same
jak w polskim CRM, więc poprawki z Moja-apka da się przenosić 1:1. Brakujący tekst zostaje po polsku —
wystarczy dopisać parę do `lt_strings.py`.

## Railway — nowy serwis

1. New Project → Deploy from GitHub repo → `oliwierels/crm-lt`, potem **Create → Database → PostgreSQL**.
2. Variables serwisu CRM:

| Zmienna | Wartość |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (Add Reference) |
| `SECRET_KEY` | dowolny długi losowy ciąg |
| `CRM_PASSWORD` | hasło do CRM |
| `PUBLIC_URL` | adres tego CRM, np. `https://crm-lt.up.railway.app` (linki w mailach do klientów) |
| `SMTP_HOST`, `SMTP_PORT` | serwer poczty cyber_Folks (465 albo 587) |
| `SMTP_USER_LT`, `SMTP_PASS_LT` | `o.latek@33bots.lt` + hasło — auto-mail z formularzem |
| `SMTP_USER_OLIWIER`, `SMTP_PASS_OLIWIER` | `o.latek@33bots.lt` + hasło — oferty i follow-upy Oliwiera |
| `SMTP_PASS_BARTEK` (+ `SMTP_USER_BARTEK`) | skrzynka Bartka, jeśli ma wysyłać z CRM |
| `INBOX_IMAP_USER`, `INBOX_IMAP_PASS` | `kontakt@33bots.lt` + hasło — zapytania z maila wpadają jako leady |
| `ANTHROPIC_API_KEY` | ten sam klucz co w polskim CRM |
| `CLICKUP_API_TOKEN`, `CLICKUP_LIST_ID` | osobna lista ClickUp dla Litwy (opcjonalnie) |
| `INFAKT_API_KEY` | ten sam co w polskim CRM (faktury z tej samej JDG) |
| `PL_DATABASE_URL` | adres bazy **polskiego** CRM — do przeniesienia litewskich leadów (patrz niżej) |
| `CRM_DEFAULT_LANG` | `pl` (domyślnie) albo `lt` — język dla nowych urządzeń |
| `CALC_BASE_8H`, `CALC_KM_RATE`, `CALC_LODGING`, `CALC_ROUND_TO` | cennik kalkulatora w EUR (domyślnie 1300 / 0,35 / 70 / 50) |

3. Settings → Networking → Generate Domain.

## Przeniesienie litewskich leadów z polskiego CRM

`PL_DATABASE_URL`: jeśli polski CRM jest w innym projekcie Railway, skopiuj z jego Postgresa
**DATABASE_PUBLIC_URL** (zakładka Variables bazy). Potem w LT CRM: menu Rinkodara/Marketing →
**Z polskiego CRM** → „Przenieś teraz”. Lead jest najpierw zapisywany tutaj (z zadaniami, komentarzami,
historią etapów, ofertami, Customer Journey i dokumentami), dopiero potem usuwany z polskiego CRM.
Po pierwszym przeniesieniu nowe litewskie leady z polskiego CRM przenoszą się same co 10 minut.
