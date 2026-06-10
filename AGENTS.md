# AGENTS.md — Kwork Scout (OpenCode)

## Контекст
Репозиторий в **предпроектной стадии**. Единственный источник правды по архитектуре, контрактам и плану — `AGENT_SYSTEM_OPENCODE_V2.md` (читать целиком, 667 строк). Без него работа в репо бессмысленна.

Цель: мультиагентный OpenCode-пайплайн (оркестратор + 6 субагентов) для парсинга, скоринга и отклика на проекты с kwork.ru.

## Чего в репо НЕТ (не искать, не запускать)
Не создано: `data/metrics.json` (опц., будущее), `README.md`, `ARCHITECTURE.md`.

Уже создано: `tests/kwork-sample.html` (День 0, v2 — День 1), `.env`/`.env.example`/`.gitignore` (День 1, `cookies.txt` добавлен День 10), `requirements.txt` (День 11, 4 строки: playwright+fastapi+uvicorn+jinja2), `tools/kwork_login.py` (День 10, ~190 строк, Playwright), `tools/monitor_server.py` (День 11, 211 строк, FastAPI+SSE), `tools/write_status.py` (День 11, 256 строк, утилита записи статуса), `tools/README.md` (День 11, документация tools/ включая monitor), `templates/monitor.html` (День 11, 435 строк, HTMX-фронтенд), `CONTRACT.md` (День 2), `config/freelancer_profile.yaml` (День 2), `prompts/{parser,orchestrator,analyzer,scorer,client-analyst,proposal-writer,history-keeper}.txt` (Дни 3-8, parser+orchestrator обновлены День 10 для preflight, orchestrator+11 для монитора), `opencode.json` (День 8, deny `cookies.txt` для build добавлен День 10), `.opencode/skills/kwork-pipeline/SKILL.md` (День 9), `proposals/2026-06-06_kwork-900001.md` (День 7), `data/history.json` + `data/rejected.json` (День 8-9, 5 + 1 entries), `logs/{parser_dry,analyzer,scorer,scorer_synthetic,client_analyst,proposal_writer,history_keeper,run}_*.json`, `logs/pipeline_status.json` (День 11, runtime), `logs/runs/` (День 11, история запусков), `reports/DAY_*.md` (Дни 0-11).

Целевая структура прод-артефактов — раздел 10 дизайн-дока. Перед `read`/`grep` всегда проверять `ls` и не строить предположений о существовании файлов.

`Temp.txt` — пустой плейсхолдер, не редактировать без явного указания.

## Секреты и креды

**v2.1+ (День 10):** live-credential — **`cookies.txt`** (экспортированная сессия kwork.ru), не `.env`.

- **`cookies.txt`** (v2.1+): сессионные cookies kwork.ru. **С v2.1+ создаются автоматически** скриптом `tools/kwork_login.py` (Playwright открывает видимый Chrome, пользователь вводит логин/пароль, скрипт извлекает cookies). Подробности — `tools/README.md`. С ними `kwork-parser` получает доступ к публичной выдаче kwork.ru (список проектов) и (опционально) к профилям заказчиков. `cookies.txt` = фактический пароль сессии. В `.gitignore`. Скрипт автоматически ставит `chmod 600`.
- **`.env`** (legacy, День 1): содержит `KWORK_LOGIN` и `KWORK_PASSWORD`. С v2.1 парсер **не читает** `.env` (cookies.txt достаточно). Оставлен как документация (напоминание, под каким логином создавались cookies). В `.gitignore`.
- **Только `kwork-parser`** имеет право читать `cookies.txt` (через `read` или `bash`). Остальные субагенты (включая `build`/orchestrator) — `deny` на `cookies.txt` и на все способы его прочитать.
- **Запрещено читать/выводить содержимое `cookies.txt` и `.env` любыми инструментами** (`read`, `cat`, `head`, `tail`, `grep`, `awk`, `sed`, `source`, `printenv`, `env | grep`) — ни одному агенту, включая оркестратор, ни в каком контексте (включая «проверить, заполнен ли файл», «посмотреть формат», «проверить наличие куки»). Для проверки существования — `ls -la cookies.txt` или `test -f cookies.txt`. Для проверки формата — `head -c 80 cookies.txt` (первые 80 символов, общий вид, без значений). **Никогда** не выводить содержимое в логи/отчёты/JSON.
- **Прецедент:** в День 1 при «проверке» через `cat .env` пароль был выведен в вывод сессии. Не повторять. **День 10+:** та же ошибка с `cookies.txt` = утечка сессии = компрометация аккаунта kwork.ru.

## Жёсткие правила (из дизайн-дока, не нарушать)
- **JSON-контракт v2.1** (раздел 3) — единственный формат обмена между агентами. Поля `_schema_version`, `_timestamp`, `_dry_run` обязательны в каждом выходе. Не вводить параллельные схемы.
- **Dry run** (`_dry_run: true`): запрещены `webfetch` к kwork.ru, запись в `data/history.json` и `data/rejected.json`. Parser в dry-режиме читает `tests/kwork-sample.html` через `read`.
- **Скоринг конфигурируется в YAML** (`config/freelancer_profile.yaml`): веса, пороги, навыки, `min_hourly_rate`, `max_project_duration_days`, `avoid_keywords`. **Не хардкодить** эти значения в промптах/коде.
- **Модели жёстко закреплены** (День 10, v2.1+, **все бесплатные**):
  - `deepseek/deepseek-v4-flash-free` — **только** `kwork-proposal-writer` и orchestrator (`build`).
  - `nvidia/nemotron-3-ultra-free` — `kwork-parser`, `kwork-analyzer`, `kwork-scorer`, `kwork-client-analyst`.
  - `minimax/minimax-m3-free` — `kwork-history-keeper` (простая задача атомарной записи).
  - Бюджет прогона: **$0.00** (free tier всех моделей).
  - **Ограничение free tier:** rate limits (RPM/TPM). Митигация: `sleep 2-3 сек` между вызовами агентов, retry с backoff при 429.
  - **Качество:** DeepSeek V4 Flash Free приблизительно соответствует Sonnet по следованию инструкциям и JSON-форматированию. Nemotron 3 Ultra Free — немного слабее, но достаточен для парсинга/скоринга. При ухудшении качества — рассматривать переход на платный tier.
