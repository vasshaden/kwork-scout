# JSON-контракт Kwork Scout

> **Канонический контракт обмена между субагентами Kwork Scout.**

---

## 1. Корневой объект (root schema)

```json
{
  "_schema_version": "2.1",
  "_timestamp": "2026-06-05T10:00:00Z",
  "_dry_run": false,
  "_pipeline_stage": "scoring",
  "_pipeline_error": null,

  "project": { /* §3 */ },
  "client":  { /* §4 */ },
  "analysis":  { /* §5 */ },
  "scoring":   { /* §6 */ },
  "proposal":  { /* §7 */ },
  "decision":  { /* §8 */ },
  "metadata":  { /* §9 */ }
}
```

| Поле | Тип | Обязательно | Заполняет | Описание |
|------|-----|-------------|-----------|----------|
| `_schema_version` | string `"2.1"` | ✓ | каждый агент | Версия контракта |
| `_timestamp` | string ISO 8601 UTC | ✓ | каждый агент | Время формирования текущего состояния (обновляется при каждом изменении) |
| `_dry_run` | bool | ✓ | orchestrator при старте | true → не отправлять реальные запросы, не писать в data/ |
| `_pipeline_stage` | enum | ✓ | каждый агент | Текущий этап: `parser` / `analyzer` / `scorer` / `client-analyst` / `proposal-writer` / `history-keeper` / `done` |
| `_pipeline_error` | string \| null | ✗ | orchestrator | null если OK, иначе описание ошибки (не FatalError — пайплайн продолжается) |

---

## 3. `project` (Parser)

```json
"project": {
  "id": 100001,
  "category": "telegram_bots",
  "categories": ["telegram_bots", "microservices"],
  "category_id": 46,
  "title": "Telegram-бот для онлайн-записи клиентов",
  "url": "https://kwork.ru/projects/100001",
  "description": "Нужен Telegram-бот на aiogram...",
  "budget_rub": 12000.0,
  "budget_ceiling_rub": 15000,
  "deadline_days": 7,
  "published_at": "2026-06-04 09:30:12",
  "time_left": "6 д. 23 ч.",
  "responses_count": 2,
  "is_higher_price": false
}
```

| Поле | Тип | Обязательно | Источник (kwork.ru) | Описание |
|------|-----|-------------|----------------------|----------|
| `id` | int | ✓ | `wants[].id` | 7-значный ID проекта |
| `category` | enum `telegram_bots` \| `microservices` \| `content_creation` | ✓ | (синтетическое) | Основная категория (первая в массиве `categories`) |
| `categories` | list of enum | ✓ | (синтетическое) | Все категории проекта (kwork.ru возвращает одни и те же проекты в нескольких категориях). Dedup в парсере сохраняет все |
| `category_id` | int | ✓ | `wants[].category_id` (string→int) | Подкатегория kwork.ru |
| `title` | string | ✓ | `wants[].name` | Заголовок |
| `url` | string URL | ✓ | constructed: `https://kwork.ru/projects/{id}` | URL страницы проекта |
| `description` | string | ✓ | `wants[].description` | Полное описание. Макс. 6000 символов (Analyzer режет длиннее) |
| `budget_rub` | number | ✓ | `parseFloat(wants[].priceLimit)` | Заявленный бюджет. **Не int** — может быть дробным (например `1500.00`) |
| `budget_ceiling_rub` | int | ✓ | `wants[].possiblePriceLimit` | Потолок, до которого заказчик готов рассматривать |
| `deadline_days` | int | ✓ | `int(wants[].max_days)` | Срок в днях. string→int |
| `published_at` | string | ✓ | `wants[].date_create` | Формат `"YYYY-MM-DD HH:MM:SS"` (без таймзоны, локальное время kwork.ru) |
| `time_left` | string | ✓ | `wants[].timeLeft` | Человекочитаемое (рус.), например `"6 д. 23 ч."` |
| `responses_count` | int | ✓ | `int(wants[].kwork_count)` | Число откликов на проект |
| `is_higher_price` | bool | ✓ | `wants[].isHigherPrice` | true → заявленный бюджет ниже потолка (заказчик готов платить больше) |

