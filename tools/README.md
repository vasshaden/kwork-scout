# tools/ — утилиты Kwork Scout

Вспомогательные скрипты для live-режима пайплайна.

## kwork_live_parse.py

**Назначение:** live-парсинг kwork.ru через Playwright (видимый Chrome).

### Режимы

| Режим | Описание | Выходной файл |
|-------|----------|---------------|
| `telegram_bots` / `microservices` / `content_creation` | Одна категория | `logs/parser_live_<cat>.json` |
| `all` | Все 3 категории, дедуп по ID | `logs/parser_live.json` |

### Дедуп по категориям (режим `all`)

kwork.ru возвращает одни и те же проекты в нескольких категориях. Режим `all`:
1. Один браузер, одна сессия cookies
2. Парсит все 3 категории последовательно (telegram_bots → microservices → content_creation)
3. Дедуплицирует по `project.id` с сохранением `categories: []` (массив всех категорий)
4. Выход: один файл со всеми уникальными проектами

**Пример проекта с несколькими категориями:**
```json
{
  "project": {
    "id": 3192454,
    "category": "telegram_bots",
    "categories": ["telegram_bots", "microservices"],
    "title": "Telegram-бот для заказов"
  }
}
```

### Использование

```bash
cd opencode

# Одна категория:
.venv/bin/python tools/kwork_live_parse.py telegram_bots
.venv/bin/python tools/kwork_live_parse.py microservices
.venv/bin/python tools/kwork_live_parse.py content_creation

# Все категории (рекомендуется):
.venv/bin/python tools/kwork_live_parse.py all
.venv/bin/python tools/kwork_live_parse.py all --max-pages 3
.venv/bin/python tools/kwork_live_parse.py all --output /tmp/all_projects.json

# Headless (НЕ рекомендуется — captcha):
.venv/bin/python tools/kwork_live_parse.py all --headless
```

### Exit codes

| Код | Значение |
|-----|----------|
| 0   | Успех |
| 1   | Ctrl+C |
| 2   | cookies.txt не найден |
| 4   | Неизвестная категория |
| 5   | Cookies невалидны (редирект на /login) |
| 6   | Captcha без решения |
| 7   | window.stateData не найден |
| 8   | wants[] пустой |

## Встроенный логин (в kwork_live_parse.py)

**Отдельный скрипт `kwork_login.py` больше не существует.** Логин встроен непосредственно в `kwork_live_parse.py`. При запуске парсер сам проверяет `cookies.txt`:

1. Если `cookies.txt` существует и валиден — парсинг начинается сразу.
2. Если файла нет или cookies протухли — открывается видимый Chrome на странице входа.
3. Пользователь вводит логин/пароль вручную в окне браузера.
4. После успешного входа скрипт сохраняет свежие cookies и **продолжает парсинг в том же браузере**.

Таким образом, **отдельный шаг логина не нужен** — достаточно запустить:

```bash
python3 tools/kwork_live_parse.py all
```

### Системные требования

- **Linux с X11/Wayland**, macOS, или Windows (любой GUI).
- Если у вас headless-сервер, используйте `xvfb-run`:
  ```bash
  xvfb-run python3 tools/kwork_live_parse.py all
  ```
- На сервере без GUI скрипт выведет предупреждение `[WARN] DISPLAY не задан`.

## Дальнейшие шаги

После успешного парсинга можно запустить полный пайплайн:

```bash
# Через kwork_run.py (live):

python3 kwork_run.py

# Через OpenCode skill:
@kwork-pipeline
```

## Безопасность

- `cookies.txt` = сессионный токен kwork.ru. Хранить только в корне проекта.
- Файл уже в `.gitignore` — случайный `git add .` его не зацепит.
- Права `chmod 600` выставляются автоматически скриптом.
- **Не** выводите содержимое `cookies.txt` в логи, чаты, отчёты.
- **Не** коммитьте `cookies.txt` (даже через `git add -f`).

## monitor_server.py

**Назначение:** веб-дашборд для мониторинга пайплайна в реальном времени.

FastAPI-сервер с SSE (Server-Sent Events) — показывает текущий шаг, проекты, логи, ошибки.
Открывается автоматически в браузере при запуске.

### Запуск

```bash
cd opencode

# Терминал 1: монитор
python3 tools/monitor_server.py
# → Откроется http://localhost:8080

# Терминал 2: пайплайн
"Найди проекты на kwork"
# или
@kwork-pipeline
```

### Что показывает

| Панель | Описание |
|--------|----------|
| Pipeline Graph | Mermaid-диаграмма: цвета шагов (🟢 завершён, 🔵 выполняется, ⚪ ожидает, 🔴 ошибка) |
| Метрики | Всего проектов, Apply/Review/Skip, Avg Score, Бюджет |
| Шаги пайплайна | Список всех шагов с таймингами и статусами |
| Проекты | Таблица: ID, Название, Бюджет, Категория, Score, Verdict, Risk, КП |
| Логи агентов | stdout/stderr каждого агента (последние 500 символов) |
| Ошибки | Список ошибок с описаниями |

### API endpoints

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/` | HTML-дашборд |
| GET | `/api/status` | Текущий статус (JSON) |
| GET | `/api/history` | Список последних 8 запусков |
| GET | `/api/runs/{run_id}` | Полный статус конкретного запуска |
| GET | `/api/events` | SSE-поток (обновления каждые 0.5с) |

### Автозапуск браузера

При старте сервер автоматически открывает `http://localhost:8080` в默认ном браузере.
Если браузер не открылся — откройте URL вручную.

### Зависимости

```
fastapi>=0.109
uvicorn>=0.27
jinja2>=3.1
```

Все уже установлены через `pip install -r requirements.txt`.

### Директории

```
logs/
├── pipeline_status.json   # Текущий статус (обновляется оркестратором)
└── runs/                  # История запусков (последние 8)
    ├── 2026-06-07_14-30-15.json
    └── ...

templates/
└── monitor.html           # HTMX-фронтенд
```

### Troubleshooting

| Симптом | Причина / Решение |
|---------|-------------------|
| `ModuleNotFoundError: No module named 'fastapi'` | `pip install -r requirements.txt` не выполнен |
| Порт 8080 занят | Измените порт в `monitor_server.py` (переменная `port`) |
| Браузер не открылся | Откройте `http://localhost:8080` вручную |
| Нет данных в UI | Запустите пайплайн — оркестратор пишет `pipeline_status.json` |
| SSE не работает | Проверьте, что `pipeline_status.json` обновляется. Fallback: polling каждые 2с |

---

## Troubleshooting

| Симптом | Причина / Решение |
|---------|-------------------|
| `playwright: command not found` | `pip install -r requirements.txt` не выполнен или скрипт запущен не из venv |
| `playwright install chromium` скачивает долго | Нормально, ~150 МБ. Повторный запуск использует кэш. |
| `[ERROR] DISPLAY не задан` | Запустите на машине с GUI или через `xvfb-run` |
| `[ERROR] Таймаут: пользователь не вошёл` | Логин не прошёл (капча, 2FA, неверный пароль). Скрипт вернёт exit 2. Повторите. |
| `[ERROR] Cookies для kwork.ru не найдены` | Логин вроде бы прошёл, но cookies не извлеклись. Проверьте URL, повторите. Exit 3. |
| `ModuleNotFoundError: No module named 'playwright'` | `pip install -r requirements.txt` не выполнен |
| Browser открылся, но `headless=True` (нет окна) | Проверьте `slow_mo` и `headless=False` в коде (должны быть актуальны) |
