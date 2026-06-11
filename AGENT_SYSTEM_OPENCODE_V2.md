# Агентная система «Kwork Scout» для OpenCode — Версия 2.1 (v2.1, live-only)

> **«Production-ready. Единый контракт v2.1, least privilege, live-парсинг, само-валидация, конфигурируемый скоринг.»**

---

## 1. Архитектура (7 шагов + Оркестратор + Web Monitor)

```
┌─────────────────────────────────────────────────────────────────┐
│  🎯 Orchestrator (build agent) — Skill: kwork-pipeline          │
│     Запуск пайплайна, агрегация, отчёт, обработка ошибок        │
└────────────────────────────┬────────────────────────────────────┘
                             │
               ┌─────────────┴─────────────┐
               ▼                           ▼
┌──────────────────────────┐    ┌─────────────────────────────────┐
│ [0] CHECK AUTH           │    │ 1. PARSER (режим all)           │
│ tools/kwork_check_auth.py│    │ tools/kwork_live_parse.py all   │
│ Playwright + cookies.txt │    │ 1 браузер, 3 категории         │
│ вход → cookies.txt       │    │ глобальный дедуп по ID         │
└──────────────────────────┘    └──────────────┬──────────────────┘
                                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. kwork-analyzer (subagent или standalone python)              │
│     Фильтр → дедупликация (history.json) → предварительный скор │
│     Инструменты: read, grep, glob                               │
│     Выход: JSON со score_prelim, skills_match, should_process   │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. kwork-scorer (subagent или standalone python)                │
│     Финальный взвешенный скор 1–100 по профилю фрилансера       │
│     Инструменты: read                                           │
│     Вход: ProjectAnalysis + freelancer_profile.yaml             │
│     Выход: FitScore (score, verdict, breakdown, risks, recs)    │
└────────────────────────────┬────────────────────────────────────┘
                             │
               ┌──────────────┴──────────────┐
               ▼                             ▼
┌────────────────────────────────┐    ┌─────────────────────────┐
│ 4. kwork-client-analyst        │    │ (ПАРАЛЛЕЛЬНО для score  │
│    (subagent)                  │    │  ≥ review_threshold)    │
│    Проверка заказчика          │    │                         │
│    Инструменты: read           │    │                         │
│    Выход: risk, red_flags[]    │    │                         │
└─────────────┬──────────────────┘    └───────────┬─────────────┘
              │                                   │
              └───────────────┬───────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. kwork-proposal-writer (subagent или standalone python)       │
│     Пишет КП → сам валидирует (≤1500 симв, цена, срок, обращ.)  │
│     Инструменты: read, write                                    │
│     ✅ Self-correction до 2 итераций                            │
│     Выход: валидный черновик КП + metadata                      │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 6. kwork-history-keeper (subagent или standalone python)         │
│     Сохраняет: history.json + rejected.json (причины отказов)   │
│     Инструменты: read, write                                    │
│     Атомарное обновление обоих файлов                           │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
                    📄 Финальный отчёт
          (Markdown таблица + proposals/*.md + run_*.json)

┌─────────────────────────────────────────────────────────────────┐
│ 7. Web Monitor (опционально) — observability в реальном времени │
│     FastAPI + SSE + HTMX на http://localhost:8080               │
│     Инструменты: write_status.py ( оркестратор пишет статус)    │
│     UI: Pipeline Graph (mermaid), Metrics, Projects Table,      │
│         Logs, Errors, History, Download KP (.md)                │
└────────────────────────────┬────────────────────────────────────┘
```

---

## 2. Конфигурация OpenCode (`opencode.json`)

