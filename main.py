"""
SIamba OS — точка входа и бэкенд SystemAPI.

ВАЖНО: НЕ вызываем window.evaluate_js() из фоновых тредов — на GTK-бэкенде
pywebview это приводит к дедлокам и падениям. Вместо этого фронтенд
опрашивает get_running_apps() раз в ~1.5 секунды.
"""

import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

import webview

DEV_MODE     = True
WINDOW_TITLE = "SIamba OS"
BASE_DIR     = Path(__file__).resolve().parent
UI_INDEX     = BASE_DIR / "ui" / "index.html"

WATCH_INTERVAL = 0.5
FORK_GRACE     = 1.5


def _log(*args):
    """Диагностика в stderr — не мешает GUI, видна в терминале."""
    print("[SIamba OS]", *args, file=sys.stderr, flush=True)


# ==================================================================
# SystemAPI
# ==================================================================
class SystemAPI:
    def __init__(self):
        # app_id -> {"proc": Popen, "started_at": float}
        self._apps: dict[str, dict] = {}
        self._lock = threading.RLock()
        threading.Thread(target=self._watch_loop, daemon=True).start()

    # ------------------------------------------------------------------
    # Низкоуровневый безопасный вызов
    # ------------------------------------------------------------------
    @staticmethod
    def _run(args, timeout=3, check=False):
        try:
            return subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=check,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError,
                subprocess.CalledProcessError, PermissionError, OSError):
            return None

    # ------------------------------------------------------------------
    # Системные метрики
    # ------------------------------------------------------------------
    def get_system_stats(self) -> dict:
        def _out(args):
            r = self._run(args, timeout=2)
            return r.stdout.strip() if r and r.returncode == 0 else "n/a"

        return {
            "user":       _out(["whoami"]),
            "kernel":     _out(["uname", "-r"]),
            "uptime":     _out(["uptime", "-p"]),
            "hostname":   _out(["hostname"]),
            "os_release": self._read_os_release(),
        }

    @staticmethod
    def _read_os_release() -> str:
        try:
            with open("/etc/os-release", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
        return "n/a"

    # ------------------------------------------------------------------
    # Запуск приложений
    # ------------------------------------------------------------------
    def launch_app(self, app_id: str) -> dict:
        if not app_id or not isinstance(app_id, str):
            return {"ok": False, "error": "empty app_id"}

        with self._lock:
            if self._is_running_locked(app_id):
                return {"ok": True, "already_running": True}

        try:
            argv = shlex.split(app_id)
        except ValueError as e:
            return {"ok": False, "error": f"parse error: {e}"}
        if not argv:
            return {"ok": False, "error": "empty argv"}

        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        except FileNotFoundError:
            return {"ok": False, "error": f"not found: {argv[0]}"}
        except PermissionError:
            return {"ok": False, "error": f"permission denied: {argv[0]}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

        with self._lock:
            self._apps[app_id] = {"proc": proc, "started_at": time.time()}

        return {"ok": True, "pid": proc.pid}

    def get_running_apps(self) -> list[str]:
        with self._lock:
            return [aid for aid in list(self._apps.keys())
                    if self._is_running_locked(aid)]

    def _is_running_locked(self, app_id: str) -> bool:
        info = self._apps.get(app_id)
        if not info:
            return False

        proc: subprocess.Popen = info["proc"]
        if proc.poll() is None:
            return True

        age = time.time() - info.get("started_at", 0)
        if age < FORK_GRACE:
            return True

        try:
            name = Path(shlex.split(app_id)[0]).name
        except Exception:
            return False
        r = self._run(["pgrep", "-x", name], timeout=1)
        return bool(r and r.returncode == 0 and r.stdout.strip())

    def _watch_loop(self):
        while True:
            time.sleep(WATCH_INTERVAL)
            with self._lock:
                dead = [aid for aid, info in self._apps.items()
                        if info["proc"].poll() is not None
                        and not self._is_running_locked(aid)]
                for aid in dead:
                    self._apps.pop(aid, None)

    def test_launch(self, cmd: str) -> dict:
        return self.launch_app(cmd)

    # ------------------------------------------------------------------
    # Питание
    # ------------------------------------------------------------------
    def shutdown(self) -> dict:
        if DEV_MODE:
            _log("shutdown requested — suppressed (DEV_MODE)")
            return {"ok": True, "dev": True, "action": "shutdown"}
        try:
            subprocess.Popen(["systemctl", "poweroff"],
                             start_new_session=True, close_fds=True)
            return {"ok": True, "action": "shutdown"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def reboot(self) -> dict:
        if DEV_MODE:
            _log("reboot requested — suppressed (DEV_MODE)")
            return {"ok": True, "dev": True, "action": "reboot"}
        try:
            subprocess.Popen(["systemctl", "reboot"],
                             start_new_session=True, close_fds=True)
            return {"ok": True, "action": "reboot"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Звук
    # ------------------------------------------------------------------
    def get_volume(self) -> dict:
        vol, muted = 0, False

        r = self._run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], timeout=2)
        if r and r.returncode == 0:
            m = re.search(r"(\d+)%", r.stdout)
            if m:
                vol = int(m.group(1))

        r = self._run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"], timeout=2)
        if r and r.returncode == 0:
            muted = r.stdout.strip().lower().endswith("yes")

        return {"volume": vol, "muted": muted}

    def set_volume(self, value) -> dict:
        try:
            v = max(0, min(100, int(value)))
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad value"}

        r = self._run(
            ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{v}%"],
            timeout=2,
        )
        if r and r.returncode == 0:
            return {"ok": True, "volume": v}
        return {"ok": False, "error": "pactl failed"}

    def toggle_mute(self) -> dict:
        r = self._run(
            ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
            timeout=2,
        )
        if r and r.returncode == 0:
            # Возвращаем уже обновлённое состояние
            return self.get_volume()
        return {"ok": False, "error": "pactl failed"}

    def play_volume_feedback(self) -> dict:
        """
        Короткий системный звук изменения громкости.
        Порядок: canberra-gtk-play → paplay с известными путями.
        Возвращает быстро, чтобы не тормозить UI при драге слайдера.
        """
        # 1) canberra — стандарт freedesktop, есть на Mint/Debian с libcanberra
        r = self._run(
            ["canberra-gtk-play", "-i", "audio-volume-change"],
            timeout=2,
        )
        if r is not None and r.returncode == 0:
            return {"ok": True, "via": "canberra"}

        # 2) paplay с известными звуковыми файлами
        candidates = [
            "/usr/share/sounds/freedesktop/stereo/audio-volume-change.oga",
            "/usr/share/sounds/freedesktop/stereo/bell.oga",
            "/usr/share/sounds/gnome/default/alerts/glass.ogg",
            "/usr/share/sounds/linuxmint/stereo/audio-volume-change.oga",
        ]
        for path in candidates:
            if Path(path).exists():
                r = self._run(["paplay", path], timeout=2)
                if r is not None and r.returncode == 0:
                    return {"ok": True, "via": "paplay", "file": path}

        return {"ok": False, "error": "no sound player available"}

    # ------------------------------------------------------------------
    # Wi-Fi
    # ------------------------------------------------------------------
    def get_wifi_state(self) -> dict:
        result = {"enabled": False, "available": False,
                  "networks": [], "connected": None}

        # nmcli вообще установлен?
        r = self._run(["nmcli", "--version"], timeout=2)
        if r is None or r.returncode != 0:
            return result
        result["available"] = True

        # 'nmcli radio wifi' → "enabled" | "disabled" (без префикса поля).
        # Это надёжнее, чем '-f WIFI general', формат которого зависит
        # от версии NetworkManager.
        r = self._run(["nmcli", "radio", "wifi"], timeout=2)
        if r is None or r.returncode != 0:
            return result
        result["enabled"] = r.stdout.strip().lower().endswith("enabled")
        if not result["enabled"]:
            return result

        # Список сетей. Многострочный формат (-m multiline) устойчив
        # к двоеточиям и пробелам в SSID — обычный '-t' ломается.
        r = self._run(
            ["nmcli", "-m", "multiline",
             "-f", "IN-USE,SSID,SIGNAL,SECURITY",
             "device", "wifi", "list", "--rescan", "auto"],
            timeout=8,
        )
        if r is None or r.returncode != 0:
            if DEV_MODE:
                _log("wifi list failed:",
                     (r.stderr.strip() if r else "timeout"))
            return result

        # Разбираем записи, разделённые пустой строкой.
        records: list[dict] = []
        current: dict = {}
        for line in r.stdout.splitlines():
            if not line.strip():
                if current:
                    records.append(current)
                    current = {}
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                current[k.strip()] = v.strip()
        if current:
            records.append(current)

        seen = set()
        for rec in records:
            ssid = (rec.get("SSID") or "").strip()
            if not ssid or ssid == "--" or ssid in seen:
                continue
            seen.add(ssid)

            try:
                sig = int(rec.get("SIGNAL") or 0)
            except ValueError:
                sig = 0

            in_use = (rec.get("IN-USE") or "").strip() == "*"
            result["networks"].append({"ssid": ssid, "signal": sig})
            if in_use:
                result["connected"] = ssid

        result["networks"].sort(key=lambda n: -n["signal"])
        result["networks"] = result["networks"][:12]

        if DEV_MODE:
            _log(f"wifi: enabled={result['enabled']}, "
                 f"connected={result['connected']!r}, "
                 f"networks={len(result['networks'])}")

        return result

    def toggle_wifi(self) -> dict:
        # Читаем состояние прямо сейчас, без кэша
        r = self._run(["nmcli", "radio", "wifi"], timeout=2)
        if r is None or r.returncode != 0:
            return {"ok": False, "error": "nmcli unavailable"}

        enabled = r.stdout.strip().lower().endswith("enabled")
        target  = "off" if enabled else "on"

        r2 = self._run(["nmcli", "radio", "wifi", target], timeout=3)
        if r2 is not None and r2.returncode == 0:
            return {"ok": True, "enabled": (target == "on")}
        return {"ok": False, "error": "nmcli radio failed"}

    def connect_wifi(self, ssid: str) -> dict:
        if not ssid:
            return {"ok": False, "error": "empty ssid"}
        r = self._run(
            ["nmcli", "device", "wifi", "connect", ssid],
            timeout=20,
        )
        if r is None:
            return {"ok": False, "error": "timeout or nmcli missing"}
        if r.returncode == 0:
            return {"ok": True}
        return {"ok": False,
                "error": (r.stderr or r.stdout or "connect failed").strip()}


# ==================================================================
# Точка входа
# ==================================================================
def main() -> int:
    if not UI_INDEX.exists():
        _log(f"UI not found: {UI_INDEX}")
        return 1

    api = SystemAPI()
    webview.create_window(
        title=WINDOW_TITLE,
        url=UI_INDEX.as_uri(),
        js_api=api,
        width=1280, height=720,
        fullscreen=not DEV_MODE,
        min_size=(800, 480),
    )
    webview.start(debug=DEV_MODE)
    return 0


if __name__ == "__main__":
    sys.exit(main())