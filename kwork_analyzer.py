#!/usr/bin/env python3
"""
Симуляция kwork-analyzer (День 4) на logs/parser_live.json + config/freelancer_profile.yaml.
Имитирует логику, которую Haiku выполнил бы по prompts/analyzer.txt.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
import yaml

ROOT = Path(__file__).parent
INPUT_FILE = ROOT / "logs" / "parser_live.json"
OUTPUT_DIR = ROOT / "logs"
CONFIG = ROOT / "config" / "freelancer_profile.yaml"
HISTORY = ROOT / "data" / "history.json"
CATEGORIES = ["telegram_bots", "microservices", "content_creation"]


def load_yaml():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def load_inputs():
    """Собирает проекты из parser_live.json."""
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"no {INPUT_FILE}")
    d = json.loads(INPUT_FILE.read_text(encoding="utf-8"))
    return d.get("projects", []), [INPUT_FILE]


def load_history_ids():
    """Возвращает set project.id из data/history.json (если есть)."""
    if not HISTORY.exists():
        return set()
    try:
        d = json.loads(HISTORY.read_text(encoding="utf-8"))
        return {p["id"] for p in d.get("processed", []) if "id" in p}
    except (json.JSONDecodeError, KeyError):
        return set()


def find_avoid_keyword(text, avoid_keywords):
    """Возвращает первое совпадение из avoid_keywords (case-insensitive) или None."""
    t = text.lower()
    for kw in avoid_keywords:
        if kw.lower() in t:
            return kw
    return None


def find_skill(text, skill):
    """Ищет skill в text (case-insensitive). Для коротких слов использует word boundary,
    но для русских слов (с кириллицей) допускает русскую морфологию (бота, боты, ботом)."""
    s = skill.lower()
    t = text.lower()
    if len(s) <= 3:
        # Для коротких используем word boundary
        pattern = r"\b" + re.escape(s) + r"\b"
        if re.search(pattern, t):
            return True
        # Для русских коротких слов (с кириллицей) — проверяем без word boundary,
        # чтобы ловить морфологию: "бота", "боты", "ботом", "ии" и т.д.
        if any('\u0400' <= c <= '\u04FF' for c in s):
            if s in t:
                return True
        # Доп. варианты: "aiogram" → "aiogram 3", "postgresql" → "postgres"
        for variant in _skill_variants(skill):
            if re.search(r"\b" + re.escape(variant) + r"\b", t):
                return True
            # Для русских вариантов — in (без word boundary)
            if any('\u0400' <= c <= '\u04FF' for c in variant):
                if variant in t:
                    return True
        return False
    return s in t or any(v in t for v in _skill_variants(skill))


def _skill_variants(skill):
    """Дополнительные написания для известных навыков (EN + RU).
    
    Русские синонимы основаны на анализе live-данных kwork.ru:
    - 'бот' встречается в 10/12 проектах → variant для python-telegram-bot
    - 'тг' встречается в 5/12 → variant для python-telegram-bot
    - 'парсер' встречается в 3/12 → variant для microservices core
    - 'ии' встречается в 7/12 → variant для... нет прямого соответствия
    """
    variants = {
        # =====================================================================
        # telegram_bots CORE (legacy tech - now in tech_stack)
        # =====================================================================
        "aiogram": ["aiogram 3", "aiogram3", "aiogram 2", "аиограм", "айограм"],
        "python-telegram-bot": [
            "python telegram bot", "ptb",
            # Русские варианты (из live-данных kwork.ru):
            "бот",         # "ТГ Бот", "чат-бот", "RPA-бот", "Tg-бота"
            "чат-бот",     # "ИИ-чат-бот"
            "чат бот",
            "тг бот",      # "ТГ Бот с фильтром"
            "тг-бот",
            "telegram бот",
            "telegram-бот",
            "tg бот",
            "tg-бот",
            "tg bot",
            "телеграм",    # "связать его с Telegram", "Claude в ТГ"
            "телеграм бот",
            "телеграм-бот",
            "тг",          # аббревиатура Telegram
        ],
        "telethon": ["телетон", "телеграм клиент"],
        # Новые core-скиллы (добавлены при калибровке min_hourly_rate=200)
        # Новые indicator-скиллы (v2.0, indicators-driven matching)
        "telegram": ["tg", "тг", "телеграм", "телеграмма"],
        "tg": ["telegram", "тг", "телеграм"],
        "бот": ["бота", "боты", "боту", "ботом", "ботами", "чат-бот", "чат бот",
                "тг-бот", "тг бот", "tg-бот", "tg бот", "telegram-бот",
                "бота для", "ботов", "бото"],
        "чат-бот": ["чат бот", "чатбот", "chat bot", "chat-bot", "бота для чата"],
        "рассылка": ["рассылок", "рассылки", "рассылать", "mass mailing", "newsletter"],
        "подписка": ["подписок", "подписки", "платная подписка", "subscription",
                     "доступ по подписке"],
        "оплата": ["платеж", "оплаты", "прием оплаты", "payment", "pay",
                   "донат", "donate", "покупка"],
        "чат": ["чата", "чаты", "online chat", "live chat", "чатик"],
        "ассистент": ["помощник", "ассистента", "виртуальный ассистент",
                      "ai ассистент", "ии ассистент", "помощника"],
        "автоматизация": ["автомат", "автоматизировать", "automation",
                          "автоматической", "робот"],
        "парсинг": ["парсер", "парсить", "сбор данных", "scraping", "scraper",
                    "сбор информации", "извлечение данных"],
        # tech_stack для telegram_bots
        "ai": ["ии", "искуственный интеллект", "ai",
               "gpt", "chatgpt", "chat-gpt", "chat gpt",
               "нейросеть", "нейронка", "нейро"],
        "openai": ["open ai", "open-ai", "gpt", "chatgpt", "chat-gpt", "chat gpt"],
        "gpt": ["chatgpt", "gpt-4", "gpt-3", "chat gpt", "chat-gpt", "llm", "языковая модель"],
        # =====================================================================
        # microservices INDICATORS
        # =====================================================================
        "микросервис": ["микро сервис", "microservice", "micro-service", "мс"],
        "backend": ["бэкенд", "бекенд", "back end", "back-end", "бэка", "серверная часть"],
        "сервер": ["серверный", "серверная", "серверное", "server", "сервера",
                   "серверной части"],
        "высоконагружен": ["highload", "high load", "high-load", "высокая нагрузка",
                           "нагруженный", "нагруженных"],
        "архитектура": ["архитектур", "архитектурный",
                        "проектирование архитектуры"],
        "go": ["golang", "го", "голинг", "гоу"],
        "grpc": ["грпс", "групк"],
        "protobuf": ["протобуф", "proto", "прото"],
        "rest": ["rest api", "restful", "rest-api", "restapi", "rest запрос"],
        # =====================================================================
        # content_creation INDICATORS
        # =====================================================================
        "статья": ["статьи", "статей", "статью", "написание статей", "article",
                   "articles", "написать статью"],
        "блог": ["блога", "блогов", "blog", "ведение блога"],
        "техническая документация": ["технический писатель", "техписатель",
                                      "тех документация", "тех док", "техдок",
                                      "документация к", "documentation"],
        "контент": ["контента", "контентный", "content", "контентная"],
        "раскадровка": ["storyboard", "сценарий", "кадровка", "скрипт для видео",
                        "сценарий видео"],
        "туториал": ["tutorial", "обучающий материал", "урок", "руководство",
                     "how-to", "howto", "как сделать"],
        "гайд": ["guide", "гид", "мануал", "manual", "инструкция",
                 "пользовательская инструкция"],
        "devrel": ["деврел", "dev rel", "девелопер релейшнс", "developer relations"],
        "technical-writing": [
            "техническое письмо",
            "техписатель", "тех писатель",
            "технический писатель",
            "документатор",
            "техдок", "техдоки",
            "техническая документация",
        ],
        # =====================================================================
        # content_creation CORE
        # =====================================================================
        "документация": [
            "доки", "документы", "техдоки",
            "documentation", "docs",
            "документирование",
        ],
        # =====================================================================
        # tech_stack items (used by all categories)
        # =====================================================================
        "python": ["питон", "пайтон", "пайтон3", "python3", "py"],
        "fastapi": ["fast api", "fast-api", "фастапи", "фаст апи"],
        "postgresql": ["postgres", "postgresq", "постгрес", "пг", "пгскл"],
        "redis": ["редис", "реддис"],
        "docker": ["докер", "контейнеризация", "контейнеры"],
        "asyncio": ["асинк", "асинхронность", "async"],
        "celery": ["селери", "селeри", "таск-очередь", "очередь задач"],
        "kubernetes": ["k8s", "кубернетес", "кубер", "куб"],
        "prometheus": ["прометеус", "прометеус"],
        "grafana": ["графана"],
        "opentelemetry": ["опентелеметри", "open telemetry", "otel"],
        "helm": ["хельм", "helm charts"],
        "devops": ["девопс", "деплой", "цикд", "ci/cd", "ci cd"],
        "sqlalchemy": ["салхими", "алхими", "sa", "alchemy"],
        "mongodb": ["монго", "mongo", "mongo db", "монгоДБ"],
        # =====================================================================
        # adjacent indicators (minor bonus for matching)
        # =====================================================================
        "нейросеть": ["нейронка", "нейро", "нейросети", "нейронные сети",
                      "neural", "nn"],
        "интеграция": ["интегрировать", "интеграционный", "integration",
                       "подключение к", "связать с"],
        "api": ["апи", "api интерфейс", "application programming interface",
                "внешнее api", "rest api"],
        "веб-хук": ["webhook", "web hook", "вебхук", "веб хуки"],
        "миграция": ["migration", "перенос данных", "мигрировать"],
        "оптимизация": ["оптимизировать", "optimization", "ускорение",
                        "производительность"],
        "масштабирование": ["scalability", "scale", "scalable", "масштабировать"],
        # =====================================================================
        # content_creation adjacent (tech_stack for docs)
        # =====================================================================
        "sphinx": ["сфинкс", "sphinx doc", "restructuredtext", "rst"],
        "mkdocs": ["mk docs", "mkdoc", "material for mkdocs"],
        "markdown": ["md", "маркдаун", "маркдавн"],
        # =====================================================================
        # AVOID SKILLS (extended for Russian)
        # =====================================================================
        "viber-api": ["вайбер", "viber"],
        "whatsapp-api": ["вацап", "whatsapp", "ватсап"],
        "vk-bot": ["вк бот", "вконтакте бот", "vk bot", "вк каналы"],
        "seo-copywriting": ["сео копирайтинг", "сео-копирайтинг", "seo copywriting"],
        "рерайт": ["rewrite", "переписывание", "рерайтер"],
        "копирайтинг-маркетинг": ["маркетинговый копирайтинг", "copywriting marketing"],
        "smm": ["смм", "соцсети", "social media"],
        "smm-менеджмент": ["управление соцсетями", "smm management"],
        "soap": ["сoap", "cоап"],
        "websphere": ["вебсфера", "web sphere"],
        "1с": ["1c", "один с", "1с предприятие", "1с8"],
        "1c-bitrix": ["1с-битрикс", "bitrix", "битрикс"],
    }
    return variants.get(skill.lower(), [])


def find_red_flags(text, red_flags):
    """Возвращает список найденных red_flag подстрок."""
    t = text.lower()
    return [rf for rf in red_flags if rf.lower() in t]


def filter_project(project, client, cfg, history_ids, seen_ids):
    """Шаг 2: проверка rejection conditions. Возвращает rejection_reason или None."""
    skip = cfg.get("preferences", {}).get("skip_if", [])
    p = project
    c = client

    if "no_client_data" in skip and (c.get("nickname") == "unknown" or c.get("user_id", 0) == 0):
        return "no_client_data"
    if "no_budget" in skip and p.get("budget_rub", 0) == 0 and p.get("budget_ceiling_rub", 0) == 0:
        return "no_budget"
    desc = (p.get("full_description") or p.get("description") or "").strip()
    if "no_description" in skip and len(desc) < 20:
        return "no_description"
    if "deadline_too_long" in skip and p.get("deadline_days", 0) > cfg["preferences"]["max_project_duration_days"]:
        return "deadline_too_long"
    if p.get("deadline_days", 0) == 0:
        return "deadline_zero"

    full_text = f"{p.get('title','')} {desc}"
    avoid_kw = cfg.get("avoid_keywords", [])
    hit = find_avoid_keyword(full_text, avoid_kw)
    if hit:
        return f"avoid_keyword:{hit}"

    cats = cfg.get("preferences", {}).get("preferred_categories", [])
    project_categories = project.get("categories", [project.get("category")])
    # Проверяем, что хотя бы одна категория проекта в preferred_categories
    if cats and not any(c in cats for c in project_categories):
        return "category_mismatch"

    if "duplicate" in skip:
        pid = p.get("id")
        cat = p.get("category")
        key = (pid, cat)
        if pid in history_ids or key in seen_ids:
            return "duplicate"

    return None


def skills_match(project, cfg):
    """Шаг 3: matching навыков — INDICATORS-DRIVEN v2.0.
    
    НЕ ищет явные названия библиотек (aiogram, sqlalchemy) — клиенты kwork
    их не пишут. Вместо этого:
      1. indicators: слова-признаки ЧТО нужно сделать (чат-бот, телеграм, парсинг).
         Формируют базовый category_skills_match (доля совпавших indicators).
      2. tech_stack: технологии, которые МЫ бы использовали. Дают бонус +0.1 за каждую
         найденную, cap +0.2 к итогу.
      3. adjacent: дополнительные признаки — дают малый бонус.
      4. avoid: дисквалификация только для категории с наилучшим match'ем.
    
    Итог: category_skills_match = min(indicators_match + tech_bonus + adj_bonus, 1.0)
    """
    categories = project.get("categories", [project.get("category")])
    
    full_text = (f"{project.get('title','')} "
                 f"{project.get('full_description') or project.get('description') or ''}")
    
    # Сначала собираем результаты по всем категориям, потом выбираем лучшую
    # Это нужно чтобы avoid_skill применялся только к лучшей категории,
    # а не срабатывал на первой попавшейся
    candidates = []
    
    for cat in categories:
        if cat not in cfg.get("skills", {}):
            continue
        skills_cfg = cfg["skills"][cat]
        indicators = skills_cfg.get("indicators", [])
        tech_stack = skills_cfg.get("tech_stack", [])
        adjacent = skills_cfg.get("adjacent", [])
        avoid = skills_cfg.get("avoid", [])
        
        if not indicators:
            continue
        
        # Avoid check для этой категории
        avoid_hit = next((a for a in avoid if find_skill(full_text, a)), None)
        
        # 1. Indicators match (базовый скор)
        matched_indicators = [s for s in indicators if find_skill(full_text, s)]
        indicators_match = len(matched_indicators) / len(indicators) if indicators else 0.0
        
        # 2. Tech stack bonus
        matched_tech = [s for s in tech_stack if find_skill(full_text, s)]
        tech_bonus = min(len(matched_tech) * 0.1, 0.2)
        
        # 3. Adjacent bonus
        matched_adj = [s for s in adjacent if find_skill(full_text, s)]
        adj_bonus = min(len(matched_adj) * 0.05, 0.1)
        
        # Итоговый category_skills_match
        cat_match_score = min(indicators_match + tech_bonus + adj_bonus, 1.0)
        
        # Запоминаем кандидата
        all_skills = indicators + tech_stack + adjacent
        all_matched = matched_indicators + matched_tech + matched_adj
        skills_match_score = len(all_matched) / len(all_skills) if all_skills else 0.0
        
        candidates.append({
            "cat": cat,
            "cat_match_score": cat_match_score,
            "skills_match_score": skills_match_score,
            "matched_indicators": matched_indicators,
            "matched_tech": matched_tech,
            "matched_adj": matched_adj,
            "all_matched": all_matched,
            "avoid_hit": avoid_hit,
        })
    
    if not candidates:
        return None
    
    # Сортируем по cat_match_score (лучший первый)
    candidates.sort(key=lambda c: -c["cat_match_score"])
    best = candidates[0]
    
    # Apply check для лучшей категории: если avoid_hit И match неплохой —
    # это не случайное совпадение
    if best["avoid_hit"] and best["cat_match_score"] < 0.3:
        # Если совпадение очень слабое (<0.3) и есть avoid — дисквалификация
        return {"avoid_skill_hit": best["avoid_hit"]}
    elif best["avoid_hit"] and best["cat_match_score"] >= 0.3:
        # Нашлась сильная категория с avoid-словом — это false positive,
        # берём следующего кандидата без avoid
        secondary = [c for c in candidates[1:] if not c["avoid_hit"]]
        if secondary:
            best = secondary[0]
            print(f"  [INFO] Смена категории: {best['cat']} (избегли avoid_skill)")
        else:
            # Все кандидаты с avoid — дисквалификация
            return {"avoid_skill_hit": best["avoid_hit"]}
    
    result = {
        "skills_matched": best["all_matched"],
        "skills_match": round(best["skills_match_score"], 3),
        "category_skills_match": round(best["cat_match_score"], 3),
        "indicators_match": round(
            len(best["matched_indicators"]) / max(len(cfg["skills"][best["cat"]].get("indicators", [])), 1), 3),
        "indicators_matched": best["matched_indicators"],
        "tech_stack_matched": best["matched_tech"],
        "adjacent_matched": best["matched_adj"],
        "matched_core_count": len(best["matched_indicators"]),
        "matched_adjacent_count": len(best["matched_adj"]),
        "category_used": best["cat"],
    }
    return result


def preliminary_score(category_skills_match, hourly_implied, deadline_days, red_flags, min_hourly_rate):
    """Шаг 6: предварительный скор 1-10."""
    base = category_skills_match * 6  # 0..6
    if hourly_implied >= min_hourly_rate:
        base += 2
    if deadline_days and deadline_days <= 7:
        base += 1
    elif deadline_days and deadline_days <= 14:
        base += 0.5
    if red_flags:
        base -= 1
    return max(1, min(10, round(base)))


def analyze(project, client, cfg, history_ids, seen_ids):
    """Запускает полный пайплайн анализа на одном проекте. Возвращает analysis dict."""
    rejection = filter_project(project, client, cfg, history_ids, seen_ids)
    if rejection:
        return {
            "skills_match": None,
            "skills_matched": [],
            "indicators_matched": [],
            "tech_stack_matched": [],
            "category_skills_match": None,
            "hourly_implied": None,
            "preliminary_score": 0,
            "red_flags": [],
            "rejection_reason": rejection,
            "category": project.get("category"),
        }

    sk = skills_match(project, cfg)
    if sk and sk.get("avoid_skill_hit"):
        return {
            "skills_match": None,
            "skills_matched": [],
            "indicators_matched": [],
            "tech_stack_matched": [],
            "category_skills_match": None,
            "hourly_implied": None,
            "preliminary_score": 0,
            "red_flags": [],
            "rejection_reason": f"avoid_skill:{sk['avoid_skill_hit']}",
            "category": project.get("category"),
            "category_used": project.get("category"),
        }

    desc = project.get("full_description") or project.get("description") or ""
    red_flags = find_red_flags(desc, cfg.get("avoid_red_flags", []))

    deadline_days = project.get("deadline_days", 0) or 0
    hourly = project.get("budget_rub", 0) / max(deadline_days * 8, 1) if deadline_days else 0.0

    score = preliminary_score(
        sk["category_skills_match"],
        hourly,
        deadline_days,
        red_flags,
        cfg["preferences"]["min_hourly_rate"],
    )

    if deadline_days == 0 and "deadline_unknown" not in red_flags:
        red_flags.append("deadline_unknown")

    return {
        "skills_match": sk["skills_match"],
        "skills_matched": sk["skills_matched"],
        "indicators_matched": sk.get("indicators_matched", []),
        "tech_stack_matched": sk.get("tech_stack_matched", []),
        "category_skills_match": sk["category_skills_match"],
        "hourly_implied": round(hourly, 1),
        "preliminary_score": score,
        "red_flags": red_flags,
        "rejection_reason": None,
        "category": project.get("category"),
        "category_used": sk.get("category_used", project.get("category")),
    }


def validate_against_contract(contract):
    """Минимальная валидация analysis по CONTRACT.md §5.
    Для rejected-проектов (rejection_reason != null) skills_match/category_skills_match/hourly_implied
    могут быть null (см. prompts/analyzer.txt)."""
    issues = []
    a = contract.get("analysis")
    if a is None:
        return ["analysis must be dict, not null at analyzer stage"]

    is_rejected = a.get("rejection_reason") is not None
    allow_null_metrics = is_rejected

    def check_num_or_null(field, required=False):
        v = a.get(field)
        if v is None and (allow_null_metrics or not required):
            return
        if not isinstance(v, (int, float)):
            issues.append(f"analysis.{field} must be number or null (rejected={is_rejected}), got {type(v).__name__}")

    check_num_or_null("skills_match", required=False)
    check_num_or_null("category_skills_match", required=False)
    check_num_or_null("hourly_implied", required=False)

    if not isinstance(a.get("skills_matched"), list):
        issues.append("analysis.skills_matched must be list")
    # v2.0: skills_missing_core заменён на indicators_matched/tech_stack_matched
    if a.get("skills_missing_core") is not None:
        # Для обратной совместимости принимаем null (нехотя)
        pass
    if not isinstance(a.get("preliminary_score"), int) or not (0 <= a["preliminary_score"] <= 10):
        issues.append(f"analysis.preliminary_score must be int 0..10, got {a.get('preliminary_score')}")
    if not isinstance(a.get("red_flags"), list):
        issues.append("analysis.red_flags must be list")
    if a.get("rejection_reason") is not None and not isinstance(a.get("rejection_reason"), str):
        issues.append("analysis.rejection_reason must be string or null")
    if a.get("category") not in CATEGORIES:
        issues.append(f"analysis.category invalid: {a.get('category')}")
    return issues


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-history", action="store_true", help="Не проверять дубликаты по data/history.json")
    args, _ = ap.parse_known_args()

    OUTPUT_DIR.mkdir(exist_ok=True)
    cfg = load_yaml()
    inputs, input_files = load_inputs()
    history_ids = set() if args.skip_history else load_history_ids()

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out_path = OUTPUT_DIR / f"analyzer_{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')}.json"

    print("=" * 78)
    print("DRY RUN: kwork-analyzer")
    print(f"  Вход: {len(input_files)} файлов, {len(inputs)} проектов")
    print(f"  История: {len(history_ids)} id (data/history.json)")
    print("=" * 78)

    # Parser уже сделал дедуп по ID с categories: [] — используем как есть
    # (может быть defensive dedup по ID на случай дублей)
    seen_ids = set()
    filtered_inputs = []
    for contract in inputs:
        pid = contract.get("project", {}).get("id")
        if pid is not None and pid in seen_ids:
            continue
        seen_ids.add(pid)
        filtered_inputs.append(contract)

    print(f"  После дедупа: {len(inputs)} → {len(filtered_inputs)} проектов")

    seen_ids = set()
    rejected_count = 0
    kept_count = 0
    duplicate_count = len(inputs) - len(filtered_inputs)
    by_category = {c: 0 for c in CATEGORIES}
    all_issues = []

    for contract in filtered_inputs:
        project = contract.get("project") or {}
        client = contract.get("client") or {}
        analysis = analyze(project, client, cfg, history_ids, seen_ids)
        contract["analysis"] = analysis
        contract["_pipeline_stage"] = "analyzer"
        contract["_timestamp"] = timestamp

        # Валидация
        issues = validate_against_contract(contract)
        if issues:
            all_issues.extend([(project.get("id"), x) for x in issues])

        pid = project.get("id")
        # Используем category_used из анализа (лучшая категория для навыков)
        cat = analysis.get("category_used", project.get("category"))

        if pid in seen_ids:
            duplicate_count += 1
            continue

        seen_ids.add(pid)

        reason = analysis["rejection_reason"]
        if reason:
            rejected_count += 1
            if reason == "duplicate":
                duplicate_count += 1
            print(f"  ❌ [{pid}] {project.get('title','')[:48]:<48} → {reason}")
        else:
            kept_count += 1
            by_category[cat] = by_category.get(cat, 0) + 1
            score = analysis["preliminary_score"]
            skm = analysis["category_skills_match"]
            hourly = analysis["hourly_implied"]
            flags = ",".join(analysis["red_flags"]) or "-"
            print(f"  ✅ [{pid}] {project.get('title','')[:48]:<48} "
                  f"score={score} skm={skm:.2f} hrly={hourly:.0f} flags={flags}")

    output = {
        "_schema_version": "2.1",
        "_timestamp": timestamp,
        "_dry_run": True,
        "_pipeline_stage": "analyzer",
        "_batch": {
            "input_count": len(inputs),
            "kept_count": kept_count,
            "rejected_count": rejected_count,
            "duplicate_count": duplicate_count,
            "by_category": by_category,
        },
        "projects": inputs,
    }

    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 78)
    print(f"  ИТОГО: вход={len(inputs)}, kept={kept_count}, rejected={rejected_count}, "
          f"duplicates={duplicate_count}")
    print(f"  По категориям (kept): {by_category}")
    print(f"  Артефакт: {out_path.name}")
    print(f"  Валидация по CONTRACT.md §5: ", end="")

    if all_issues:
        print(f"❌ {len(all_issues)} проблем:")
        for pid, issue in all_issues:
            print(f"    [{pid}] {issue}")
        return 1
    else:
        print(f"✅ все {len(inputs)} контрактов валидны")
        return 0


if __name__ == "__main__":
    sys.exit(main())
