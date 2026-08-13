# Automatyczna Sekretarka AI — kancelarie notarialne

FastAPI backend obsługujący webhooki z Vapi (rozmowy głosowe przez Zadarma SIP)
dla 2-3 kancelarii notarialnych, jeden tenant = jeden Vapi Assistant + jeden
numer Zadarma. Cała logika multi-tenancy, idempotency i ekstrakcji danych
z rozmów żyje w tym repo — bez Make/Zapier/n8n.

## Stack

FastAPI (async) · SQLAlchemy 2.x + asyncpg · PostgreSQL · Alembic · Pydantic v2
· OpenAI Structured Outputs (ekstrakcja danych z transkryptu) · Google Calendar
API (Calendar Tools: sprawdzanie dostępności i rezerwacja spotkań) · Docker
Compose

## Uruchomienie lokalne

```bash
cp .env.example .env
# uzupełnij VAPI_WEBHOOK_SECRET / VAPI_WEBHOOK_HMAC_SECRET, OPENAI_API_KEY

docker compose up --build
```

`GET http://localhost:8000/health` powinno zwrócić `{"status": "ok"}`.

Migracje bazy:

```bash
docker compose exec app alembic upgrade head
```

## Testy

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"     # Windows
.venv/bin/pip install -e ".[dev]"         # Linux/macOS

python -m pytest tests/ -v
```

Testy używają SQLite w pamięci (przez `httpx.ASGITransport` + dependency
override na `get_db`) i mockowanego klienta OpenAI — nie wymagają
uruchomionego Postgresa ani realnego kluczy API.

## Dodanie nowego tenanta (kancelarii)

1. **Zadarma**: wykup/skonfiguruj numer SIP dla kancelarii, zapisz dane
   uwierzytelniające (login/hasło SIP) jako zmienne środowiskowe, np.
   `ZADARMA_TENANT_<NAZWA>_SIP_USERNAME` / `_SIP_PASSWORD`.

2. **Vapi — credential BYO SIP Trunk**:

   ```bash
   curl -X POST https://api.vapi.ai/credential \
     -H "Authorization: Bearer $VAPI_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "provider": "byo-sip-trunk",
       "name": "zadarma-kancelaria-a",
       "gateways": [{ "ip": "<ZADARMA_SIP_IP>", "inboundEnabled": true }],
       "outboundAuthenticationPlan": {
         "authUsername": "<SIP_USERNAME>",
         "authPassword": "<SIP_PASSWORD>"
       },
       "outboundLeadingPlusEnabled": true
     }'
   ```

   Jeśli organizacja Vapi jest hostowana w UE, używaj `https://api.eu.vapi.ai`
   oraz `sip.eu.vapi.ai` konsekwentnie (RODO / lokalizacja danych klientów).

3. **Vapi — numer telefonu (BYO phone number)**:

   ```bash
   curl -X POST https://api.vapi.ai/phone-number \
     -H "Authorization: Bearer $VAPI_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "provider": "byo-phone-number",
       "name": "kancelaria-a",
       "number": "+48XXXXXXXXX",
       "credentialId": "<credential.id z kroku 2>",
       "numberE164CheckEnabled": true
     }'
   ```

4. **Vapi — Assistant**: utwórz asystenta w dashboardzie (lub przez API),
   dobierz transcriber i TTS z obsługą języka polskiego (np. Deepgram
   Nova-2/Nova-3 z `language: "multi"`, lub Google STT multilingual).
   Ustaw **Server URL na poziomie Assistant** (nie organizacji) na:

   ```
   https://<twoja-domena>/webhooks/vapi
   ```

   Skonfiguruj też uwierzytelnianie webhooka — patrz sekcja niżej.

5. **Baza danych**: dodaj wiersz w `tenants`:

   ```sql
   INSERT INTO tenants (id, name, vapi_assistant_id, vapi_phone_number_id, active)
   VALUES (gen_random_uuid(), 'Kancelaria A', '<assistant.id>', '<phoneNumber.id>', true);
   ```

## Uwierzytelnianie webhooka

`app/security/webhook.py::verify_vapi_webhook` wspiera dwa mechanizmy —
wybór jest automatyczny na podstawie tego, która zmienna env jest ustawiona
(HMAC ma pierwszeństwo):

- **HMAC (zalecane)** — ustaw `VAPI_WEBHOOK_HMAC_SECRET` i skonfiguruj w
  Vapi credential typu HMAC dla danego Server Config. Nazwa nagłówka
  podpisu (`HMAC_SIGNATURE_HEADER` w `webhook.py`, domyślnie
  `x-vapi-signature`) — **zweryfikuj w dashboardzie Vapi przy pierwszym
  realnym podłączeniu** i skoryguj stałą, jeśli się różni.
- **Legacy static secret** — ustaw `VAPI_WEBHOOK_SECRET`, wysyłany w
  nagłówku `x-vapi-secret`.

Body jest weryfikowane na surowych bajtach (`await request.body()`) przed
parsowaniem Pydantic.

## Calendar Tools (sprawdzanie i rezerwacja terminów)

W trakcie żywej rozmowy asystent może wywołać narzędzia `check_availability`
i `create_appointment`. W przeciwieństwie do `end-of-call-report` (przetwarzane
w tle), event `tool-calls` z Vapi jest obsługiwany **synchronicznie** na tym
samym endpoincie `POST /webhooks/vapi` (routing po `message.type` w
`app/api/vapi_webhook.py`) — asystent czeka na odpowiedź, żeby ją od razu
wypowiedzieć klientowi.

### 1. Google Cloud — service account

