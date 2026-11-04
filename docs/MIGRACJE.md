# Migracje bazy — proces sprzedaży (P1–P3)

Migracje wykonuje `ensure_schema()` przy starcie aplikacji. Dodają **tylko nowe kolumny** (nic nie usuwają, nie
nadpisują danych leadów). Stare leady mają w nowych polach `NULL` i nie są blokowane wstecz — bramki działają
dopiero przy zmianie etapu albo wysyłce oferty.

## P1 — bramki etapów

| Tabela | Kolumna | Typ | Do czego |
|---|---|---|---|
| leads | client_type | VARCHAR(20) | typ klienta: firma / prywatna / agencja / partner |
| leads | country | VARCHAR(4) | kraj (PL, LT, DE, RO…); pusty = podpowiedź z danych leada |
| leads | lost_reason | VARCHAR(30) | powód straty |
| leads | lost_comment | TEXT | komentarz do powodu („inne”) |
| leads | win_confirmation | VARCHAR(20) | podstawa wygranej: umowa / mail |
| leads | call_answers | TEXT (JSON) | odpowiedzi z telefonu potwierdzającego |
| leads | response_alert_at | TIMESTAMP | kiedy zgłoszono przekroczony czas reakcji (P2) |
| activities | outcome | VARCHAR(20) | wynik kontaktu: odebral / nie_odebral / odpisal / bez_odpowiedzi |
| stage_history | reason, note | VARCHAR(30), TEXT | powód / podstawa przy tej zmianie etapu |

Cofnięcie (PostgreSQL; najpierw wdrożyć poprzednią wersję kodu):

```sql
ALTER TABLE leads DROP COLUMN client_type, DROP COLUMN country, DROP COLUMN lost_reason, DROP COLUMN lost_comment,
  DROP COLUMN win_confirmation, DROP COLUMN call_answers, DROP COLUMN response_alert_at;
ALTER TABLE activities DROP COLUMN outcome;
ALTER TABLE stage_history DROP COLUMN reason, DROP COLUMN note;
```

Awaryjne wyłączenie bramek bez cofania kodu: zmienna `STAGE_GATES=0` w Railway.

## P2 i P3

Bez nowych kolumn poza powyższymi — nowe wskaźniki liczą się z istniejących danych (historia etapów, zadania,
`handled_at`, oferty). Ustawienia (cele, kurs EUR) są w tabeli `app_settings` (klucze `sales_targets`, `fx_EUR`);
cofnięcie: `DELETE FROM app_settings WHERE key IN ('sales_targets', 'fx_EUR', 'fx_EUR_date');`.
