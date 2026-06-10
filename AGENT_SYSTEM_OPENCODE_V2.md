# Агентная система «Kwork Scout» для OpenCode — Версия 2.0 (Improved)

> **«Production-ready с первого дня. Единый контракт, least privilege, dry run, само-валидация, конфигурируемый скоринг.»**

---

## 1. Архитектура (6 субагентов + Оркестратор + Web Monitor)

```
┌─────────────────────────────────────────────────────────────────┐
│  🎯 Orchestrator (build agent) — Skill: kwork-pipeline          │
│     Запуск пайплайна, агрегация, отчёт, обработка ошибок        │
└────────────────────────────┬────────────────────────────────────┘
                             │
               ┌──────────────┼──────────────┐
               ▼              ▼              ▼
┌─────────────────────┐ ┌─────────────┐ ┌─────────────┐  (ПАРАЛЛЕЛЬНО по категориям)
│ 1. kwork-parser     │ │ 1. kwork-   │ │ 1. kwork-   │
│    (subagent)       │ │ parser      │ │ parser      │
│    webfetch, bash   │ │ (bots)      │ │ (content)   │
└─────────┬───────────┘ └──────┬──────┘ └──────┬──────┘
          │                    │               │
          └────────────────────┼───────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. kwork-analyzer (subagent)                                    │
│     Фильтр → дедупликация (history.json) → предварительный скор │
│     Инструменты: read, grep, glob                               │
│     Выход: JSON со score_prelim, skills_match, should_process   │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. kwork-scorer (subagent) — НОВЫЙ АГЕНТ                        │
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
│    Инструменты: read, webfetch,│    │                         │
│    websearch                   │    │                         │
│    Выход: rating, risk,        │    │                         │
│    red_flags[]                 │    │                         │
└─────────────┬──────────────────┘    └───────────┬─────────────┘
              │                                   │
              └───────────────┬───────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. kwork-proposal-writer (subagent)                             │
│     Пишет КП → сам валидирует (≤1500 симв, цена, срок, обращ.)  │
│     Инструменты: read, write                                    │
│     ✅ Self-correction до 2 итераций                            │
│     Выход: валидный черновик КП + metadata                      │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 6. kwork-history-keeper (subagent)                              │
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

  "model": "nvidia/nemotron-3-ultra-free",
  "small_model": "nvidia/nemotron-3-ultra-free",

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
      "model": "nvidia/nemotron-3-ultra-free",
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
      "model": "nvidia/nemotron-3-ultra-free",
      "prompt": "{file:./prompts/parser.txt}",
      "permission": {
        "bash": "allow", "read": "allow", "write": "allow", "webfetch": "allow",
        "grep": "deny", "glob": "deny", "edit": "deny"
      }
    },

    "kwork-analyzer": {
      "description": "Фильтрует, дедуплицирует, предварительный скор проектов",
      "mode": "subagent",
      "model": "nvidia/nemotron-3-ultra-free",
      "prompt": "{file:./prompts/analyzer.txt}",
      "permission": {
        "read": "allow", "grep": "allow", "glob": "allow",
        "bash": "deny", "write": "deny", "edit": "deny", "webfetch": "deny"
      }
    },

    "kwork-scorer": {
      "description": "Финальный взвешенный скор 1-100 по профилю фрилансера",
      "mode": "subagent",
      "model": "nvidia/nemotron-3-ultra-free",
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
      "model": "nvidia/nemotron-3-ultra-free",
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
      "model": "nvidia/nemotron-3-ultra-free",
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
      "model": "nvidia/nemotron-3-ultra-free",
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
    "model_calls": { "haiku": 5, "sonnet": 1 },
    "estimated_cost_usd": 0.08
  }
}
```

---

## 4. Профиль фрилансера (`config/freelancer_profile.yaml`)

