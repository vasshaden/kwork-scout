"""
kwork_login.py — авто-логин на kwork.ru через Playwright.

Запускает видимый Chromium, ждёт ручного ввода логина/пароля,
извлекает cookies и сохраняет их в ./cookies.txt в Netscape-формате.

Использование:
    python3 tools/kwork_login.py                  # login + cookies (default timeout 5 мин)
    python3 tools/kwork_login.py --timeout 600    # таймаут 10 мин
    python3 tools/kwork_login.py --url <URL>      # кастомный URL (default: https://kwork.ru/login)
    python3 tools/kwork_login.py --help           # справка

Зависимости:
    pip install -r requirements.txt
    playwright install chromium   # ~150 МБ, один раз

Безопасность:
    - cookies.txt содержит сессионные куки = фактический пароль
    - chmod 600 выставляется автоматически
    - в .gitignore (не коммитится)

Exit codes:
    0 — успех, cookies сохранены
    1 — Ctrl+C пользователем
    2 — таймаут входа
    3 — нет cookies для kwork.ru (что-то пошло не так)
    4 — Playwright не установлен (pip install -r requirements.txt)
"""
import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

# Импорт Playwright — ленивый, чтобы --help работал без установленного модуля.
# Реальный импорт происходит в main() (после парсинга аргументов).
PW_TIMEOUT_ERROR = None
sync_playwright = None  # type: ignore


def _import_playwright():
    """Импортирует Playwright. Бросает понятную ошибку, если модуль не установлен."""
    global PW_TIMEOUT_ERROR, sync_playwright
    if sync_playwright is not None:
        return
    try:
        from playwright.sync_api import (  # type: ignore
            TimeoutError as _PWTimeout,
            sync_playwright as _sync_playwright,
        )
        PW_TIMEOUT_ERROR = _PWTimeout
        sync_playwright = _sync_playwright
    except ModuleNotFoundError as e:
        print(
            "[ERROR] Модуль 'playwright' не установлен.\n"
            "        Установите:\n"
            "          pip install -r requirements.txt\n"
            "          playwright install chromium\n"
            "        Подробности: tools/README.md",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(4) from e


# Пути
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent.resolve()
COOKIES_FILE = PROJECT_ROOT / "cookies.txt"

# Константы
LOGIN_URL = "https://kwork.ru/login"
DEFAULT_TIMEOUT = 300  # 5 минут
PROFILE_LOCALE = "ru-RU"
VIEWPORT = {"width": 1280, "height": 800}
SLOW_MO_MS = 50  # задержка для наглядности работы скрипта

# Маркеры успешного входа (CSS-селекторы, которые появляются на kwork.ru после авторизации)
# Пробуем несколько, kwork.ru менял вёрстку несколько раз
AUTH_SUCCESS_SELECTORS = [
    '[class*="user-menu"]',
    '[class*="userMenu"]',
    '[data-test*="user"]',
    '[class*="profile"]',
    'a[href*="/user"]',
    '.header__user',
    '[class*="user-avatar"]',
]


def check_display() -> None:
    """Предупреждаем, если нет DISPLAY (только для Linux с X11)."""
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        print(
            "[WARN] Переменная DISPLAY не задана. Скрипт требует GUI.\n"
            "       Запустите на машине с X11/Wayland, либо через `xvfb-run`.",
            file=sys.stderr,
            flush=True,
        )


def wait_for_login(page, timeout_sec: int) -> bool:
    """
    Ждём успешного входа.

    Стратегия: URL сменился с /login на что-то другое, и на странице появился
    маркер авторизованного пользователя (user-menu, аватар и т.п.).
    """
    print(
        f"[INFO] Ждём входа (timeout={timeout_sec} сек).",
        flush=True,
    )
    print(
        "[INFO] Введите логин/пароль (и SMS-код при 2FA) в открывшемся окне Chrome.",
        flush=True,
    )
    print(
        "[INFO] Если появится капча — решите её. После успешного входа скрипт продолжит работу.",
        flush=True,
    )

    # Ждём, что URL перестал содержать /login или /signup
    try:
        page.wait_for_url(
            lambda url: "kwork.ru" in url and "/login" not in url and "/signup" not in url,
            timeout=timeout_sec * 1000,
        )
    except PW_TIMEOUT_ERROR:
        return False

    # Дополнительно: ждём появления одного из маркеров авторизации (5 сек)
    for selector in AUTH_SUCCESS_SELECTORS:
        try:
            page.wait_for_selector(selector, timeout=2000)
            return True
        except PW_TIMEOUT_ERROR:
            continue

    # URL сменился, но маркер не нашли — всё равно считаем вход успешным
    return True


def to_netscape(cookies: list) -> str:
    """
    Конвертирует cookies из формата Playwright (list of dict) в Netscape HTTP Cookie File.

    Netscape-формат (7 tab-separated полей):
        domain  flag  path  secure  expiration  name  value

    Где:
        domain    — домен (с точкой или без)
        flag      — TRUE, если домен начинается с точки (доступен поддоменам)
        path      — путь
        secure    — TRUE, если cookie только для HTTPS
        expiration — Unix timestamp (0 для session cookies)
        name      — имя cookie
        value     — значение
    """
    lines = [
        "# Netscape HTTP Cookie File",
        "# https://curl.haxx.se/rfc/cookie_spec.html",
        "# Generated by kwork_login.py. Do not edit manually.",
        "",
    ]

    for c in cookies:
        domain = c["domain"]
        # Netscape flag: TRUE если домен начинается с точки (поддомены включены)
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure", False) else "FALSE"

        # expires: -1 или None = session cookie. В Netscape это 0.
        expires = c.get("expires", -1)
        if expires is None or expires < 0:
            expires = 0
        # Приводим к int (Playwright иногда возвращает float)
        expires = int(expires)

        name = c["name"]
        value = c["value"]

        lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}")

    return "\n".join(lines) + "\n"


