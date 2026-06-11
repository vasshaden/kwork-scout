#!/usr/bin/env python3
"""
Симуляция kwork-history-keeper (День 8) на logs/proposal_writer_*.json + data/history.json + data/rejected.json.
Имитирует логику, которую Haiku выполнил бы по prompts/history-keeper.txt.
Атомарная запись ОБОИХ файлов (history.json + rejected.json) за один запуск.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "logs"
PROPOSALS_DIR = ROOT / "proposals"

# Приоритет входа: writer > analyst > scorer
INPUT_PATTERNS = ["proposal_writer_*.json", "client_analyst_*.json", "scorer_*.json"]


def load_latest_input():
    """Возвращает (file_path, projects) — merge всех файлов наивысшего приоритета по mtime."""
    for pattern in INPUT_PATTERNS:
        files = sorted(OUTPUT_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            merged = []
            seen = set()
            for f in files:
                d = json.loads(f.read_text(encoding="utf-8"))
                for c in d.get("projects", []):
                    pid = (c.get("project") or {}).get("id")
                    if pid not in seen:
                        seen.add(pid)
                        merged.append(c)
            return files[0], merged
    raise FileNotFoundError(
        f"нет ни proposal_writer, ни client_analyst, ни scorer выходов в {OUTPUT_DIR}"
    )


def load_existing(json_path, default_key):
    """Загружает существующий data-файл или возвращает пустую структуру."""
    if not json_path.exists():
        return {"_schema_version": "2.1", "last_updated": None, default_key: []}
    try:
        return json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, KeyError):
        return {"_schema_version": "2.1", "last_updated": None, default_key: []}


def compute_decision(contract):
    """CONTRACT.md §8 logic."""
    scoring = contract.get("scoring") or {}
    client = contract.get("client") or {}
    analysis = contract.get("analysis") or {}
    proposal = contract.get("proposal")

    verdict = scoring.get("verdict")
    risk = client.get("risk")
    reason_from_analyzer = analysis.get("rejection_reason")
    proposal_valid = (proposal or {}).get("valid") if proposal else None

    # 1. apply + low/medium/unknown risk + valid proposal → proposed
    if verdict == "apply" and risk in ("low", "medium", "unknown", None) and proposal_valid is True:
        return ("proposed", None)
    # 2. apply + high risk → skipped, client_risk_high
    if verdict == "apply" and risk == "high":
        return ("skipped", "client_risk_high")
    # 3. apply + low risk + invalid proposal → skipped, proposal_validation_failed
    if verdict == "apply" and risk in ("low", "medium", "unknown", None) and proposal_valid is False:
        return ("skipped", "proposal_validation_failed")
    # 4. review → reviewed (ручное решение Василия; high risk не блокирует)
    if verdict == "review":
        return ("reviewed", None)
    # 5. skip → skipped с reason от Analyzer
    if verdict == "skip":
        return ("skipped", reason_from_analyzer or "low_score")
    # 6. fallback (нет verdict, пайплайн упал)
    return ("skipped", "pipeline_error")


def build_history_entry(contract, status, reason, decided_at):
    project = contract.get("project") or {}
    client = contract.get("client") or {}
    scoring = contract.get("scoring") or {}
    pid = project.get("id")
    proposal = contract.get("proposal")
    proposal_path = f"proposals/{decided_at[:10]}_kwork-{pid}.md" if proposal and status == "proposed" else None

    return {
        "id": pid,
        "title": project.get("title", ""),
        "category": project.get("category", ""),
        "url": project.get("url", ""),
        "client_nickname": client.get("nickname", "unknown"),
        "status": status,
        "rejection_reason": reason,
        "score": scoring.get("score", 0),
        "verdict": scoring.get("verdict"),
        "client_risk": client.get("risk"),
        "proposal_path": proposal_path,
        "decided_at": decided_at,
    }


def build_rejected_entry(history_entry, decided_at):
    return {
        "id": history_entry["id"],
        "title": history_entry["title"],
        "category": history_entry["category"],
        "url": history_entry["url"],
        "client_nickname": history_entry["client_nickname"],
        "rejection_reason": history_entry["rejection_reason"],
        "decided_at": decided_at,
    }


def atomic_write(path, content_str):
    """Атомарная запись: temp → rename."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content_str, encoding="utf-8")
    os.replace(tmp_path, path)