```jsonc
{
  "$schema": "https://opencode.ai/config.json",

  "model": "opencode/nemotron-3-ultra-free",
  "small_model": "opencode/nemotron-3-ultra-free",

  "permission": {
    "*": "allow",
    "bash": {
      "*": "ask",
      "python3 *": "allow",
      "curl *": "allow",
      "pip install *": "allow"
    },
    "edit": "allow",
    "external_directory": "ask",
    "skill": {
      "*": "allow",
      "internal-*": "deny"
    }
  },

  "agent": {
    "build": {
      "mode": "primary",
      "model": "opencode/nemotron-3-ultra-free",
      "prompt": "{file:./prompts/orchestrator.txt}",
      "permission": {
        "bash": "allow",
        "edit": "allow",
        "task": { "*": "allow", "kwork-*": "allow" }
      }
    },

    "kwork-parser": {
      "description": "Парсит новые проекты с kwork.ru по одной категории",
      "mode": "subagent",
      "model": "opencode/nemotron-3-ultra-free",
      "prompt": "{file:./prompts/parser.txt}",
      "permission": {
        "bash": "allow", "read": "allow", "write": "allow", "webfetch": "allow",
        "grep": "deny", "glob": "deny", "edit": "deny"
      }
    },

    "kwork-analyzer": {
      "description": "Фильтрует, дедуплицирует, предварительный скор проектов",
      "mode": "subagent",
      "model": "opencode/nemotron-3-ultra-free",
      "prompt": "{file:./prompts/analyzer.txt}",
      "permission": {
        "read": "allow", "grep": "allow", "glob": "allow",
        "bash": "deny", "write": "deny", "edit": "deny", "webfetch": "deny"
      }
    },

    "kwork-scorer": {
      "description": "Финальный взвешенный скор 1-100 по профилю фрилансера",
      "mode": "subagent",
      "model": "opencode/nemotron-3-ultra-free",
      "prompt": "{file:./prompts/scorer.txt}",
      "permission": {
        "read": "allow",
        "bash": "deny", "write": "deny", "edit": "deny",
        "webfetch": "deny", "grep": "deny", "glob": "deny"
      }
    },

    "kwork-client-analyst": {
      "description": "Анализирует репутацию и надёжность заказчика",
      "mode": "subagent",
      "model": "opencode/nemotron-3-ultra-free",
      "prompt": "{file:./prompts/client-analyst.txt}",
      "permission": {
        "read": "allow", "webfetch": "allow", "websearch": "allow",
        "bash": "deny", "write": "deny", "edit": "deny",
        "grep": "deny", "glob": "deny"
      }
    },

    "kwork-proposal-writer": {
      "description": "Пишет и валидирует КП (≤1500 симв, self-correction)",
      "mode": "subagent",
      "model": "opencode/nemotron-3-ultra-free",
      "prompt": "{file:./prompts/proposal-writer.txt}",
      "permission": {
        "read": "allow", "write": "allow",
        "bash": "deny", "edit": "deny", "webfetch": "deny",
        "grep": "deny", "glob": "deny"
      }
    },

    "kwork-history-keeper": {
      "description": "Сохраняет историю проектов и причины отказов",
      "mode": "subagent",
      "model": "opencode/nemotron-3-ultra-free",
      "prompt": "{file:./prompts/history-keeper.txt}",
      "permission": {
        "read": "allow", "write": "allow",
        "bash": "deny", "edit": "deny", "webfetch": "deny",
        "grep": "deny", "glob": "deny"
      }
    }
  },

  "skills": { "paths": ["./skills/"] }
}
```

---

## 3. Единый JSON-контракт v2.1 (единственный источник правды)

