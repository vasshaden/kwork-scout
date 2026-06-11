---
name: kwork-pipeline
description: Запускает полный пайплайн Kwork Scout (6 субагентов: parser → analyzer → scorer → client-analyst → proposal-writer → history-keeper) для поиска, скоринга и отклика на проекты с kwork.ru. Use ONLY when the user asks to find projects on kwork, run the kwork pipeline, generate Kwork proposals, check the project funnel, or invokves `@kwork-pipeline` (e.g. "Найди проекты на kwork", "@kwork-pipeline", "прогони пайплайн"). Do NOT use for general kwork.ru browsing, manual proposals, or non-kwork freelance sites.
---

# Kwork Pipeline Skill

Этот skill запускает **полный пайплайн** мультиагентной системы **Kwork Scout v2.1** (6 субагентов, см. `prompts/orchestrator.txt`).

## Что делает skill

1. Парсит kwork.ru (режим `all`: 3 категории в одном браузере, дедуп по ID).
2. Анализирует + скорит + проверяет заказчиков + генерирует КП.
3. Атомарно сохраняет в `data/history.json` + `data/rejected.json`.
4. Возвращает **markdown-отчёт** (таблица + сводка по затратам/времени).

## Как вызвать

Пользователь говорит:
- «Найди проекты на kwork» или «@kwork-pipeline» → полный live-пайплайн.
- «@kwork-pipeline max_projects=3» → первые 3 проекта после парсера.
- «@kwork-pipeline clear_history=true» → очистить `data/` перед прогоном (осторожно).

## Параметры (извлекаются из команды)

| Параметр | Default | Описание |
|----------|---------|----------|
| `categories` | все 3 | Список категорий из: `telegram_bots`, `microservices`, `content_creation` |
| `max_projects` | без лимита | Макс. проектов после Шага 1 (parser) |
| `clear_history` | `false` | Очистить `data/history.json` + `data/rejected.json` перед стартом |

## Граф пайплайна (CONTRACT.md §1)

```
[Parser(all)] → [Analyzer] → [Scorer] → [Client-Analyst ∥ Writer] → [History-Keeper] → [Markdown Report]
```

1 парсер (режим all) — все категории в одном браузере; client-analyst и writer — параллельно; остальные — последовательно.

## Где смотреть результат

- **Сразу:** markdown-таблица в ответе primary-агента (см. `opencode.json` build agent).
- **Артефакты:** `logs/{parser_live.json, analyzer_*.json, scorer_*.json, client_analyst_*.json, proposal_writer_*.json, history_keeper_*.json}`.
- **КП:** `proposals/YYYY-MM-DD_kwork-<ID>.md` (только для `verdict=apply ∧ risk≠high`).
- **История:** `data/history.json` (все) + `data/rejected.json` (только skipped).

## Hard rules (см. `prompts/orchestrator.txt` §"ПРАВИЛА")

1. **Ошибка агента** → логировать, **не останавливать** пайплайн.
2. **Атомарная запись** history — только через `kwork-history-keeper` за один запуск.
3. **Не модифицировать** проекты вручную — только агрегировать.
4. **Не выводить** секреты (логины, пароли, cookies) в отчёте.
5. **Параллелизм:** ≤5 одновременных тасков, 60 с таймаут на агента, цикл ≤8 мин на 10 проектов.
6. **Бюджет:** **$0.00** на 10 проектов (все модели — free tier). **Rate limits:** возможны 429 ошибки → ретрай с backoff, `sleep 2-3 сек` между вызовами агентов.

## Секреты и креды

**Только** `kwork-parser` имеет право читать `cookies.txt` (сессионные cookies kwork.ru, экспортируются пользователем вручную — см. `tools/README.md`). Остальные агенты (включая primary `build`) — `deny` на `cat cookies.txt`, `head cookies.txt`, `grep .* cookies.txt`, `read cookies.txt`. Нарушение = утечка сессии = компрометация аккаунта kwork.ru (прецедент Дня 1 был с `.env`).

**`.env`** (legacy) содержит `KWORK_LOGIN`/`KWORK_PASSWORD`. С v2.1 парсер **не читает** его — `cookies.txt` достаточно. `.env` остаётся в `.gitignore` как документация (напоминание, под каким логином создавались cookies).

## Запуск в OpenCode

После настройки `SKILL.md` и `opencode.json`:
1. Вызвать: «Найди проекты на kwork» или `@kwork-pipeline`.
2. Primary-агент (`build` в `opencode.json`) прочитает этот skill, вызовет 6 субагентов через `task` tool, агрегирует, выдаст markdown-отчёт.

## Связанные файлы

- `prompts/orchestrator.txt` — полный промпт primary-агента.
- `prompts/{parser,analyzer,scorer,client-analyst,proposal-writer,history-keeper}.txt` — субагента.
- `opencode.json` — конфиг агентов и прав (единая модель: `opencode/nemotron-3-ultra-free` для всех 7 агентов).
- `CONTRACT.md` v2.1 — JSON-схема обмена.
- `config/freelancer_profile.yaml` — пороги, веса, навыки (источник правды).
- `AGENTS.md` — главный README проекта, hard rules.