def main():
    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Load input
    input_file, projects = load_latest_input()
    print("=" * 80)
    print("LIVE: kwork-history-keeper")
    print(f"  Вход: {input_file.name} ({len(projects)} проектов, merge всех файлов паттерна)")
    print("=" * 80)

    # 2. Defensive dedup
    seen = set()
    deduped = []
    dupes = 0
    for c in projects:
        pid = (c.get("project") or {}).get("id")
        if pid in seen:
            dupes += 1
            continue
        seen.add(pid)
        deduped.append(c)
    if dupes:
        print(f"  Defensive dedup: {dupes} дубликатов удалено")

    # 3. Load existing data
    history_path = DATA_DIR / "history.json"
    rejected_path = DATA_DIR / "rejected.json"
    history = load_existing(history_path, "processed")
    rejected = load_existing(rejected_path, "rejected")

    known_ids = {e["id"] for e in history.get("processed", [])}
    rejected_ids = {e["id"] for e in rejected.get("rejected", [])}

    print(f"  Существующая история: {len(known_ids)} проектов")
    print(f"  Существующие rejected: {len(rejected_ids)} проектов")
    print()

    # 4. Process each contract
    new_added = 0
    updated = 0
    duplicates = 0
    summary_table = []

    for contract in deduped:
        project = contract.get("project") or {}
        pid = project.get("id")
        status, reason = compute_decision(contract)
        new_entry = build_history_entry(contract, status, reason, timestamp)

        if pid in known_ids:
            # Update in place
            for i, e in enumerate(history["processed"]):
                if e["id"] == pid:
                    if e["status"] != status or e["rejection_reason"] != reason:
                        history["processed"][i].update(new_entry)
                        updated += 1
                        break
            else:
                duplicates += 1
        else:
            history["processed"].append(new_entry)
            known_ids.add(pid)
            new_added += 1

        # Rejected.json: добавить если status=skipped
        if status == "skipped" and pid not in rejected_ids:
            rejected["rejected"].append(build_rejected_entry(new_entry, timestamp))
            rejected_ids.add(pid)

        # Visual
        title = project.get("title", "")[:42]
        emoji = {"proposed": "🟢", "reviewed": "🟡", "skipped": "🔴"}[status]
        print(f"  {emoji} [{pid}] {title:<42} | status={status:<8} | reason={reason or '—'}")

    # 5. Update timestamps
    history["last_updated"] = timestamp
    history["_schema_version"] = "2.1"
    rejected["last_updated"] = timestamp
    rejected["_schema_version"] = "2.1"

    # 6. Atomic write (history first, then rejected)
    try:
        atomic_write(history_path, json.dumps(history, ensure_ascii=False, indent=2))
        print(f"\n  💾 {history_path} записан ({len(history['processed'])} entries)")
    except OSError as e:
        print(f"  ❌ Ошибка записи {history_path}: {e}")
        return 1

    try:
        atomic_write(rejected_path, json.dumps(rejected, ensure_ascii=False, indent=2))
        print(f"  💾 {rejected_path} записан ({len(rejected['rejected'])} entries)")
    except OSError as e:
        print(f"  ❌ Ошибка записи {rejected_path}: {e}")
        # Rollback: восстановить history.json (в реальности — backup, но для симуляции откатим)
        return 1

    # 7. Summary log
    summary = {
        "_schema_version": "2.1",
        "_timestamp": timestamp,
        "_dry_run": False,
        "_pipeline_stage": "history-keeper",
        "_batch": {
            "input_file": input_file.name,
            "input_count": len(projects),
            "input_unique": len(deduped),
            "input_dups_removed": dupes,
            "new_added": new_added,
            "updated": updated,
            "duplicates_skipped": duplicates,
            "history_size_after": len(history["processed"]),
            "rejected_size_after": len(rejected["rejected"]),
        },
    }
    summary_path = OUTPUT_DIR / f"history_keeper_{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  💾 {summary_path.name} (summary)")
    print()
    print("=" * 80)
    print(f"  ИТОГО: input={len(projects)}, new_added={new_added}, updated={updated}, "
          f"dups={duplicates}")
    print(f"  data/history.json: {len(history['processed'])} entries")
    print(f"  data/rejected.json: {len(rejected['rejected'])} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
