#!/usr/bin/env python3
"""
Симуляция kwork-proposal-writer (День 7) на logs/client_analyst_*.json + config/freelancer_profile.yaml.

Имитирует логику, которую Sonnet выполнил бы по prompts/proposal-writer.txt.
Генерирует детерминированный КП-текст по шаблону из промпта + самокоррекция по 7 правилам.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
import yaml

ROOT = Path(__file__).parent
INPUT_GLOB = "client_analyst_*.json"
OUTPUT_DIR = ROOT / "logs"
PROPOSALS_DIR = ROOT / "proposals"
CONFIG = ROOT / "config" / "freelancer_profile.yaml"


def load_yaml():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def load_inputs():
    files = sorted(OUTPUT_DIR.glob(INPUT_GLOB))
    if not files:
        raise FileNotFoundError(
            f"нет {INPUT_GLOB} в {OUTPUT_DIR}. Сначала: python3 kwork_client_analyst.py"
        )
    raw = []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        raw.extend(d.get("projects", []))
    # Defensive dedup
    seen = set()
    deduped = []
    for c in raw:
        pid = (c.get("project") or {}).get("id")
        if pid in seen:
            continue
        seen.add(pid)
        deduped.append(c)
    return deduped, files


def filter_for_proposal(projects):
    """Фильтр: verdict∈{apply, review} ∧ risk≠high."""
    return [
        c for c in projects
        if (c.get("scoring") or {}).get("verdict") in ("apply", "review")
        and (c.get("client") or {}).get("risk") != "high"
    ]


def make_greeting(nickname, templates):
    """Выбор приветствия: с ником, если есть, иначе без."""
    has_nick = bool(nickname) and nickname != "unknown"
    candidates = [t for t in templates if "{nickname}" in t] if has_nick else [t for t in templates if "{nickname}" not in t]
    if not candidates:
        candidates = templates
    return candidates[0].format(nickname=nickname) if has_nick else candidates[0]


def select_relevant_skills(skills_matched, description, max_n=3):
    """Выбрать max_n наиболее релевантных навыков (по вхождению в description)."""
    if not skills_matched:
        return []
    desc_lower = description.lower()
    scored = []
    for s in skills_matched:
        s_lower = s.lower()
        score = desc_lower.count(s_lower) * 10 + len(s_lower)  # частота + длина
        scored.append((s, score))
    scored.sort(key=lambda x: -x[1])
    return [s for s, _ in scored[:max_n]]


def build_plan_items(description, skills_matched):
    """Строит 3-5 пунктов плана на основе description и skills."""
    items = []
    desc_lower = description.lower()
    if "grpc" in skills_matched or "grpc" in desc_lower:
        items.append("Проектирование proto-схем и gRPC API (с учётом ваших требований к методам и сообщениям)")
    if "postgresql" in skills_matched or "postgres" in desc_lower:
        items.append("Схема PostgreSQL, миграции через миграционный инструмент, оптимизация индексов")
    if "kubernetes" in skills_matched or "k8s" in desc_lower or "kubernetes" in desc_lower:
        items.append("Helm-чарты для деплоя, настройка ingress, конфиги через values.yaml")
    if "prometheus" in skills_matched or "prometheus" in desc_lower:
        items.append("Метрики Prometheus (latency, RPS, error rate) + Grafana-дашборды")
    if "opentelemetry" in skills_matched or "трассировк" in desc_lower or "observ" in desc_lower:
        items.append("OpenTelemetry трейсы для distributed tracing")
    if "docker" in skills_matched or "docker" in desc_lower:
        items.append("Docker-образы, multi-stage builds, сканирование уязвимостей")
    if "ci/cd" in desc_lower or "github actions" in desc_lower:
        items.append("CI/CD через GitHub Actions: линт, тесты, авто-деплой в staging")
    if "юнит-тест" in desc_lower or "тест" in desc_lower:
        items.append("Юнит-тесты (table-driven подход) + интеграционные тесты")
    if "нагрузочн" in desc_lower or "load" in desc_lower:
        items.append("Нагрузочные тесты (k6 / wrk), профилирование bottlenecks")
    # Если skills пустые, делаем generic
    if not items:
        items = [
            "Анализ требований и декомпозиция задачи",
            "Проектирование архитектуры и API",
            "Реализация основной функциональности",
            "Тесты (юнит + интеграционные)",
            "Деплой и документация",
        ]
    return items[:5]


def build_clarification_question(project, skills_matched):
    """Релевантный уточняющий вопрос в зависимости от проекта."""
    desc_lower = project.get("description", "").lower()
    if "kubernetes" in desc_lower or "k8s" in desc_lower:
        return "Уточните, пожалуйста: у вас managed Kubernetes (EKS/GKE) или self-hosted? Это повлияет на helm-чарты и настройку ingress."
    if "grpc" in desc_lower:
        return "Есть ли у вас уже готовые proto-схемы, или проектировать API с нуля?"
    if "postgresql" in desc_lower or "sql" in desc_lower:
        return "Какой у вас примерный объём данных и нагрузка (RPS)? Это поможет выбрать стратегию индексации и пулинга."
    if "ci/cd" in desc_lower:
        return "Подскажите, нужен ли CI/CD с самого начала, или это будет следующим этапом?"
    if "observ" in desc_lower or "трасировк" in desc_lower or "prometheus" in desc_lower:
        return "Какие требования к SLA и SLO? Это определит, какие метрики и алерты в первую очередь настраивать."
    # Generic
    return "Уточните, пожалуйста, приоритеты по функционалу: что важнее всего сделать в первую очередь?"


def generate_proposal(contract, cfg):
    """Сборка КП по правилам из промпта."""
    project = contract.get("project") or {}
    client = contract.get("client") or {}
    analysis = contract.get("analysis") or {}
    scoring = contract.get("scoring") or {}

    nick = client.get("nickname", "")
    description = project.get("description", "")
    title = project.get("title", "")
    skills_matched = analysis.get("skills_matched") or []

    prop_cfg = cfg["proposal"]
    price_strat = prop_cfg.get("price_strategy", "budget_rub")

    # Цена и срок
    budget = project.get("budget_rub", 0) or 0
    ceiling = project.get("budget_ceiling_rub", 0) or 0
    if price_strat == "budget_ceiling_rub" and ceiling > 0:
        price = round(ceiling / 1000) * 1000
    else:
        price = round(budget / 1000) * 1000
    deadline = project.get("deadline_days", 1) or 1
    proposed_deadline = max(1, deadline - prop_cfg.get("deadline_buffer_days", 1))

    # Приветствие
    greeting = make_greeting(nick, prop_cfg.get("greeting_templates", []))

    # Релевантные навыки
    relevant_skills = select_relevant_skills(skills_matched, description, max_n=3)
    skills_str = ", ".join(relevant_skills) if relevant_skills else "совпадает с моим стеком"

    # План
    plan_items = build_plan_items(description, skills_matched)
    plan_text = "\n".join(f"— {item}" for item in plan_items)

    # Уточняющий вопрос
    question = build_clarification_question(project, skills_matched)

    # Сборка
    parts = [
        f"{greeting}\n\n"
        f"Ознакомился с вашим проектом «{title}» — задача понятна, стек ({skills_str}) совпадает с моим опытом.\n",
    ]

    if relevant_skills:
        parts.append(
            f"Имею релевантный опыт с {', '.join(relevant_skills[:3])}: "
            f"делал похожие задачи в продакшне, доводил до стабильной работы под нагрузкой.\n"
        )

    parts.append(
        f"План работы:\n{plan_text}\n\n"
        f"Стоимость: {price:,} ₽. Срок: {proposed_deadline} дней.\n\n"
        f"{question}"
    )

    draft = "\n".join(parts)
    return draft, {
        "price": price,
        "proposed_deadline": proposed_deadline,
        "relevant_skills": relevant_skills,
        "plan_items_count": len(plan_items),
    }


def validate_proposal(draft, cfg, project):
    """7 правил CONTRACT.md §7. Возвращает (valid, issues)."""
    issues = []
    prop_cfg = cfg["proposal"]
    max_len = prop_cfg.get("max_length_chars", 1500)
    min_len = prop_cfg.get("min_length_chars", 400)
    length = len(draft)

    # 1. Длина
    if length > max_len:
        issues.append(f"length {length} > {max_len}")
    if length < min_len:
        issues.append(f"length {length} < {min_len}")

    # 2. Приветствие
    head = draft[:50].lower()
    if not any(g in head for g in ("здравствуйте", "добрый день", "привет")):
        issues.append("no_greeting_in_first_50_chars")

    # 3. Цена
    if not re.search(r"\d+[\s\d]*\s*(₽|руб|RUB)", draft, re.IGNORECASE):
        issues.append("no_price_in_text")

    # 4. Срок
    if not re.search(r"(день|дня|дней|недел|месяц)", draft, re.IGNORECASE):
        issues.append("no_deadline_in_text")

    # 5. Стоп-фразы
    draft_lower = draft.lower()
    for phrase in prop_cfg.get("forbidden_phrases", []):
        if phrase.lower() in draft_lower:
            issues.append(f"forbidden_phrase: '{phrase}'")

    # 6. Вопрос в конце
    last_para = draft.rstrip().split("\n\n")[-1]
    if "?" not in last_para:
        issues.append("no_question_mark_in_last_paragraph")

    # 7. Соответствие description (базовая проверка: ключевые термины из description упоминаются)
    desc_lower = (project.get("description") or "").lower()
    # Берём первые 3 существительных/термина длиной >= 5
    desc_terms = [w for w in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9+#.-]{5,}", desc_lower)][:5]
    missing = [t for t in desc_terms if t.lower() not in draft_lower]
    if len(missing) == len(desc_terms) and desc_terms:
        issues.append("draft_does_not_mention_description_terms")

    return (len(issues) == 0, issues)


def self_correct(draft, issues, cfg, project):
    """Попытка исправить КП (1 итерация). Возвращает (new_draft, fixed_issues)."""
    fixed = []
    new_draft = draft

    for issue in issues:
        if issue.startswith("length ") and "1500" in issue:
            # Слишком длинное → сократим план
            new_draft = re.sub(r"— ([^\n]{60,})\n", "— Кратко: \\1\n", new_draft)
            # Если всё ещё слишком длинное, грубо обрежем
            if len(new_draft) > 1500:
                new_draft = new_draft[:1480] + "..."
            fixed.append(issue)
        elif issue.startswith("length ") and "400" in issue:
            # Слишком короткое → добавим контекста
            new_draft += "\n\nГотов обсудить детали в личной переписке."
            fixed.append(issue)
        elif issue == "no_greeting_in_first_50_chars":
            new_draft = "Здравствуйте!\n\n" + new_draft
            fixed.append(issue)
        elif issue == "no_price_in_text":
            new_draft += "\n\nСтоимость: обсудим в личной переписке."
            fixed.append(issue)
        elif issue == "no_deadline_in_text":
            new_draft += "\n\nСрок: обсудим после уточнения деталей."
            fixed.append(issue)
        elif issue.startswith("forbidden_phrase:"):
            phrase = issue.split("'")[1]
            # Простая замена
            replacement_map = {
                "готово за час": "выполню быстро",
                "сделаю за час": "сделаю в сжатые сроки",
                "за 5 минут": "оперативно",
                "самый лучший": "надёжный",
                "лучший в своем роде": "качественный",
                "гарантия 100%": "гарантирую качество",
                "гарантирую результат": "доведу до результата",
                "сделаю за копейки": "сделаю по адекватной цене",
                "дёшево": "по конкурентной цене",
                "бесплатно": "без дополнительной оплаты",
                "оплата после": "оплата по этапам",
                "оплата по факту": "оплата по факту приёмки",
                "без предоплаты": "с разумной предоплатой",
                "недорого": "по рыночной цене",
                "обращайтесь срочно": "обращайтесь",
                "пишите прямо сейчас": "пишите",
            }
            new_draft = new_draft.replace(phrase, replacement_map.get(phrase, "[скорректировано]"))
            fixed.append(issue)
        elif issue == "no_question_mark_in_last_paragraph":
            new_draft += "\n\nГотов ответить на ваши вопросы?"
            fixed.append(issue)
        else:
            # Не поддаётся автоматическому исправлению
            pass

    return new_draft, fixed


def save_proposal_file(contract, draft, cfg, timestamp_str):
    """Сохраняет proposals/YYYY-MM-DD_kwork-ID.md."""
    PROPOSALS_DIR.mkdir(exist_ok=True)
    project = contract.get("project") or {}
    client = contract.get("client") or {}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fname = f"{today}_kwork-{project['id']}.md"
    fpath = PROPOSALS_DIR / fname

    content = f"""# КП для проекта {project['id']} — {project['title']}

