# Wygląd CRM (wspólny dla CRM PL i CRM LT/DE)

## Zasady

- **Firmowy ciemny motyw domyślnie** (barwy z ofert PDF 33bots: tło #0A0C10, panele #15171C, akcent #2F6FE0 / #7FA8F5,
  nagłówki Poppins), jasny jednym kliknięciem: ☀ / 🌙 na pasku u góry (albo w menu konta); wybór zostaje w przeglądarce.
- **Menu: jedna pozycja na sekcję** (Mój dzień, Leady, Poczta, Oferty, Customer Journey, Kalendarz, Logistyka, Kampanie,
  Zespół, Raporty, System); strony sekcji są zakładkami u góry strony (np. Leady: Tablica / Lista / Do obdzwonienia /
  Z ClickUp / Duplikaty).
- **Szukajka Ctrl+K** (albo „/”): leady po firmie, osobie, telefonie, mailu + strony + akcje.
- **Dzwonek** 🔔: przypomnienia, które nie blokują pracy (raport dnia, strażnik, „czy ktoś oddzwaniał?”, leady czekające
  u innych). Blokują tylko: nowy lead u jego opiekuna i brakujący raport dnia z poprzednich dni.
- **Wygrana** 🏆: przejście leada na „Wygrany” (tablica, nagłówek, edycja) pokazuje konfetti, deszcz monet i kwotę.
- **Statystyki użycia stron** (System → Stan CRM): każde otwarcie strony; strony z zerem po kilku tygodniach to kandydaci
  do usunięcia.
- **Kolory tylko ze zmiennych** w `templates/crm/base.html` (`--bg`, `--panel`, `--panel2`, `--border`, `--text`, `--muted`,
  `--accent`, `--green`, `--yellow`, `--red`, `--blue`, `--purple` i wersje `-soft` na tła). Nowe ekrany nie wpisują kolorów
  na sztywno — wtedy działają w obu motywach.
- **Bez zewnętrznych CDN**: ikony (Bootstrap Icons), czcionka (Inter) i kalendarz (FullCalendar) są w `static/`.
- **Jedno miejsce na tworzenie**: „Nowy lead” i „Zadanie” są na górze menu bocznego (klawisz N = nowy lead). Pływający
  przycisk w rogu to tylko asystent.
- **Zalogowana osoba** jest na dole menu. „Zmień osobę” wylogowuje (przy haśle CRM) — nie da się już kliknięciem udawać
  kogoś innego. Nowe zadanie domyślnie przypisuje się do zalogowanej osoby.

## Karta leada

1. **Nagłówek** (`_lead_header.html`): firma, ocena, źródło, aktywność; etap jako pasek (klik = zmiana etapu z bramkami);
   opiekun (zmiana przenosi jego otwarte zadania i zostawia komentarz „🔁 Opiekun: …”), kontakt, wartość, wydarzenie
   i następny krok. Szybkie akcje: Zadzwoń, Napisz, Oferta, Wyceń.
2. **Szeroka kolumna pracy**: dodaj zadanie → do zrobienia → komentarze → korespondencja → zrobione → historia etapów.
3. **Panel boczny**: szczegóły, proces sprzedaży, oferta z Claude, oferty, Customer Journey, dane do faktury (zwinięte).

## Alarmy

- Okno „nowy lead do obsłużenia dziś” widzi **opiekun leada** (lead bez opiekuna — każdy). W CRM PL nowe leady ma Jerzy,
  więc pozostałych nie blokują cudze leady.
- Paski (raport dnia, strażnik) mają jeden styl `.notice` (`notice-warn`, `notice-danger`).

## Wspólne pliki szablonów

Te pliki są identyczne w obu repozytoriach (różnice rynku robią warunki: `all_mkts|length > 1`, `has_route(...)`,
`ui_langs is defined`, `mkt.currency`): `base.html`, `_alerts.html`, `_callback.html`, `_raport_gate.html`, `_health.html`,
`_lead_header.html`, `dashboard.html`, `leads.html`, `login.html`. Tłumaczenia interfejsu LT: `lt_strings.py` (CRM LT/DE).
