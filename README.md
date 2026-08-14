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

Narzędzia tworzy się osobno przez `POST /tool`, a dopiero potem podpina do
asystenta przez `model.toolIds` (aktualny mechanizm Vapi — starsze przykłady
z inline `functions` na asystencie są nieaktualne). `date`/`time` mają zawsze
przyjść w formacie `YYYY-MM-DD` / `HH:MM` (24h) — to model ma przełożyć
wypowiedzianą datę na ten format, backend celowo nie parsuje polskiego
języka naturalnego.

```bash
curl -X POST https://api.vapi.ai/tool \
  -H "Authorization: Bearer $VAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "function",
    "async": false,
    "function": {
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
    },
    "server": { "url": "https://<twoja-domena>/webhooks/vapi" }
  }'
```

```bash
curl -X POST https://api.vapi.ai/tool \
  -H "Authorization: Bearer $VAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "function",
    "async": false,
    "function": {
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
    },
    "server": { "url": "https://<twoja-domena>/webhooks/vapi" }
  }'
```

Każdy `curl` zwraca `{"id": "..."}`. Oba ID wpisz w `model.toolIds` przy
tworzeniu/aktualizacji asystenta (patrz sekcja "Prompt Asystenta" niżej) —
`server.url` na narzędziu wskazujemy jawnie na `/webhooks/vapi`, żeby nie
zależeć od tego, czy Server URL na poziomie Assistant jest już ustawiony w
momencie tworzenia narzędzia.

### 5. Logika bezpieczeństwa rezerwacji

`create_appointment` (`app/services/calendar_tool_service.py`) nie ufa
wcześniejszemu `check_availability` — tuż przed zapisem ponownie sprawdza
Google `freeBusy`, a na końcu zapis do tabeli `appointments` ma unikalny
indeks `(tenant_id, starts_at)` jako ostatnią linię obrony przed podwójną
rezerwacją. Jeśli backend przegra ten wyścig, cofa właśnie utworzone
wydarzenie w Google (`cancel_appointment`), żeby nie zostawić osieroconego
wpisu w kalendarzu.

## Prompt Asystenta (persona sekretarki)

