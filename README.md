# Kwork Scout v2.1

> **Мультиагентный пайплайн** для автоматизированного поиска, скоринга и формирования откликов на проекты с [kwork.ru](https://kwork.ru).
>
> **Бюджет:** $0.00 — все компоненты работают на free-tier моделях.
> **Платформа:** OpenCode (OpenAI Code Agent).
> **Статус:** ✅ E2E dry-run пройден, ✅ live-парсинг работает, ✅ веб-монитор активен, ✅ калибровка скоринга v2.0.

---

## Содержание

- [Описание](#описание)
- [Архитектура](#архитектура)
- [Быстрый старт](#быстрый-старт)
- [Пайплайн (6 шагов)](#пайплайн-6-шагов)
- [Веб-монитор](#веб-монитор)
- [Конфигурация](#конфигурация)
- [Модели](#модели)
- [Структура файлов](#структура-файлов)
- [Примеры использования](#примеры-использования)
- [Решение проблем](#решение-проблем)
- [Безопасность](#безопасность)
- [Roadmap](#roadmap)

---

## Описание

**Kwork Scout** — это мультиагентная система на базе OpenCode, которая:

1. **Парсит** kwork.ru по трём категориям (telegram_bots, microservices, content_creation)
2. **Фильтрует** проекты по навыкам из профиля фрилансера
3. **Вычисляет** взвешенный скор (1–100) по 5 компонентам: tech_match, budget_fit, timeline_fit, client_quality, competition
4. **Оценивает** риск заказчика (low / medium / high)
5. **Генерирует** коммерческое предложение (КП) для подходящих проектов
6. **Сохраняет** историю и отклонённые проекты атомарно

Система спроектирована по принципу **least privilege**: каждый агент имеет ровно те права, которые необходимы для его задачи. Веса скоринга, пороги и навыки вынесены в YAML-конфиг — изменение поведения без правки промптов.

> **v2.0 (indicators-driven matching):** Клиенты kwork.ru редко пишут названия библиотек — они описывают, _что_ нужно сделать. Поэтому matching переработан: `indicators` (слова-признаки: «чат-бот», «телеграм», «api») + `tech_stack` (бонусные технологии) + `adjacent`. Это повысило `skills_match` с ~0.2 до 0.3–0.8 для релевантных проектов.

### Целевая аудитория

- Фрилансеры, которые хотят автоматизировать поиск заказов на kwork.ru
- Разработчики мультиагентных систем на OpenCode
- Все, кто интересуется AI-автоматизацией фриланса

---

## Архитектура

```
┌─────────────────────────────────────────────────────────────┐
│  ORCHESTRATOR (build agent)                                 │
│  DeepSeek V4 Flash Free                                     │
│  Запускает граф, агрегирует результаты, пишет отчёт         │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. PARSER (Nemotron 3 Ultra Free)                           │
│    Парсит kwork.ru (3 категории, дедуп по ID)               │
│    → logs/parser_live.json                                  │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. ANALYZER (Nemotron 3 Ultra Free)                         │
│    Фильтр по навыкам, дедуп, prel. score 1-10               │
│    → logs/analyzer_*.json                                   │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. SCORER (Nemotron 3 Ultra Free)                           │
│    Финальный скор 1-100 + verdict (apply/review/skip)       │
│    → logs/scorer_*.json                                     │
└──────────────────────┬──────────────────────────────────────┘
                       │
            ┌──────────┴──────────┐
            ▼                     ▼
┌────────────────────┐  ┌────────────────────────────────┐
│ 4. CLIENT-ANALYST  │  │ 5. PROPOSAL-WRITER             │
│ (Nemotron 3 Ultra) │  │ (Nemotron 3 Ultra Free)        │
│ risk для apply/    │  │ КП ≤1500 симв, self-correct    │
│ review             │  │ → proposals/*.md               │
└─────────┬──────────┘  └──────────┬─────────────────────┘
          └──────────┬─────────────┘
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. HISTORY-KEEPER (Nemotron 3 Ultra Free)                   │
│    Атомарная запись → data/history.json + rejected.json     │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 7. ORCHESTRATOR формирует markdown-отчёт                    │
└─────────────────────────────────────────────────────────────┘
```

### Параллелизм

- **Шаг 1:** Парсер запускается один раз с категорией `all` — один браузер, все 3 категории последовательно.
- **Шаги 4 и 5:** `client-analyst` и `proposal-writer` работают **параллельно**.
- Остальные шаги — строго последовательно.

---

## Быстрый старт

### 1. Установка

```bash
# Клонировать репозиторий
cd opencode

# Создать виртуальное окружение
python3 -m venv .venv
source .venv/bin/activate

# Установить зависимости
pip install -r requirements.txt
playwright install chromium   # ~150 МБ, только для live-режима
```

### 2. Авторизация на kwork.ru (live-режим)

Для live-парсинга нужна сессия kwork.ru:

```bash
# Запустить авто-логин (откроется видимый Chrome)
python3 tools/kwork_login.py

# Войти вручную в opened браузере
# Скрипт сам сохранит cookies в cookies.txt
```

Подробности: [MANUAL_LOGIN.md](MANUAL_LOGIN.md).

### 3. Запуск пайплайна

#### Dry-run (без интернета, тестовые данные):

```bash
python3 kwerk_run.py
```

#### Live-режим (реальные проекты с kwork.ru):

```bash
python3 kwerk_run.py --live
```

#### С очисткой истории:

```bash
python3 kwerk_run.py --live --clear
```

#### Ограничить число проектов:

```bash
python3 kwerk_run.py --max-projects 5
```

### 4. Веб-монитор

```bash
# Терминал 1: запустить монитор
python3 tools/monitor_server.py
# → http://localhost:8080

# Терминал 2: запустить пайплайн
python3 kwerk_run.py --live
```

Монитор обновляется в реальном времени через SSE. После прогона таблица проектов заполнится автоматически.

---

## Пайплайн (6 шагов)

### Шаг 1: Парсер (`kwork-parser`)

| Режим | Источник данных | Команда |
|-------|----------------|---------|
| Dry | `tests/kwork-sample.html` | Чтение локального файла |
| Live | kwork.ru (3 категории) | Playwright + cookies.txt |

Парсит все 3 категории в одном браузере, дедуплицирует по `project.id`, сохраняет массив `categories: []`.

**Выход:** `logs/parser_live.json`.

### Шаг 2: Анализатор (`kwork-analyzer`)

- Фильтрует проекты по навыкам (core / adjacent / avoid из `freelancer_profile.yaml`)
- Дедуплицирует (defensive layer)
- Вычисляет предварительный скор 1–10
- Проверяет `avoid_keywords` и `avoid_red_flags`

**Выход:** `logs/analyzer_*.json`.

### Шаг 3: Скорер (`kwork-scorer`)

Взвешенный скор 1–100 по 5 компонентам:

| Компонент | Вес | Описание |
|-----------|-----|----------|
| tech_match | 0.30 | Соответствие навыкам (indicators-driven) |
| budget_fit | 0.25 | Ставка ₽/ч |
| timeline_fit | 0.10 | Срок выполнения |
| client_quality | 0.25 | Качество заказчика (↑ v2.0) |
| competition | 0.10 | Число откликов |

**Вердикты:**

| Скор | Вердикт | Действие |
|------|---------|----------|
| ≥ 65 | 🟢 **apply** | Генерировать КП |
| 55–64 | 🟡 **review** | Ручное решение |
| < 55 | 🔴 **skip** | Отклонить |

> **Калибровка v2.0 (День 14):** client_quality ↑ (0.15→0.25), competition ↓ (0.15→0.10), timeline_fit ↓ (0.15→0.10). Формула `client_quality`: `hp * 0.7 + min(badges_n * 12, 50)`. Пороги понижены: apply ≥ 65 (было 80), review ≥ 55 (было 60).

**Выход:** `logs/scorer_*.json`.

### Шаг 4: Клиент-аналитик (`kwork-client-analyst`)

Оценивает риск заказчика (`client.risk`) для проектов с вердиктом `apply` или `review`:

- **low:** hired_percent ≥ 70, есть бейдж «Мощный покупатель» или «Более 3 лет с Kwork»
- **high:** hired_percent < 30 или completed_projects < 3
- **medium:** всё остальное
- **unknown:** нет данных

**Выход:** `logs/client_analyst_*.json`.

### Шаг 5: Писатель КП (`kwork-proposal-writer`)

Генерирует коммерческое предложение для проектов с `verdict=apply` и `risk≠high`.

**Требования к КП:**
- 400–1500 символов
- Обращение по имени заказчика
- Цена и срок (с запасом +1 день)
- Уточняющий вопрос в конце
- Без спам-фраз (25+ запрещённых фраз)
- Self-correction: до 2 итераций валидации

**Выход:** `proposals/YYYY-MM-DD_kwork-{id}.md`.

### Шаг 6: Хранитель истории (`kwork-history-keeper`)

Атомарная запись: за **один вызов** пишет оба файла:

- `data/history.json` — все обработанные проекты
- `data/rejected.json` — только отклонённые (с причиной)

Использует `temp → os.replace()` — POSIX-атомарно, риск рассинхрона исключён.

---

## Веб-монитор

### Запуск

```bash
python3 tools/monitor_server.py
```

Открывает `http://localhost:8080` с дашбордом.

### Возможности

| Панель | Описание |
|--------|----------|
| **Pipeline Graph** | Mermaid-диаграмма: 🟢 завершён, 🔵 выполняется, ⚪ ожидает, 🔴 ошибка |
| **Метрики** | Всего проектов, Apply/Review/Skip, Avg Score, Бюджет (∑) |
| **Шаги** | Список с таймингами и статусами |
| **Проекты** | Таблица: ID, Название, Бюджет, Score, Verdict, Risk, 📄 КП |
| **Логи** | stdout/stderr агентов |
| **Ошибки** | Список с описаниями |

### API

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/` | HTML-дашборд |
| GET | `/api/status` | Текущий статус (JSON) |
| GET | `/api/history` | Список последних 8 запусков |
| GET | `/api/runs/{run_id}` | Статус конкретного запуска |
| GET | `/api/proposal/{filename}` | Скачать .md КП |
| GET | `/api/events` | SSE-поток (обновления каждые 0.5с) |

### Команды write_status.py

```bash
# Инициализация
python3 tools/write_status.py init --run-id 2026-06-09_12-00-00 --mode dry_run

# Старт шага
python3 tools/write_status.py start --step "parser(all)"

# Финиш шага
python3 tools/write_status.py finish --step "parser(all)" --summary "25 projects"

# Метрики
python3 tools/write_status.py metrics --total 25 --kept 23 --apply 0 --review 0 --skip 25 --avg-score 28.1

# Добавить проект
python3 tools/write_status.py project --id 3192454 --title "Telegram-бот" --budget 20000 --score 63 --verdict review

# Ошибка
python3 tools/write_status.py error --msg "Rate limit exceeded"

# Сохранить запуск
python3 tools/write_status.py save-run
```

---

## Конфигурация

### `config/freelancer_profile.yaml`

Центральный конфиг, определяющий поведение пайплайна. Изменение не требует правки промптов.

| Секция | Описание |
|--------|----------|
| `skills` | Навыки по категориям (indicators, tech_stack, adjacent, avoid) — v2.0 |
| `avoid_keywords` | Слова-триггеры для безусловного отклонения |
| `avoid_red_flags` | Жёлтые флаги (проект идёт в review вместо apply) |
| `preferences` | `min_hourly_rate`, `max_project_duration_days`, preferred_categories |
| `scoring_weights` | Веса 5 компонентов скора |
| `thresholds` | Пороги apply/review/skip |
| `budget_fit_thresholds` | Множители бюджетного скоринга |
| `timeline_fit_thresholds` | Дни для скоринга срока |
| `competition_thresholds` | Число откликов для скоринга конкуренции |
| `client_quality_thresholds` | Условия low/medium/high risk |
| `proposal` | Лимиты КП, стоп-фразы, шаблоны |

**Калибровка:** после live-прогонов анализируйте `data/rejected.json`. Если хорошие проекты массово отклоняются — снизьте `min_hourly_rate` или откалибруйте `scoring_weights`.

---

## Модели

Все компоненты используют **free-tier** модели. Бюджет прогона: **$0.00**.

| Компонент | Модель | Провайдер |
|-----------|--------|-----------|
| **Build / Orchestrator** | `deepseek/deepseek-v4-flash-free` | DeepSeek |
| **kwork-parser** | `nvidia/nemotron-3-ultra-free` | NVIDIA |
| **kwork-analyzer** | `nvidia/nemotron-3-ultra-free` | NVIDIA |
| **kwork-scorer** | `nvidia/nemotron-3-ultra-free` | NVIDIA |
| **kwork-client-analyst** | `nvidia/nemotron-3-ultra-free` | NVIDIA |
| **kwork-proposal-writer** | `deepseek/deepseek-v4-flash-free` | DeepSeek |
| **kwork-history-keeper** | `minimax/minimax-m3-free` | MiniMax |

> **Бюджет прогона: $0.00** — все модели free-tier. DeepSeek V4 Flash Free (аналог Sonnet) используется для генерации КП и оркестрации. Nemotron 3 Ultra Free — для парсинга и скоринга. MiniMax M3 Free — для атомарной записи (малый токен-расход).

---

## Структура файлов

```
opencode/
├── prompts/                          # Системные промпты агентов
│   ├── orchestrator.txt              #   Оркестратор (build agent)
│   ├── parser.txt                    #   Парсер kwork.ru
│   ├── analyzer.txt                  #   Фильтр + prelim score
│   ├── scorer.txt                    #   Взвешенный скор 1-100
│   ├── client-analyst.txt            #   Оценка риска заказчика
│   ├── proposal-writer.txt           #   Генерация КП
│   └── history-keeper.txt            #   Атомарная запись истории
│
├── config/
│   └── freelancer_profile.yaml       # Профиль: навыки, веса, пороги
│
├── data/                             # Данные (пишутся агентами)
│   ├── history.json                  #   Все обработанные проекты
│   └── rejected.json                 #   Отклонённые (с причинами)
│
├── proposals/                        # Сгенерированные КП
│   └── 2026-06-09_kwork-900001.md    #   Пример КП
│
├── logs/                             # Логи прогонов
│   ├── parser_live.json              #   Выход парсера (live)
│   ├── analyzer_*.json               #   Выход анализатора
│   ├── scorer_*.json                 #   Выход скорера
│   ├── client_analyst_*.json         #   Выход клиент-аналитика
│   ├── proposal_writer_*.json        #   Выход писателя КП
│   ├── history_keeper_*.json         #   Выход хранителя истории
│   ├── run_*.json                    #   Лог прогона (метаданные)
│   ├── pipeline_status.json          #   Runtime статус (веб-монитор)
│   └── runs/                         #   История запусков
│
├── tests/
│   └── kwork-sample.html             # Тестовый HTML для dry-run
│
├── tools/                            # Утилиты
│   ├── kwork_login.py                #   Авто-логин (Playwright, видимый Chrome)
│   ├── kwork_live_parse.py           #   Live-парсер (Playwright)
│   ├── monitor_server.py             #   Веб-монитор (FastAPI + SSE)
│   ├── write_status.py               #   Утилита записи статуса
│   └── README.md                     #   Документация tools/
│
├── templates/
│   └── monitor.html                  # HTMX-фронтенд веб-монитора
│
├── .opencode/
│   └── skills/kwork-pipeline/
│       └── SKILL.md                  # Skill-регистрация (@kwork-pipeline)
│
├── kwerk_run.py                     # Единая точка входа (главная команда)
├── simulate_*_dry_run.py            # Симуляторы (внутренние, для отладки)
├── generate_synthetic_scorer.py     # Генератор тестовых данных
│
├── opencode.json                     # Конфиг агентов + least privilege
├── CONTRACT.md                       # JSON-контракт v2.1
├── AGENT_SYSTEM_OPENCODE_V2.md       # Дизайн-док (архитектура)
├── AGENTS.md                         # Hard rules и контекст
├── MANUAL_LOGIN.md                   # Инструкция по авто-логину
├── manual-workflow.md                # Исследование kwork.ru (25 КБ)
├── CONCLUSION.md                     # Заключение по проекту
├── requirements.txt                  # Зависимости Python
├── .env.example                      # Пример .env (legacy)
├── .gitignore
└── README.md                         # Этот файл
```

---

## Примеры использования

### Dry-run (без интернета)

```bash
python3 kwerk_run.py
```

Использует `tests/kwork-sample.html` как источник данных. Весь пайплайн проходит за секунды.

### Live-прогон (реальные проекты)

```bash
python3 kwerk_run.py --live
```

Требует `cookies.txt`. Если файла нет — запустите `python3 tools/kwork_login.py`.

### Ограничение числа проектов

```bash
python3 kwerk_run.py --max-projects 5
```

Обработать только первые 5 проектов после парсера.

### Очистка истории

```bash
python3 kwerk_run.py --live --clear
```

Очищает `data/history.json` и `data/rejected.json` перед прогоном.

### Веб-монитор + пайплайн

```bash
# Терминал 1:
python3 tools/monitor_server.py
# → http://localhost:8080

# Терминал 2:
python3 kwerk_run.py --live
```

---

## Решение проблем

### «cookies.txt не найден»

```bash
# Запустить авто-логин
python3 tools/kwork_login.py
# Откроется Chrome, войдите в kwork.ru, скрипт сам сохранит cookies
```

### 100% проектов получают skip

Причина: `min_hourly_rate: 500` в `config/freelancer_profile.yaml` может быть высок для реальных бюджетов kwork.ru (медиана ~100–200 ₽/ч).

**Решение:** снизить порог:
```yaml
# config/freelancer_profile.yaml
preferences:
  min_hourly_rate: 200   # было 500
```

### Проект отклонился, хотя подходит

Проверьте `data/rejected.json` — там указана причина:
- `low_score` — не хватило скора (калибруйте веса)
- `avoid_keyword:*` — сработал стоп-слово
- `avoid_skill:*` — проект подходит под avoid-навык
- `duplicate` — уже в history.json

### Web-монитор не показывает данные

```bash
# Проверить, запущен ли сервер
pgrep -a -f monitor_server

# Проверить наличие файла статуса
cat logs/pipeline_status.json

# Если порт 8080 занят
# → измените port в tools/monitor_server.py
```

### Playwright: «DISPLAY не задан»

На headless-сервере без GUI:
```bash
xvfb-run python3 tools/kwork_login.py
```

### Ошибка модели: rate limit

Free-tier модели имеют ограничения RPM/TPM. Пайплайн автоматически делает `sleep` между вызовами и retry с backoff (5с → 15с → 60с). Если всё равно превышен — пайплайн продолжается с тем, что есть (graceful degradation).

---

## Безопасность

### Критические правила

1. **`cookies.txt` = сессионный пароль kwork.ru.** Хранить только в корне проекта. В `.gitignore`. Права `chmod 600`.
2. **Никогда не выводить** содержимое `cookies.txt` или `.env` в логи, чаты, отчёты.
3. **Только `kwork-parser`** имеет право читать `cookies.txt`. Оркестратор и все остальные агенты — `deny`.
4. **Least privilege:** каждый агент имеет минимум прав (см. `opencode.json`).

### Проверка

```bash
# Проверить, что cookies.txt не закоммитится
git check-ignore cookies.txt   # → выводит cookies.txt (если в .gitignore)

# Права доступа
ls -la cookies.txt              # → -rw------- (chmod 600)
```

---

## Roadmap

| Неделя | Что планируется | Статус |
|--------|----------------|--------|
| 0–9 | MVP: все 6 агентов, CONTRACT, YAML, dry-run E2E | ✅ |
| 10–11 | Live-парсинг, веб-монитор, авто-логин | ✅ |
| 12–13 | Калибровка скоринга (indicators-driven v2.0) | ✅ |
| **14** | **README + калибровка client_quality/весов + E2E dry-run** | ✅ |
| **14b** | **kwerk_run.py — единая точка входа + починка монитора** | ✅ |
| Будущее | Competitor Analyst, Telegram-бот, мульти-биржа | 📅 |

---

## Документация

| Документ | Описание |
|----------|----------|
| [AGENT_SYSTEM_OPENCODE_V2.md](AGENT_SYSTEM_OPENCODE_V2.md) | Полный дизайн-док (779 строк) |
| [CONTRACT.md](CONTRACT.md) | JSON-контракт v2.1 (489 строк) |
| [AGENTS.md](AGENTS.md) | Hard rules и контекст |
| [MANUAL_LOGIN.md](MANUAL_LOGIN.md) | Инструкция по авто-логину |
| [tools/README.md](tools/README.md) | Документация утилит |
| [CONCLUSION.md](CONCLUSION.md) | Заключение по проекту |
| [manual-workflow.md](manual-workflow.md) | Исследование структуры kwork.ru |

---

## Лицензия

Учебный проект. Все права на kwork.ru принадлежат ООО «Кворк».

---

*Создано с использованием OpenCode, FastAPI, Playwright, HTMX и free-tier AI-моделей (NVIDIA Nemotron 3 Ultra Free).*
