#!/usr/bin/env python3
"""
kwerk_run.py — Единая точка входа для Kwork Scout v2.1.

Запускает полный пайплайн: parser → analyzer → scorer → client-analyst →
proposal-writer → history-keeper → markdown-отчёт.

Использование:
  # Dry-run (тестовый HTML, без интернета)
  python3 kwerk_run.py

  # Live (реальные проекты с kwork.ru)
  python3 kwerk_run.py --live

  # Live + очистить историю
  python3 kwerk_run.py --live --clear

  # Dry-run + не больше 5 проектов
  python3 kwerk_run.py --max-projects 5

После прогона открой http://localhost:8080 — монитор покажет все проекты.
"""
import argparse
import json
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable
LOGS = ROOT / "logs"
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
PROPOSALS = ROOT / "proposals"
TIMEOUT = 120  # секунд на шаг


def run(cmd, description="", timeout=TIMEOUT):
    """Запускает команду, возвращает (ok, stdout, stderr, duration)."""
    t0 = time.perf_counter()
    print(f"  → {description or ' '.join(cmd[:3])}...")
    try:
        r = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout
        )
        dur = round(time.perf_counter() - t0, 1)
        ok = r.returncode == 0
        if ok:
            print(f"    ✅ {dur:.1f}с")
        else:
            print(f"    ❌ exit={r.returncode} ({dur:.1f}с)")
            err = r.stderr[-300:] if r.stderr else r.stdout[-300:]
            print(f"       {err}")
        return ok, r.stdout, r.stderr, dur
    except subprocess.TimeoutExpired:
        dur = round(time.perf_counter() - t0, 1)
        print(f"    ❌ timeout {timeout}s ({dur:.1f}с)")
        return False, "", f"timeout after {timeout}s", dur


def step_wrapper(step_name, cmd, description, timeout=TIMEOUT):
    """Запускает шаг и пишет статус в pipeline_status."""
    # start
    subprocess.run(
        [PY, str(ROOT / "tools" / "write_status.py"), "start", "--step", step_name],
        capture_output=True, timeout=10
    )
    ok, stdout, stderr, dur = run(cmd, description, timeout)
    # finish / fail
    subprocess.run(
        [PY, str(ROOT / "tools" / "write_status.py"),
         "finish" if ok else "fail",
         "--step", step_name,
         "--summary", f"{'OK' if ok else 'FAILED'}",
         "--duration", str(dur)],
        capture_output=True, timeout=10
    )
    return ok, stdout, stderr, dur


def import_projects_to_monitor():
    """Импортирует проекты из последнего scorer в pipeline_status."""
    subprocess.run(
        [PY, str(ROOT / "tools" / "write_status.py"), "import-projects"],
        capture_output=True, timeout=15
    )
    print("    ✅ projects imported to monitor")