**Что НЕ входит в `project` (намеренно):**
- `tags[]` — нет в источнике. Извлекается в `analysis.extracted_skills[]`.
- `views_count` — не используется в скоринге (можно вернуть при надобности).
- `files[]` — игнорируется на этапе Parser.

---

## 4. `client` (Parser + Client Analyst)

```json
"client": {
  "nickname": "studio_maria",
  "user_id": 100001,
  "profile_url": "https://kwork.ru/user/studio_maria",
  "completed_projects": 12,
  "hired_percent": 80,
  "badges": ["Покупатель с подтверждённым телефоном", "Более 1 года с Kwork"],
  "risk": "low",
  "risk_source": "analyst",
  "red_flags": [],
  "rating": null
}
```

| Поле | Тип | Обязательно | Источник | Заполняет |
|------|-----|-------------|----------|-----------|
| `nickname` | string | ✓ | `user.username` | Parser |
| `user_id` | int | ✓ | `user.USERID` | Parser |
| `profile_url` | string URL | ✓ | `wantUserGetProfileUrl` | Parser |
| `completed_projects` | int | ✓ | `int(user.data.wants_count)` | Parser |
| `hired_percent` | int 0-100 | ✓ | `int(user.data.wants_hired_percent)` | Parser |
| `badges` | list of string | ✓ | `[b.badge.title for b in user.badges]` (извлечение title из вложенной структуры) | Parser |
| `risk` | enum `low` \| `medium` \| `high` \| `unknown` | ✓ | (computed) | Client Analyst |
| `risk_source` | enum `analyst` \| `inferred_from_badges` | ✗ | (computed) | Client Analyst |
| `red_flags` | list of string | ✗ (default `[]`) | (computed) | Client Analyst |
| `rating` | null | — | — | **DEPRECATED.** Всегда null. Оставлено для обратной совместимости, не использовать |

**Логика Client Analyst:**
- Если `hired_percent ≥ 80` И `badges` содержит `"Мощный покупатель"` → `risk: "low"`.
- Если `hired_percent < 30` ИЛИ `badges` пустой И `completed_projects < 3` → `risk: "high"`.
- Иначе `risk: "medium"`.
- Если данных недостаточно (например, нет бейджей) → `risk: "unknown"`, `red_flags: ["no_data"]`.
- Webfetch страницы профиля (`profile_url`) — для проектов со score ≥ review_score.

**Что НЕ входит в `client`:**
- Прямой `rating` (1-5) — отсутствует в kwork.ru.

---

## 5. `analysis` (Analyzer)

```json
"analysis": {
  "extracted_skills": ["python", "aiogram", "fastapi", "docker"],
  "category_skills_match": 0.8,
  "missing_skills": ["postgresql", "redis"],
  "complexity": "medium",
  "estimated_hours": 18,
  "hourly_rate_implied": 666.67,
  "score_prelim": 7,
  "score_reason_prelim": "Бюджет ниже медианы, но чёткое ТЗ",
  "red_flags": ["нечёткое ТЗ по части админки"],
  "green_flags": ["адекватный бюджет для категории"],
  "clarification_needed": ["детали админки", "деплой на какой сервер"],
  "should_process": true,
  "skip_reason": null
}
```

