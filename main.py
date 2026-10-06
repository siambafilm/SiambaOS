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
import os
import json

import webview

DEV_MODE     = True
DEVTOOLS = False
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
        self._apps: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._apps_cache: tuple[float, list[dict]] | None = None
        self._apps_cache_ttl = 60.0   # сек
        self._dock_config_path = Path.home() / ".config" / "siamba-os" / "dock.json"
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
    def launch_app(self, app_id: str, exec_cmd: str | None = None) -> dict:
        """
        app_id — ключ, под которым приложение регистрируется в состоянии
                (совпадает с data-app иконки в доке).
        exec_cmd — что реально запускать; если None, запускается сам app_id.
        """
        if not app_id or not isinstance(app_id, str):
            return {"ok": False, "error": "empty app_id"}

        with self._lock:
            if self._is_running_locked(app_id):
                return {"ok": True, "already_running": True}

        cmd_str = exec_cmd if exec_cmd else app_id
        try:
            argv = shlex.split(cmd_str)
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
            self._apps[app_id] = {
                "proc": proc,
                "started_at": time.time(),
                "argv": argv,          # для pgrep-fallback
            }

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

        argv = info.get("argv") or [app_id]
        try:
            name = Path(argv[0]).name
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

    # ------------------------------------------------------------------
    # Сканирование приложений
    # ------------------------------------------------------------------
    def scan_linux_apps(self) -> list[dict]:
        """Читает .desktop-файлы из системной и пользовательской директорий."""
        now = time.time()
        if self._apps_cache and (now - self._apps_cache[0]) < self._apps_cache_ttl:
            return self._apps_cache[1]

        dirs = [
            Path("/usr/share/applications"),
            Path("/usr/local/share/applications"),
            Path.home() / ".local/share/applications",
        ]

        seen: set[str] = set()
        results: list[dict] = []

        for d in dirs:
            if not d.is_dir():
                continue
            for desktop_file in d.glob("*.desktop"):
                entry = self._parse_desktop(desktop_file)
                if entry and entry["id"] not in seen:
                    seen.add(entry["id"])
                    results.append(entry)

        results.sort(key=lambda x: x["name"].lower())
        self._apps_cache = (now, results)
        if DEV_MODE:
            _log(f"scanned {len(results)} .desktop entries")
        return results

    @staticmethod
    def _localized(fields: dict, base: str) -> str:
        """Возвращает Name[ru_RU] → Name[ru] → Name по локали окружения."""
        langs: list[str] = []
        for var in ("LC_MESSAGES", "LANG"):
            val = os.environ.get(var, "")
            if val:
                part = val.split(".")[0]
                if part:
                    langs.append(part)
                    langs.append(part.split("_")[0])
        for lang in langs:
            key = f"{base}[{lang}]"
            if key in fields:
                return fields[key]
        return fields.get(base, "")

    def _parse_desktop(self, path: Path) -> dict | None:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        in_entry = False
        fields: dict[str, str] = {}
        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("["):
                in_entry = (line == "[Desktop Entry]")
                continue
            if not in_entry or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k not in fields:          # первое значение выигрывает
                fields[k] = v.strip()

        if fields.get("Type") != "Application":
            return None
        if fields.get("NoDisplay", "").lower() == "true":
            return None
        if fields.get("Hidden", "").lower() == "true":
            return None

        name = self._localized(fields, "Name").strip()
        exec_line = fields.get("Exec", "").strip()
        if not name or not exec_line:
            return None

        # Убираем field codes ( %f %F %u %U %d %D %n %N %i %c %k %v %m )
        exec_clean = re.sub(r"%[fFuUdDnNickvm]", "", exec_line)
        exec_clean = re.sub(r"\s+", " ", exec_clean).strip()
        if not exec_clean:
            return None

        if fields.get("Terminal", "").lower() == "true":
            exec_clean = "x-terminal-emulator -e " + exec_clean

        # basename первого токена — совпадает с data-app иконок дока
        try:
            bin_name = Path(shlex.split(exec_clean)[0]).name
        except Exception:
            bin_name = path.stem

        icon = fields.get("Icon", "").strip()
        icon_path = self._resolve_icon(icon) if icon else None

        return {
            "id":        path.stem,
            "name":      name,
            "exec":      exec_clean,
            "bin":       bin_name,
            "icon":      icon,
            "icon_path": icon_path,
            "comment":   self._localized(fields, "Comment").strip(),
        }

    @staticmethod
    def _resolve_icon(icon: str) -> str | None:
        if not icon:
            return None
        p = Path(icon)
        if p.is_absolute() and p.is_file():
            return str(p)

        bases = [
            Path("/usr/share/icons/hicolor"),
            Path("/usr/share/icons/Adwaita"),
            Path("/usr/share/pixmaps"),
            Path.home() / ".local/share/icons",
        ]
        sizes = ["256x256", "128x128", "64x64", "48x48", "32x32", "scalable"]
        exts  = [".png", ".svg", ".xpm"]

        for base in bases:
            if not base.is_dir():
                continue
            for ext in exts:                        # плоская раскладка
                f = base / (icon + ext)
                if f.is_file():
                    return str(f)
            for size in sizes:                      # <base>/<size>/apps/
                for ext in exts:
                    f = base / size / "apps" / (icon + ext)
                    if f.is_file():
                        return str(f)
        return None

    # ------------------------------------------------------------------
    # Конфигурация дока
    # ------------------------------------------------------------------
    DEFAULT_DOCK = [
        "xed", "gnome-terminal", "firefox", "thunderbird", "rhythmbox",
        "eog", "gnome-calendar", "gnome-control-center", "nautilus",
    ]

    def get_dock_config(self) -> list[str]:
        try:
            if self._dock_config_path.exists():
                data = json.loads(self._dock_config_path.read_text(encoding="utf-8"))
                if isinstance(data, list) and all(isinstance(x, str) for x in data):
                    return data
        except (OSError, json.JSONDecodeError) as e:
            _log(f"dock config read failed: {e}")
        return list(self.DEFAULT_DOCK)

    def set_dock_config(self, ids: list) -> dict:
        if not isinstance(ids, list):
            return {"ok": False, "error": "expected list"}

        clean: list[str] = []
        for x in ids:
            if isinstance(x, str) and x.strip() and x not in clean:
                clean.append(x.strip())
            if len(clean) >= 30:
                break

        try:
            self._dock_config_path.parent.mkdir(parents=True, exist_ok=True)
            self._dock_config_path.write_text(
                json.dumps(clean, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return {"ok": True, "dock": clean}
        except OSError as e:
            return {"ok": False, "error": str(e)}


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
    webview.start(debug=DEVTOOLS)
    return 0


if __name__ == "__main__":
    sys.exit(main())