```json
{
  "_schema_version": "2.1",
  "_timestamp": "2026-06-05T10:00:00Z",
  "_dry_run": false,

  "project": {
    "id": "kwork-123456",
    "title": "Telegram-бот для записи клиентов",
    "url": "https://kwork.ru/projects/123456",
    "description": "Нужен бот на aiogram с интеграцией Google Sheets...",
    "budget_rub": 15000,
    "deadline_days": 7,
    "category": "telegram_bots",
    "published_at": "2026-06-02T08:00:00Z",
    "client_nickname": "customer123",
    "tags": ["python", "aiogram", "postgresql", "docker"]
  },

  "client": {
    "nickname": "customer123",
    "rating": 4.8,
    "completed_projects": 42,
    "risk": "low",
    "red_flags": []
  },

  "analysis": {
    "score_prelim": 7,
    "score_reason_prelim": "Хороший бюджет, чёткое ТЗ",
    "skills_match": 0.9,
    "missing_skills": [],
    "complexity": "medium",
    "estimated_hours": 40,
    "hourly_rate_implied": 375,
    "red_flags": ["нечёткое ТЗ"],
    "green_flags": ["адекватный бюджет"],
    "clarification_needed": ["детали админки"],
    "should_process": true,
    "skip_reason": null
  },

  "scoring": {
    "score": 87,
    "verdict": "apply",
    "score_breakdown": {
      "tech_match": 95,
      "budget_fit": 80,
      "timeline_fit": 90,
      "client_quality": 85,
      "competition": 70
    },
    "risks": ["требуют правки после сдачи"],
    "recommendation": "Сильный матч. Готовим предложение с акцентом на опыт с aiogram + постоплата."
  },

  "proposal": {
    "draft": "Здравствуйте, Имя! Меня зовут Василий...",
    "valid": true,
    "issues": [],
    "length_chars": 1240,
    "iterations": 1
  },

  "decision": {
    "status": "proposed",
    "rejection_reason": null
  },

  "metadata": {
    "processing_time_sec": 35,
    "model_calls": { "nemotron-3": 6 },
    "estimated_cost_usd": 0.0
  }
}
```

---

## 4. Профиль фрилансера (`config/freelancer_profile.yaml`)

**Актуальная версия (v2.0, indicators-driven):** см. `config/freelancer_profile.yaml`. Ниже — пример (упрощённый, без indicators).

```yaml
skills:
  telegram_bots:
    indicators: ["чат-бот", "телеграм", "telegram", "tg", "бот", "рассылка", "подписка", "оплата", "чат", "bot", "ассистент", "автоматизация", "парсинг"]
    tech_stack: [python, aiogram, python-telegram-bot, telethon, fastapi, postgresql, redis, asyncio, celery, ai, openai, gpt]
    adjacent: ["нейросеть", "ai", "нейро", "интеграция", "api", "веб-хук", "webhook"]
    avoid: [viber-api, whatsapp-api, vk-bot, vk парсинг]
  microservices:
    indicators: ["микросервис", "backend", "api", "сервер", "высоконагружен", "golang", "go", "grpc", "архитектура", "разработка сервис", "серверная часть", "rest", "rest api"]
    tech_stack: [go, golang, postgresql, grpc, protobuf, python, fastapi, redis, docker, kubernetes, prometheus, grafana, opentelemetry]
    adjacent: ["высокая нагрузка", "highload", "база данных", "бэкенд", "миграция", "оптимизация", "масштабирование"]
    avoid: [soap, websphere]
  content_creation:
    indicators: ["статья", "блог", "devrel", "техническая документация", "документация", "контент", "раскадровка", "сценарий", "туториал", "гайд", "учебный материал", "технический писатель", "technical writing"]
    tech_stack: [python, postgresql, microservices, fastapi, go, devops, markdown, sphinx, mkdocs]
    adjacent: ["автор", "писатель", "редакция", "text", "курс", "обучение", "knowledge base", "база знаний"]
    avoid: [seo-copywriting, рерайт, копирайтинг-маркетинг, smm, smm-менеджмент]

avoid_keywords: [wordpress, 1с, 1c, bitrix, битрикс, нативный ios, нативный android, ios native, android native, native ios, native android, 1c-bitrix, 1с-битрикс]

preferences:
  min_hourly_rate: 200
  max_project_duration_days: 14
  preferred_categories: [telegram_bots, microservices, content_creation]

scoring_weights:
  tech_match: 0.30
  budget_fit: 0.25
  timeline_fit: 0.10
  client_quality: 0.25
  competition: 0.10

thresholds:
  auto_apply_score: 65
  review_score: 55
  skip_below: 40
  client_risk_block: "high"
```

---

## 5. Веб-монитор (Web Monitor) — Observability

### 5.1 Архитектура