def main():
    ap = argparse.ArgumentParser(
        description="Kwork Scout — единый пайплайн",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python3 kwerk_run.py              # dry-run\n"
            "  python3 kwerk_run.py --live        # live-парсинг\n"
            "  python3 kwerk_run.py --live --clear  # live + очистить историю\n"
            "  python3 kwerk_run.py --max-projects 3  # только 3 проекта\n"
        )
    )
    ap.add_argument("--live", action="store_true", help="Live-режим (реальные проекты с kwork.ru)")
    ap.add_argument("--clear", action="store_true", help="Очистить data/history.json + data/rejected.json перед стартом")
    ap.add_argument("--max-projects", type=int, default=0, help="Максимум проектов после парсера")
    args = ap.parse_args()

    started_at = datetime.now(timezone.utc)
    ts = started_at.strftime("%Y-%m-%d_%H-%M-%S")
    run_id = f"{'live' if args.live else 'dry'}_{ts}"

    print("=" * 60)
    print(f"  Kwork Scout — {'LIVE' if args.live else 'DRY'} RUN")
    print(f"  Run ID: {run_id}")
    print(f"  Max projects: {'∞' if not args.max_projects else args.max_projects}")
    print("=" * 60)

    # ------------------------------------------------------------------
    # PREP
    # ------------------------------------------------------------------
    # Очистка data/
    if args.clear:
        for p in (DATA / "history.json", DATA / "rejected.json"):
            if p.exists():
                p.unlink()
                print(f"  🗑  Удалён {p.name}")
        # также удаляем parser_live.json и старые промежуточные логи
        for f in [LOGS / "parser_live.json"]:
            if f.exists():
                f.unlink()
                print(f"  🗑  Удалён {f.name}")
        for pattern in ("analyzer_*.json", "scorer_*.json", "client_analyst_*.json",
                        "proposal_writer_*.json", "history_keeper_*.json", "run_*.json"):
            for f in LOGS.glob(pattern):
                f.unlink()

    # Инициализация pipeline_status
    subprocess.run(
        [PY, str(ROOT / "tools" / "write_status.py"), "init",
         "--run-id", run_id,
         "--mode", "live" if args.live else "dry_run",
         "--categories", "telegram_bots,microservices,content_creation"],
        capture_output=True, timeout=10
    )
    print("  ✅ Monitor initialised")

    steps_ok = 0
    steps_total = 6

    # ------------------------------------------------------------------
    # STEP 1: PARSER
    # ------------------------------------------------------------------
    print("\n[1/6] Parser")
    if args.live:
        parser_cmd = [PY, str(ROOT / "tools" / "kwork_live_parse.py"), "all", "--max-pages", "1"]
        if args.max_projects:
            parser_cmd.extend(["--max-projects", str(args.max_projects)])
        ok, *_ = step_wrapper("parser(live)", parser_cmd, "Парсинг kwork.ru (live)")
    else:
        # Dry-run: читаем tests/kwork-sample.html
        ok, *_ = step_wrapper("parser(dry)", [PY, str(ROOT / "simulate_parser_dry_run.py")], "Парсинг из fixture")
        # Сливаем parser_dry_*.json в parser_live.json (анализатор читает только parser_live.json)
        dry_files = sorted(LOGS.glob("parser_dry_*.json"))
        if dry_files:
            all_projects = []
            for f in dry_files:
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    projects = data.get("projects", [])
                    # Добавляем категорию из имени файла
                    cat = f.stem.replace("parser_dry_", "")
                    for p in projects:
                        if "categories" not in p.get("project", {}):
                            if "project" in p:
                                p["project"]["categories"] = [cat]
                        all_projects.append(p)
                except Exception:
                    pass
            # Дедуп по project.id
            seen = set()
            deduped = []
            for p in all_projects:
                pid = (p.get("project") or {}).get("id")
                if pid and pid not in seen:
                    seen.add(pid)
                    deduped.append(p)
            live = {
                "_schema_version": "2.1",
                "_dry_run": True,
                "_pipeline_stage": "parser",
                "projects": deduped,
            }
            (LOGS / "parser_live.json").write_text(
                json.dumps(live, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"    → parser_live.json создан из {len(dry_files)} dry-файлов ({len(deduped)} проектов)")
    if ok:
        steps_ok += 1

    # ------------------------------------------------------------------
    # STEP 2: ANALYZER
    # ------------------------------------------------------------------
    print("\n[2/6] Analyzer")
    ok, *_ = step_wrapper("analyzer",
                           [PY, str(ROOT / "simulate_analyzer_dry_run.py"), "--skip-history"],
                           "Анализ проектов")
    if ok:
        steps_ok += 1

    # ------------------------------------------------------------------
    # STEP 3: SCORER
    # ------------------------------------------------------------------
    print("\n[3/6] Scorer")
    ok, *_ = step_wrapper("scorer",
                           [PY, str(ROOT / "simulate_scorer_dry_run.py")],
                           "Скоринг проектов")
    if ok:
        steps_ok += 1

    # ------------------------------------------------------------------
    # STEP 4: CLIENT-ANALYST
    # ------------------------------------------------------------------
    print("\n[4/6] Client Analyst")
    ok, *_ = step_wrapper("client-analyst",
                           [PY, str(ROOT / "simulate_client_analyst_dry_run.py")],
                           "Оценка рисков заказчиков")
    if ok:
        steps_ok += 1

    # ------------------------------------------------------------------
    # STEP 5: PROPOSAL-WRITER
    # ------------------------------------------------------------------
    print("\n[5/6] Proposal Writer")
    ok, *_ = step_wrapper("proposal-writer",
                           [PY, str(ROOT / "simulate_proposal_writer_dry_run.py")],
                           "Генерация КП")
    if ok:
        steps_ok += 1

    # ------------------------------------------------------------------
    # STEP 6: HISTORY-KEEPER
    # ------------------------------------------------------------------
    print("\n[6/6] History Keeper")
    ok, *_ = step_wrapper("history-keeper",
                           [PY, str(ROOT / "simulate_history_keeper_dry_run.py")],
                           "Атомарное сохранение истории")
    if ok:
        steps_ok += 1

    # ------------------------------------------------------------------
    # Импортируем проекты в монитор (после всех шагов — есть и risk, и КП)
    # ------------------------------------------------------------------
    print("\n  → Importing projects to monitor...")
    import_projects_to_monitor()

    # ------------------------------------------------------------------
    # FINALISE
    # ------------------------------------------------------------------
    # Сохраняем копию в logs/runs/
    subprocess.run(
        [PY, str(ROOT / "tools" / "write_status.py"), "save-run"],
        capture_output=True, timeout=10
    )

    finished_at = datetime.now(timezone.utc)
    total_dur = round((finished_at - started_at).total_seconds(), 1)

    print("\n" + "=" * 60)
    print(f"  ✅ ПАЙПЛАЙН ЗАВЕРШЁН: {steps_ok}/{steps_total} шагов успешно")
    print(f"  ⏱  {total_dur}с")
    print(f"  📊 Монитор: http://localhost:8080")
    print("=" * 60)

    # Вывод сводки
    print()
    history_path = DATA / "history.json"
    if history_path.exists():
        try:
            h = json.loads(history_path.read_text(encoding="utf-8"))
            entries = h.get("processed", [])
            if entries:
                by_status = {}
                for e in entries:
                    s = e.get("status", "unknown")
                    by_status[s] = by_status.get(s, 0) + 1
                print("  Финальная воронка:")
                for status in ("proposed", "reviewed", "skipped"):
                    cnt = by_status.get(status, 0)
                    icon = {"proposed": "🟢", "reviewed": "🟡", "skipped": "🔴"}.get(status, "⚪")
                    print(f"    {icon} {status}: {cnt}")
                print(f"    Всего: {len(entries)} проектов")
        except Exception:
            pass

    # Генерация markdown-отчёта
    try:
        report_path = REPORTS / f"RUN_{run_id}.md"
        md_lines = [
            f"# Kwork Scout — отчёт от {started_at.strftime('%Y-%m-%d %H:%M')}",
            "",
            f"**Режим:** {'live' if args.live else 'dry_run'}",
            f"**Run ID:** {run_id}",
            f"**Время:** {total_dur}с",
            f"**Шаги:** {steps_ok}/{steps_total}",
            "",
        ]
        if history_path.exists():
            try:
                h = json.loads(history_path.read_text(encoding="utf-8"))
                entries = h.get("processed", [])
                md_lines.append("## Сводка")
                md_lines.append("")
                md_lines.append("| Статус | Кол-во |")
                md_lines.append("|--------|--------|")
                for status in ("proposed", "reviewed", "skipped"):
                    cnt = sum(1 for e in entries if e.get("status") == status)
                    icon = {"proposed": "🟢", "reviewed": "🟡", "skipped": "🔴"}.get(status, "⚪")
                    md_lines.append(f"| {icon} {status} | {cnt} |")
                md_lines.append(f"| **Всего** | **{len(entries)}** |")
                md_lines.append("")

                # Таблица проектов
                md_lines.append("## Проекты")
                md_lines.append("")
                md_lines.append("| ID | Название | Бюджет | Score | Verdict | Risk | Статус |")
                md_lines.append("|----|----------|--------|-------|---------|------|--------|")
                for e in entries:
                    pid = e.get("project_id") or e.get("id", "?")
                    title = (e.get("title") or "—")[:50].replace("|", "\\|")
                    budget_rub = e.get("budget_rub")
                    budget_ceiling = e.get("budget_ceiling_rub")
                    if budget_rub and budget_ceiling:
                        budget_str = f"{int(budget_rub):,}–{int(budget_ceiling):,} ₽".replace(",", " ")
                    elif budget_rub:
                        budget_str = f"{int(budget_rub):,} ₽".replace(",", " ")
                    else:
                        budget_str = "—"
                    score = e.get("score", "—")
                    verdict = e.get("verdict", "—")
                    risk = e.get("client_risk", "—") or "—"
                    status = e.get("status", "—")
                    icon = {"proposed": "🟢", "reviewed": "🟡", "skipped": "🔴"}.get(status, "⚪")
                    md_lines.append(f"| {pid} | {title} | {budget_str} | {score} | {verdict} | {risk} | {icon} {status} |")
                md_lines.append("")
            except Exception:
                pass

        REPORTS.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
        print(f"\n  📄 Отчёт: {report_path.relative_to(ROOT)}")
    except Exception as e:
        print(f"\n  ⚠️  Не удалось создать отчёт: {e}")

    return 0 if steps_ok == steps_total else 1


if __name__ == "__main__":
    sys.exit(main())
