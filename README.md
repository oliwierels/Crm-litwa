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

## WhatsApp — wysyłka ceny z CRM (oficjalne WhatsApp Cloud API)

1. business.facebook.com → konto Meta Business (najlepiej zweryfikowane).
2. developers.facebook.com → Create App → typ Business → dodaj produkt **WhatsApp** → podepnij **osobny numer**
   (nie może być jednocześnie w zwykłej aplikacji WhatsApp).
3. Business Settings → System Users → nowy użytkownik (Admin) → Generate token z uprawnieniami
   `whatsapp_business_messaging` i `whatsapp_business_management` → to jest **stały token**.
4. WhatsApp Manager → Message templates → **Create template**: kategoria Utility, nazwa `pasiulymas_kaina`,
   język **Lithuanian (lt)**, treść (dokładnie 4 pola):

```
Laba diena! Dėkojame, kad užpildėte formą. Humanoidinio roboto Unitree G1 nuoma jūsų renginiui {{1}} ({{2}}, {{3}}) kainuotų {{4}} € be PVM.

Į kainą įskaičiuota: atvykimas, asmeninis operatorius, roboto pritaikymas jūsų renginiui ir techninė pagalba vietoje.

Ar rezervuojame šią datą?
```
   Przykłady do zgłoszenia: {{1}} = 2026-12-12, {{2}} = 18:00–22:00, {{3}} = Šiaulių arena, {{4}} = 2 000.

5. Railway → Variables:

| Zmienna | Wartość |
|---|---|
| `WHATSAPP_TOKEN` | stały token z kroku 3 |
| `WHATSAPP_PHONE_ID` | Phone number ID (WhatsApp → API Setup) |
| `WHATSAPP_TEMPLATE` | `pasiulymas_kaina` |
| `WHATSAPP_VERIFY_TOKEN` | dowolne hasło, np. `33bots-wa-2026` |
| `WHATSAPP_APP_SECRET` | App secret (App settings → Basic) — sprawdzanie podpisu webhooka |

6. Webhook (żeby odpowiedzi klientów wpadały do CRM): WhatsApp → Configuration → Callback URL
   `https://<adres LT CRM>/whatsapp/webhook`, Verify token = `WHATSAPP_VERIFY_TOKEN`, subskrybuj pole **messages**.

Przy leadzie: „🚀 Wyślij przez WhatsApp” — pierwsza wiadomość idzie jako szablon; gdy klient odpisze, odpowiedź
trafia do komentarzy leada (+ zadanie „Odpowiedz…”), a przez 24 h CRM wysyła już zwykły tekst z okienka.