```yaml
skills:
  - python
  - javascript/typescript
  - go
  - postgresql
  - redis
  - docker
  - kubernetes
  - aiogram
  - fastapi
  - next.js

preferences:
  min_hourly_rate: 1500
  max_project_duration_days: 14
  preferred_categories:
    - telegram_bots
    - microservices
    - backend_development
    - api_integration
    - content_creation
  avoid_keywords:
    - "wordpress"
    - "1с"
    - "bitrix"
    - "нативный ios/android"

scoring_weights:
  tech_match: 0.30
  budget_fit: 0.25
  timeline_fit: 0.15
  client_quality: 0.15
  competition: 0.15

thresholds:
  auto_apply_score: 80
  review_score: 60
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
python3 tools/write_status.py init --run-id $(date +%Y-%m-%d_%H-%M-%S) --mode dry_run --categories telegram_bots,microservices,content_creation

# Шаг: parser
python3 tools/write_status.py start --step "parser(telegram_bots)"
<task: kwork-parser>
python3 tools/write_status.py finish --step "parser(telegram_bots)" --summary "12 projects"

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

1. **Парсинг** — 3× kwork-parser параллельно по категориям
2. **Анализ** — kwork-analyzer (фильтр + дедуп + предварительный скор)
3. **Скоринг** — kwork-scorer (финальный взвешенный скор 1-100)
4. **Проверка заказчика** — kwork-client-analyst (параллельно для score ≥ review)
5. **КП** — kwork-proposal-writer (для score ≥ auto_apply И risk != high)
6. **История** — kwork-history-keeper (атомарное сохранение)

## Запуск

- `Найди проекты на kwork` или `@kwork-pipeline`
- Dry run: `@kwork-pipeline dry_run=true`

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
Ты — оркестратор мультиагентной системы Kwork Scout.

ПОРЯДОК РАБОТЫ:
1. Запусти 3× kwork-parser ПАРАЛЛЕЛЬНО (categories: telegram_bots, microservices, content_creation). Передай dry_run.
2. Объедини результаты. Передай список в kwork-analyzer.
3. Для каждого проекта с should_process=true запусти kwork-scorer (можно параллельно).
4. Для проектов с score ≥ review_threshold ПАРАЛЛЕЛЬНО запусти kwork-client-analyst.
5. Для проектов с score ≥ auto_apply_threshold И client.risk != "high" запусти kwork-proposal-writer.
6. Запусти kwork-history-keeper со всеми результатами.
7. Сформируй Markdown-отчёт: таблица + сводка по затратам/времени.

ПРАВИЛА:
- При ошибке любого агента: логируй, но НЕ останавливай пайплайн.
- dry_run=true → не пиши в history.json/rejected.json, не отправляй в webfetch живые запросы.
- Лимит параллельных тасков: 5 одновременно.
- Таймаут на агента: 60 сек.
```

### 6.2 Parser (`prompts/parser.txt`)

