# 33bots CRM — Litwa 🇱🇹 i Niemcy 🇩🇪

Jeden CRM dla dwóch rynków — kopia polskiego CRM (Moja-apka), z interfejsem po polsku **i** po litewsku
(przełącznik 🇵🇱 PL / 🇱🇹 LT w lewym dolnym rogu, zapamiętany na urządzeniu).

**Przełącznik rynku** (🇱🇹 Litwa / 🇩🇪 Niemcy) jest na górze menu — jeden link, dwa CRM-y. Każdy lead ma swój
rynek: widok pokazuje tylko leady oglądanego rynku (z ich zadaniami, ofertami, umowami, mailami, kampaniami
i skrzynką zapytań). Link do leada z drugiego rynku sam przełącza widok. Wszystko, co idzie do klienta, jest
**w języku rynku leada** — po litewsku albo po niemiecku, nigdy po angielsku: maile, oferta (mail + PDF;
CRM odrzuca ofertę w innym języku i Claude musi ją przepisać), umowa DOCX, instrukcja bazy wiedzy (PDF),
formularz dla klienta, wycena, wiadomość WhatsApp/Viber, follow-upy, przypomnienia, faktura, kampanie.
Teksty i ustawienia rynków są w `market.py`.

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

## 🚨 Nowe leady — obsłużyć tego samego dnia

Każdy nowy lead (osobno dla Litwy i Niemiec) wyskakuje w oknie, które zasłania CRM na każdej stronie, aż zostanie
obsłużony: z okna można otworzyć leada, kliknąć „📞 Zadzwoniłem”, „📵 Nie odebrał → mail” albo „✉️ Mail z prośbą
o dane” (Claude pisze maila w języku klienta, Ty sprawdzasz i wysyłasz), „📄 Oferta z Claude” albo „✅ Obsłużony”.
Lead znika też sam, gdy z CRM pójdzie do niego mail, oferta, wycena albo WhatsApp, albo gdy zadanie „telefon” jest
odhaczone. Okno można odłożyć najwyżej na 10 minut; nowy lead = sygnał dźwiękowy, licznik w tytule karty
i (po włączeniu) powiadomienie przeglądarki. `LEAD_ALERT_DAYS` — z ilu dni wstecz brać leady przy pierwszym
uruchomieniu (domyślnie 2).

## 📋 Przegląd poczty przez Claude'a

Codziennie o `MAIL_REVIEW_HOURS` (domyślnie 12 i 15, czas polski) Claude przegląda odebrane i wysłane ze wszystkich
skrzynek rynku (ostatnie `MAIL_REVIEW_DAYS`, domyślnie 14 dni) i wypisuje sprawy, które czekają na nas:
nieodpisane maile, prośby o umowę / spotkanie / ofertę, przypomnienia klientów, obietnice bez pokrycia.
Od `MAIL_TASKS_SHOW_FROM` (domyślnie 16:00) sprawy pokazują się w tym samym oknie co nowe leady. „✍️ Odpowiedz
z Claude” otwiera odpowiedź z gotowym szkicem; wysłana odpowiedź (także z Outlooka) zamyka sprawę.
Lista i przycisk „🔍 Przejrzyj pocztę teraz” są też w Poczcie. Model: `MAIL_REVIEW_AI_MODEL`
(domyślnie `claude-opus-5-5`), `MAIL_REVIEW=0` wyłącza przegląd.

## Poczta: ✨ Claude napisze maila

W okienku odpowiedzi / nowego maila: wpisz polecenie (np. „odpisz, że 12 grudnia jest wolny, cena jak w CRM”) albo wklej
starego maila — Claude pisze maila po polsku (lewa strona) i od razu w języku klienta (prawa). Widzi maila, na który
odpowiadasz, wątek i dane leada (cenę z kalkulatora, daty, miejsce). Nic nie wychodzi bez kliknięcia „Wyślij”.
Wersję w złym języku CRM odrzuca i Claude pisze ją jeszcze raz. Model: `MAIL_REPLY_AI_MODEL` (domyślnie `claude-opus-5-5`).