```
┌─────────────────────────────────────────────────────────────────┐
│  Оркестратор                                                    │
│  ├─ python3 tools/write_status.py init --run-id ...             │
│  ├─ python3 tools/write_status.py start --step "parser..."      │
│  ├─ <task: kwork-parser>                                        │
│  ├─ python3 tools/write_status.py finish --step "parser..."     │
│  └─ ... (для каждого шага)                                      │
└────────────────────────────┬────────────────────────────────────┘
                             │ (logs/pipeline_status.json)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  FastAPI Server (tools/monitor_server.py)                       │
│  ├─ GET  /              → templates/monitor.html                │
│  ├─ GET  /api/status    → текущий JSON статуса                  │
│  ├─ GET  /api/history   → список последних 8 запусков           │
│  ├─ GET  /api/runs/{id} → полный статус конкретного запуска     │
│  ├─ GET  /api/events    → SSE (обновления каждые 0.5с)          │
│  └─ GET  /api/proposal/{filename} → скачивание .md КП           │
└────────────────────────────┬────────────────────────────────────┘
                             │ (SSE: Server-Sent Events)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Browser (HTMX + mermaid.js + Tailwind CSS)                     │
│  ├─ Pipeline Graph: mermaid диаграмма с цветами шагов           │
│  ├─ Метрики: Total, Apply/Review/Skip, Avg Score, Budget (₽)   │
│  ├─ Steps: список с таймингами и статусами                      │
│  ├─ Projects: таблица (ID, Title, Budget, Score, Verdict, Risk, КП) │
│  ├─ Logs: stdout/stderr агентов                                 │
│  └─ Errors: список ошибок                                       │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 Компоненты

| Файл | Назначение |
|------|------------|
| `tools/monitor_server.py` | FastAPI backend: SSE, REST API, авто-открытие браузера |
| `templates/monitor.html` | HTMX фронтенд: mermaid граф, таблица проектов, логи, метрики, история |
| `tools/write_status.py` | Утилита записи `pipeline_status.json` (init, start, finish, metrics, project, error, save-run) |
| `logs/pipeline_status.json` | Runtime статус (обновляется оркестратором) |
| `logs/runs/` | История запусков (последние 8) |

### 5.3 API Endpoints

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/` | HTML-дашборд |
| GET | `/api/status` | Текущий статус (JSON) |
| GET | `/api/history` | Список последних 8 запусков |
| GET | `/api/runs/{run_id}` | Полный статус конкретного запуска |
| GET | `/api/events` | SSE-поток (обновления каждые 0.5с) |
| GET | `/api/proposal/{filename}` | Скачивание .md КП (attachment) |

### 5.4 Интеграция с оркестратором

Оркестратор вызывает `tools/write_status.py` на каждом шаге:

```bash
# Инициализация
python3 tools/write_status.py init --run-id $(date +%Y-%m-%d_%H-%M-%S) --mode live --categories telegram_bots,microservices,content_creation

# Шаг: parser
python3 tools/write_status.py start --step "parser(all)"
python3 kwork_live_parse.py all --max-pages 3
python3 tools/write_status.py finish --step "parser(all)" --summary "36 projects"

# Метрики после analyzer/scorer
python3 tools/write_status.py metrics --total 26 --kept 18 --apply 3 --review 7 --skip 8 --avg-score 52.4

# Проекты в таблицу
python3 tools/write_status.py project --id 3192454 --title "Telegram-бот" --budget 20000 --category telegram_bots --score 63 --verdict review --risk low

# Сохранить запуск
python3 tools/write_status.py save-run
```

### 5.5 Запуск монитора

```bash
cd opencode
setsid .venv/bin/python3 tools/monitor_server.py > /tmp/monitor.log 2>&1 &
# Открыть http://localhost:8080
```

---

## 6. Скилл-оркестратор (`skills/kwork-pipeline/SKILL.md`)