- **Least privilege** (раздел 7): таблица разрешений субагентов обязательна. `kwork-analyzer` и `kwork-scorer` — `read`/`grep`/`glob` без `bash`/`webfetch`/`write`. Не раздавать лишних прав.
- **Атомарная запись** истории: `kwork-history-keeper` за один запуск пишет **оба** файла (`history.json` + `rejected.json`). Не разделять на два вызова.
- **Дедупликация тройная**: (1) `kwork_live_parse.py` в режиме `all` — дедуп по `project.id` с `categories: []` массивом; (2) `kwork-analyzer` — defensive-dedup; (3) `kwork-history-keeper` — финальная дедуп.
- **Self-correction КП**: `kwork-proposal-writer` сам валидирует (≤1500 симв, цена, срок, обращение, вопрос в конце, без спам-фраз), до 2 итераций.
- **Ошибки агентов не роняют пайплайн** — логировать в `logs/run_*.json` и продолжать.
- **Таймауты/параллелизм**: ≤5 параллельных тасков, 60 с на агента, цикл ≤8 мин на 10 проектов.
- **Секреты никогда не читаются/выводятся** (см. раздел «Секреты и креды»): `kwork-parser` — единственный агент с правом `source .env`; все остальные (включая оркестратор) — `deny` на `.env` и на `bash`-команды, способные вывести его содержимое. Нарушение = утечка кредов в выводе сессии, недопустимо.

## Пайплайн (порядок строго задан)
1. `kwork-parser` (режим `all`) — парсит все 3 категории в одном браузере, дедуп по `project.id` с `categories: []` массивом → `logs/parser_live.json`.
2. `kwork-analyzer` (фильтр по навыкам + defensive-dedup + предварительный скор 1–10).
3. `kwork-scorer` (взвешенный скор 1–100 по `scoring_weights`).
4. **Параллельно с (5)**: `kwork-client-analyst` для `verdict ∈ {apply, review}` (т.е. `score ≥ review_score = 60`).
5. `kwork-proposal-writer` для `verdict == "apply"` И `client.risk != "high"`.
6. `kwork-history-keeper` — атомарное сохранение всех проектов (apply/review/skip).
7. Markdown-отчёт: таблица (`ID | Название | Бюджет | Score | Verdict | Risk | КП`) + сводка по затратам/времени.

## Порядок разработки (раздел 8 дизайн-дока, **не прыгать**)
Parser → Analyzer → Scorer → Client Analyst → Proposal Writer → History Keeper → Оркестратор/Skill → E2E. Каждый этап завершать проверкой соответствующего пункта Definition of Done (раздел 9).

## Куда класть файлы (быстрая карта по дизайн-доку)
- Конфиг агентов и разрешений → `opencode.json` (раздел 2).
- Системные промпты → `prompts/{orchestrator,parser,analyzer,scorer,client-analyst,proposal-writer,history-keeper}.txt` (раздел 6).
- Профиль фрилансера → `config/freelancer_profile.yaml` (раздел 4).
- Скилл-оркестратор → `.opencode/skills/kwork-pipeline/SKILL.md` (раздел 5, `name: kwork-pipeline`, запуск: `Найди проекты на kwork` или `@kwork-pipeline`, dry: `@kwork-pipeline dry_run=true`).
- Тестовый HTML → `tests/kwork-sample.html` (нужен до старта Parser, см. день 0–3).
- История/отказы → `data/history.json`, `data/rejected.json` (атомарно).
- КП → `proposals/YYYY-MM-DD_kwork-ID.md`.
- Live-данные парсера → `logs/parser_live.json` (все категории, дедуп по ID).
- Лог прогона → `logs/run_YYYY-MM-DD_HH-MM-SS.json`.

## Команды
На текущей стадии **никаких** `build`/`test`/`lint`/dev-server команд не существует. Не выдумывать. Проверка — ручной запуск OpenCode + сверка с Definition of Done (раздел 9).

## Чего НЕ делать
- Не добавлять категории парсинга сверх трёх утверждённых.
- Не менять распределение моделей (Sonnet → только writer/orchestrator).
- Не вводить параллельные форматы выхода мимо JSON v2.1.
- Не выкидывать `dry_run` ветку при рефакторинге.
- Не ослаблять `Definition of Done` (раздел 9): dry run обязателен, КП ≤1500 симв, дубли = 0, `rejected.json` с причиной для каждого отклонённого.
- **Не читать `.env` (`cat`/`read`/`grep`/`head`/`tail`/`source`/`printenv`)** ни одному агенту кроме `kwork-parser`, ни в каком контексте — даже «чтобы проверить, заполнен ли файл». Использовать `ls -la .env` или `test -f .env` для проверки существования.
- **Не читать `cookies.txt` ни одним инструментом** (`read`/`cat`/`head`/`tail`/`grep`/`awk`/`sed`) — ни одному агенту, кроме `kwork-parser`, ни в каком контексте. Это сессионные cookies kwork.ru = фактический пароль. Для проверки существования — `ls -la cookies.txt` или `test -f cookies.txt`. Для проверки формата — `head -c 80 cookies.txt` (первые 80 символов, общий вид, без значений). Никогда не выводить содержимое в логи/отчёты/JSON.