## Niemcy 🇩🇪 (ten sam serwis, te same zmienne + kilka nowych)

| Zmienna | Wartość |
|---|---|
| `MAIL_PASSWORD` | wspólne hasło skrzynek — wystarczy ono dla o.latek@ i kontakt@ w 33bots.lt **i** 33bots.de (Poczta, wysyłka, import zapytań) |
| `MAILBOXES_DE` | (opcjonalnie) inna lista skrzynek DE po przecinku — domyślnie `o.latek@33bots.de,kontakt@33bots.de` |
| `INBOX_IMAP_USER_DE`, `INBOX_IMAP_PASS_DE` | (opcjonalnie) inna skrzynka zapytań DE — domyślnie kontakt@33bots.de z `MAIL_PASSWORD` |
| `CALC_BASE_8H_DE`, `CALC_KM_RATE_DE`, `CALC_LODGING_DE` | cennik DE (domyślnie 2500 € / 0,75 € / 120 €) — km od granicy Frankfurt/Oder albo Görlitz (bliższa) |
| `WHATSAPP_TEMPLATE_DE` (+ `WHATSAPP_TEMPLATE_LANG_DE`) | zatwierdzony szablon WhatsApp po niemiecku (4 pola: data, godziny, miejsce, cena) |
| `NOTIFY_EMAILS_DE` | (opcjonalnie) gdzie wysłać powiadomienie o wypełnionym formularzu DE — domyślnie o.latek@33bots.de |

**Niemcy: nic nie wychodzi do klienta automatycznie** (w kodzie, niezależnie od zmiennych). Nowy lead DE dostaje
w CRM gotową cenę, wygenerowany mail z wyceną po niemiecku (do poprawienia w leadzie i wysłania jednym kliknięciem)
oraz wiadomość na WhatsApp/Viber; oferta z Claude też czeka na sprawdzenie w „Oferty”. Automat (mail od razu
po wpadnięciu leada i po wypełnieniu formularza) działa tylko dla Litwy.

Nadawca maila do klienta: skrzynka opiekuna leada w domenie rynku (o.latek@33bots.de), a gdy osoba jej nie ma —
kontakt@ rynku podpisana jej imieniem. Umowa DE: osobny wzór (Customer Journey → ustawienia, przy włączonym
rynku Niemcy), prawo niemieckie, kwota słownie po niemiecku, reverse charge.

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

## Automatyczna wycena mailem (nowe leady — tylko Litwa)

Nowy lead z godzinami i miejscem w zapytaniu (np. „iki 4 valandų”, „Miejsce wydarzenia – Šiaulių arena”) od razu
dostaje mail z ceną (kalkulator: od granicy, nocleg, zaokrąglenie do setek od 25 €) — ze skrzynki 33bots.lt
opiekuna leada. Lead przechodzi na „Oferta”, wartość = cena, a drugim krokiem jest follow-up mailem za 3 dni.
Brak godzin albo miejsca → jak dotąd mail z prośbą o dane (formularz); po wypełnieniu formularza wycena wychodzi sama.

| Zmienna | Znaczenie |
|---|---|
| `SMTP_USER_OLIWIER` + `SMTP_PASS_OLIWIER` | `o.latek@33bots.lt` — wycena od Oliwiera |
| `SMTP_USER_BARTEK` + `SMTP_PASS_BARTEK` | `b.wysocki@33bots.lt` — wycena od Bartosza (tylko adres .lt jest brany) |
| `LT_QUOTE_SENDER` | `oliwier` albo `bartek` — zawsze ta osoba (domyślnie opiekun leada) |
| `LT_AUTO_QUOTE` | `0` wyłącza automatyczną wycenę (zostaje mail z formularzem) |