| Поле | Тип | Обязательно | Описание |
|------|-----|-------------|----------|
| `extracted_skills` | list of string | ✓ | Извлечённые из `description` навыки (keyword matching по `freelancer_profile.yaml::skills.{category}.core + adjacent`) |
| `category_skills_match` | float 0-1 | ✓ | `len(extracted ∩ profile.skills.{category}.core+adjacent) / len(profile.skills.{category}.core+adjacent)` |
| `missing_skills` | list of string | ✓ | Из core+adjacent, которых нет в extracted |
| `complexity` | enum `trivial` \| `low` \| `medium` \| `high` \| `expert` | ✓ | Оценка Analyzer'а на основе длины description, наличия интеграций, тестов, деплоя и т.п. |
| `estimated_hours` | int | ✓ | Грубая оценка (для budget_fit). Алгоритм — в `prompts/analyzer.txt` (День 4) |
| `hourly_rate_implied` | number | ✓ (если `should_process`) | `project.budget_rub / estimated_hours` |
| `score_prelim` | int 1-10 | ✓ | Предварительный скор (формула в §6.3 дизайн-дока) |
| `score_reason_prelim` | string | ✓ | 1-2 фразы почему |
| `red_flags` | list of string | ✓ (default `[]`) | Триггеры для skip/review |
| `green_flags` | list of string | ✓ (default `[]`) | Положительные сигналы |
| `clarification_needed` | list of string | ✓ (default `[]`) | Что Writer должен спросить в КП |
| `should_process` | bool | ✓ | false → проект идёт в `rejected.json` сразу |
| `skip_reason` | enum \| null | ✓ (если `!should_process`) | `avoid_keyword_{word}` \| `skill_mismatch` \| `duplicate` \| `description_too_short` \| `budget_below_min` \| `deadline_too_long` \| `no_description` |