Poniższy system prompt realizuje pełny scenariusz rozmowy: przywitanie,
ustalenie sprawy, zebranie danych klienta i — jeśli klient chce — sprawdzenie
i zarezerwowanie terminu spotkania przez `check_availability`/
`create_appointment`. Wklej go w dashboardzie Vapi (zakładka Model asystenta,
pole System Prompt) — to najprostsza droga, bo dokładna nazwa pola w samym
JSON-ie API (`model.messages[0]` z `role: "system"` czy `model.systemPrompt`
bezpośrednio) różni się między wersjami dokumentacji Vapi; przez dashboard
unikasz tej niejednoznaczności. Jeśli tworzysz/aktualizujesz asystenta przez
API, zweryfikuj aktualne pole na [Create Assistant](https://docs.vapi.ai/api-reference/assistants/create)
przed wysłaniem żądania.

**System Prompt:**

```
Jesteś wirtualną sekretarką kancelarii notarialnej [NAZWA KANCELARII].
Rozmawiasz wyłącznie po polsku, zwracasz się do rozmówcy per Pan/Pani.
Nie jesteś notariuszem i nie udzielasz porad prawnych.

Przebieg rozmowy:
1. Przywitaj się i przedstaw się jako asystentka kancelarii notarialnej.
2. Zapytaj, w jakiej sprawie dzwoni klient — pozwól mu się swobodnie
   wypowiedzieć, nie przerywaj.
3. Ustal rodzaj czynności notarialnej (np. sprzedaż nieruchomości, zakup
   nieruchomości, darowizna, testament, pełnomocnictwo, poświadczenie
   podpisu, akt notarialny, sprawy spadkowe, inne). Jeśli nie jest jasne,
   dopytaj krótko.
4. Zbierz imię i nazwisko klienta.
5. Zbierz numer telefonu do kontaktu (jeśli dzwoni z numeru, z którego chce
   być kontaktowany, potwierdź to zamiast pytać ponownie).
6. Zapytaj o adres e-mail — to opcjonalne, jeśli klient nie chce podawać,
   nie naciskaj.
7. Dopytaj o istotne dodatkowe informacje potrzebne do sprawy (np. inni
   uczestnicy czynności, lokalizacja nieruchomości) — tylko jeśli naturalnie
   wynika to z rozmowy, nie prowadź przesłuchania.
8. Zapytaj, czy klient chciałby umówić się na spotkanie z pracownikiem
   kancelarii w celu omówienia sprawy.
   - Jeśli tak: zapytaj o preferowaną datę i godzinę.
   - Wywołaj narzędzie check_availability z datą i godziną zawsze w formacie
     YYYY-MM-DD i HH:MM, niezależnie jak klient je wypowiedział.
   - Jeśli termin jest wolny: wywołaj create_appointment z datą, godziną,
     imieniem i nazwiskiem, telefonem i krótkim tematem sprawy, a następnie
     przekaż klientowi wynik dokładnie tak, jak zwróciło narzędzie.
   - Jeśli termin jest zajęty albo poza godzinami pracy kancelarii:
     poinformuj o tym klienta treścią zwróconą przez narzędzie i zapytaj o
     inny termin, powtarzając sprawdzenie.
   - Nigdy nie zgaduj dostępności terminu — zawsze polegaj wyłącznie na
     wyniku narzędzia.
9. Przed zakończeniem rozmowy krótko podsumuj i potwierdź kluczowe dane:
   imię i nazwisko, temat sprawy, umówiony termin (jeśli był), sposób
   kontaktu.
10. Zakończ rozmowę profesjonalnie, dziękując za kontakt.

Zasady:
- Nigdy nie udzielaj porad prawnych ani interpretacji przepisów. Jeśli
  klient o to prosi, powiedz wprost, że nie możesz udzielać porad prawnych,
  a szczegółowe pytania przekażesz notariuszowi lub pracownikowi kancelarii
  podczas umówionego spotkania.
- Mów zwięźle i naturalnie, unikaj sztywnych formułek.
- Nie powtarzaj informacji, które klient już podał.
- Bądź odporna na przerwania — jeśli klient zacznie mówić w trakcie Twojej
  wypowiedzi, przerwij i wysłuchaj.
- Jeśli czegoś nie dosłyszałaś, dopytaj wprost zamiast zgadywać.
- Nie ujawniaj klientowi szczegółów technicznych (nazw narzędzi, komunikatów
  o błędach systemowych). Jeśli sprawdzenie kalendarza się nie powiedzie,
  powiedz naturalnie: "Mam teraz problem z podglądem kalendarza, zapiszę
  zgłoszenie, a pracownik skontaktuje się w sprawie terminu."
```

**First Message:** `Dzień dobry, kancelaria notarialna [NAZWA], w czym mogę pomóc?`

Warto też ustawić `analysisPlan.summaryPrompt` na asystencie (np. "Podsumuj
w 2-3 zdaniach cel rozmowy i ustalenia") — to tanio i natywnie wypełnia
`calls.summary` niezależnie od naszego własnego Agenta ekstrakcyjnego
(`app/services/extraction_service.py`), który i tak działa w tle po
zakończeniu rozmowy i zapisuje pełne dane do `enquiries`.

## Otwarte kwestie (do potwierdzenia operacyjnie)

- Dokładna nazwa nagłówka HMAC — do zweryfikowania przy pierwszym realnym
  podłączeniu w dashboardzie Vapi.
- Czy każdy tenant ma w pełni osobne konto SIP w Zadarma (zakładamy tak).
- Dokładne pole na system prompt w JSON-ie API (`model.messages` vs
  `model.systemPrompt`) — dashboard omija ten problem, do zweryfikowania
  jeśli tworzenie asystentów ma być zautomatyzowane.
- `cancel_appointment` jest zaimplementowany w `CalendarService` (używany
  wewnętrznie do wycofania rezerwacji przy przegranym wyścigu), ale nie jest
  jeszcze wystawiony jako narzędzie głosowe dla klienta — do dodania, gdyby
  klienci mieli też odwoływać spotkania przez telefon.
- Kalendarz zakłada **jeden wspólny kalendarz na kancelarię** (nie per
  pracownik) — wystarczające przy 2–3 kancelariach, większa struktura
  wymagałaby rozszerzenia schematu `tenants`.

## Deployment (produkcja)

Produkcyjny stos różni się od lokalnego `docker compose up`: dochodzi
**Caddy** (reverse proxy, automatyczne certyfikaty Let's Encrypt) i deploy
dzieje się automatycznie przez **GitHub Actions** przy pushu na `master`.
Definicja: `docker-compose.prod.yml` (samodzielny plik, nie override dla
`docker-compose.yml`) + `Caddyfile`. W przeciwieństwie do wersji lokalnej,
`app`/`db` nie publikują żadnych portów na hosta — jedynym punktem wejścia
z internetu jest Caddy na 80/443.

### Jednorazowy setup serwera (95.158.64.196)

1. **DNS**: dodaj rekord `A` — `ai-call.webaby.io` → `95.158.64.196` u
   dostawcy DNS dla `webaby.io`. Jeśli używasz Cloudflare, na start zostaw
   wpis **DNS only** (szara chmurka) — upraszcza pierwsze wystawienie
   certyfikatu przez Caddy; pomarańczowe proxy Cloudflare da się dołączyć
   później.
2. **Docker na serwerze**: zainstaluj Docker Engine + plugin Compose
   (`docker compose version` powinno działać).
3. **Katalog i repo**:
   ```bash
   mkdir -p /opt/auto-calls && cd /opt/auto-calls
   git clone https://github.com/maciejmar/auto-calls.git .
   ```
4. **Sekrety na serwerze** (nigdy przez Git/CI):
   ```bash
   cp .env.example .env   # uzupełnij realnymi wartościami
   mkdir secrets
   # wgraj tu google-service-account.json, np. przez scp z Twojego komputera:
   #   scp -P 2222 secrets/google-service-account.json <user>@95.158.64.196:/opt/auto-calls/secrets/
   ```
5. **Klucz SSH dla GitHub Actions** — wygenerowany lokalnie
   (`ssh-keygen -t ed25519`, para w scratchpadzie tej sesji). Dodaj **klucz
   publiczny** do `~/.ssh/authorized_keys` użytkownika, na którego będzie
   logował się deploy, na serwerze `95.158.64.196`.
6. **SSH i firewall**: serwer nasłuchuje SSH na **porcie 2222** (nie 22 —
   workflow ma to już zaszyte w `.github/workflows/deploy.yml`). Otwórz w
   firewallu tylko 80, 443 i 2222; upewnij się, że 8000 i 5432 nie są
   dostępne z zewnątrz (w `docker-compose.prod.yml` i tak nie są
   publikowane, firewall to dodatkowa warstwa).
7. **Sekrety w GitHub** (Settings → Secrets and variables → Actions →
   *New repository secret*) w repo `maciejmar/auto-calls`:
   - `SSH_HOST` = `95.158.64.196`
   - `SSH_USER` = użytkownik z kroku 5
   - `SSH_PRIVATE_KEY` = zawartość **prywatnego** klucza z kroku 5 (plik bez
     rozszerzenia `.pub`) — nigdy nie wklejaj go nigdzie indziej.
   - Port (2222) nie jest sekretem, jest już wpisany wprost w workflow.
8. **Pierwsze uruchomienie** (ręcznie, na serwerze, żeby nie czekać na
   pierwszy push):
   ```bash
   cd /opt/auto-calls
   docker compose -f docker-compose.prod.yml up -d --build
   docker compose -f docker-compose.prod.yml exec -T app alembic upgrade head
   ```
   Sprawdź `curl https://ai-call.webaby.io/health` — Caddy powinien mieć już
   ważny certyfikat.

### Jak działa automatyczny deploy

`.github/workflows/deploy.yml`: push na `master` → job `test` (pytest, bez
zależności od Postgresa — testy używają SQLite w pamięci) → dopiero po
zielonych testach job `deploy` łączy się SSH i uruchamia
`scripts/deploy.sh` w `/opt/auto-calls` na serwerze (`git pull` → rebuild →
restart → `alembic upgrade head`). Status widać w zakładce **Actions** w
GitHubie.

### Rollback

```bash
# na serwerze
cd /opt/auto-calls
git checkout <poprzedni-dobry-sha>
docker compose -f docker-compose.prod.yml up -d --build
```

## Struktura repo

Zobacz `app/` — routing (`api/`), modele ORM (`models/`), schematy Pydantic
(`schemas/`), logika domenowa (`services/`), dostęp do danych
(`repositories/`), bezpieczeństwo webhooka (`security/`). Migracje w
`alembic/`, testy w `tests/` (fixture realnego payloadu Vapi w
`tests/fixtures/end_of_call_report.json`).
