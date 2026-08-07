# Automatyczna Sekretarka AI — kancelarie notarialne

FastAPI backend obsługujący webhooki z Vapi (rozmowy głosowe przez Zadarma SIP)
dla 2-3 kancelarii notarialnych, jeden tenant = jeden Vapi Assistant + jeden
numer Zadarma. Cała logika multi-tenancy, idempotency i ekstrakcji danych
z rozmów żyje w tym repo — bez Make/Zapier/n8n.

## Stack

FastAPI (async) · SQLAlchemy 2.x + asyncpg · PostgreSQL · Alembic · Pydantic v2
· OpenAI Structured Outputs (ekstrakcja danych z transkryptu) · Docker Compose

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

## Otwarte kwestie (do potwierdzenia operacyjnie)

- Dokładna nazwa nagłówka HMAC — do zweryfikowania przy pierwszym realnym
  podłączeniu w dashboardzie Vapi.
- Czy każdy tenant ma w pełni osobne konto SIP w Zadarma (zakładamy tak).
- Kalendarz (Calendar Tools: `check_availability`, `create_appointment`,
  `cancel_appointment`) — celowo poza zakresem MVP, `CalendarService` jest
  gotowym punktem rozszerzenia (`app/services/calendar_service.py`).

## Struktura repo

Zobacz `app/` — routing (`api/`), modele ORM (`models/`), schematy Pydantic
(`schemas/`), logika domenowa (`services/`), dostęp do danych
(`repositories/`), bezpieczeństwo webhooka (`security/`). Migracje w
`alembic/`, testy w `tests/` (fixture realnego payloadu Vapi w
`tests/fixtures/end_of_call_report.json`).