```markdown
---
name: kwork-pipeline
description: Запускает пайплайн поиска и анализа проектов с kwork.ru
license: MIT
compatibility: opencode
metadata:
  author: vasiliy
  workflow: freelance
---

## Что я делаю

Полный цикл обработки проектов:

1. **Проверка авторизации** — kwork_check_auth.py (cookies.txt)
2. **Парсинг** — kwork_live_parse.py all (1 браузер, 3 категории, дедуп по ID)
3. **Анализ** — kwork-analyzer (фильтр + дедуп + предварительный скор)
4. **Скоринг** — kwork-scorer (финальный взвешенный скор 1-100)
5. **Проверка заказчика** — kwork-client-analyst (параллельно для score ≥ review)
6. **КП** — kwork-proposal-writer (для score ≥ auto_apply И risk != high)
7. **История** — kwork-history-keeper (атомарное сохранение)

## Запуск

- `python3 kwork_run.py` (standalone pipeline)
- `Найди проекты на kwork` или `@kwork-pipeline` (OpenCode skill)

## Результат

- Markdown-отчёт с таблицей: ID | Название | Бюджет | Score | Verdict | Risk | КП
- Файлы КП в `proposals/YYYY-MM-DD_kwork-ID.md`
- Лог в `logs/run_*.json`
- Обновлённые `data/history.json`, `data/rejected.json`
```

---

## 6. Системные промпты агентов

### 6.1 Orchestrator (`prompts/orchestrator.txt`)

```
Ты — оркестратор мультиагентной системы Kwork Scout v2.1.

ПОРЯДОК РАБОТЫ:
1. Запусти kwork-parser (режим all) — 1 вызов, все 3 категории, дедуп по ID.
2. Передай список в kwork-analyzer.
3. Для проектов с should_process=true запусти kwork-scorer.
4. Для проектов с score ≥ review_threshold запусти kwork-client-analyst.
5. Для проектов с score ≥ auto_apply_threshold И client.risk != "high" запусти kwork-proposal-writer.
6. Запусти kwork-history-keeper со всеми результатами.
7. Сформируй Markdown-отчёт: таблица + сводка по затратам/времени.

ПРАВИЛА:
- При ошибке любого агента: логируй, но НЕ останавливай пайплайн.
- live-режим: используй cookies.txt для авторизации.
- Лимит параллельных тасков: 5 одновременно.
- Таймаут на агента: 60 сек.
```

### 6.2 Parser (`prompts/parser.txt`)

```
Ты — парсер проектов с kwork.ru (режим all или одна категория).

КАТЕГОРИИ: telegram_bots | microservices | content_creation | all

ИНСТРУМЕНТЫ: bash (вызов tools/kwork_live_parse.py), read (результаты).

ИЗВЛЕКАЙ ИЗ stateData.wants[]: id, title, budget_rub (priceLimit), budget_ceiling_rub (possiblePriceLimit), deadline_days, description, url, category, client_nickname, published_at, responses_count, is_higher_price.

ВЫХОД (JSON):
{
  "_schema_version": "2.1",
  "_batch": { "category": "all", "count": N, "error": null },
  "projects": [...]
}

В режиме all: запускай tools/kwork_live_parse.py all --max-pages 3.
Ошибка парсинга → верни error с описанием, projects: [].
```

### 6.3 Analyzer (`prompts/analyzer.txt`)

```
Ты — аналитик проектов. Вход: список проектов от parser.

ДЛЯ КАЖДОГО ПРОЕКТА:
1. Фильтр по навыкам (Python/aiogram/Go/PostgreSQL/микросервисы/контент).
2. Дедупликация: проверь data/history.json по project.id.
3. Предварительный скор 1-10:
   - Бюджет: <5000=2, 5000-15000=5, >15000=8, >30000=10
   - Ясность ТЗ: 1-10 (по описанию)
   - Срок: 1-3д=10, 4-7д=7, >7д=5
   - score_prelim = среднее
4. skills_match: доля навыков из профиля, найденных в проекте.
5. Определи: complexity, estimated_hours, hourly_rate_implied, red/green flags.

ВЫХОД: массив проектов с добавленными полями:
score_prelim, score_reason_prelim, skills_match, missing_skills,
complexity, estimated_hours, hourly_rate_implied,
red_flags[], green_flags[], clarification_needed[],
should_process (bool), skip_reason (string|null)
```

### 6.4 Scorer (`prompts/scorer.txt`) — НОВЫЙ АГЕНТ