**Правила `should_process = false`:**
- `description` пустой или < 50 символов.
- `avoid_keywords` (из профиля) найдены в `description` или `title` (case-insensitive, в т.ч. `1с`/`1c`).
- Дубль по `project.id` в `data/history.json` (дедупликация Analyzer'ом).
- `complexity = "expert"` И `estimated_hours > 80` (проект слишком большой — выйдем за рамки).
- `category_skills_match < 0.2` (профиль не подходит).

---

## 6. `scoring` (Scorer)

```json
"scoring": {
  "score": 67,
  "verdict": "review",
  "score_breakdown": {
    "tech_match": 80,
    "budget_fit": 30,
    "timeline_fit": 80,
    "client_quality": 70,
    "competition": 80
  },
  "risks": ["требуют правки после сдачи", "низкий hourly"],
  "recommendation": "Средний матч. Стоит ли брать — зависит от загрузки."
}
```

| Поле | Тип | Обязательно | Описание |
|------|-----|-------------|----------|
| `score` | int 0-100 | ✓ | `Σ(weight_i × component_i)`, округлено |
| `verdict` | enum `apply` \| `review` \| `skip` | ✓ | `apply` если score ≥ `auto_apply_score` (80), `review` если ≥ `review_score` (60), иначе `skip` |
| `score_breakdown.tech_match` | int 0-100 | ✓ | `category_skills_match × 100` (без штрафа за missing — они в `risks`) |
| `score_breakdown.budget_fit` | int 0-100 | ✓ | По формуле из `freelancer_profile.yaml::budget_fit_thresholds` × `project.is_higher_price` бонус |
| `score_breakdown.timeline_fit` | int 0-100 | ✓ | По `timeline_fit_thresholds` |
| `score_breakdown.client_quality` | int 0-100 | ✓ | `hired_percent × 0.7 + min(badges_count × 10, 30)` |
| `score_breakdown.competition` | int 0-100 | ✓ | По `competition_thresholds` (обратная функция от `responses_count`) |
| `risks` | list of string | ✓ (default `[]`) | Агрегация `analysis.red_flags` + `client.red_flags` + специфические скоринговые |
| `recommendation` | string | ✓ | 1-2 фразы: `"Сильный матч. Готовим КП."` / `"Средний матч. Ручное решение."` / `"Слабый. Пропускаем."` |

**Пересчёт score с учётом `is_higher_price`:**
- Если `is_higher_price == true` И `client.risk != "high"`, к `budget_fit` применяется множитель 1.1 (но не выше 100). Это сигнал, что заказчик готов платить больше.

**`verdict == "skip"` И `analysis.skip_reason` непустой →** в `decision.status = "skipped"`, `decision.rejection_reason = analysis.skip_reason`.

---

## 7. `proposal` (Proposal Writer)

```json
"proposal": {
  "draft": "Здравствуйте! Меня зовут Василий, делаю Telegram-ботов на aiogram...",
  "valid": true,
  "issues": [],
  "length_chars": 1240,
  "iterations": 1
}
```

| Поле | Тип | Обязательно | Описание |
|------|-----|-------------|----------|
| `draft` | string | ✓ | Текст КП (≤ 1500 символов) |
| `valid` | bool | ✓ | Прошла ли валидация (см. ниже) |
| `issues` | list of string | ✓ (default `[]`) | Список нарушений (пустой если valid=true) |
| `length_chars` | int | ✓ | `len(draft)` |
| `iterations` | int 1-2 | ✓ | Число итераций самокоррекции (≤ 2 по design doc) |

**Правила валидации (`valid = true` требует все):**
- `length_chars ≤ 1500` и `≥ 400`
- Начало содержит `"Здравствуйте"` / `"Добрый день"` / `"Привет"` (case-insensitive)
- Есть цена: regex `(\d+[\s\d]*)\s*(₽|руб|RUB)`
- Есть срок: regex `(день|дня|дней|недел|месяц)`
- Нет запрещённых фраз (из `freelancer_profile.yaml::proposal.forbidden_phrases`)
- Последний абзац содержит вопросительный знак `?` (задаёт уточняющий вопрос)

**Если `valid == false` после 2 итераций:** `proposal` всё равно сохраняется, `decision.status = "skipped"`, `decision.rejection_reason = "proposal_validation_failed"`.

---

## 8. `decision` (Orchestrator)

```json
"decision": {
  "status": "proposed",
  "rejection_reason": null,
  "decided_at": "2026-06-05T10:05:32Z",
  "decided_by": "orchestrator"
}
```

| Поле | Тип | Обязательно | Описание |
|------|-----|-------------|----------|
| `status` | enum `proposed` \| `reviewed` \| `skipped` | ✓ | Итоговое решение |
| `rejection_reason` | enum \| null | ✓ (если `status == "skipped"`) | Дубль `analysis.skip_reason` + `proposal_validation_failed` |
| `decided_at` | string ISO 8601 | ✓ | Когда принято решение |
| `decided_by` | string | ✓ | `"orchestrator"` или `"auto_threshold"` |

**Логика orchestrator'а:**
- `verdict == "apply"` И `client.risk != "high"` И `proposal.valid == true` → `status: "proposed"`, `rejection_reason: null`.
- `verdict == "apply"` И `client.risk == "high"` → `status: "skipped"`, `rejection_reason: "client_risk_high"`.
- `verdict == "review"` → `status: "reviewed"` (ручное решение Василия).
- `verdict == "skip"` → `status: "skipped"`, `rejection_reason = analysis.skip_reason`.

---

## 9. `metadata` (Orchestrator)

```json
"metadata": {
  "processing_time_sec": 35,
  "model_calls": { "haiku": 5, "sonnet": 1 },
  "estimated_cost_usd": 0.08,
  "agents_run": ["parser", "analyzer", "scorer", "client-analyst", "proposal-writer"],
  "errors": []
}
```

| Поле | Тип | Обязательно | Описание |
|------|-----|-------------|----------|
| `processing_time_sec` | int | ✓ | Полное время обработки проекта |
| `model_calls.haiku` | int | ✓ | Число вызовов Haiku (все субагента кроме writer/orchestrator) |
| `model_calls.sonnet` | int | ✓ | Число вызовов Sonnet (writer + orchestrator) |
| `estimated_cost_usd` | number | ✓ | Грубая оценка (для лимита $0.12 на 10 проектов) |
| `agents_run` | list of string | ✓ | Список агентов, которых проект прошёл |
| `errors` | list of {agent, message} | ✓ (default `[]`) | Некритичные ошибки (пайплайн не упал) |

---

## 10. Полный пример контракта

(Минимальный, для проекта, который прошёл до Writer.)

```json
{
  "_schema_version": "2.1",
  "_timestamp": "2026-06-05T10:05:32Z",
  "_dry_run": true,
  "_pipeline_stage": "done",
  "_pipeline_error": null,

  "project": {
    "id": 100001,
    "category": "telegram_bots",
    "category_id": 46,
    "title": "Telegram-бот для онлайн-записи клиентов",
    "url": "https://kwork.ru/projects/100001",
    "description": "Нужен Telegram-бот на aiogram 3.x для онлайн-записи клиентов к мастеру маникюра. Бот должен показывать свободные слоты по датам, принимать запись, отправлять напоминание за 24 часа до визита, иметь админку на FastAPI. Интеграция с Google Sheets для хранения записей и бэкапа. Деплой на VPS, Docker. Бюджет обсуждаем в указанном диапазоне.",
    "budget_rub": 12000.0,
    "budget_ceiling_rub": 15000,
    "deadline_days": 7,
    "published_at": "2026-06-04 09:30:12",
    "time_left": "6 д. 23 ч.",
    "responses_count": 2,
    "is_higher_price": false
  },

  "client": {
    "nickname": "studio_maria",
    "user_id": 100001,
    "profile_url": "https://kwork.ru/user/studio_maria",
    "completed_projects": 12,
    "hired_percent": 80,
    "badges": ["Покупатель с подтверждённым телефоном", "Более 1 года с Kwork"],
    "risk": "low",
    "risk_source": "inferred_from_badges",
    "red_flags": [],
    "rating": null
  },

  "analysis": {
    "extracted_skills": ["python", "aiogram", "fastapi", "docker"],
    "category_skills_match": 0.8,
    "missing_skills": ["postgresql", "redis"],
    "complexity": "medium",
    "estimated_hours": 18,
    "hourly_rate_implied": 666.67,
    "score_prelim": 6,
    "score_reason_prelim": "Бюджет ниже медианы, но чёткое ТЗ и опытный заказчик",
    "red_flags": ["hourly ниже min"],
    "green_flags": ["чёткое ТЗ", "опытный заказчик"],
    "clarification_needed": ["детали админки", "какой VPS-тариф"],
    "should_process": true,
    "skip_reason": null
  },

  "scoring": {
    "score": 67,
    "verdict": "review",
    "score_breakdown": {
      "tech_match": 80,
      "budget_fit": 30,
      "timeline_fit": 80,
      "client_quality": 76,
      "competition": 80
    },
    "risks": ["hourly ниже min"],
    "recommendation": "Средний матч. Стоит ли брать — зависит от загрузки."
  },

  "proposal": {
    "draft": "Здравствуйте, Мария! Меня зовут Василий, делаю Telegram-ботов на aiogram 3.x. Понимаю задачу: бот записи с админкой на FastAPI, интеграция с Google Sheets, деплой в Docker на VPS. Срок — 6 дней. Цена — 12 000 ₽ (соответствует вашему бюджету). Уточняющий вопрос: админка нужна на том же VPS или отдельно? Готов созвониться для обсуждения деталей.",
    "valid": true,
    "issues": [],
    "length_chars": 458,
    "iterations": 1
  },

  "decision": {
    "status": "reviewed",
    "rejection_reason": null,
    "decided_at": "2026-06-05T10:05:32Z",
    "decided_by": "auto_threshold"
  },

  "metadata": {
    "processing_time_sec": 35,
    "model_calls": {"haiku": 5, "sonnet": 1},
    "estimated_cost_usd": 0.08,
    "agents_run": ["parser", "analyzer", "scorer", "client-analyst", "proposal-writer"],
    "errors": []
  }
}
```

---

## 11. Валидация (инварианты, которые должен проверять orchestrator)

1. **Всегда есть `_schema_version: "2.1"`** в любом выходе.
2. **`_dry_run: true`** → ни один файл в `data/` не изменяется, `webfetch` запрещён.
3. **`project.id` уникален** в рамках одного прогона. Дубли → отбрасываются Analyzer'ом или History Keeper'ом.
4. **`should_process: false`** ⇔ `skip_reason != null` ⇔ `decision.status == "skipped"`.
5. **`verdict: "apply"` И `client.risk: "high"`** → `decision.status == "skipped"` (не отправляем КП плохому клиенту).
6. **`proposal.iterations <= 2`** (жёсткий лимит из дизайн-дока).
7. **`scoring.score` ∈ [0, 100]**, `verdict` соответствует порогам.
8. **Бюджет:** `budget_rub > 0` И `budget_rub <= budget_ceiling_rub`.

---

## 12. Матрица «агент → поля»

| Поле | Parser | Analyzer | Scorer | Client Analyst | Writer | Orchestrator |
|------|:------:|:--------:|:------:|:--------------:|:------:|:------------:|
| `_schema_version` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `_timestamp` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `_dry_run` | — | — | — | — | — | ✓ (на входе) |
| `_pipeline_stage` | parser | analyzer | scorer | client-analyst | proposal-writer | done |
| `project.*` | ✓ | (read) | (read) | (read) | (read) | (read) |
| `client.nickname`, `user_id`, `profile_url` | ✓ | (read) | (read) | (read) | (read) | (read) |
| `client.completed_projects`, `hired_percent`, `badges` | ✓ | (read) | (read) | (read) | (read) | (read) |
| `client.risk`, `risk_source`, `red_flags` | — | — | — | ✓ | (read) | (read) |
| `analysis.*` | — | ✓ | (read) | — | (read) | (read) |
| `scoring.*` | — | — | ✓ | (read) | (read) | (read) |
| `proposal.*` | — | — | — | — | ✓ | (read) |
| `decision.*` | — | — | — | — | — | ✓ |
| `metadata.*` | — | (accrue) | (accrue) | (accrue) | (accrue) | ✓ |

**Примечание:** «(read)» — агент читает, но не меняет. «(accrue)» — оркестратор аккумулирует.

---

## 13. Edge cases (как агенты должны обрабатывать)

### 13.1 Битые/неполные проекты в выдаче kwork.ru

| Состояние | Что делает Parser | Что делает Analyzer |
|-----------|-------------------|---------------------|
| `user == null` или `user == {}` | `client.nickname = "unknown"`, `client.user_id = 0`, `client.badges = []`, `client.completed_projects = 0`, `client.hired_percent = 0` | `should_process = false`, `skip_reason = "no_client_data"` |
| `description` пустой или < 50 символов | (не фильтрует) | `should_process = false`, `skip_reason = "description_too_short"` |
| `priceLimit` отсутствует | `budget_rub = 0`, `budget_ceiling_rub = 0` | `should_process = false`, `skip_reason = "no_budget"` |
| `isWantActive = false` или `status != "active"` | (не фильтрует) | `should_process = false`, `skip_reason = "project_inactive"` |

### 13.2 Конфликты при дедупликации

Если `project.id` уже в `data/history.json`:
- Analyzer: `should_process = false`, `skip_reason = "duplicate"`, **НО не пропускает** (помечает `analysis.duplicate_of_history_entry = true` для трассировки).
- History Keeper: финальная проверка, не пишет в `history.json` повторно (но если `status` изменился — обновляет запись).

### 13.3 Ошибка агента

Если Parser/Analyzer/Scorer/ClientAnalyst/Writer упал с exception:
- `_pipeline_error = "agent_name: error message"` (не FatalError, просто лог).
- Агент, который должен был заполнить свои поля, оставляет их в дефолтном состоянии (`null` / `[]` / `0`).
- Orchestrator решает судьбу: если критичный шаг упал (Parser, Analyzer) → `decision.status = "skipped"`, `rejection_reason = "pipeline_error_{agent}"`.

---

## 14. Что НЕ входит в контракт (но может появиться в будущем)

- `project.files[]` (прикреплённые ТЗ от заказчика) — не парсим, но можем маркировать.
- Полный текст `description` без обрезки (сейчас max 6000 символов).
- История откликов (отдельный endpoint API kwork).
- Метрики по `wants_hired_percent` за период (сейчас только total).
- Multi-language (en) проекты — пока не фильтруем.
- `isNeural: true` — пока не используем в скоринге.
- A/B варианты `scoring_weights` (нужны 50+ прогонов для калибровки).

---

## 15. Связанные документы

- **Дизайн-док** (источник архитектуры): `AGENT_SYSTEM_OPENCODE_V2.md`.
- **Профиль** (источник навыков/порогов): `config/freelancer_profile.yaml`.
- **Реальная вёрстка kwork.ru**: `manual-workflow.md` §2.
- **Тестовая фикстура**: `tests/kwork-sample.html` (v2, реалистичный формат).
- **AGENTS.md** (правила для AI-агентов): жёсткие правила, в т.ч. про секреты.