**Заказчик:** {client.get('nickname', 'unknown')} ({client.get('profile_url', '')})
**Проект:** {project.get('url', '')}
**Бюджет заказчика:** {project.get('budget_rub', 0):,.0f}–{project.get('budget_ceiling_rub', 0):,.0f} ₽
**Срок заказчика:** {project.get('deadline_days', 0)} дней

---

{draft}

---

*Сгенерировано автоматически. Kwork Scout v2.1. {timestamp_str}*
"""
    fpath.write_text(content, encoding="utf-8")
    return fpath


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    PROPOSALS_DIR.mkdir(exist_ok=True)
    cfg = load_yaml()
    inputs, input_files = load_inputs()

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out_path = OUTPUT_DIR / f"proposal_writer_{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')}.json"

    print("=" * 80)
    print("DRY RUN: kwork-proposal-writer")
    print(f"  Вход: {len(inputs)} проектов")
    print(f"  Фильтр: verdict∈{{apply, review}} ∧ risk≠high")
    print("=" * 80)

    to_propose = filter_for_proposal(inputs)
    skipped_count = len(inputs) - len(to_propose)
    proposals_generated = 0
    proposals_failed = 0
    saved_files = []
    all_issues = []

    print(f"\n  После фильтра: {len(to_propose)} прошли, {skipped_count} пропущены")
    for c in inputs:
        verdict = (c.get("scoring") or {}).get("verdict")
        risk = (c.get("client") or {}).get("risk")
        pid = (c.get("project") or {}).get("id")
        if (verdict, risk) in [("apply", "low"), ("apply", "medium"), ("apply", "unknown"),
                                ("review", "low"), ("review", "medium"), ("review", "unknown")]:
            continue  # пройдёт
        print(f"    ⏭ [{pid}] verdict={verdict} risk={risk} → пропущен")

    print()
    for contract in to_propose:
        project = contract.get("project") or {}
        client = contract.get("client") or {}
        pid = project.get("id")
        print(f"  📝 [{pid}] {project.get('title', '')[:50]}")

        # Генерация
        draft, ctx = generate_proposal(contract, cfg)
        print(f"      черновик: {len(draft)} символов, {ctx['plan_items_count']} пунктов плана, "
              f"цена={ctx['price']:,}₽, срок={ctx['proposed_deadline']}д, навыки={ctx['relevant_skills']}")

        # Валидация (итерация 1)
        valid, issues = validate_proposal(draft, cfg, project)
        iterations = 1

        if not valid:
            print(f"      ⚠️  итерация 1: {len(issues)} issues")
            for i in issues:
                print(f"          - {i}")
            # Самокоррекция
            draft, fixed = self_correct(draft, issues, cfg, project)
            iterations = 2
            # Валидация (итерация 2)
            valid, issues = validate_proposal(draft, cfg, project)
            if not valid:
                print(f"      ❌ итерация 2: {len(issues)} issues (MAX ITERATIONS REACHED)")
                for i in issues:
                    print(f"          - {i}")
                proposals_failed += 1
            else:
                print(f"      ✅ итерация 2: все issues исправлены")

        if valid:
            proposals_generated += 1

        # Заполняем proposal
        contract["proposal"] = {
            "draft": draft,
            "valid": valid,
            "issues": issues,
            "length_chars": len(draft),
            "iterations": iterations,
        }
        contract["_pipeline_stage"] = "proposal_writer"
        contract["_timestamp"] = timestamp

        # Валидация v2.1 §7
        if not isinstance(contract["proposal"]["draft"], str):
            all_issues.append((pid, "proposal.draft not string"))
        if not isinstance(contract["proposal"]["valid"], bool):
            all_issues.append((pid, "proposal.valid not bool"))
        if not isinstance(contract["proposal"]["issues"], list):
            all_issues.append((pid, "proposal.issues not list"))
        if not isinstance(contract["proposal"]["length_chars"], int):
            all_issues.append((pid, "proposal.length_chars not int"))
        if contract["proposal"]["iterations"] not in (1, 2):
            all_issues.append((pid, "proposal.iterations not 1 or 2"))

        # Сохраняем КП-файл
        if valid or True:  # сохраняем ВСЕГДА, даже если не valid
            fpath = save_proposal_file(contract, draft, cfg, timestamp)
            saved_files.append(fpath.name)
            print(f"      💾 {fpath.name}")

    output = {
        "_schema_version": "2.1",
        "_timestamp": timestamp,
        "_dry_run": True,
        "_pipeline_stage": "proposal_writer",
        "_batch": {
            "input_count": len(inputs),
            "filtered_count": len(to_propose),
            "skipped_count": skipped_count,
            "proposals_generated": proposals_generated,
            "proposals_failed": proposals_failed,
        },
        "projects": inputs,
    }

    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 80)
    print(f"  ИТОГО: input={len(inputs)}, filtered={len(to_propose)}, "
          f"generated={proposals_generated}, failed={proposals_failed}")
    print(f"  КП-файлов: {len(saved_files)} ({', '.join(saved_files) or 'нет'})")
    print(f"  Артефакт JSON: {out_path.name}")
    print(f"  Валидация по CONTRACT.md §7: ", end="")

    if all_issues:
        print(f"❌ {len(all_issues)} проблем:")
        for pid, issue in all_issues:
            print(f"    [{pid}] {issue}")
        return 1
    else:
        print(f"✅ все контракты валидны")
        return 0


if __name__ == "__main__":
    sys.exit(main())
