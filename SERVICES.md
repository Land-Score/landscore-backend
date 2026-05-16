# SERVICES.md

Полное описание каждого микросервиса LandScore Backend и правила написания кода.

---

## Оглавление

1. [Общая архитектура](#общая-архитектура)
2. [api-gateway](#1-api-gateway)
3. [auth-service](#2-auth-service)
4. [check-service](#3-check-service)
5. [search-service](#4-search-service)
6. [document-service](#5-document-service)
7. [ai-orchestrator](#6-ai-orchestrator)
8. [data-collector](#7-data-collector)
9. [geo-service](#8-geo-service)
10. [market-service](#9-market-service)
11. [Правила написания кода](#правила-написания-кода)

---

## Общая архитектура

```
Браузер / мобильное приложение
           │  HTTP/REST
           ▼
    ┌─────────────┐
    │ api-gateway │  :8000  FastAPI — единственная точка входа
    └──────┬──────┘
           │  gRPC
     ┌─────┼──────────┬───────────┐
     ▼     ▼          ▼           ▼
  auth  check      search    document
  :50051 :50052    :50053    :50054
                      │
              ai-orchestrator :50055
              (gRPC + Celery worker)
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
  data-collector   geo-service  market-service
     :50056          :50057       :50058
```

**Принципы взаимодействия:**
- Клиент общается только с `api-gateway` по HTTP/REST
- Между сервисами — исключительно gRPC (proto-контракты в `proto/`)
- Бизнес-логика НЕ живёт в `api-gateway` — он только транслирует HTTP → gRPC
- Фоновые задачи (пайплайн агентов) — Celery поверх Redis
- `ai-orchestrator` вызывает `data-collector`, `geo-service`, `market-service`, `document-service` во время пайплайна

---

## 1. api-gateway

**Порт:** 8000 (HTTP)  
**Стек:** FastAPI, slowapi, python-jose

### Назначение

Единственная точка входа в систему. Принимает HTTP-запросы от фронтенда, валидирует JWT-токен, проксирует запросы к нужному gRPC-сервису, формирует HTTP-ответ.

**Чего gateway НЕ делает:** не обращается к БД, не содержит бизнес-логики, не хранит состояние.

### Функционал

#### Middleware: AuthMiddleware
- Извлекает `Authorization: Bearer <token>` из заголовка
- Декодирует JWT (секрет из `JWT_SECRET`)
- Кладёт `user_id` и `email` в `request.state` для использования в роутерах
- Публичные пути (без проверки токена):
  ```
  GET  /health
  POST /api/auth/register
  POST /api/auth/login
  POST /api/auth/refresh
  ```
- При невалидном токене — 401 Unauthorized

#### Rate Limiting
- Используется `slowapi` (обёртка над `limits`)
- Лимит по умолчанию: 60 запросов/минуту с одного IP
- Для `/api/checks` и `/api/searches` POST: 10 запросов/минуту (создание новых задач дорогое)

#### CORS
- В dev режиме: `http://localhost:3000`
- В prod: домен из env `ALLOWED_ORIGINS`

#### Роутеры

**`/api/auth`**
```
POST /register        → auth-service.Register
POST /login           → auth-service.Login
POST /refresh         → auth-service.RefreshToken
GET  /me              → auth-service.GetProfile
PATCH /me/profile     → auth-service.UpdateProfile
```

**`/api/checks`**
```
POST /                → check-service.CreateCheck
GET  /                → check-service.ListChecks
GET  /{id}/status     → check-service.GetCheckStatus
GET  /{id}/report     → check-service.GetCheckReport
```

**`/api/searches`**
```
POST /                     → search-service.CreateSearch
GET  /                     → search-service.ListSearches
GET  /{id}/status          → search-service.GetSearchStatus
GET  /{id}/criteria        → search-service.GetSearchCriteria
PUT  /{id}/criteria        → search-service.ConfirmCriteria
GET  /{id}/results         → search-service.GetSearchResults
GET  /{id}/recommendation  → search-service.GetRecommendation
```

**`/api/documents`**
```
POST /upload          → document-service.StoreDocument → возвращает document_id
GET  /{id}            → document-service.GetDocument
DELETE /{id}          → document-service.DeleteDocument
```

#### Формат ошибок (единый для всего API)
```json
{
  "error": "NOT_FOUND",
  "message": "Check 550e8400-... not found",
  "request_id": "uuid"
}
```
gRPC статус коды транслируются в HTTP: NOT_FOUND → 404, INVALID_ARGUMENT → 400, UNAUTHENTICATED → 401, INTERNAL → 500.

---

## 2. auth-service

**Порт:** 50051 (gRPC)  
**БД:** `auth_db` (postgres-auth)  
**Стек:** grpcio, SQLAlchemy asyncio, asyncpg, passlib[bcrypt], python-jose

### Назначение

Управление пользователями и аутентификацией. Единственный сервис, который знает о паролях и токенах. Остальные сервисы доверяют `user_id` из JWT, не ходят в auth-service для проверки каждого запроса (JWT проверяет сам gateway).

### Схема БД

**`users`**
| Колонка | Тип | Описание |
|---|---|---|
| id | UUID PK | Идентификатор пользователя |
| email | VARCHAR(255) UNIQUE | Email (логин) |
| name | VARCHAR(255) | Отображаемое имя |
| password_hash | VARCHAR(255) | bcrypt hash |
| created_at | TIMESTAMP | Дата регистрации |

**`user_profiles`**
| Колонка | Тип | Описание |
|---|---|---|
| user_id | UUID PK FK→users | |
| client_type | VARCHAR(50) | private / developer / agroholding / investor / legal |
| main_task | VARCHAR(50) | land_check / land_plot_selection / portfolio |
| region | VARCHAR(255) | Предпочтительный регион |
| priority | VARCHAR[] | Массив: margin / legal_risk / infrastructure |
| risk_tolerance | VARCHAR(20) | low / medium / high |
| preferred_scenarios | VARCHAR[] | construction / agriculture / resale / development |
| organization | VARCHAR(255) | Название организации (для B2B) |
| budget | FLOAT | Бюджет в рублях (0 = не указан) |

### Методы gRPC

**`Register(email, name, password)`**
1. Проверить, что email не занят → ALREADY_EXISTS если занят
2. Захешировать пароль через bcrypt (cost=12)
3. Создать запись `users`
4. Создать пустую запись `user_profiles` с дефолтами
5. Вернуть `access_token` (JWT, 15 мин) + `refresh_token` (JWT, 7 дней) + `user_id`

**`Login(email, password)`**
1. Найти пользователя по email → NOT_FOUND если нет
2. Проверить пароль через bcrypt.verify → UNAUTHENTICATED если неверный
3. Вернуть пару токенов

**`RefreshToken(refresh_token)`**
1. Декодировать refresh_token
2. Проверить что тип токена `refresh` и срок не истёк → UNAUTHENTICATED если нет
3. Выдать новую пару access + refresh

**`ValidateToken(token)`**
- Используется gateway в middleware
- Декодирует JWT, возвращает `user_id` + `email`
- Не делает запросы в БД (JWT самодостаточен)

**`GetProfile(user_id)`**
- Возвращает `User` + `UserProfile` одним запросом (JOIN)

**`UpdateProfile(user_id, profile_fields...)`**
- Partial update: обновляет только переданные поля
- Возвращает обновлённый профиль

### JWT структура
```json
{
  "sub": "user_id (UUID)",
  "email": "user@example.com",
  "type": "access | refresh",
  "iat": 1234567890,
  "exp": 1234567890
}
```

---

## 3. check-service

**Порт:** 50052 (gRPC)  
**БД:** `main_db` (postgres-main)  
**Стек:** grpcio, SQLAlchemy asyncio, asyncpg, redis[hiredis]

### Назначение

Управляет жизненным циклом **проверки конкретного участка**. Хранит состояние задачи, прогресс выполнения агентов и финальный результат. Сам анализ НЕ выполняет — делегирует ai-orchestrator.

### Пользовательский сценарий

```
Пользователь вводит кадастровый номер / адрес
→ POST /api/checks (gateway → check-service.CreateCheck)
→ check-service создаёт запись + публикует Celery-задачу
→ Фронт опрашивает GET /api/checks/{id}/status каждые 2 секунды
→ ai-orchestrator вызывает check-service.UpdateCheckProgress по мере работы агентов
→ Когда пайплайн завершён, ai-orchestrator вызывает check-service.SaveCheckResult
→ Фронт получает статус completed, переходит на страницу отчёта
→ GET /api/checks/{id}/report
```

### Схема БД

**`land_checks`**
| Колонка | Тип | Описание |
|---|---|---|
| id | UUID PK | |
| user_id | UUID INDEX | Владелец |
| status | VARCHAR(20) | pending / processing / completed / failed |
| cadastral_number | VARCHAR(50) NULL | Кадастровый номер если указан |
| address | VARCHAR(500) NULL | Адрес если указан |
| lat | FLOAT NULL | Координата если указана |
| lng | FLOAT NULL | Координата если указана |
| purpose | VARCHAR(255) | Цель использования от пользователя |
| created_at | TIMESTAMP | |
| completed_at | TIMESTAMP NULL | |

**`check_steps`** — прогресс по каждому агенту
| Колонка | Тип | Описание |
|---|---|---|
| id | UUID PK | |
| check_id | UUID INDEX | |
| agent_name | VARCHAR(100) | Имя агента (напр. "LegalAgent") |
| status | VARCHAR(20) | pending / running / done / failed |
| progress_pct | INT | 0–100 |
| output_json | JSON NULL | Результат агента (для отладки) |
| started_at | TIMESTAMP NULL | |
| completed_at | TIMESTAMP NULL | |

**`check_results`** — финальный результат
| Колонка | Тип | Описание |
|---|---|---|
| check_id | UUID PK | |
| plot_id | VARCHAR(50) NULL | Кадастровый номер (нормализованный) |
| overall_score | INT NULL | LandScore 0–100 |
| legal_risk | VARCHAR(20) NULL | low / medium / high / critical |
| stop_factors | VARCHAR[] | Список стоп-факторов (пустой = нет) |
| best_scenario | VARCHAR(50) NULL | Лучший сценарий использования |
| report_json | JSON NULL | Полный отчёт (все агенты) |
| explanation | TEXT NULL | Объяснение на русском для пользователя |
| next_steps | VARCHAR[] | Список конкретных следующих шагов |

### Методы gRPC

**`CreateCheck(user_id, cadastral_number?, address?, lat?, lng?, purpose, user_profile_json)`**
1. Создать запись `land_checks` со статусом `pending`
2. Опубликовать Celery-задачу `run_check_task` в Redis с `check_id` и профилем пользователя
3. Вернуть `check_id`

**`GetCheckStatus(check_id)`**
1. Запросить `land_checks.status`
2. Запросить последний активный шаг из `check_steps` (WHERE status IN ('running', 'done') ORDER BY completed_at DESC LIMIT 1)
3. Вычислить общий прогресс: `COUNT(done steps) / total_steps * 100`
4. Вернуть `status`, `current_step`, `progress_pct`

**`UpdateCheckProgress(check_id, agent_name, status, progress_pct, output_json?)`**
- Вызывается ai-orchestrator после каждого агента
- UPSERT в `check_steps`
- Обновить `land_checks.status = 'processing'` если ещё `pending`
- Записать в Redis ключ `check:{check_id}:progress` для fast polling (TTL 1 час)

**`SaveCheckResult(check_id, ...все поля CheckResult...)`**
- Вызывается ai-orchestrator по завершении пайплайна
- INSERT в `check_results`
- UPDATE `land_checks.status = 'completed'`, `completed_at = now()`
- Если пайплайн упал — `status = 'failed'`

**`GetCheckReport(check_id)`**
- Возвращает `check_results` по `check_id`
- Если результата ещё нет — NOT_FOUND
- Если статус `failed` — возвращает ошибку с причиной

**`ListChecks(user_id, limit, offset)`**
- Возвращает список `land_checks` для пользователя, сортировка по `created_at DESC`

---

## 4. search-service

**Порт:** 50053 (gRPC)  
**БД:** `main_db` (postgres-main)  
**Стек:** grpcio, SQLAlchemy asyncio, asyncpg, redis[hiredis]

### Назначение

Управляет жизненным циклом **поиска участка под задачу**. В отличие от check-service, здесь есть точка паузы: пользователь должен подтвердить критерии поиска перед запуском разведки участков.

### Пользовательский сценарий

```
Пользователь описывает задачу в свободной форме
→ POST /api/searches → search-service.CreateSearch
→ Запускается только первый этап пайплайна (SearchCriteriaAgent)
→ Агент извлекает структурированные критерии → search-service.SaveCriteria
→ Пайплайн приостанавливается, ждёт подтверждения
→ Фронт показывает критерии пользователю на /search/{id}/criteria
→ Пользователь редактирует/подтверждает → PUT /api/searches/{id}/criteria
→ search-service.ConfirmCriteria → возобновляет пайплайн (LandScout → ...)
→ Агенты находят и ранжируют кандидатов → search-service.SaveCandidate
→ ChiefDecisionAgent формирует рекомендацию → GET /api/searches/{id}/recommendation
```

### Схема БД

**`land_searches`**
| Колонка | Тип | Описание |
|---|---|---|
| id | UUID PK | |
| user_id | UUID INDEX | |
| status | VARCHAR(20) | pending / awaiting_confirmation / processing / completed / failed |
| query | TEXT | Исходный запрос пользователя |
| user_profile_json | JSON NULL | Снапшот профиля на момент создания |
| created_at | TIMESTAMP | |
| completed_at | TIMESTAMP NULL | |

**`search_criteria`** — критерии, извлечённые агентом
| Колонка | Тип | Описание |
|---|---|---|
| id | UUID PK | |
| search_id | UUID UNIQUE | |
| criteria_json | JSON | Структурированные критерии поиска |
| confirmed | BOOLEAN | Подтверждены пользователем? |
| confirmed_at | TIMESTAMP NULL | |

**`search_steps`** — прогресс агентов (аналог check_steps)
| Колонка | Тип | Описание |
|---|---|---|
| id | UUID PK | |
| search_id | UUID INDEX | |
| agent_name | VARCHAR(100) | |
| status | VARCHAR(20) | pending / running / done / failed |
| progress_pct | INT | |
| started_at | TIMESTAMP NULL | |
| completed_at | TIMESTAMP NULL | |

**`search_candidates`** — найденные участки-кандидаты
| Колонка | Тип | Описание |
|---|---|---|
| id | UUID PK | |
| search_id | UUID INDEX | |
| plot_id | VARCHAR(50) | Кадастровый номер |
| rank | INT | Позиция в итоговом списке (1 = лучший) |
| scores_json | JSON | Детальные оценки по критериям |
| plot_summary_json | JSON NULL | Краткий паспорт участка для карточки |

**`search_recommendations`** — финальная рекомендация
| Колонка | Тип | Описание |
|---|---|---|
| search_id | UUID PK | |
| recommendation_json | JSON | Полный отчёт сравнения кандидатов |
| top_plot_ids | VARCHAR[] | Топ-3 кадастровых номера |
| explanation | TEXT | Объяснение выбора по-русски |

### Методы gRPC

**`CreateSearch(user_id, query, user_profile_json)`**
1. Создать `land_searches` (status=pending)
2. Создать пустую `search_criteria` (confirmed=false)
3. Запустить Celery-задачу `run_search_task` с флагом `phase=criteria_extraction`
4. Вернуть `search_id`

**`SaveCriteria(search_id, criteria_json)`**
- Вызывается ai-orchestrator после SearchCriteriaAgent
- UPSERT `search_criteria.criteria_json`
- UPDATE `land_searches.status = 'awaiting_confirmation'`
- Пайплайн приостанавливается — ai-orchestrator polling для confirmed

**`ConfirmCriteria(search_id, criteria_json)`**
- Вызывается пользователем через API (может скорректировать критерии)
- UPDATE `search_criteria.confirmed = true`, сохранить финальные критерии
- UPDATE `land_searches.status = 'processing'`
- Опубликовать Celery-задачу `run_search_task` с `phase=scouting`

**`SaveCandidate(search_id, plot_id, rank, scores_json)`**
- Вызывается ai-orchestrator по мере ранжирования участков
- UPSERT по `(search_id, plot_id)` — один участок можно обновить

**`GetSearchCriteria(search_id)`**
- Возвращает `criteria_json` + `confirmed` флаг
- Фронт показывает критерии для подтверждения

**`GetSearchResults(search_id)`**
- Возвращает `search_candidates` упорядоченные по `rank ASC`
- Включает `plot_summary_json` для рендера карточек

**`GetRecommendation(search_id)`**
- Возвращает `search_recommendations`

---

## 5. document-service

**Порт:** 50054 (gRPC)  
**Хранилище:** MinIO (S3-compatible)  
**Стек:** grpcio, minio, pypdf, pytesseract, Pillow, weasyprint

### Назначение

Всё, что связано с файлами: принимает загруженные документы, извлекает из них текст для агентов, генерирует PDF-отчёты для скачивания.

### Функционал

#### Приём документов
- Пользователь загружает файл через `POST /api/documents/upload`
- Gateway передаёт байты в `document-service.StoreDocument`
- Сервис сохраняет файл в MinIO в бакет `documents`
- Возвращает `document_id` (UUID) и `file_key` (путь в MinIO)

#### Извлечение текста (`ExtractText`)
- Вызывается ai-orchestrator → DocumentExtractionAgent
- Для **PDF с текстовым слоем** (цифровой PDF): `pypdf.PdfReader` → извлекает текст напрямую
- Для **отсканированного PDF / изображений**: конвертация страниц в изображения → Tesseract OCR (русский + английский язык)
- Предобработка изображений через Pillow:
  - Grayscale конвертация
  - Binarization (threshold)
  - Deskew если страница перекошена
- Возвращает извлечённый текст + метаданные (количество страниц, метод извлечения)

#### Генерация отчёта (`GenerateReport`)
- Вызывается ai-orchestrator → ReportAgent после завершения анализа
- Принимает структурированные данные отчёта (JSON)
- Рендерит HTML-шаблон с результатами анализа
- Конвертирует HTML → PDF через WeasyPrint (поддерживает кириллицу через libpango)
- Сохраняет PDF в MinIO в бакет `reports`
- Возвращает `report_url` (presigned URL MinIO, TTL 24 часа)

#### Управление документами
- `GetDocument(document_id)` — возвращает метаданные + presigned URL для скачивания
- `DeleteDocument(document_id)` — удаляет из MinIO

### MinIO структура
```
documents/
  {user_id}/{document_id}.pdf   # загруженные пользователем файлы
  {user_id}/{document_id}.jpg   # загруженные изображения

reports/
  {check_id}/report.pdf         # сгенерированные отчёты
```

---

## 6. ai-orchestrator

**Порт:** 50055 (gRPC) + Celery worker  
**БД:** Redis (broker db=1, state db=0)  
**Стек:** grpcio, celery, openai (Yandex AI Studio), tenacity

### Назначение

Мозг системы. Запускает конвейер агентов для анализа участка или поиска участков. Каждый агент — изолированная единица с чётко определёнными входами и выходами.

### Архитектура пайплайна

```
AgentContext  ←  общий контекст, передаётся через весь пайплайн
     │
PipelineRunner  ←  выполняет агентов, вызывает ProgressCallback
     │
 ┌───┴───────────────────────────────────────────────┐
 │  Список агентов (может включать ParallelGroup)    │
 │                                                   │
 │  Agent.run(input, ctx) → AgentResult              │
 │  ParallelGroup([A, B, C]) → asyncio.gather(...)   │
 └───────────────────────────────────────────────────┘
```

### Пайплайн проверки участка (check)

| # | Агент | Тип | Что делает |
|---|---|---|---|
| 1 | RequestUnderstandingAgent | LLM | Извлекает кадастровый номер, адрес, цель из запроса |
| 2 | ObjectIdentificationAgent | LLM | Идентифицирует участок, подтверждает кадастровый номер |
| 3 | DataRequestAgent | Code | Запрашивает данные в data-collector (ЕГРН, кадастр) |
| 4 | SearchPlanningAgent | Code | Определяет список документов для запроса |
| 5 | DocumentExtractionAgent | LLM | Извлекает факты из полученных документов |
| 6 | FactNormalizationAgent | Code | Сводит данные из всех источников в PlotPassport |
| 7a | LegalAgent | LLM | Юридический анализ (параллельно) |
| 7b | LandUseAgent | LLM | Анализ ВРИ и категории (параллельно) |
| 7c | RestrictionsAgent | LLM | Зоны ЗОУИТ, обременения (параллельно) |
| 7d | InfrastructureAgent | Code | Дороги, коммуникации (geo-service) (параллельно) |
| 7e | GeoAgent | Code | Охраняемые зоны, рельеф (geo-service) (параллельно) |
| 7f | MarketAgent | Code | Рыночная цена, аналоги (market-service) (параллельно) |
| 8 | CriticalRiskAgent | LLM | **Синтез рисков. Если есть стоп-фактор → флаг** |
| 9 | ScenarioSelectorAgent | Code | Выбирает применимые сценарии под профиль |
| 10a–e | ConstructionAgent и др. | Code | Расчёт параметров каждого сценария (параллельно) |
| 11 | ProfitabilityCalculatorAgent | Code | ROI для каждого сценария (чистая математика) |
| 12 | ScenarioRankingAgent | Code | Ранжирует сценарии по ROI и fit с профилем |
| 13 | ChiefDecisionAgent | LLM | **Финальный вердикт. ОБЯЗАН следовать CriticalRiskAgent** |
| 14 | DealFitAgent | LLM | Персонализированная оценка соответствия профилю |
| 15a | ReportAgent | LLM | Полный структурированный отчёт (параллельно) |
| 15b | ClientExplanationAgent | LLM | Объяснение для пользователя простым языком (параллельно) |
| 15c | NextStepsAgent | LLM | Конкретные следующие шаги (параллельно) |

### Пайплайн поиска участка (search)

| # | Агент | Тип | Что делает |
|---|---|---|---|
| 1 | SearchCriteriaAgent | LLM | Извлекает структурированные критерии из запроса |
| — | **ПАУЗА** | — | Ждёт подтверждения критериев от пользователя |
| 2 | LandScoutAgent | Code | Ищет кандидатов в data-collector по критериям |
| 3 | CandidateFilteringAgent | Code | Отсеивает явно неподходящих |
| 4 | (для каждого кандидата) DataRequestAgent | Code | Запрашивает данные ЕГРН параллельно |
| 5 | ShortlistRankingAgent | Code | Предварительный ранг по жёстким критериям |
| 6 | (топ-5 кандидатов) FullAnalysisPipeline | — | Запускает урезанный check-пайплайн на каждого |
| 7 | CandidateComparisonAgent | LLM | Сравнивает лучших кандидатов |
| 8 | ChiefDecisionAgent | LLM | Выбирает победителя, объясняет выбор |
| 9 | NextStepsAgent | LLM | Шаги для лучшего кандидата |

### Изоляция контекста агентов

Каждый агент объявляет, какие данные из предыдущих агентов он может читать:

```python
# LegalAgent видит только юридические данные
legal_context = ctx.get_for_agent("FactNormalizationAgent", "DocumentExtractionAgent")

# MarketAgent видит только рыночные данные — НЕ видит юридические риски
market_context = ctx.get_for_agent("ObjectIdentificationAgent", "FactNormalizationAgent")

# ChiefDecisionAgent видит всё кроме внутренних данных документов
decision_context = ctx.get_for_agent(
    "CriticalRiskAgent", "ScenarioRankingAgent", "DealFitAgent", "LegalAgent",
    "MarketAgent", "InfrastructureAgent", "GeoAgent"
)
```

**Правило:** если агент не объявил ключ в `get_for_agent` — он его не получает, даже если данные есть.

### Взаимодействие с Yandex AI Studio

- Клиент: `openai.AsyncOpenAI` с `base_url` Yandex Foundation Models
- Модель по умолчанию: `yandexgpt-lite` (быстрая) или `yandexgpt` (точная)
- Все аналитические агенты используют `response_format` с JSON-схемой → структурированный вывод
- Retry: 3 попытки с exponential backoff (2s → 10s) через `tenacity`
- Температуры:
  - Аналитические агенты (Legal, LandUse, CriticalRisk, ChiefDecision): `0.1`
  - Синтетические агенты (Report, Explanation, NextSteps): `0.3`
  - Поисковые агенты (SearchCriteria, ObjectIdentification): `0.2`

### Celery задачи

**`run_check_task(check_id, user_profile_json)`**
1. Загрузить профиль пользователя → `UserProfile`
2. Создать `AgentContext(job_id, owner_id=check_id, owner_type="check", profile)`
3. Построить пайплайн через `build_check_pipeline()`
4. Запустить `PipelineRunner.run(ctx, on_progress=callback)`
5. В `callback`: вызвать `check-service.UpdateCheckProgress` (gRPC)
6. По завершении: вызвать `check-service.SaveCheckResult` (gRPC)
7. При ошибке: вызвать `check-service.UpdateCheckProgress` с `status=failed`

**`run_search_task(search_id, user_profile_json, phase)`**
- `phase=criteria_extraction`: запустить только SearchCriteriaAgent
- `phase=scouting`: запустить полный поисковый пайплайн начиная с LandScout

---

## 7. data-collector

**Порт:** 50056 (gRPC)  
**Кеш:** Redis (db=0)  
**Стек:** grpcio, redis[hiredis], httpx

### Назначение

Интеграция с Росреестром и кадастровыми источниками. Абстрагирует источники данных от остальных сервисов. Поддерживает два режима работы для разработки без реального API.

### Режимы работы

Управляются переменной окружения `ROSREESTR_MODE`:

**`mock`** (дефолт для разработки)
- Возвращает реалистичные фейковые данные
- Данные детерминированы по кадастровому номеру (одинаковый номер → одинаковый результат)
- Симулирует задержки реального API (0.5–2 секунды)
- Покрывает все типичные кейсы: чистые участки, с обременениями, с ошибками

**`real`**
- Реальные запросы к API Росреестра
- Требует ключи доступа в env
- Stub: NotImplementedError (будет реализован позже)

### Кеширование

Все ответы кешируются в Redis:
- Ключ: `rosreestr:{method}:{cadastral_number}`
- TTL: 24 часа (данные ЕГРН не меняются часто)
- При cache hit — возвращает кешированные данные без обращения к источнику

### Методы gRPC

**`GetPlotByCadastral(cadastral_number)`**
- Возвращает базовые данные участка: площадь, категория, ВРИ, адрес, кадастровая стоимость
- Формат кадастрового номера: `XX:XX:XXXXXXX:XX`

**`GetPlotByAddress(address, region)`**
- Геокодирует адрес → находит кадастровый номер
- Возвращает те же данные что `GetPlotByCadastral`

**`GetEGRN(cadastral_number)`**
- Возвращает выписку ЕГРН:
  - Правообладатель (тип: физ.лицо / юр.лицо / государство)
  - Обременения (залоги, аресты, сервитуты) с датами
  - История переходов права собственности
  - Координаты поворотных точек
  - Дата постановки на кадастровый учёт

**`SearchPlots(criteria_json, limit)`**
- Поиск участков по критериям для LandScout-агента
- Criteria: регион, площадь min/max, категория, ВРИ, ценовой диапазон
- Возвращает список кадастровых номеров с базовыми данными

### Структура mock-данных

Mock генерирует данные так, чтобы покрывать тест-кейсы:
- Кадастровые номера, оканчивающиеся на чётное — чистые участки
- Оканчивающиеся на нечётное — участки с 1–2 обременениями
- Оканчивающиеся на 0 или 5 — участки с критическими проблемами (для тестирования стоп-факторов)

---

## 8. geo-service

**Порт:** 50057 (gRPC)  
**БД:** `geo_db` (postgres-geo с PostGIS)  
**Стек:** grpcio, geoalchemy2, shapely, asyncpg

### Назначение

Пространственный анализ участков. Всё, что связано с географическим положением: расстояния, зоны, инфраструктура. Использует PostGIS для эффективных геозапросов.

### Схема БД

**`protected_zones`** — предзагруженные охранные зоны
| Колонка | Тип |
|---|---|
| id | UUID PK |
| zone_type | VARCHAR(50) |
| name | VARCHAR(500) |
| geometry | GEOMETRY(MULTIPOLYGON, 4326) |
| restriction_description | TEXT |

**`road_network`** — дорожная сеть (OSM данные)
| Колонка | Тип |
|---|---|
| id | UUID PK |
| road_type | VARCHAR(50) |
| name | VARCHAR(255) |
| geometry | GEOMETRY(LINESTRING, 4326) |

**`settlements`** — населённые пункты
| Колонка | Тип |
|---|---|
| id | UUID PK |
| name | VARCHAR(255) |
| population | INT |
| geometry | GEOMETRY(POINT, 4326) |

### Методы gRPC

**`AnalyzePlotLocation(lat, lng, area_ha)`**
- Определяет ближайший населённый пункт и расстояние до него
- Находит ближайшую дорогу с твёрдым покрытием
- Проверяет попадание в охранные зоны (PostGIS `ST_Intersects`)
- Определяет рельеф (ровный / с уклоном / холмистый) по DEM-данным
- Возвращает: `distance_to_settlement_km`, `distance_to_road_km`, `zone_intersections[]`, `terrain_type`

**`CheckProtectedZones(lat, lng, radius_m)`**
- Возвращает все охранные зоны в радиусе от точки
- Используется InfrastructureAgent и GeoAgent
- Зоны: ООПТ, водоохранные, санитарно-защитные, зоны ЛЭП

**`GetDistances(lat, lng, targets[])`**
- Батч-запрос расстояний от точки до объектов (дороги, сёла, города)
- Использует `ST_Distance` с geography-типом для метрических расстояний

**`SearchPlotsByArea(polygon_wkt, filters)`**
- Поиск участков в заданном полигоне
- Используется LandScout-агентом для сценария поиска

---

## 9. market-service

**Порт:** 50058 (gRPC)  
**БД:** ClickHouse (market DB)  
**Стек:** grpcio, clickhouse-connect

### Назначение

Рыночный анализ цен на земельные участки. ClickHouse выбран за скорость аналитических запросов по большим объёмам данных о сделках.

### Схема ClickHouse

**`land_listings`** — объявления о продаже (загружаются из открытых источников)
```sql
CREATE TABLE land_listings (
    id           UUID,
    cadastral    String,
    region       String,
    district     String,
    category     String,
    allowed_use  String,
    area_ha      Float32,
    price        Float64,
    price_per_ha Float64,
    lat          Float32,
    lng          Float32,
    listed_at    Date,
    source       String   -- 'avito', 'cian', 'rosreestr'
) ENGINE = MergeTree()
ORDER BY (region, listed_at);
```

**`land_transactions`** — реальные сделки (из Росреестра)
```sql
CREATE TABLE land_transactions (
    id           UUID,
    cadastral    String,
    region       String,
    category     String,
    area_ha      Float32,
    price        Float64,
    price_per_ha Float64,
    transaction_date Date
) ENGINE = MergeTree()
ORDER BY (region, transaction_date);
```

### Методы gRPC

**`GetMarketAnalysis(cadastral, region, category, area_ha)`**
- Средняя цена за га в районе (объявления за последние 90 дней)
- Медианная цена по аналогам
- Динамика: изменение цены за 6 месяцев (%)
- Ликвидность: сколько дней объявления висят в среднем
- Оценка рыночной стоимости конкретного участка

**`GetComparables(cadastral, region, category, area_ha, limit)`**
- Находит N наиболее похожих объявлений (аналоги для оценки)
- Критерии схожести: регион, категория, площадь ±30%, расстояние
- Возвращает список аналогов с ценами и характеристиками

**`GetPriceStats(region, category, date_from, date_to)`**
- Статистика по рынку: min, max, median, p25, p75 цена за га
- Разбивка по типам разрешённого использования
- Тренды по месяцам

---

## Правила написания кода

### 1. Типизация

**Обязательные type hints на всех сигнатурах.** Нет implicit `Any`.

```python
# ПРАВИЛЬНО
async def create_check(
    user_id: str,
    cadastral: str | None,
    purpose: str,
) -> str:  # возвращает check_id

# НЕПРАВИЛЬНО
async def create_check(user_id, cadastral, purpose):
```

Используй `X | None` вместо `Optional[X]` (Python 3.10+).  
Используй `list[str]` вместо `List[str]` (Python 3.9+).

---

### 2. Async везде где есть I/O

Любое обращение к БД, gRPC, Redis, MinIO, HTTP — async.

```python
# ПРАВИЛЬНО
async def get_check(check_id: str) -> LandCheck | None:
    async with get_session() as session:
        return await session.get(LandCheck, check_id)

# НЕПРАВИЛЬНО — блокирует event loop
def get_check(check_id: str) -> LandCheck | None:
    with Session() as session:
        return session.get(LandCheck, check_id)
```

SQLAlchemy сессии: всегда через `async with`, закрывай до следующего `await` в другом контексте.

---

### 3. Настройки через pydantic-settings

Никогда `os.environ.get()` напрямую в коде. Все настройки живут в `app/config.py`:

```python
# ПРАВИЛЬНО
from app.config import settings
client = AsyncOpenAI(api_key=settings.yandex_ai_api_key)

# НЕПРАВИЛЬНО
import os
client = AsyncOpenAI(api_key=os.environ.get("YANDEX_AI_KEY"))
```

---

### 4. Логирование через structlog

Никогда `print()` в production-коде. Никогда `logging.basicConfig()`.

```python
import structlog
log = structlog.get_logger()

# ПРАВИЛЬНО
log.info("check_created", check_id=check_id, user_id=user_id)
log.error("pipeline_failed", check_id=check_id, error=str(e))

# НЕПРАВИЛЬНО
print(f"Check created: {check_id}")
logging.info("Check created")
```

Каждый лог должен иметь контекстные поля (`check_id`, `user_id`, `agent_name`), не просто строку.

---

### 5. Ошибки через shared exceptions

```python
from landscore_shared.exceptions import NotFoundError, ValidationError

# ПРАВИЛЬНО
raise NotFoundError(f"Check {check_id} not found")

# НЕПРАВИЛЬНО
raise Exception("not found")
context.set_code(grpc.StatusCode.NOT_FOUND)  # только в servicer boundary
```

В gRPC servicer'е — перехватывай `LandScoreError` и транслируй в gRPC статус:

```python
async def GetCheck(self, request, context):
    try:
        return await get_check(request.check_id)
    except NotFoundError as e:
        await context.abort(grpc.StatusCode.NOT_FOUND, str(e))
    except Exception as e:
        log.error("unexpected_error", error=str(e))
        await context.abort(grpc.StatusCode.INTERNAL, "Internal error")
```

---

### 6. gRPC servicer'ы без UNIMPLEMENTED заглушек в продакшне

Каждый реализованный метод должен или работать, или явно отвечать ошибкой с причиной. `UNIMPLEMENTED` — только временно во время разработки.

---

### 7. Изоляция контекста агентов — строгое правило

Каждый агент **обязан** объявить, какие данные он использует через `ctx.get_for_agent()`:

```python
# ПРАВИЛЬНО — явная декларация зависимостей
class LegalAgent(BaseLLMAgent):
    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        data = ctx.get_for_agent("FactNormalizationAgent", "DocumentExtractionAgent")
        return json.dumps(data, ensure_ascii=False)

# НЕПРАВИЛЬНО — агент видит всё, нарушает изоляцию
class LegalAgent(BaseLLMAgent):
    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        return json.dumps(ctx._facts, ensure_ascii=False)  # ЗАПРЕЩЕНО
```

---

### 8. LLM-агенты всегда с response_schema

Свободный текст от LLM не допускается в аналитическом пайплайне — только структурированный JSON:

```python
# ПРАВИЛЬНО
class LegalAgent(BaseLLMAgent):
    response_schema = LEGAL_SCHEMA  # JSON Schema dict
    
# НЕПРАВИЛЬНО — нет схемы = непредсказуемый формат
class LegalAgent(BaseLLMAgent):
    response_schema = None
```

Исключение: `ClientExplanationAgent` и `NextStepsAgent` могут работать без схемы (вывод — свободный текст для пользователя).

---

### 9. Температуры LLM

| Тип агента | Температура | Примеры |
|---|---|---|
| Аналитические | 0.1 | LegalAgent, CriticalRiskAgent, ChiefDecisionAgent |
| Поисковые / классификация | 0.2 | RequestUnderstandingAgent, ObjectIdentificationAgent, SearchCriteriaAgent |
| Генеративные | 0.3 | ReportAgent, ClientExplanationAgent, NextStepsAgent |

---

### 10. Критическое правило: стоп-факторы непреодолимы

ChiefDecisionAgent **всегда** проверяет вывод CriticalRiskAgent перед финальным решением:

```python
class ChiefDecisionAgent(BaseLLMAgent):
    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        critical = ctx.get("CriticalRiskAgent", {})
        if critical.get("stop_has_critical"):
            # Принудительно добавляем в промпт — модель НЕ должна это обойти
            return json.dumps({
                "STOP_FACTORS_PRESENT": True,
                "stop_factors": critical.get("stop_factors", []),
                "instruction": "recommendation MUST be not_recommended",
                **ctx.get_for_agent("ScenarioRankingAgent", "DealFitAgent"),
            }, ensure_ascii=False)
```

Никакой ROI, никакой "хорошей локации" не отменяет `stop_has_critical=True`.

---

### 11. Celery задачи — идемпотентны

Задачи должно быть безопасно перезапускать при сбое:

```python
@celery_app.task(bind=True, max_retries=3)
def run_check_task(self, check_id: str, user_profile_json: str):
    # Проверить текущий статус — если уже completed, выйти
    # Если processing — продолжить с последнего выполненного шага
    # Не начинать заново если часть шагов уже сохранена
```

---

### 12. Имена агентов — константы

Имя агента используется как ключ в `AgentContext._facts`, в `check_steps.agent_name`, и в прогресс-коллбэках. Оно должно совпадать везде:

```python
class LegalAgent(BaseLLMAgent):
    name = "LegalAgent"  # используется как ctx.set("LegalAgent", result)
```

Не меняй `name` без обновления всех мест где он используется как строка.

---

### 13. Нет бизнес-логики в api-gateway

Gateway — тонкий прокси. Если обнаружил, что в роутере появляется if/else или вычисления — это сигнал вынести логику в нужный сервис:

```python
# ПРАВИЛЬНО — gateway только транслирует
@router.post("/")
async def create_check(body: CreateCheckRequest, request: Request):
    response = await check_stub.CreateCheck(check_pb2.CreateCheckRequest(
        user_id=request.state.user_id,
        cadastral_number=body.cadastral_number or "",
        purpose=body.purpose,
    ))
    return {"check_id": response.check_id}

# НЕПРАВИЛЬНО — бизнес-логика в gateway
@router.post("/")
async def create_check(body: CreateCheckRequest, request: Request):
    if not body.cadastral_number and not body.address:
        raise HTTPException(400, "Either cadastral or address required")
    if len(body.purpose) > 500:
        raise HTTPException(400, "Purpose too long")
    # Это должно быть в check-service
```

Валидация входных данных допускается в gateway только для защиты от явно мусорных запросов (пустые строки, превышение размера файла).

---

### 14. Формат UUID и дат в proto

- UUID передаётся как `string` (не bytes)
- Даты передаются как ISO 8601 string: `"2024-01-15T10:30:00Z"`
- В Python: `datetime.utcnow().isoformat() + "Z"`

---

### 15. Миграции только через Alembic

Никогда `Base.metadata.create_all()` в production-коде. Схема БД управляется только через Alembic миграции:

```bash
# Новая миграция
cd services/auth-service
alembic revision --autogenerate -m "add refresh_tokens table"
alembic upgrade head
```

`create_all()` допустимо только в тестах.

---

### 16. Константы статусов

Никаких магических строк для статусов. Каждый сервис определяет константы:

```python
# services/check-service/app/constants.py
class CheckStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class AgentStatus:
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
```

---

### 17. MinIO бакеты — единые имена

| Бакет | Содержимое |
|---|---|
| `documents` | Загруженные пользователями файлы |
| `reports` | Сгенерированные PDF-отчёты |

Пути внутри бакетов: `{user_id}/{document_id}.{ext}` и `{check_id}/report.pdf`.

---

### 18. gRPC каналы — переиспользуй, не создавай на каждый запрос

```python
# ПРАВИЛЬНО — один канал на сервис, создаётся при старте
_check_channel = grpc.aio.insecure_channel(settings.check_service_grpc)
_check_stub = check_pb2_grpc.CheckServiceStub(_check_channel)

# НЕПРАВИЛЬНО — новый канал на каждый запрос
async def call_check_service():
    channel = grpc.aio.insecure_channel(...)  # дорого
    stub = check_pb2_grpc.CheckServiceStub(channel)
```

---

### 19. Не обрабатывай то, что не может произойти

Не добавляй `try/except` вокруг кода, который не может упасть. Не проверяй `if result is None` если метод всегда возвращает значение. Обрабатывай только реальные граничные случаи:

```python
# ПРАВИЛЬНО — реальная граничная ситуация
result = await session.get(LandCheck, check_id)
if result is None:
    raise NotFoundError(f"Check {check_id} not found")

# НЕПРАВИЛЬНО — защита от невозможного
data = ctx.get("LegalAgent")
if data is not None:  # LegalAgent всегда пишет в контекст если выполнился
    process(data)
```

---

### 20. Структура каждого сервиса

```
services/{service-name}/
  Dockerfile
  pyproject.toml
  app/
    __init__.py
    main.py         # gRPC server start / FastAPI app
    config.py       # pydantic-settings Settings class
    models.py       # SQLAlchemy ORM models (если есть БД)
    servicer.py     # gRPC servicer implementation
    constants.py    # статусы и прочие строковые константы
  alembic/          # если есть БД
  alembic.ini       # если есть БД
```

Не добавляй файлы пока в них нет необходимости. Три одинаковых случая — основание для нового модуля, один — нет.