```
Ты — финальный скоринг-агент. Вход: ProjectAnalysis + freelancer_profile.yaml.

РАСЧЁТ SCORE 1-100 (взвешенный по scoring_weights):
1. tech_match (0-100): skills_match * 100, штраф за missing_skills.
2. budget_fit (0-100): hourly_rate_implied vs min_hourly_rate.
   ≥ min_rate*1.5=100, =min_rate=70, <min_rate=30, <min_rate*0.5=0.
3. timeline_fit (0-100): deadline_days vs max_project_duration_days.
   ≤3д=100, ≤7д=80, ≤14д=60, >14д=30.
4. client_quality (0-100): из client.rating (rating*20), completed_projects бонус.
5. competition (0-100): responses_count эвристика (0=100, 1-3=80, 4-7=60, 8+=40).

ИТОГО: score = Σ(weight_i * component_i). Округли до целого.

VERDICT:
- score ≥ auto_apply_threshold → "apply"
- score ≥ review_threshold → "review"
- иначе → "skip"

RISKS: агрегируй из analysis.red_flags + client.red_flags + competition.
RECOMMENDATION: 1-2 фразы для freelancer.

ВЫХОД (JSON): score, verdict, score_breakdown, risks[], recommendation.
```

### 6.5 Client Analyst (`prompts/client-analyst.txt`)

```
Ты — аналитик заказчиков. Вход: client_nickname.

ДЕЙСТВИЯ:
1. webfetch https://kwork.ru/user/<nickname>
2. Извлеки: rating, completed_projects, reviews (последние 5).
3. websearch "<nickname> kwork отзывы" если мало данных.
4. Оцени risk: low/medium/high/unknown.
5. red_flags: частые правки, неоплата, грубость, неадекватное ТЗ, отмены.

ВЫХОД (JSON):
{
  "nickname": "...",
  "rating": 4.8,
  "completed_projects": 42,
  "risk": "low",
  "red_flags": []
}
Нет данных → risk="unknown", red_flags=["no_data"].
```

### 6.6 Proposal Writer (`prompts/proposal-writer.txt`)

```
Ты — автор коммерческих предложений. Вход: полный контракт проекта.

ШАБЛОН КП:
1. Приветствие по имени (если есть в профиле заказчика) или нейтральное.
2. Подтверждение понимания задачи (1-2 фразы, конкретика).
3. Релевантный опыт (2-3 проекта, стек, результат).
4. Конкретная цена (budget_rub ±10%) и срок (deadline_days - 1 день запас).
5. Уточняющий вопрос по clarification_needed.

ВАЛИДАЦИЯ (автоматическая, до 2 итераций):
- Длина ≤1500 символов
- Есть обращение (Привет/Здравствуйте/Добрый день)
- Есть цена (число + ₽/руб)
- Есть срок (дней/дней/неделя)
- Нет спам-фраз ("готово за час", "самый лучший", "гарантия 100%")
- Есть вопрос в конце

ВЫХОД (JSON):
{
  "draft": "...",
  "valid": true,
  "issues": [],
  "length_chars": 1240,
  "iterations": 1
}
```

### 6.7 History Keeper (`prompts/history-keeper.txt`)

```
Ты — хранитель истории. Вход: массив полных контрактов проекта.

ФАЙЛЫ:
data/history.json — ВСЕ просмотренные проекты:
[{"id": "kwork-123", "status": "proposed|reviewed|skipped", "timestamp": "...", "score": 87, "verdict": "apply"}]

data/rejected.json — ТОЛЬКО отклонённые с причинами:
[{"id": "kwork-456", "rejection_reason": "budget_too_low|skill_mismatch|client_risk_high|deadline_unrealistic|duplicate", "score": 34, "timestamp": "..."}]

АЛГОРИТМ:
1. Прочитай оба файла.
2. Для каждого входного проекта:
   - Если id уже в history.json → пропусти (дубль).
   - Иначе добавь в history.json.
   - Если verdict != "apply" → добавь в rejected.json с rejection_reason.
3. Атомарно запиши оба файла (write).
```

---

## 7. Least Privilege — таблица разрешений

| Субагент | read | write | bash | grep | glob | webfetch | websearch | edit | task |
|----------|------|-------|------|------|------|----------|-----------|------|------|
| Orchestrator | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Parser | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| Analyzer | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Scorer | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Client Analyst | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ |
| Proposal Writer | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| History Keeper | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## 8. План разработки на 14 дней (ВЫПОЛНЕН)