1. W Google Cloud Console utwórz projekt (lub użyj istniejącego), włącz
   **Google Calendar API**.
2. Utwórz Service Account, wygeneruj klucz JSON.
3. Zapisz plik jako `./secrets/google-service-account.json` (katalog
   `secrets/` jest w `.gitignore` — nigdy nie trafia do repo). Docker Compose
   montuje go jako `/srv/app/secrets/...` — ścieżka ta jest już domyślną
   wartością `GOOGLE_SERVICE_ACCOUNT_FILE` w `.env.example`.

Jeden service account (jeden klucz) obsługuje wszystkie kancelarie —
rozróżnieniem jest `tenants.calendar_id`, nie osobne poświadczenia.

### 2. Udostępnienie kalendarza kancelarii

W Google Calendar danej kancelarii: Ustawienia → wybrany kalendarz →
"Udostępnij konkretnym osobom" → dodaj e-mail service accounta (widoczny w
pliku klucza jako `client_email`) z uprawnieniem **"Wprowadzanie zmian w
wydarzeniach"**.

### 3. Konfiguracja tenanta w bazie

```sql
UPDATE tenants
SET calendar_provider = 'google',
    calendar_id = 'kancelaria-a@group.calendar.google.com',
    timezone = 'Europe/Warsaw',
    business_hours_start = '09:00',
    business_hours_end = '17:00',
    appointment_duration_minutes = 30
WHERE vapi_assistant_id = '<assistant.id>';
```

Domyślnie (bez `UPDATE`) `calendar_provider = 'none'` i narzędzia grzecznie
odpowiadają, że kalendarz online nie jest jeszcze skonfigurowany, zamiast się
wysypać.

### 4. Dodanie narzędzi do Vapi Assistant

W dashboardzie Vapi (zakładka Tools asystenta) lub przez API dodaj dwa
function tools z poniższymi `parameters` (JSON Schema). Ważne: `date`/`time`
mają zawsze przyjść w formacie `YYYY-MM-DD` / `HH:MM` (24h) — to model ma
przełożyć wypowiedzianą datę na ten format, backend celowo nie parsuje
polskiego języka naturalnego.

```json
{
  "name": "check_availability",
  "description": "Sprawdza, czy podany termin spotkania w kancelarii jest wolny.",
  "parameters": {
    "type": "object",
    "properties": {
      "date": { "type": "string", "description": "Data w formacie YYYY-MM-DD" },
      "time": { "type": "string", "description": "Godzina w formacie 24h HH:MM" }
    },
    "required": ["date", "time"]
  }
}
```

```json
{
  "name": "create_appointment",
  "description": "Rezerwuje spotkanie w kancelarii na podany termin, jeśli jest wolny.",
  "parameters": {
    "type": "object",
    "properties": {
      "date": { "type": "string", "description": "Data w formacie YYYY-MM-DD" },
      "time": { "type": "string", "description": "Godzina w formacie 24h HH:MM" },
      "client_name": { "type": "string", "description": "Imię i nazwisko klienta" },
      "client_phone": { "type": "string", "description": "Numer telefonu klienta" },
      "topic": { "type": "string", "description": "Czego dotyczy spotkanie" }
    },
    "required": ["date", "time"]
  }
}
```

Osobnego Server URL na narzędziu nie trzeba ustawiać — bez niego Vapi użyje
Server URL już skonfigurowanego na poziomie Assistant (patrz sekcja wyżej),
czyli tego samego `/webhooks/vapi`.

### 5. Logika bezpieczeństwa rezerwacji

`create_appointment` (`app/services/calendar_tool_service.py`) nie ufa
wcześniejszemu `check_availability` — tuż przed zapisem ponownie sprawdza
Google `freeBusy`, a na końcu zapis do tabeli `appointments` ma unikalny
indeks `(tenant_id, starts_at)` jako ostatnią linię obrony przed podwójną
rezerwacją. Jeśli backend przegra ten wyścig, cofa właśnie utworzone
wydarzenie w Google (`cancel_appointment`), żeby nie zostawić osieroconego
wpisu w kalendarzu.

## Otwarte kwestie (do potwierdzenia operacyjnie)

- Dokładna nazwa nagłówka HMAC — do zweryfikowania przy pierwszym realnym
  podłączeniu w dashboardzie Vapi.
- Czy każdy tenant ma w pełni osobne konto SIP w Zadarma (zakładamy tak).
- Dokładny sposób podpięcia function tools do assistenta (zakładka Tools w
  dashboardzie vs. `PATCH /assistant`) — do zweryfikowania operacyjnie,
  parametry narzędzi powyżej są jednak stabilne.
- `cancel_appointment` jest zaimplementowany w `CalendarService` (używany
  wewnętrznie do wycofania rezerwacji przy przegranym wyścigu), ale nie jest
  jeszcze wystawiony jako narzędzie głosowe dla klienta — do dodania, gdyby
  klienci mieli też odwoływać spotkania przez telefon.
- Kalendarz zakłada **jeden wspólny kalendarz na kancelarię** (nie per
  pracownik) — wystarczające przy 2–3 kancelariach, większa struktura
  wymagałaby rozszerzenia schematu `tenants`.

## Struktura repo

Zobacz `app/` — routing (`api/`), modele ORM (`models/`), schematy Pydantic
(`schemas/`), logika domenowa (`services/`), dostęp do danych
(`repositories/`), bezpieczeństwo webhooka (`security/`). Migracje w
`alembic/`, testy w `tests/` (fixture realnego payloadu Vapi w
`tests/fixtures/end_of_call_report.json`).
