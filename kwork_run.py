#!/usr/bin/env python3
"""
kwork_run.py — Единая точка входа для Kwork Scout v2.1.

Запускает полный live-пайплайн: check-auth → parser → analyzer → scorer →
client-analyst → proposal-writer → history-keeper → markdown-отчёт.

Использование:
  python3 kwork_run.py                  # Запуск с реальными данными
  python3 kwork_run.py --clear          # + очистить историю перед запуском
  python3 kwork_run.py --max-projects 5 # + ограничить число проектов

После прогона открой http://localhost:8080 — монитор покажет все проекты.
"""
import argparse
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
    subprocess.run(
        [PY, str(ROOT / "tools" / "write_status.py"), "start", "--step", step_name],
        capture_output=True, timeout=10
    )
    ok, stdout, stderr, dur = run(cmd, description, timeout)
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
    """Импортирует проекты в pipeline_status."""
    subprocess.run(
        [PY, str(ROOT / "tools" / "write_status.py"), "import-projects"],
        capture_output=True, timeout=15
    )
    print("    ✅ projects imported to monitor")


def main():
    ap = argparse.ArgumentParser(
        description="Kwork Scout — live-пайплайн",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python3 kwork_run.py              # стандартный запуск\n"
            "  python3 kwork_run.py --clear       # + очистить историю\n"
            "  python3 kwork_run.py --max-projects 5  # + ограничить проекты\n"
        )
    )
    ap.add_argument("--clear", action="store_true",
                    help="Очистить data/history.json + data/rejected.json перед стартом")
    ap.add_argument("--max-projects", type=int, default=0,
                    help="Максимум проектов после парсера")
    args = ap.parse_args()

    started_at = datetime.now(timezone.utc)
    ts = started_at.strftime("%Y-%m-%d_%H-%M-%S")
    run_id = f"live_{ts}"

    print("=" * 60)
    print(f"  Kwork Scout — LIVE RUN")
    print(f"  Run ID: {run_id}")
    print(f"  Max projects: {'∞' if not args.max_projects else args.max_projects}")
    print("=" * 60)

    # ------------------------------------------------------------------
    # PREP
    # ------------------------------------------------------------------
    if args.clear:
        for p in (DATA / "history.json", DATA / "rejected.json"):
            if p.exists():
                p.unlink()
                print(f"  🗑  Удалён {p.name}")
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
         "--mode", "live",
         "--categories", "telegram_bots,microservices,content_creation"],
        capture_output=True, timeout=10
    )
    print("  ✅ Monitor initialised")

    steps_ok = 0
    steps_total = 7

    # ------------------------------------------------------------------
    # STEP 0: CHECK AUTH
    # ------------------------------------------------------------------
    print("\n[0/7] Check auth")
    auth_ok, *_ = step_wrapper(
        "check-auth",
        [PY, str(ROOT / "tools" / "kwork_check_auth.py")],
        "Проверка авторизации на kwork.ru",
        timeout=600
    )
    if not auth_ok:
        print("  ❌ Авторизация не выполнена. Пайплайн остановлен.")
        print("  Запустите python3 tools/kwork_check_auth.py для входа.")
        sys.exit(1)
    steps_ok += 1

    # ------------------------------------------------------------------
    # STEP 1: PARSER
    # ------------------------------------------------------------------
    print("\n[1/7] Parser")
    parser_cmd = [PY, str(ROOT / "tools" / "kwork_live_parse.py"), "all", "--max-pages", "3"]
    if args.max_projects:
        parser_cmd.extend(["--max-projects", str(args.max_projects)])
    ok, *_ = step_wrapper("parser(live)", parser_cmd, "Парсинг kwork.ru")
    if ok:
        steps_ok += 1

    # ------------------------------------------------------------------
    # STEP 2: ANALYZER
    # ------------------------------------------------------------------
    print("\n[2/7] Analyzer")
    ok, *_ = step_wrapper("analyzer",
                           [PY, str(ROOT / "kwork_analyzer.py"), "--skip-history"],
                           "Анализ проектов")
    if ok:
        steps_ok += 1

    # ------------------------------------------------------------------
    # STEP 3: SCORER
    # ------------------------------------------------------------------
    print("\n[3/7] Scorer")
    ok, *_ = step_wrapper("scorer",
                           [PY, str(ROOT / "kwork_scorer.py")],
                           "Скоринг проектов")
    if ok:
        steps_ok += 1

    # ------------------------------------------------------------------
    # STEP 4: CLIENT-ANALYST
    # ------------------------------------------------------------------
    print("\n[4/7] Client Analyst")
    ok, *_ = step_wrapper("client-analyst",
                           [PY, str(ROOT / "kwork_client_analyst.py")],
                           "Оценка рисков заказчиков")
    if ok:
        steps_ok += 1

    # ------------------------------------------------------------------
    # STEP 5: PROPOSAL-WRITER
    # ------------------------------------------------------------------
    print("\n[5/7] Proposal Writer")
    ok, *_ = step_wrapper("proposal-writer",
                           [PY, str(ROOT / "kwork_proposal_writer.py")],
                           "Генерация КП")
    if ok:
        steps_ok += 1

    # ------------------------------------------------------------------
    # STEP 6: HISTORY-KEEPER
    # ------------------------------------------------------------------
    print("\n[6/7] History Keeper")
    ok, *_ = step_wrapper("history-keeper",
                           [PY, str(ROOT / "kwork_history_keeper.py")],
                           "Атомарное сохранение истории")
    if ok:
        steps_ok += 1

    # ------------------------------------------------------------------
    # IMPORT PROJECTS TO MONITOR
    # ------------------------------------------------------------------
    print("\n  → Importing projects to monitor...")
    import_projects_to_monitor()

    # ------------------------------------------------------------------
    # FINALISE
    # ------------------------------------------------------------------
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

    # Сводка
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

    # Markdown-отчёт
    try:
        report_path = REPORTS / f"RUN_{run_id}.md"
        md_lines = [
            f"# Kwork Scout — отчёт от {started_at.strftime('%Y-%m-%d %H:%M')}",
            "",
            f"**Режим:** live",
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