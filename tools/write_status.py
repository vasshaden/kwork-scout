#!/usr/bin/env python3
"""
Kwork Scout — утилита записи pipeline_status.json для веб-монитора.

Использование:
  # Инициализация нового запуска
  python3 tools/write_status.py init --run-id 2026-06-07_14-30-15 --mode dry_run --categories telegram_bots,microservices,content_creation

  # Пометить шаг как running
  python3 tools/write_status.py start --step "parser(telegram_bots)"

  # Пометить шаг как completed
  python3 tools/write_status.py finish --step "parser(telegram_bots)" --summary "12 projects" --duration 2.1

  # Пометить шаг как failed
  python3 tools/write_status.py fail --step "parser(telegram_bots)" --error "Connection timeout"

  # Обновить aggregated метрики
  python3 tools/write_status.py metrics --total 26 --kept 18 --apply 3 --review 7 --skip 8 --avg-score 52.4

  # Добавить проект в таблицу
  python3 tools/write_status.py project --id 3192454 --title "Telegram-бот" --budget 20000 --category telegram_bots --score 63 --verdict review --risk low

  # Добавить ошибку
  python3 tools/write_status.py error --msg "Rate limit exceeded for scorer"

   # Сохранить копию в logs/runs/
   python3 tools/write_status.py save-run

   # Импортировать все проекты из последнего scorer_*.json (для монитора)
   python3 tools/write_status.py import-projects
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATUS_FILE = PROJECT_ROOT / "logs" / "pipeline_status.json"
RUNS_DIR = PROJECT_ROOT / "logs" / "runs"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read() -> dict:
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "run_id": None,
        "steps": [],
        "aggregated": {},
        "projects": [],
        "errors": [],
        "_dry_run": True,
        "started_at": None,
        "categories": [],
    }


def _write(data: dict):
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATUS_FILE)


def cmd_init(args):
    d = _read()
    d["run_id"] = args.run_id
    d["started_at"] = _now_iso()
    d["_dry_run"] = args.mode != "live"
    d["categories"] = [c.strip() for c in args.categories.split(",")] if args.categories else []
    d["current_step"] = 0
    d["steps"] = []
    d["aggregated"] = {}
    d["projects"] = []
    d["errors"] = []
    d["rate_limits"] = {"retries": 0, "last_retry_agent": None}
    _write(d)
    print(json.dumps({"ok": True, "run_id": d["run_id"]}))


def cmd_start(args):
    d = _read()
    d["steps"].append({
        "name": args.step,
        "status": "running",
        "started": _now_iso(),
        "finished": None,
        "duration": None,
        "output_summary": None,
        "error": None,
    })
    d["current_step"] = len(d["steps"])
    _write(d)
    print(json.dumps({"ok": True, "step": args.step, "status": "running"}))


def cmd_finish(args):
    d = _read()
    for s in reversed(d["steps"]):
        if s["name"] == args.step:
            s["status"] = "completed"
            s["finished"] = _now_iso()
            if args.duration is not None:
                s["duration"] = args.duration
            elif s["started"]:
                try:
                    start = datetime.fromisoformat(s["started"].replace("Z", "+00:00"))
                    s["duration"] = round((datetime.now(timezone.utc) - start).total_seconds(), 1)
                except Exception:
                    pass
            if args.summary:
                s["output_summary"] = args.summary
            break
    _write(d)
    print(json.dumps({"ok": True, "step": args.step, "status": "completed"}))


def cmd_fail(args):
    d = _read()
    for s in reversed(d["steps"]):
        if s["name"] == args.step:
            s["status"] = "failed"
            s["finished"] = _now_iso()
            s["error"] = args.error or "Unknown error"
            if s["started"]:
                try:
                    start = datetime.fromisoformat(s["started"].replace("Z", "+00:00"))
                    s["duration"] = round((datetime.now(timezone.utc) - start).total_seconds(), 1)
                except Exception:
                    pass
            break
    _write(d)
    print(json.dumps({"ok": True, "step": args.step, "status": "failed"}))


def cmd_metrics(args):
    d = _read()
    d["aggregated"] = {
        "projects_total": args.total or 0,
        "projects_kept": args.kept or 0,
        "apply": args.apply or 0,
        "review": args.review or 0,
        "skip": args.skip or 0,
        "avg_score": args.avg_score or 0,
    }
    _write(d)
    print(json.dumps({"ok": True, "aggregated": d["aggregated"]}))


def cmd_project(args):
    d = _read()
    proj = {
        "id": args.id,
        "title": args.title or "",
        "budget": args.budget,
        "category": args.category or "",
        "score": args.score,
        "verdict": args.verdict or "",
        "client_risk": args.risk or "",
        "proposal_path": args.proposal or None,
    }
    # Dedup by id
    d["projects"] = [p for p in d["projects"] if p["id"] != args.id]
    d["projects"].append(proj)
    _write(d)
    print(json.dumps({"ok": True, "project_id": args.id}))


def cmd_error(args):
    d = _read()
    d["errors"].append(args.msg)
    _write(d)
    print(json.dumps({"ok": True, "error": args.msg}))


def cmd_import_projects(args):
    """Читает последний scorer_*.json и proposal_writer_*.json, заливает все проекты в pipeline_status."""
    d = _read()
    logs_dir = PROJECT_ROOT / "logs"

    # 1. Ищем свежайший scorer
    scorer_files = sorted(logs_dir.glob("scorer_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not scorer_files:
        print(json.dumps({"ok": False, "error": "Нет scorer_*.json в logs/"}))
        return
    scorer_data = json.loads(scorer_files[0].read_text(encoding="utf-8"))
    print(json.dumps({"ok": True, "source": scorer_files[0].name, "projects": len(scorer_data.get("projects", []))}))

    # 2a. Ищем client_analyst для обогащения (risk)
    analyst_files = sorted(logs_dir.glob("client_analyst_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    analyst_lookup = {}
    if analyst_files:
        adata = json.loads(analyst_files[0].read_text(encoding="utf-8"))
        for c in adata.get("projects", []):
            pid = (c.get("project") or {}).get("id")
            client = c.get("client") or {}
            if pid:
                analyst_lookup[pid] = {
                    "risk": client.get("risk", ""),
                }

    # 2b. Ищем proposal_writer для обогащения (proposal_path)
    writer_files = sorted(logs_dir.glob("proposal_writer_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    writer_lookup = {}
    if writer_files:
        wdata = json.loads(writer_files[0].read_text(encoding="utf-8"))
        for c in wdata.get("projects", []):
            pid = (c.get("project") or {}).get("id")
            prop = c.get("proposal") or {}
            if pid and prop:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                fname = f"{today}_kwork-{pid}.md"
                fpath = PROJECT_ROOT / "proposals" / fname
                writer_lookup[pid] = {
                    "valid": prop.get("valid", False),
                    "path": str(fpath.relative_to(PROJECT_ROOT)) if fpath.exists() else None,
                }

    # 3. Заливаем проекты
    category_map = {}
    for c in scorer_data.get("projects", []):
        proj = c.get("project") or {}
        scoring = c.get("scoring") or {}
        client = c.get("client") or {}
        analysis = c.get("analysis") or {}

        pid = proj.get("id")
        if not pid:
            continue

        cats = proj.get("categories") or []
        primary_cat = cats[0] if cats else (proj.get("category") or "")

        # budget: пробуем budget_rub, потом budget
        budget = proj.get("budget_rub") or proj.get("budget") or 0
        if isinstance(budget, float):
            budget = int(budget)

        # proposal_path
        proposal_path = None
        if pid in writer_lookup and writer_lookup[pid]["valid"]:
            proposal_path = writer_lookup[pid]["path"]

        # Категория из analysis если есть
        cat_key = analysis.get("best_category") or primary_cat
        category_map[cat_key] = category_map.get(cat_key, 0) + 1

        # risk: сначала из analyst (там верные данные), потом из scorer
        risk = client.get("risk") or ""
        if pid in analyst_lookup and analyst_lookup[pid]["risk"]:
            risk = analyst_lookup[pid]["risk"]

        entry = {
            "id": pid,
            "title": (proj.get("title") or "")[:80],
            "budget": budget if budget else None,
            "category": cat_key,
            "score": scoring.get("score"),
            "verdict": scoring.get("verdict", "skip"),
            "client_risk": risk,
            "proposal_path": proposal_path,
        }
        # Dedup
        d["projects"] = [p for p in d["projects"] if p.get("id") != pid]
        d["projects"].append(entry)

    # 4. Обновляем aggregated если не заполнены
    if not d.get("aggregated") or not d["aggregated"].get("projects_total"):
        batch = scorer_data.get("_batch", {})
        apply_n = sum(1 for p in d["projects"] if p.get("verdict") == "apply")
        review_n = sum(1 for p in d["projects"] if p.get("verdict") == "review")
        skip_n = sum(1 for p in d["projects"] if p.get("verdict") == "skip")
        scores = [p.get("score") for p in d["projects"] if p.get("score") is not None]
        avg = round(sum(scores) / len(scores), 1) if scores else 0
        d["aggregated"] = {
            "projects_total": len(d["projects"]),
            "projects_kept": len(d["projects"]),
            "apply": apply_n,
            "review": review_n,
            "skip": skip_n,
            "avg_score": avg,
        }

    _write(d)
    print(json.dumps({"ok": True, "imported": len(d["projects"]), "aggregated": d["aggregated"]}))


def cmd_save_run(args):
    d = _read()
    if not d.get("run_id"):
        print(json.dumps({"ok": False, "error": "No run_id in status"}))
        return
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_file = RUNS_DIR / f"{d['run_id']}.json"
    run_file.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"ok": True, "saved": str(run_file)}))


def main():
    parser = argparse.ArgumentParser(description="Kwork Scout pipeline status writer")
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    p = sub.add_parser("init")
    p.add_argument("--run-id", required=True)
    p.add_argument("--mode", default="dry_run", choices=["dry_run", "live"])
    p.add_argument("--categories", default="telegram_bots,microservices,content_creation")

    # start
    p = sub.add_parser("start")
    p.add_argument("--step", required=True)

    # finish
    p = sub.add_parser("finish")
    p.add_argument("--step", required=True)
    p.add_argument("--summary", default=None)
    p.add_argument("--duration", type=float, default=None)

    # fail
    p = sub.add_parser("fail")
    p.add_argument("--step", required=True)
    p.add_argument("--error", default=None)

    # metrics
    p = sub.add_parser("metrics")
    p.add_argument("--total", type=int, default=None)
    p.add_argument("--kept", type=int, default=None)
    p.add_argument("--apply", type=int, default=None)
    p.add_argument("--review", type=int, default=None)
    p.add_argument("--skip", type=int, default=None)
    p.add_argument("--avg-score", type=float, default=None)

    # project
    p = sub.add_parser("project")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--title", default=None)
    p.add_argument("--budget", type=int, default=None)
    p.add_argument("--category", default=None)
    p.add_argument("--score", type=int, default=None)
    p.add_argument("--verdict", default=None)
    p.add_argument("--risk", default=None)
    p.add_argument("--proposal", default=None)

    # error
    p = sub.add_parser("error")
    p.add_argument("--msg", required=True)

    # save-run
    sub.add_parser("save-run")

    # import-projects
    sub.add_parser("import-projects")

    args = parser.parse_args()
    cmds = {
        "init": cmd_init, "start": cmd_start, "finish": cmd_finish,
        "fail": cmd_fail, "metrics": cmd_metrics, "project": cmd_project,
        "error": cmd_error, "save-run": cmd_save_run,
        "import-projects": cmd_import_projects,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
