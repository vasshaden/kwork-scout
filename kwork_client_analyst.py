#!/usr/bin/env python3
"""
kwork-client-analyst (День 6) на logs/scorer_*.json + config/freelancer_profile.yaml.
Оценивает client.risk для проектов с verdict ∈ {apply, review}.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import yaml

ROOT = Path(__file__).parent
OUTPUT_DIR = ROOT / "logs"
CONFIG = ROOT / "config" / "freelancer_profile.yaml"
HISTORY = ROOT / "data" / "history.json"


def load_scorer_data():
    """Загружает и merge ВСЕ scorer_*.json из OUTPUT_DIR по project.id."""
    files = sorted(OUTPUT_DIR.glob("scorer_*.json"))
    if not files:
        raise FileNotFoundError(
            f"нет scorer_*.json в {OUTPUT_DIR}. Сначала запусти: python3 kwork_scorer.py"
        )
    # Собираем все проекты из всех файлов, дедуп по id
    seen = set()
    projects = []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        for c in d.get("projects", []):
            pid = (c.get("project") or {}).get("id")
            if pid not in seen:
                seen.add(pid)
                projects.append(c)
    return {"projects": projects}


def load_yaml():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def load_history_nicks():
    if not HISTORY.exists():
        return set()
    try:
        d = json.loads(HISTORY.read_text(encoding="utf-8"))
        return {p.get("nickname") for p in d.get("processed", []) if p.get("nickname")}
    except (json.JSONDecodeError, KeyError, AttributeError):
        return set()


def evaluate_risk(client, cfg, history_nicks):
    """Возвращает (risk, risk_source, red_flags)."""
    hp = client.get("hired_percent") or 0
    badges = client.get("badges") or []
    projects_n = client.get("completed_projects") or 0
    nick = client.get("nickname")
    thr = cfg["client_quality_thresholds"]
    low = thr.get("low_if", {})
    high = thr.get("high_if", {})

    # 1. Edge case: insufficient data
    if hp == 0 and not badges and projects_n == 0:
        return ("unknown", "inferred_from_badges", ["no_data"])

    # 2. low (ВСЕ условия)
    is_low = (
        hp >= low.get("hired_percent_min", 70)
        and any(b in badges for b in low.get("has_badge_any_of", []))
    )

    # 3. high (ЛЮБОЕ условие)
    is_high = (
        hp < high.get("hired_percent_below", 30)
        or projects_n < high.get("completed_projects_below", 3)
    )

    if is_low and not is_high:
        return ("low", "analyst", [])
    if is_high and not is_low:
        # Собрать red_flags
        red_flags = []
        if hp < high.get("hired_percent_below", 30):
            red_flags.append(f"low_hired_percent_{hp}%")
        if projects_n < high.get("completed_projects_below", 3):
            red_flags.append(f"few_completed_projects_{projects_n}")
        if not badges:
            red_flags.append("no_badges")
        if nick in history_nicks:
            red_flags.append("client_in_history")
        return ("high", "analyst", red_flags)

    # 4. medium (default)
    # Если часть условий сработала, но не все — например, is_high=True, но is_low=True (конфликт, невозможен
    # при hp>=70 И hp<30, но возможен при completed_projects<3 И hp>=70+badge → low wins)
    # На практике medium = оба False
    return ("medium", "analyst", [])


def validate_client_update(contract):
    """Валидация по CONTRACT.md §9 — 3 поля."""
    issues = []
    c = contract.get("client") or {}
    risk = c.get("risk")
    if risk not in ("low", "medium", "high", "unknown"):
        issues.append(f"client.risk invalid: {risk} (expected low|medium|high|unknown)")
    rs = c.get("risk_source")
    if rs not in (None, "analyst", "inferred_from_badges"):
        issues.append(f"client.risk_source invalid: {rs}")
    rf = c.get("red_flags")
    if not isinstance(rf, list):
        issues.append("client.red_flags must be list")
    # Проверим что нетронутые Parser'овские поля не изменены
    for must in ("nickname", "user_id", "profile_url", "completed_projects", "hired_percent", "badges", "rating"):
        if must not in c:
            issues.append(f"client.{must} missing (Client Analyst should NOT touch this)")
    return issues


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    cfg = load_yaml()
    src = load_scorer_data()
    history_nicks = load_history_nicks()

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out_path = OUTPUT_DIR / f"client_analyst_{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')}.json"

    print("=" * 80)
    print("DRY RUN: kwork-client-analyst")
    print(f"  Вход: {src['_batch']['input_count']} проектов из scorer_*.json")
    print(f"  История: {len(history_nicks)} ников")
    print(f"  YAML client_quality_thresholds: {cfg['client_quality_thresholds']}")
    print("=" * 80)

    # Defensive dedup по project.id (на случай если scorer-выходов несколько)
    seen = set()
    deduped = []
    dupes = 0
    for c in src["projects"]:
        pid = (c.get("project") or {}).get("id")
        if pid in seen:
            dupes += 1
            continue
        seen.add(pid)
        deduped.append(c)

    if dupes:
        print(f"  ⚠️ Defensive dedup: {dupes} дубликатов удалено")

    # Фильтр: только verdict ∈ {apply, review}
    checked = []
    skipped = []
    for c in deduped:
        verdict = (c.get("scoring") or {}).get("verdict")
        if verdict in ("apply", "review"):
            checked.append(c)
        else:
            skipped.append(c)

    print(f"  После фильтра verdict ∈ {{apply, review}}: {len(checked)} проверяем, {len(skipped)} пропускаем")
    print()

    risk_dist = {"low": 0, "medium": 0, "high": 0, "unknown": 0}
    all_issues = []

    for contract in deduped:  # сохраняем ВСЕ проекты, но заполняем только checked
        scoring = contract.get("scoring") or {}
        if scoring.get("verdict") not in ("apply", "review"):
            continue  # не трогаем
        client = contract.get("client") or {}
        risk, source, red_flags = evaluate_risk(client, cfg, history_nicks)
        client["risk"] = risk
        client["risk_source"] = source
        client["red_flags"] = red_flags
        contract["_pipeline_stage"] = "client_analyst"
        contract["_timestamp"] = timestamp
        risk_dist[risk] += 1

        issues = validate_client_update(contract)
        if issues:
            all_issues.extend([((contract.get("project") or {}).get("id"), x) for x in issues])

        pid = (contract.get("project") or {}).get("id")
        title = (contract.get("project") or {}).get("title", "")[:42]
        emoji = {"low": "🟢", "medium": "🟡", "high": "🔴", "unknown": "⚪"}[risk]
        print(f"  {emoji} [{pid}] {title:<42} | risk={risk:<7} | source={source:<22} | red_flags={red_flags}")

    if not checked:
        print("  (нет проектов для проверки — все verdict=skip)")

    output = {
        "_schema_version": "2.1",
        "_timestamp": timestamp,
        "_dry_run": True,
        "_pipeline_stage": "client_analyst",
        "_batch": {
            "input_count": len(deduped),
            "dedup_count": dupes,
            "checked_count": len(checked),
            "skipped_count": len(skipped),
            "risk_distribution": risk_dist,
        },
        "projects": deduped,  # все, включая skipped — History Keeper заберёт
    }

    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 80)
    print(f"  ИТОГО: checked={len(checked)}, skipped={len(skipped)}")
    print(f"  Risk distribution: {risk_dist}")
    print(f"  Артефакт: {out_path.name}")
    print(f"  Валидация по CONTRACT.md §9: ", end="")

    if all_issues:
        print(f"❌ {len(all_issues)} проблем:")
        for pid, issue in all_issues:
            print(f"    [{pid}] {issue}")
        return 1
    else:
        print(f"✅ все {len(checked)} проверенных контрактов валидны")
        return 0


if __name__ == "__main__":
    sys.exit(main())