```
Ты — парсер проектов с kwork.ru для ОДНОЙ категории.

КАТЕГОРИИ: telegram_bots | microservices | content_creation

ИНСТРУМЕНТЫ: webfetch (основной), bash (curl fallback), read (dry_run HTML).

ИЗВЛЕКАЙ ИЗ HTML: id, title, budget_rub (min/max), deadline_days, description, url, category, client_nickname, tags[], published_at.

ВЫХОД (JSON):
{
  "projects": [...],
  "count": N,
  "category": "telegram_bots",
  "error": null
}

dry_run=true → читай tests/kwork-sample.html через read.
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

## 8. План разработки на 10 дней (обновлённый)

| День | Этап | Задачи | Результат | Время |
|------|------|--------|-----------|-------|
| **0** | Подготовка | Установить OpenCode, создать структуру папок, сохранить `tests/kwork-sample.html` | Готово к коду | 0.5 ч |
| **1** | Ручной прогон | Пройти весь цикл вручную в браузере/чате. Записать результаты | Понимание edge cases | 1.5 ч |
| **2** | Контракт + профиль | Утвердить JSON v2.1, создать `freelancer_profile.yaml` | `CONTRACT.md`, `config/` | 1 ч |
| **3** | Parser + dry run | `kwork-parser` в opencode.json. Тест на HTML. 3 категории. | Парсинг работает | 2.5 ч |
| **4** | Analyzer | `kwork-analyzer`. Фильтр + дедуп + предварительный скор. | score_prelim выдаётся | 2 ч |
| **5** | Scorer | `kwork-scorer`. Взвешенный скор 1-100 по профилю. | Финальный score + verdict | 2 ч |
| **6** | Client Analyst | `kwork-client-analyst`. Проверка заказчика. | JSON с rating/risk | 1.5 ч |
| **7** | Writer + Validator | `kwork-proposal-writer` с self-correction (2 итерации). | Валидный КП всегда | 3 ч |
| **8** | History Keeper | `kwork-history-keeper`. `history.json` + `rejected.json`. | История работает | 1.5 ч |
| **9** | Оркестратор + скилл | `kwork-pipeline/SKILL.md`. Соединить всех. Обработка ошибок. | Запуск через `@kwork-pipeline` | 2.5 ч |
| **10** | E2E + боевой | Прогон на 10 реальных проектах. Исправить баги. README. Первый отклик. | Рабочая система | 2 ч |

**Итого: 20 часов** (≈2 часа в день)

---

## 9. Definition of Done (жёсткие критерии)

- [ ] Пайплайн запускается одной командой: `@kwork-pipeline`
- [ ] Dry run работает на `tests/kwork-sample.html` без live-запросов
- [ ] На выходе: Markdown-отчёт + `proposals/*.md` + `logs/run_*.json`
- [ ] Дубли исключаются (проверено: 2 запуска подряд → 0 новых в history)
- [ ] Стоимость прогона **$0.00** (Nemotron 3 Ultra Free для всех 7 агентов — free tier)
- [ ] Время цикла ≤ 8 минут для 10 проектов
- [ ] КП всегда ≤1500 символов, проходит валидацию с 1-2 итераций
- [ ] `rejected.json` содержит причину для **каждого** непринятого проекта
- [ ] Ошибка любого агента не роняет пайплайн (логируется, продолжаем)
- [ ] `freelancer_profile.yaml` меняет поведение скоринга без правки кода

---

## 10. Файловая структура проекта

```
kwork-scout-v2.0/
├── opencode.json                          # Конфиг агентов и разрешений
├── .opencode/
│   ├── agents/                            # (опционально) Markdown-агенты
│   └── skills/
│       └── kwork-pipeline/
│           └── SKILL.md                   # Скилл-оркестратор
├── prompts/
│   ├── orchestrator.txt
│   ├── parser.txt
│   ├── analyzer.txt
│   ├── scorer.txt                         # НОВЫЙ
│   ├── client-analyst.txt
│   ├── proposal-writer.txt
│   └── history-keeper.txt
├── config/
│   └── freelancer_profile.yaml            # Навыки, веса, пороги
├── data/
│   ├── history.json
│   ├── rejected.json
│   └── metrics.json
├── proposals/
│   └── 2026-06-05_kwork-123456.md
├── tests/
│   └── kwork-sample.html                  # Для dry run
├── logs/
│   ├── run_2026-06-05_10-00-00.json
│   ├── pipeline_status.json               # Runtime статус (веб-монитор)
│   └── runs/                              # История запусков (веб-монитор)
├── tools/
│   ├── monitor_server.py                  # FastAPI + SSE backend
│   ├── write_status.py                    # Утилита записи статуса
│   ├── kwork_login.py                     # Playwright авто-логин
│   └── kwork_live_parse.py                # Live парсер через Playwright
├── templates/
│   └── monitor.html                       # HTMX + mermaid.js фронтенд
├── CONTRACT.md                            # JSON-схема v2.1
├── ARCHITECTURE.md                        # Этот файл
└── README.md
```

---

## 11. Риски и митигация (обновлено)

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| **Kwork меняет вёрстку** | Средняя | Dry run на сохранённой HTML, Parser изолирован, селекторы в промпте |
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

## 13. Готовый запрос для первого запуска (Day 3)

```text
Создай субагента kwork-parser в opencode.json:
- Режим: subagent
- Модель: nvidia/nemotron-3-ultra-free
- Разрешения: bash=allow, read=allow, write=allow, webfetch=allow, grep=deny, glob=deny, edit=deny
- Системная инструкция: файл prompts/parser.txt (парсинг одной категории, dry_run поддержка)
- Выход: JSON с projects[], count, category, error

Сохрани в .opencode/agents/kwork-parser.md (альтернатива JSON) ИЛИ добавь в opencode.json.
```

---

### Что улучшено по сравнению с v1.0:

1. **Отдельный Scorer** — чистое разделение предварительного фильтра и финального взвешенного скоринга
2. **Конфигурируемый профиль** — веса, пороги, навыки в YAML, не в коде
3. **Параллельный парсинг** по 3 категориям сразу (быстрее в 3 раза)
4. **Least privilege таблица** — безопасность по умолчанию
5. **Жёсткий Definition of Done** — измеримые критерии приёмки
6. **Metadata в контракте** — стоимость, время, вызовы моделей для оптимизации
7. **Rejection reason enum** — структурированные причины для ML на неделе 7-8
8. **Обработка ошибок в оркестраторе** — graceful degradation
