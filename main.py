"""
MyOS — точка входа.
Запускает окно pywebview и регистрирует SystemAPI для вызовов из JS.
"""

import json
import subprocess
import sys
from pathlib import Path

import webview

# ------------------------------------------------------------------
# Глобальные настройки
# ------------------------------------------------------------------
DEV_MODE = True          # True — окно 1280x720 + DevTools, False — fullscreen
WINDOW_TITLE = "MyOS"
BASE_DIR = Path(__file__).resolve().parent
UI_INDEX = BASE_DIR / "ui" / "index.html"


# ------------------------------------------------------------------
# API, доступное из JS через window.pywebview.api.*
# ------------------------------------------------------------------
class SystemAPI:
    """Мост между JS-фронтендом и системой."""

    # ---------- системные метрики ----------
    def get_system_stats(self) -> dict:
        """
        Возвращает словарь с базовыми метриками системы.
        Фронтенд получает его как JS-объект (pywebview сериализует dict -> JSON).
        """
        def _run(args: list[str]) -> str:
            try:
                out = subprocess.check_output(
                    args, stderr=subprocess.DEVNULL, text=True
                )
                return out.strip()
            except Exception as e:
                return f"n/a ({e.__class__.__name__})"

        return {
            "user":      _run(["whoami"]),
            "kernel":    _run(["uname", "-r"]),
            "uptime":    _run(["uptime", "-p"]),
            "hostname":  _run(["hostname"]),
            "os_release": self._read_os_release(),
        }

    @staticmethod
    def _read_os_release() -> str:
        """Читает PRETTY_NAME из /etc/os-release."""
        try:
            with open("/etc/os-release", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
        return "n/a"

    # ---------- запуск процессов ----------
    def test_launch(self, cmd: str) -> dict:
        """
        Безопасно запускает процесс в Linux.
        cmd — строка, парсится через shlex.split (без shell=True!).
        """
        import shlex

        if not cmd or not isinstance(cmd, str):
            return {"ok": False, "error": "empty command"}

        try:
            argv = shlex.split(cmd)
        except ValueError as e:
            return {"ok": False, "error": f"parse error: {e}"}

        if not argv:
            return {"ok": False, "error": "empty argv"}

        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,   # отвязываем от родителя
            )
            return {"ok": True, "pid": proc.pid, "argv": argv}
        except FileNotFoundError:
            return {"ok": False, "error": f"not found: {argv[0]}"}
        except PermissionError:
            return {"ok": False, "error": f"permission denied: {argv[0]}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ------------------------------------------------------------------
# Точка входа
# ------------------------------------------------------------------
def main() -> int:
    if not UI_INDEX.exists():
        print(f"[MyOS] UI not found: {UI_INDEX}", file=sys.stderr)
        return 1

    api = SystemAPI()

    window = webview.create_window(
        title=WINDOW_TITLE,
        url=UI_INDEX.as_uri(),   # корректный file:// URI
        js_api=api,
        width=1280,
        height=720,
        fullscreen=not DEV_MODE,
        min_size=(800, 480),
    )

    # debug=True включает WebInspector (DevTools) в pywebview
    webview.start(debug=DEV_MODE)
    return 0


if __name__ == "__main__":
    sys.exit(main())