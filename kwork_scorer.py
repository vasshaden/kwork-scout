#!/usr/bin/env python3
"""
Симуляция kwork-scorer (День 5) на logs/analyzer_*.json + config/freelancer_profile.yaml.
Имитирует логику, которую Haiku выполнил бы по prompts/scorer.txt.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import yaml

ROOT = Path(__file__).parent
INPUT_GLOB = "analyzer_*.json"
OUTPUT_DIR = ROOT / "logs"
CONFIG = ROOT / "config" / "freelancer_profile.yaml"


def load_yaml():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def load_inputs():
    """Читает ВСЕ analyzer_*.json, merge по project.id с дедуп."""
    files = sorted(OUTPUT_DIR.glob(INPUT_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"no {INPUT_GLOB} in {OUTPUT_DIR}")
    print(f"  Источников: {len(files)} файлов, свежий: {files[0].name}")
    seen = set()
    all_projects = []
    dupes = 0
    file_sources = []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        raw = d.get("projects", [])
        file_sources.append(f)
        for c in raw:
            pid = (c.get("project") or {}).get("id")
            if pid in seen:
                dupes += 1
                continue
            seen.add(pid)
            all_projects.append(c)
    return all_projects, file_sources, dupes


def tech_match(project, analysis):
    return round(analysis["category_skills_match"] * 100)


def budget_fit(project, analysis, cfg):
    hourly = analysis.get("hourly_implied") or 0
    min_hr = cfg["preferences"]["min_hourly_rate"]
    thr = cfg["budget_fit_thresholds"]
    poor = thr["poor_multiplier"]        # 0.5
    acc = thr["acceptable_multiplier"]   # 1.0
    exc = thr["excellent_multiplier"]    # 1.5
    bonus = thr["higher_price_bonus"]    # 1.1

    if min_hr <= 0:
        return 0  # защита от деления на 0

    ratio = hourly / min_hr
    if ratio <= poor:
        base = 0
    elif ratio <= acc:
        base = 70 * (ratio - poor) / (acc - poor)
    elif ratio <= exc:
        base = 70 + 30 * (ratio - acc) / (exc - acc)
    else:
        base = 100

    if project.get("is_higher_price"):
        base = min(100, base * bonus)
    return round(base)


def timeline_fit(project, cfg):
    days = project.get("deadline_days") or 0
    if days == 0:
        return 30
    thr = cfg["timeline_fit_thresholds"]
    if days <= thr["excellent_days"]:
        return 100
    if days <= thr["good_days"]:
        return 80
    if days <= thr["acceptable_days"]:
        return 60
    return 30


def client_quality_score(client, cfg):
    hp = client.get("hired_percent") or 0
    badges_n = len(client.get("badges") or [])
    # v2.0: badges дают больше веса. "Мега Покупатель" и "Более 5 лет с Kwork" = опытный клиент
    # Увеличено: 12 за бейдж (было 10), cap 50 (было 30)
    return round(min(100, hp * 0.7 + min(badges_n * 12, 50)))


def competition_score(project, cfg):
    n = project.get("responses_count")
    if n is None:
        return 50  # нет данных — нейтрально
    thr = cfg["competition_thresholds"]
    if n <= thr["no_competition_max"]:
        return 100
    if n <= thr["low_max"]:
        return 80
    if n <= thr["medium_max"]:
        return 60
    return 40


def make_risks(project, client, analysis, breakdown, cfg):
    risks = []
    # 1. red_flags
    for rf in analysis.get("red_flags") or []:
        risks.append(f"red_flag:{rf}")
    # 2. специфические
    if breakdown["budget_fit"] < 30:
        risks.append(f"низкий hourly ({analysis.get('hourly_implied', 0):.0f}₽/ч < {cfg['preferences']['min_hourly_rate']}₽/ч)")
    if breakdown["timeline_fit"] < 60:
        risks.append("сжатые сроки")
    if breakdown["competition"] < 60:
        n = project.get("responses_count", 0)
        risks.append(f"высокая конкуренция ({n} откликов)")
    if breakdown["client_quality"] < 40:
        risks.append("слабый профиль клиента")
    # 3. missing core
    for m in analysis.get("skills_missing_core") or []:
        risks.append(f"нет core-навыка: {m}")
    # 4. edge cases
    if (project.get("deadline_days") or 0) == 0 and "неопределённый срок" not in risks:
        risks.append("неопределённый срок")
    n_resp = project.get("responses_count") or 0
    if n_resp > 100 and "подозрительная активность" not in risks:
        risks.append("подозрительная активность")
    # dedup, sort
    return sorted(set(risks))


def make_recommendation(verdict, risks, breakdown):
    top = risks[:2] if risks else []
    if verdict == "apply":
        if not risks:
            return "Сильный матч. Готовим КП."
        return f"Сильный матч, но есть риски: {', '.join(top)}. Готовим КП с осторожностью."
    if verdict == "review":
        if not risks:
            return "Средний матч. Стоит ли брать — зависит от загрузки."
        return f"Средний матч, риски: {', '.join(top)}. Ручное решение."
    # skip
    if not risks:
        return "Слабый матч. Пропускаем."
    if not breakdown:
        return f"Отклонён: {top[0]}"
    return f"Слабый матч: {', '.join(top)}. Пропускаем."


def score_project(project, client, analysis, cfg):
    # 1. Rejected → fast path
    if analysis.get("rejection_reason"):
        reason = analysis["rejection_reason"]
        return {
            "score": 0,
            "verdict": "skip",
            "score_breakdown": None,
            "risks": [reason],
            "recommendation": f"Отклонён: {reason}",
        }

    # 2. Components
    breakdown = {
        "tech_match": tech_match(project, analysis),
        "budget_fit": budget_fit(project, analysis, cfg),
        "timeline_fit": timeline_fit(project, cfg),
        "client_quality": client_quality_score(client, cfg),
        "competition": competition_score(project, cfg),
    }

    # 3. Final score
    w = cfg["scoring_weights"]
    score = (
        w["tech_match"] * breakdown["tech_match"]
        + w["budget_fit"] * breakdown["budget_fit"]
        + w["timeline_fit"] * breakdown["timeline_fit"]
        + w["client_quality"] * breakdown["client_quality"]
        + w["competition"] * breakdown["competition"]
    )
    score = round(max(0, min(100, score)))

    # 4. Verdict
    t = cfg["thresholds"]
    if score >= t["auto_apply_score"]:
        verdict = "apply"
    elif score >= t["review_score"]:
        verdict = "review"
    else:
        verdict = "skip"
    if score == 0:
        verdict = "skip"

    # 5. Risks
    risks = make_risks(project, client, analysis, breakdown, cfg)

    # 6. Recommendation
    rec = make_recommendation(verdict, risks, breakdown)

    return {
        "score": score,
        "verdict": verdict,
        "score_breakdown": breakdown,
        "risks": risks,
        "recommendation": rec,
    }


def validate_scoring(contract):
    issues = []
    s = contract.get("scoring")
    if s is None:
        return ["scoring must be dict at scorer stage"]
    if not isinstance(s.get("score"), int) or not (0 <= s["score"] <= 100):
        issues.append(f"scoring.score must be int 0..100, got {s.get('score')}")
    if s.get("verdict") not in ("apply", "review", "skip"):
        issues.append(f"scoring.verdict invalid: {s.get('verdict')}")

    is_rejected = contract.get("analysis", {}).get("rejection_reason") is not None
    bd = s.get("score_breakdown")
    if is_rejected:
        if bd is not None:
            issues.append("scoring.score_breakdown must be null for rejected")
    else:
        if not isinstance(bd, dict):
            issues.append("scoring.score_breakdown must be dict for non-rejected")
        else:
            for comp in ("tech_match", "budget_fit", "timeline_fit", "client_quality", "competition"):
                v = bd.get(comp)
                if not isinstance(v, int) or not (0 <= v <= 100):
                    issues.append(f"scoring.score_breakdown.{comp} must be int 0..100, got {v}")
    if not isinstance(s.get("risks"), list):
        issues.append("scoring.risks must be list")
    if not isinstance(s.get("recommendation"), str):
        issues.append("scoring.recommendation must be string")
    return issues


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    cfg = load_yaml()
    inputs, input_files, dupes = load_inputs()

    # Validate config
    w = cfg["scoring_weights"]
    wsum = sum(w.values())
    if abs(wsum - 1.0) > 0.001:
        print(f"❌ scoring_weights sum = {wsum}, expected 1.0")
        return 1

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out_path = OUTPUT_DIR / f"scorer_{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')}.json"

    print("=" * 90)
    print("DRY RUN: kwork-scorer")
    print(f"  Вход: {len(input_files)} файлов, {len(inputs) + dupes} проектов (после dedup: {len(inputs)}, дублей: {dupes})")
    print(f"  scoring_weights: {w} (sum={wsum})")
    print(f"  thresholds: apply≥{cfg['thresholds']['auto_apply_score']}, review≥{cfg['thresholds']['review_score']}, skip<{cfg['thresholds']['review_score']}")
    print("=" * 90)

    counts = {"apply": 0, "review": 0, "skip": 0}
    non_rejected_scores = []
    all_issues = []

    for contract in inputs:
        project = contract.get("project") or {}
        client = contract.get("client") or {}
        analysis = contract.get("analysis") or {}
        if not analysis:
            print(f"  ⚠️ [{project.get('id')}] no analysis — skipping (analyzer не отработал)")
            continue

        scoring = score_project(project, client, analysis, cfg)
        contract["scoring"] = scoring
        contract["_pipeline_stage"] = "scoring"
        contract["_timestamp"] = timestamp

        # Validate
        issues = validate_scoring(contract)
        if issues:
            all_issues.extend([(project.get("id"), x) for x in issues])

        # Stats
        v = scoring["verdict"]
        counts[v] += 1
        if scoring["score"] > 0:
            non_rejected_scores.append(scoring["score"])

        # Print
        pid = project.get("id")
        title = project.get("title", "")[:42]
        bd = scoring.get("score_breakdown") or {}
        if bd:
            bd_str = (
                f"tech={bd['tech_match']:>3} bud={bd['budget_fit']:>3} "
                f"tl={bd['timeline_fit']:>3} cli={bd['client_quality']:>3} "
                f"comp={bd['competition']:>3}"
            )
        else:
            bd_str = "(rejected)"
        v_emoji = {"apply": "🟢", "review": "🟡", "skip": "🔴"}[v]
        print(f"  {v_emoji} [{pid}] {title:<42} score={scoring['score']:>3} {v:<6} | {bd_str}")
        if scoring["risks"]:
            print(f"      risks: {scoring['risks']}")

    output = {
        "_schema_version": "2.1",
        "_timestamp": timestamp,
        "_dry_run": True,
        "_pipeline_stage": "scoring",
        "_batch": {
            "input_count": len(inputs) + dupes,
            "dedup_count": dupes,
            "apply_count": counts["apply"],
            "review_count": counts["review"],
            "skip_count": counts["skip"],
            "avg_score": round(sum(non_rejected_scores) / len(non_rejected_scores), 1) if non_rejected_scores else 0,
        },
        "projects": inputs,
    }

    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 90)
    print(f"  ИТОГО: apply={counts['apply']}, review={counts['review']}, skip={counts['skip']}, "
          f"avg_score={output['_batch']['avg_score']}")
    print(f"  Артефакт: {out_path.name}")
    print(f"  Валидация по CONTRACT.md §6: ", end="")

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