| День | Этап | Статус |
|------|------|--------|
| **0** | Подготовка | ✅ Выполнено |
| **1** | Ручной прогон | ✅ Выполнено |
| **2** | Контракт + профиль | ✅ `CONTRACT.md`, `config/` |
| **3** | Parser | ✅ Playwright, cookies, категории |
| **4** | Analyzer | ✅ Фильтр + дедуп + скор |
| **5** | Scorer | ✅ Взвешенный 1-100 |
| **6** | Client Analyst | ✅ risk: low/medium/high |
| **7** | Writer + Validator | ✅ Self-correction ≤2 итераций |
| **8** | History Keeper | ✅ Атомарная запись |
| **9** | Оркестратор + скилл | ✅ `kwork-pipeline` |
| **10** | E2E + боевой | ✅ Live-прогон, README |
| **11** | Веб-монитор | ✅ FastAPI + SSE + HTMX |
| **12** | Indicators-driven matching | ✅ v2.0 |
| **13** | Дедуп по категориям (all) | ✅ Глобальный дедуп |
| **14** | Финальный прогон | ✅ 36 проектов, отчёт |

**Итого: ~24 часа**

---

## 9. Definition of Done (жёсткие критерии) — текущий статус

- [x] Пайплайн запускается одной командой: `python3 kwork_run.py`
- [x] Live-парсинг работает через Playwright + cookies.txt (36 проектов за прогон)
- [x] На выходе: Markdown-отчёт + `proposals/*.md` + `logs/run_*.json`
- [x] Дубли исключаются (тройная дедупликация: парсер → analyzer → history-keeper)
- [x] Стоимость прогона **$0.00** (Nemotron 3 Ultra Free для всех 7 агентов — free tier)
- [x] Время цикла ≤ 8 минут для 10 проектов
- [x] КП всегда ≤1500 символов, проходит валидацию с 1-2 итераций
- [x] `rejected.json` содержит причину для **каждого** непринятого проекта
- [x] Ошибка любого агента не роняет пайплайн (логируется, продолжаем)
- [x] `freelancer_profile.yaml` меняет поведение скоринга без правки кода

---

## 10. Файловая структура проекта

```
kwork-scout-v2.1/
├── kwork_run.py                           # Единая точка входа
├── kwork_analyzer.py                      # Анализ проектов
├── kwork_scorer.py                        # Скоринг
├── kwork_client_analyst.py                # Оценка рисков заказчиков
├── kwork_proposal_writer.py               # Генерация КП
├── kwork_history_keeper.py                # Атомарное сохранение истории
├── opencode.json                          # Конфиг агентов и разрешений
├── .opencode/
│   └── skills/
│       └── kwork-pipeline/
│           └── SKILL.md                   # Скилл-оркестратор
├── prompts/
│   ├── orchestrator.txt
│   ├── parser.txt
│   ├── analyzer.txt
│   ├── scorer.txt
│   ├── client-analyst.txt
│   ├── proposal-writer.txt
│   └── history-keeper.txt
├── config/
│   └── freelancer_profile.yaml            # Навыки, веса, пороги (v2.0 indicators-driven)
├── data/                                  # Runtime (в .gitignore)
│   ├── history.json
│   └── rejected.json
├── reports/                               # Отчёты прогонов
│   └── RUN_*.md
├── proposals/                             # КП (в .gitignore)
│   └── YYYY-MM-DD_kwork-{id}.md
├── logs/                                  # Runtime (в .gitignore)
│   ├── parser_live.json
│   ├── analyzer_*.json
│   ├── scorer_*.json
│   ├── client_analyst_*.json
│   ├── proposal_writer_*.json
│   ├── history_keeper_*.json
│   ├── pipeline_status.json               # Runtime статус (веб-монитор)
│   └── runs/                              # История запусков (веб-монитор)
├── tools/
│   ├── kwork_live_parse.py                # Парсер Playwright (режим all, встроенный логин)
│   ├── kwork_check_auth.py                # Проверка/выполнение входа на kwork.ru
│   ├── monitor_server.py                  # FastAPI + SSE backend
│   ├── write_status.py                    # Утилита записи статуса
│   └── README.md                          # Документация tools/
├── templates/
│   └── monitor.html                       # HTMX + mermaid.js фронтенд
├── CONTRACT.md                            # JSON-схема v2.1
├── AGENTS.md                              # Hard rules
├── AGENT_SYSTEM_OPENCODE_V2.md            # Этот файл (дизайн-док)
└── README.md                              # Актуальная документация
```