def atomic_write(path: Path, content: str) -> None:
    """
    Атомарная запись: пишем в tmp-файл, потом os.replace, потом chmod 600.
    Исключение: если что-то пошло не так — удаляем tmp-файл.
    """
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".cookies_", dir=str(path.parent))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
        # chmod 600: только владелец может читать/писать
        os.chmod(path, 0o600)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Kwork.ru auto-login через Playwright (видимый браузер).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Таймаут входа в секундах (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--url",
        default=LOGIN_URL,
        help=f"URL логина (default: {LOGIN_URL})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=COOKIES_FILE,
        help=f"Путь к выходному файлу cookies (default: {COOKIES_FILE})",
    )
    args = parser.parse_args()

    output_path: Path = args.output.resolve()
    check_display()
    _import_playwright()  # lazy: только после парсинга аргументов

    if output_path.exists():
        print(
            f"[INFO] {output_path} уже существует. Будет перезаписан после успешного входа.",
            flush=True,
        )

    print(f"[INFO] Запускаю Chromium (headless=False)...", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=SLOW_MO_MS)
        try:
            context = browser.new_context(
                viewport=VIEWPORT,
                locale=PROFILE_LOCALE,
                # user_agent — не задаём явно, используется дефолтный Chromium
            )
            page = context.new_page()
            print(f"[INFO] Открываю {args.url}...", flush=True)
            page.goto(args.url, wait_until="domcontentloaded", timeout=30000)

            if not wait_for_login(page, args.timeout):
                print(
                    f"[ERROR] Таймаут: пользователь не вошёл за {args.timeout} сек.",
                    file=sys.stderr,
                    flush=True,
                )
                return 2

            # Извлекаем cookies из контекста
            print("[INFO] Извлекаю cookies...", flush=True)
            all_cookies = context.cookies()
            kwork_cookies = [c for c in all_cookies if "kwork.ru" in c.get("domain", "")]

            if not kwork_cookies:
                print(
                    "[ERROR] Cookies для kwork.ru не найдены в контексте браузера.\n"
                    "        Проверьте, что логин прошёл успешно (аватар в правом верхнем углу).",
                    file=sys.stderr,
                    flush=True,
                )
                return 3

            # Сохраняем
            content = to_netscape(kwork_cookies)
            atomic_write(output_path, content)

            # Сводка (БЕЗ значений cookies)
            domains = sorted({c["domain"] for c in kwork_cookies})
            session = sum(1 for c in kwork_cookies if c.get("expires", -1) in (None, -1, 0))
            persistent = len(kwork_cookies) - session
            print(
                f"[OK] Сохранено {len(kwork_cookies)} cookies для {len(domains)} домен(ов):",
                flush=True,
            )
            for d in domains:
                print(f"     - {d}", flush=True)
            print(
                f"     Из них: {session} сессионных (истекают при закрытии браузера), "
                f"{persistent} с явным expiration.",
                flush=True,
            )
            print(f"[OK] Файл: {output_path} (chmod 600).", flush=True)
            print(f"[OK] Размер: {output_path.stat().st_size} байт.", flush=True)
            return 0

        except KeyboardInterrupt:
            print(
                "\n[WARN] Прервано пользователем (Ctrl+C). cookies.txt НЕ сохранён.",
                file=sys.stderr,
                flush=True,
            )
            return 1
        finally:
            try:
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
