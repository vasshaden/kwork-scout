"""
kwork_live_parse.py — live-парсинг kwork.ru через Playwright (видимый Chrome).

Зачем: простой HTTP-запрос (urllib/curl/webfetch) триггерит isYandexSmartCaptcha=true
на /projects?fc=X, потому что kwork.ru детектирует отсутствие JS-движка.
Real Chromium проходит антибот-проверку.

ПАГИНАЦИЯ (День 10, ч.3):
    kwork.ru — Vue SPA, и простой ?page=N в URL игнорируется (full page load
    возвращает page 1). Работает ТОЛЬКО переход по pagination.next_page_url из
    stateData (внутрисессионный URL, фильтр fc=X сохраняется в сессии/куках).
    Скрипт собирает ВСЕ страницы последовательно с dedup по project.id.
    Лимит --max-pages (default 10).

ДЕДУП ПО КАТЕГОРИЯМ (День 11, v2):
    kwork.ru возвращает одни и те же проекты в нескольких категориях.
    Режим 'all' использует ОДИН браузер, парсит все 3 категории последовательно,
    дедуплицирует по project.id с сохранением categories: [] (массив всех категорий).
    Выход: один файл logs/parser_live.json со всеми уникальными проектами.

Использование:
    .venv/bin/python tools/kwork_live_parse.py telegram_bots          # одна категория
    .venv/bin/python tools/kwork_live_parse.py microservices
    .venv/bin/python tools/kwork_live_parse.py content_creation
    .venv/bin/python tools/kwork_live_parse.py all                    # все 3, дедуп по ID
    .venv/bin/python tools/kwork_live_parse.py all --output /tmp/foo.json
    .venv/bin/python tools/kwork_live_parse.py all --max-pages 3     # только 3 стр. на кат.
    .venv/bin/python tools/kwork_live_parse.py all --fetch-details   # + полное описание каждого проекта
    .venv/bin/python tools/kwork_live_parse.py all --fetch-details --max-detail-pages 5  # топ-5 проектов

Exit codes:
    0 — успех, projects в JSON
    1 — Ctrl+C пользователем
    2 — cookies.txt не найден
    4 — Playwright не установлен / неизвестная категория
    5 — cookies невалидны (редирект на /login)
    6 — captcha challenge без решения
    7 — window.stateData не найден на странице
    8 — wants[] пустой
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import (
    TimeoutError as PWTimeout,
    sync_playwright,
)

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent.resolve()
COOKIES_FILE = PROJECT_ROOT / "cookies.txt"
LOG_DIR = PROJECT_ROOT / "logs"

CATEGORIES = {
    "telegram_bots": 46,
    "microservices": 5,
    "content_creation": 3,
}

DEFAULT_TIMEOUT = 60  # секунд на загрузку страницы
CAPTCHA_HINT_WAIT = 90  # секунд ждать решения captcha пользователем
MAX_PAGES_DEFAULT = 10  # лимит страниц при пагинации (kwork.ru обычно 1-10)
PAGE_DELAY = 2  # секунд между страницами (anti-bot friendly)
DETAIL_PAGE_DELAY = 3  # секунд между открытием отдельных проектов (anti-bot friendly)
MAX_DETAIL_PAGES_DEFAULT = 10  # макс. проектов для открытия детальной страницы

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def load_cookies_to_context(context, cookies_path: Path) -> int:
    """Загружает cookies из Netscape-файла в Playwright-контекст. Возвращает кол-во."""
    if not cookies_path.exists():
        return -1
    pairs = []
    with open(cookies_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _, path, secure, expires, name, value = parts[:7]
            cookie = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path or "/",
            }
            if secure == "TRUE":
                cookie["secure"] = True
            try:
                exp_int = int(expires) if expires and expires != "0" else -1
            except ValueError:
                exp_int = -1
            if exp_int > 0:
                cookie["expires"] = exp_int
            pairs.append(cookie)
    if pairs:
        context.add_cookies(pairs)
    return len(pairs)


def wants_to_v21(item: dict, categories: list) -> dict:
    """Конвертирует wants[i] в v2.1-контракт с множественными категориями.

    Args:
        item: словарь с данными проекта из kwork.ru
        categories: список категорий (например, ["telegram_bots", "microservices"])
    """
    user = item.get("user") or {}
    user_data = user.get("data") or {}
    badges = [b.get("badge", {}).get("title") for b in user.get("badges", []) if isinstance(b, dict)]
    
    # Основная категория — первая в списке (для совместимости с полем category)
    primary_category = categories[0] if categories else "unknown"
    
    project = {
        "id": int(item.get("id", 0)),
        "category": primary_category,
        "categories": categories,  # НОВОЕ поле: все категории проекта
        "category_id": int(item.get("category_id", 0)),
        "title": item.get("name", ""),
        "url": f"https://kwork.ru/projects/{item.get('id', '')}",
        "description": (item.get("description") or "")[:6000],
        "budget_rub": float(item.get("priceLimit", 0) or 0),
        "budget_ceiling_rub": int(item.get("possiblePriceLimit", 0) or 0),
        "deadline_days": int(item.get("max_days", 0) or 0),
        "published_at": item.get("date_create", ""),
        "time_left": item.get("timeLeft", ""),
        "responses_count": int(item.get("kwork_count", 0) or 0),
        "is_higher_price": bool(item.get("isHigherPrice", False)),
    }
    client = {
        "nickname": user.get("username", "unknown"),
        "user_id": int(user.get("USERID", 0) or 0),
        "profile_url": item.get("wantUserGetProfileUrl", ""),
        "completed_projects": int(user_data.get("wants_count", 0) or 0),
        "hired_percent": int(user_data.get("wants_hired_percent", 0) or 0),
        "badges": badges,
        "risk": None,
        "risk_source": None,
        "red_flags": [],
        "rating": None,
    }
    return {
        "_schema_version": "2.1",
        "_timestamp": datetime.now(timezone.utc).isoformat(),
        "_dry_run": False,
        "_pipeline_stage": "parser",
        "_pipeline_error": None,
        "project": project,
        "client": client,
        "analysis": None,
        "scoring": None,
        "proposal": None,
        "decision": None,
        "metadata": None,
    }


def _fetch_one_page(page, url: str, page_num: int, headless: bool):
    """Переходит на страницу, обрабатывает captcha, извлекает wants+pagination.

    Returns:
        (wants: list, pagination: dict) при успехе
        (None, None) при ошибке (редирект на логин / stateData отсутствует / captcha unsolvable)
    """
    print(f"[INFO] → стр. {page_num}: {url}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT * 1000)
    except PWTimeout:
        print(f"[WARN] Стр. {page_num}: таймаут загрузки {url}", file=sys.stderr)
        return None, None

    # Проверяем, что мы не на странице логина
    current_url = page.url
    if "/login" in current_url or "/signup" in current_url:
        print(f"[ERROR] Редирект на {current_url} — cookies невалидны", file=sys.stderr)
        return None, None

    # Ждём, пока window.stateData появится (10 сек)
    try:
        page.wait_for_function(
            "() => typeof window.stateData !== 'undefined' && window.stateData !== null",
            timeout=10000,
        )
    except PWTimeout:
        pass

    # Извлекаем stateData из window
    state_data = page.evaluate("() => window.stateData")

    if not state_data:
        print(f"[WARN] Стр. {page_num}: window.stateData == null", file=sys.stderr)
        return None, None

    # Проверяем captcha — НО: на 2026-06-07 выяснилось, что kwork.ru ставит
    # isYandexSmartCaptcha=true в stateData ПРЕВЕНТИВНО (всегда), но сам виджет
    # captcha в DOM не отрисовывается при обычном GET-просмотре. Данные доступны.
    # Поэтому: проверяем наличие captcha в DOM, а не в stateData.
    captcha_dom_selectors = [
        "#smartCaptcha",
        ".smart-captcha",
        "[class*='captcha']",
        "iframe[src*='captcha']",
    ]
    if state_data.get("isYandexSmartCaptcha"):
        captcha_in_dom = False
        for sel in captcha_dom_selectors:
            if page.locator(sel).count() > 0:
                captcha_in_dom = True
                break

        if captcha_in_dom:
            # Реальная captcha в DOM — пользователь должен решить
            print(f"\n{'='*60}", flush=True)
            print(f"[CAPTCHA] ⚠️  kwork.ru показал SmartCaptcha в DOM на стр. {page_num}!", flush=True)
            print(f"[CAPTCHA] Посмотрите на окно Chrome и решите её.", flush=True)
            print(f"[CAPTCHA] У вас {CAPTCHA_HINT_WAIT} сек.", flush=True)
            print(f"{'='*60}\n", flush=True)
            if not headless:
                try:
                    # Ждём, пока captcha-элемент исчезнет из DOM
                    for sel in captcha_dom_selectors:
                        try:
                            page.wait_for_selector(sel, state="detached", timeout=CAPTCHA_HINT_WAIT * 1000)
                            break
                        except PWTimeout:
                            continue
                    # Перечитываем state
                    state_data = page.evaluate("() => window.stateData")
                    if not state_data:
                        print(f"[ERROR] stateData исчез после captcha", file=sys.stderr)
                        return None, None
                    print("[OK] Captcha решена, продолжаю")
                except PWTimeout:
                    print(f"[ERROR] Captcha не решена за {CAPTCHA_HINT_WAIT} сек", file=sys.stderr)
                    return None, None
            else:
                return None, None
        elif page_num == 1:
            # captcha-флаг в state, но НЕТ в DOM — это false positive
            print(f"[INFO] isYandexSmartCaptcha=true в state, но в DOM нет — игнорирую")

    wants = state_data.get("wants", [])
    pagination = state_data.get("pagination", {}) or {}
    return wants, pagination


def _fetch_project_detail(page, project_id: int, project_url: str) -> str:
    """Открывает страницу отдельного проекта и извлекает полное описание.

    Args:
        page: Playwright page object
        project_id: ID проекта
        project_url: URL страницы проекта (https://kwork.ru/projects/{id})

    Returns:
        Полное описание (строка) или пустая строка при ошибке.
    """
    try:
        page.goto(project_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT * 1000)
    except PWTimeout:
        print(f"  [WARN] Деталь {project_id}: таймаут загрузки", file=sys.stderr)
        return ""

    # Проверка редиректа на логин
    if "/login" in page.url or "/signup" in page.url:
        print(f"  [WARN] Деталь {project_id}: редирект на логин — cookies невалидны", file=sys.stderr)
        return ""

    # Пробуем получить описание из stateData.want
    try:
        page.wait_for_function(
            "() => typeof window.stateData !== 'undefined' && window.stateData !== null",
            timeout=10000,
        )
    except PWTimeout:
        pass

    state_data = page.evaluate("() => window.stateData")
    if not state_data:
        print(f"  [WARN] Деталь {project_id}: window.stateData == null", file=sys.stderr)
        return ""

    # 1. Приоритет: stateData.wantData.description (полные данные проекта со страницы /view)
    #    Проекты kwork.ru редиректятся с /projects/{id} на /projects/{id}/view,
    #    и stateData содержит wantData (dict) вместо want.
    want_data = state_data.get("wantData") or {}
    if isinstance(want_data, dict):
        full_desc = want_data.get("description") or ""
        if full_desc and len(full_desc) > 20:
            print(f"  [OK] Деталь {project_id}: {len(full_desc)} символов (wantData)")
            return full_desc[:10000]

    # 2. Fallback: stateData.want (используется на listing-странице)
    want = state_data.get("want") or {}
    full_desc = want.get("description") or ""
    if full_desc:
        print(f"  [OK] Деталь {project_id}: {len(full_desc)} символов (want)")
        return full_desc[:10000]

    # 3. Fallback: попробовать из DOM (на случай, если stateData не загрузился)
    dom_selectors = [
        ".description-text",
        ".want-description",
        "[data-want-description]",
        ".kwork-description",
        ".project-description",
        "[class*='description']",
        ".content-text",
        ".text-content",
        ".want__description",
    ]
    for sel in dom_selectors:
        try:
            el = page.locator(sel).first
            if el and el.count() > 0:
                text = el.inner_text()
                if text and len(text) > 50:
                    print(f"  [OK] Деталь {project_id}: {len(text)} символов (DOM: {sel})")
                    return text[:10000]
        except Exception:
            continue

    print(f"  [WARN] Деталь {project_id}: полное описание не найдено", file=sys.stderr)
    return ""


def parse_one_category(p, category: str, fc: int, headless: bool = False,
                       max_pages: int = MAX_PAGES_DEFAULT) -> dict:
    """Парсит одну категорию через Playwright С ПАГИНАЦИЕЙ. Возвращает v2.1 batch.

    Алгоритм:
        1. Прогрев: открыть https://kwork.ru/ + пауза 3 сек.
        2. Стр. 1: fetch → stateData.pagination → last_page, total.
        3. Стр. 2..min(last_page, max_pages): fetch → merge с dedup по project.id.
        4. Anti-bot пауза PAGE_DELAY между страницами.
        5. Фильтр active + конверсия в v2.1.
    """
    base_url = f"https://kwork.ru/projects?fc={fc}"
    print(f"\n[INFO] === {category} (fc={fc}) ===")
    print(f"[INFO] URL: {base_url}")
    print(f"[INFO] Пагинация: до {max_pages} страниц")

    browser = p.chromium.launch(
        headless=headless,
        slow_mo=50,
        args=["--disable-blink-features=AutomationControlled"],
    )
    try:
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="ru-RU",
            user_agent=USER_AGENT,
        )
        n_cookies = load_cookies_to_context(context, COOKIES_FILE)
        if n_cookies < 0:
            return {"_batch": {"category": category, "count": 0, "error": "cookies_not_found"},
                    "projects": []}
        print(f"[INFO] Загружено {n_cookies} cookies из {COOKIES_FILE.name}")

        page = context.new_page()
        # Прогрев
        print(f"[INFO] Прогрев: открываю https://kwork.ru/ чтобы сессия ожила...")
        try:
            page.goto("https://kwork.ru/", wait_until="domcontentloaded", timeout=30000)
        except PWTimeout:
            pass
        print(f"[INFO] Пауза 3 сек — посмотрите на окно Chrome...")
        time.sleep(3)

        # === Цикл по страницам ===
        # ВАЖНО (День 10, ч.3): kwork.ru — Vue SPA. URL `&page=N` игнорируется
        # (возвращает те же проекты что page 1). Работает ТОЛЬКО переход по
        # `pagination.next_page_url` из stateData (внутрисессионный URL ?page=N,
        # фильтр fc=X сохраняется в сессии/куках). Тест подтвердил 2026-06-07.
        all_wants = []
        seen_ids = set()
        pagination_first = {}
        pages_fetched = 0
        fatal_error = None
        next_page_url = None  # инициализируется после page 1

        for page_num in range(1, max_pages + 1):
            if page_num == 1:
                url = base_url
            else:
                if not next_page_url:
                    print(f"[INFO] next_page_url отсутствует — пагинация закончена на стр. {page_num - 1}")
                    break
                # next_page_url приходит как http://, приводим к https:// для Playwright
                url = next_page_url.replace("http://", "https://", 1)

            wants, pagination = _fetch_one_page(page, url, page_num, headless=headless)
            pages_fetched += 1

            if wants is None:
                if page_num == 1:
                    # Критическая ошибка — page 1 не загрузилась
                    fatal_error = "fetch_failed"
                    return {"_batch": {"category": category, "count": 0, "error": fatal_error},
                            "projects": []}
                else:
                    # Последующие страницы: останавливаем пагинацию, не критично
                    print(f"[WARN] Стр. {page_num}: ошибка, останавливаю пагинацию (есть {len(all_wants)} проектов)")
                    break

            # Merge с dedup по id
            new_count = 0
            for w in wants:
                wid = w.get("id")
                if wid is not None and wid not in seen_ids:
                    seen_ids.add(wid)
                    all_wants.append(w)
                    new_count += 1

            if page_num == 1:
                pagination_first = pagination or {}
                total_pages = pagination.get("last_page", 1) if pagination else 1
                total_in_cat = pagination.get("total", "?") if pagination else "?"
                per_page = pagination.get("per_page", "?") if pagination else "?"
                print(f"[INFO] Page 1/{total_pages}: {len(wants)} fetched, {new_count} new "
                      f"(category total={total_in_cat}, per_page={per_page})")
            else:
                print(f"[INFO] Page {page_num}/{total_pages}: {len(wants)} fetched, "
                      f"{new_count} new (unique total: {len(all_wants)})")

            # Получить next URL для следующей итерации
            next_page_url = (pagination or {}).get("next_page_url") if pagination else None

            # Стоп-условия
            if pagination:
                if pagination.get("current_page", page_num) >= pagination.get("last_page", 1):
                    break
                if not next_page_url:
                    break

            # Anti-bot пауза между страницами (перед следующей)
            if page_num < max_pages and next_page_url:
                time.sleep(PAGE_DELAY)

        if not all_wants:
            print(f"[INFO] За все страницы получено 0 проектов")
            return {
                "_schema_version": "2.1",
                "_timestamp": datetime.now(timezone.utc).isoformat(),
                "_dry_run": False,
                "_pipeline_stage": "parser",
                "_batch": {
                    "category": category, "count": 0, "error": None,
                    "pagination": {"pages_fetched": pages_fetched,
                                   "total_pages": pagination_first.get("last_page"),
                                   "total_in_category": pagination_first.get("total"),
                                   "per_page": pagination_first.get("per_page")},
                },
                "projects": [],
            }

        # Фильтр активных
        active = [w for w in all_wants
                  if w.get("isWantActive", True) and w.get("status", "active") == "active"]
        skipped = len(all_wants) - len(active)
        print(f"[INFO] ИТОГО: {len(all_wants)} уникальных, {len(active)} активных, "
              f"{skipped} non-active/duplicate")

        projects = [wants_to_v21(w, [category]) for w in active]

        return {
            "_schema_version": "2.1",
            "_timestamp": datetime.now(timezone.utc).isoformat(),
            "_dry_run": False,
            "_pipeline_stage": "parser",
            "_batch": {
                "category": category,
                "count": len(projects),
                "error": None,
                "pagination": {
                    "pages_fetched": pages_fetched,
                    "total_pages": pagination_first.get("last_page"),
                    "total_in_category": pagination_first.get("total"),
                    "per_page": pagination_first.get("per_page"),
                },
            },
            "projects": projects,
        }
    finally:
        try:
            browser.close()
        except Exception:
            pass


def parse_all_categories(p, headless: bool = False,
                         max_pages: int = MAX_PAGES_DEFAULT,
                         fetch_details: bool = False,
                         max_detail_pages: int = MAX_DETAIL_PAGES_DEFAULT) -> dict:
    """Парсит ВСЕ категории в одном браузере, дедуплицирует по ID с categories: [].

    Алгоритм:
        1. Один браузер, один контекст, одна сессия cookies.
        2. Прогрев: https://kwork.ru/ + пауза 3 сек.
        3. Для каждой категории (telegram_bots → microservices → content_creation):
           - Парсит все страницы (с пагинацией)
           - Собирает raw wants с указанием категории
        4. Глобальный дедуп по project.id:
           - Если проект уже_seen — добавляем только новую категорию в categories[]
           - Если проект новый — создаём с categories: [текущая_категория]
        5. Фильтр active + конверсия в v2.1 с categories: [].
        6. Выход: один файл logs/parser_live.json со всеми уникальными проектами.
    """
    print(f"\n{'='*60}")
    print(f"[INFO] === ПОЛНЫЙ ПАРСИНГ ВСЕХ КАТЕГОРИЙ ===")
    print(f"[INFO] Категории: {list(CATEGORIES.keys())}")
    print(f"[INFO] Пагинация: до {max_pages} страниц на категорию")
    print(f"{'='*60}")

    browser = p.chromium.launch(
        headless=headless,
        slow_mo=50,
        args=["--disable-blink-features=AutomationControlled"],
    )
    try:
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="ru-RU",
            user_agent=USER_AGENT,
        )
        n_cookies = load_cookies_to_context(context, COOKIES_FILE)
        if n_cookies < 0:
            return {"_batch": {"category": "all", "count": 0, "error": "cookies_not_found"},
                    "projects": []}
        print(f"[INFO] Загружено {n_cookies} cookies из {COOKIES_FILE.name}")

        page = context.new_page()
        # Прогрев
        print(f"[INFO] Прогрев: открываю https://kwork.ru/ чтобы сессия ожила...")
        try:
            page.goto("https://kwork.ru/", wait_until="domcontentloaded", timeout=30000)
        except PWTimeout:
            pass
        print(f"[INFO] Пауза 3 сек — посмотрите на окно Chrome...")
        time.sleep(3)

        # === Глобальный дедуп: {project_id: {"item": want, "categories": [str]}} ===
        global_dedup = {}
        category_stats = {}

        for cat_name, fc in CATEGORIES.items():
            print(f"\n[INFO] === {cat_name} (fc={fc}) ===")
            base_url = f"https://kwork.ru/projects?fc={fc}"

            all_wants = []
            seen_ids = set()
            pagination_first = {}
            pages_fetched = 0
            next_page_url = None

            for page_num in range(1, max_pages + 1):
                if page_num == 1:
                    url = base_url
                else:
                    if not next_page_url:
                        print(f"[INFO] next_page_url отсутствует — пагинация закончена на стр. {page_num - 1}")
                        break
                    url = next_page_url.replace("http://", "https://", 1)

                wants, pagination = _fetch_one_page(page, url, page_num, headless=headless)
                pages_fetched += 1

                if wants is None:
                    if page_num == 1:
                        print(f"[ERROR] Стр. 1 не загрузилась для {cat_name}, пропускаю категорию")
                        break
                    else:
                        print(f"[WARN] Стр. {page_num}: ошибка, останавливаю пагинацию")
                        break

                # Merge с dedup по id (внутри категории)
                new_count = 0
                for w in wants:
                    wid = w.get("id")
                    if wid is not None and wid not in seen_ids:
                        seen_ids.add(wid)
                        all_wants.append(w)
                        new_count += 1

                if page_num == 1:
                    pagination_first = pagination or {}
                    total_pages = pagination.get("last_page", 1) if pagination else 1
                    total_in_cat = pagination.get("total", "?") if pagination else "?"
                    per_page = pagination.get("per_page", "?") if pagination else "?"
                    print(f"[INFO] Page 1/{total_pages}: {len(wants)} fetched, {new_count} new "
                          f"(category total={total_in_cat}, per_page={per_page})")
                else:
                    print(f"[INFO] Page {page_num}/{total_pages}: {len(wants)} fetched, "
                          f"{new_count} new (unique total: {len(all_wants)})")

                next_page_url = (pagination or {}).get("next_page_url") if pagination else None

                if pagination:
                    if pagination.get("current_page", page_num) >= pagination.get("last_page", 1):
                        break
                    if not next_page_url:
                        break

                if page_num < max_pages and next_page_url:
                    time.sleep(PAGE_DELAY)

            # Фильтр активных
            active = [w for w in all_wants
                      if w.get("isWantActive", True) and w.get("status", "active") == "active"]
            skipped = len(all_wants) - len(active)
            print(f"[INFO] {cat_name}: {len(all_wants)} уникальных, {len(active)} активных, "
                  f"{skipped} non-active")

            # Глобальный дедуп: добавляем/обновляем categories
            new_in_category = 0
            existing_updated = 0
            for w in active:
                wid = w.get("id")
                if wid is None:
                    continue
                if wid in global_dedup:
                    # Проект уже есть — добавляем категорию (без дублей)
                    if cat_name not in global_dedup[wid]["categories"]:
                        global_dedup[wid]["categories"].append(cat_name)
                    existing_updated += 1
                else:
                    # Новый проект
                    global_dedup[wid] = {"item": w, "categories": [cat_name]}
                    new_in_category += 1

            category_stats[cat_name] = {
                "fetched": len(all_wants),
                "active": len(active),
                "new_unique": new_in_category,
                "existing_updated": existing_updated,
                "pages_fetched": pages_fetched,
            }
            print(f"[INFO] {cat_name}: +{new_in_category} новых, "
                  f"{existing_updated} уже были (обновлены категории)")

            # Пауза между категориями
            if cat_name != list(CATEGORIES.keys())[-1]:
                print(f"[INFO] Пауза 5 сек перед следующей категорией...")
                time.sleep(5)

        # === ИТОГО: конвертация в v2.1 ===
        if not global_dedup:
            print(f"[INFO] Ни одного уникального проекта не найдено")
            return {
                "_schema_version": "2.1",
                "_timestamp": datetime.now(timezone.utc).isoformat(),
                "_dry_run": False,
                "_pipeline_stage": "parser",
                "_batch": {
                    "category": "all",
                    "count": 0,
                    "error": None,
                    "categories_stats": category_stats,
                },
                "projects": [],
            }

        # Конвертируем в v2.1 с categories: []
        projects = []
        for wid, data in global_dedup.items():
            v21 = wants_to_v21(data["item"], data["categories"])
            projects.append(v21)

        print(f"\n{'='*60}")
        print(f"[INFO] ИТОГО УНИКАЛЬНЫХ ПРОЕКТОВ: {len(projects)}")
        for cat, stats in category_stats.items():
            print(f"  {cat}: fetched={stats['fetched']}, active={stats['active']}, "
                  f"new={stats['new_unique']}, updated={stats['existing_updated']}")
        print(f"{'='*60}")

        # === Детальный парсинг (опционально) ===
        if fetch_details and projects:
            # Сортируем по бюджету — сначала самые дорогие
            sorted_projects = sorted(projects, key=lambda x: -x["project"]["budget_rub"])
            detail_targets = sorted_projects[:max_detail_pages]
            print(f"\n[INFO] === ДЕТАЛЬНЫЙ ПАРСИНГ: {len(detail_targets)} проектов ===")
            print(f"[INFO] Открываю страницы проектов для получения полного описания...")

            details_fetched = 0
            details_failed = 0
            for idx, v21 in enumerate(detail_targets, 1):
                proj = v21["project"]
                pid = proj["id"]
                url = proj["url"]
                print(f"\n[INFO] Деталь {idx}/{len(detail_targets)}: {proj['title'][:50]}...")
                full_desc = _fetch_project_detail(page, pid, url)
                if full_desc:
                    proj["full_description"] = full_desc
                    details_fetched += 1
                else:
                    proj["full_description"] = proj.get("description", "")
                    details_failed += 1

                # Anti-bot пауза между детальными страницами
                if idx < len(detail_targets):
                    print(f"[INFO] Пауза {DETAIL_PAGE_DELAY} сек...")
                    time.sleep(DETAIL_PAGE_DELAY)

            print(f"\n[INFO] Детальный парсинг: {details_fetched} загружено, "
                  f"{details_failed} пропущено")
            # Обновляем batch счётчик с full_description
            projects_with_details = sum(1 for p in projects
                                        if p["project"].get("full_description"))
            print(f"[INFO] Проектов с full_description: {projects_with_details}")

        return {
            "_schema_version": "2.1",
            "_timestamp": datetime.now(timezone.utc).isoformat(),
            "_dry_run": False,
            "_pipeline_stage": "parser",
            "_batch": {
                "category": "all",
                "count": len(projects),
                "error": None,
                "categories_stats": category_stats,
            },
            "projects": projects,
        }
    finally:
        try:
            browser.close()
        except Exception:
            pass


def save_batch(batch: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] Сохранено: {output_path} ({batch['_batch']['count']} проектов)")


def main() -> int:
    parser = argparse.ArgumentParser(description="kwork.ru live-парсинг через Playwright")
    parser.add_argument("category", help=f"Одна из: {list(CATEGORIES.keys())} или 'all'")
    parser.add_argument("--headless", action="store_true",
                        help="Запустить Chromium в headless-режиме (НЕ рекомендуется: captcha-вызов)")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES_DEFAULT, metavar="N",
                        help=f"Максимум страниц при пагинации (default: {MAX_PAGES_DEFAULT})")
    parser.add_argument("--fetch-details", action="store_true",
                        help="Открывать каждый проект для получения полного описания")
    parser.add_argument("--max-detail-pages", type=int, default=MAX_DETAIL_PAGES_DEFAULT, metavar="N",
                        help=f"Максимум проектов для детального парсинга (default: {MAX_DETAIL_PAGES_DEFAULT})")
    parser.add_argument("--output", help="Путь к выходному JSON (default: logs/parser_live_<cat>.json)")
    args = parser.parse_args()

    if not COOKIES_FILE.exists():
        print(f"[ERROR] {COOKIES_FILE} не найден. Запустите tools/kwork_login.py",
              file=sys.stderr)
        return 2

    headless = args.headless
    if headless:
        print("[WARN] Headless-режим: kwork.ru с высокой вероятностью покажет captcha. "
              "Рекомендую без --headless.", file=sys.stderr)

    with sync_playwright() as p:
        if args.category == "all":
            # === НОВЫЙ РЕЖИМ: все категории в одном браузере с глобальным дедупом ===
            try:
                batch = parse_all_categories(p, headless=headless, max_pages=args.max_pages,
                                             fetch_details=args.fetch_details,
                                             max_detail_pages=args.max_detail_pages)
            except KeyboardInterrupt:
                print(f"\n[WARN] Прервано пользователем (Ctrl+C)", file=sys.stderr)
                return 1

            output_path = (
                Path(args.output).resolve() if args.output
                else LOG_DIR / "parser_live.json"
            )
            save_batch(batch, output_path)

            # Краткая сводка
            if batch.get("projects"):
                print(f"\n=== ТОП-5 по бюджету (все категории) ===")
                for proj in sorted(batch["projects"],
                                   key=lambda x: -x["project"]["budget_rub"])[:5]:
                    pj = proj["project"]
                    cl = proj["client"]
                    cats = ", ".join(pj.get("categories", []))
                    print(f"  {pj['id']:>7} | {pj['budget_rub']:>8.0f}₽ | "
                          f"{pj['title'][:45]:<45} | [{cats}] | {cl['nickname']}")
        else:
            # === СТАРЫЙ РЕЖИМ: одна категория ===
            if args.category not in CATEGORIES:
                print(f"[ERROR] Неизвестная категория: {args.category}. "
                      f"Допустимые: {list(CATEGORIES.keys())} или 'all'", file=sys.stderr)
                return 4

            cat = args.category
            fc = CATEGORIES[cat]
            try:
                batch = parse_one_category(p, cat, fc, headless=headless, max_pages=args.max_pages)
            except KeyboardInterrupt:
                print(f"\n[WARN] Прервано пользователем (Ctrl+C)", file=sys.stderr)
                return 1

            output_path = (
                Path(args.output).resolve() if args.output
                else LOG_DIR / f"parser_live_{cat}.json"
            )
            save_batch(batch, output_path)

            # Краткая сводка
            if batch.get("projects"):
                print(f"\n=== ТОП-5 по бюджету ({cat}) ===")
                for proj in sorted(batch["projects"],
                                   key=lambda x: -x["project"]["budget_rub"])[:5]:
                    pj = proj["project"]
                    cl = proj["client"]
                    print(f"  {pj['id']:>7} | {pj['budget_rub']:>8.0f}₽ | "
                          f"{pj['title'][:50]:<50} | {cl['nickname']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