---

## 11. Риски и митигация (обновлено)

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| **Kwork меняет структуру stateData** | Средняя | Playwright + stateData (не DOM-scraping), fallback на DOM-селекторы |
| **Агент врёт об успехе** | Высокая | Writer сам себя валидирует (2 итерации), History Keeper логирует всё |
| **Дубли проектов** | Высокая | History Keeper + проверка в Analyzer (двойной щит) |
| **Нет данных о заказчике** | Средняя | Client Analyst ставит risk="unknown", scorer штрафует |
| **Стоимость моделей** | Низкая | Nemotron 3 Ultra Free для всех 7 агентов. Бюджет $0.00 (free tier) |
| **Система не приносит пользы** | Средняя | Feedback через rejected.json, анализ через 2 недели, A/B весов |
| **Скоринг не калибруется** | Средняя | Явные веса в yaml, метрики в metadata, план переобучения на неделе 3 |
| **Веб-монитор недоступен** | Низкая | Fallback: polling каждые 2с (в monitor.html), логи в `logs/run_*.json`, CLI отчёт |

---

## 12. План масштабирования (по неделям после v2.0)

| Неделя | Что добавить | Сложность | Зависимости |
|--------|--------------|-----------|-------------|
| **3** | Competitor Analyst (анализ других откликов) | Средняя | Нужен парсинг откликов |
| **4** | Telegram-бот для пуш-уведомлений | Низкая | Только отправка отчёта |
| **5** | Автоматический отклик через API Kwork | Высокая | Нужен OAuth, лимиты API |
| **6** | Мульти-биржевой парсинг (FL.ru, Habr Freelance) | Высокая | Новые парсеры, общий контракт |
| **7–8** | Feedback loop — обучение скоринга на rejected.json | Очень высокая | Накоплена статистика 50+ проектов |
| **—** | **Веб-монитор (FastAPI + SSE + HTMX)** | ✅ **Выполнено** | `monitor_server.py`, `monitor.html`, `write_status.py` |


---

## 13. Историческая справка: создание kwork-parser (Day 3)

Конфигурация `kwork-parser` в `opencode.json`:

- Режим: subagent
- Модель: `opencode/nemotron-3-ultra-free`
- Разрешения: bash=allow, read=allow, write=allow, webfetch=allow, grep=deny, glob=deny, edit=deny

> **Примечание:** на текущий момент (v2.1, Day 14) парсинг выполняется через `tools/kwork_live_parse.py` (режим `all`), а не через AI-агента. Конфигурация в `opencode.json` сохранена для совместимости.

---

### Что улучшено по сравнению с v1.0:

1. **Отдельный Scorer** — чистое разделение предварительного фильтра и финального взвешенного скоринга
2. **Конфигурируемый профиль** — веса, пороги, навыки в YAML, не в коде
3. **Единая модель** — `opencode/nemotron-3-ultra-free` для всех 7 агентов, бюджет $0.00
4. **Глобальный дедуп по категориям** — один браузер, режим `all`, `categories: []`
5. **Live-парсинг через Playwright** — обход SmartCaptcha, cookies.txt, встроенный логин
6. **Indicators-driven matching v2.0** — отказ от поиска библиотек в пользу семантических индикаторов
7. **Веб-монитор** — FastAPI + SSE + HTMX, live-дашборд
8. **Least privilege таблица** — безопасность по умолчанию
9. **Жёсткий Definition of Done** — измеримые критерии приёмки
10. **Rejection reason enum** — структурированные причины для ML на неделе 7-8
11. **Обработка ошибок** — graceful degradation, ни один агент не роняет пайплайн
12. **Атомарная запись истории** — `history.json` + `rejected.json` за один вызов
