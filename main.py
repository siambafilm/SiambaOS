"""
SIamba OS — точка входа и бэкенд SystemAPI.

ВАЖНО: НЕ вызываем window.evaluate_js() из фоновых тредов — на GTK-бэкенде
pywebview это приводит к дедлокам и падениям. Вместо этого фронтенд
опрашивает get_running_apps() раз в ~1.5 секунды.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path

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
            # --- новое ---
        self._siamba_apps: dict[str, object] = {}     # "siamba:<id>" -> window
        self._app_manager = None                       # AppManager
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
            linux = [aid for aid in list(self._apps.keys())
                    if self._is_running_locked(aid)]
            siamba = list(self._siamba_apps.keys())
        return linux + siamba

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

    # ------------------------------------------------------------------
    # Интеграция с AppManager
    # ------------------------------------------------------------------
    def set_app_manager(self, mgr):
        self._app_manager = mgr

    def install_app(self, package_path: str) -> dict:
        if not self._app_manager:
            return {"ok": False, "error": "app manager unavailable"}
        return self._app_manager.install_app(package_path)

    def list_my_apps(self) -> list:
        if not self._app_manager:
            return []
        return self._app_manager.list_apps()

    def launch_my_app(self, app_id: str) -> dict:
        if not self._app_manager:
            return {"ok": False, "error": "app manager unavailable"}
        return self._app_manager.launch_my_app(app_id)

    def uninstall_app(self, app_id: str) -> dict:
        if not self._app_manager:
            return {"ok": False, "error": "app manager unavailable"}
        return self._app_manager.uninstall_app(app_id)

    # ------------------------------------------------------------------
    # Регистрация окон siamba-приложений (для точек в доке)
    # ------------------------------------------------------------------
    def _register_siamba_app(self, key: str, window) -> None:
        with self._lock:
            self._siamba_apps[key] = window

    def _unregister_siamba_app(self, key: str) -> None:
        with self._lock:
            self._siamba_apps.pop(key, None)

# ==================================================================
# AppBridge — js_api для окна конкретного siamba-приложения
# ==================================================================
class AppBridge:
    """
    Изолирует UI приложения от бэкенда и от системы:
      JS (ui приложения) ↔ AppBridge.call(...) ↔ subprocess stdin
      subprocess stdout (proxy) ↔ AppBridge ↔ SystemAPI (whitelist)
    """

    # Разрешённые системные методы — только их может вызвать приложение.
    # Всё остальное отбивается с ошибкой "method not allowed".
    ALLOWED_PROXY = {
        "get_system_stats",
        "get_volume", "set_volume", "toggle_mute",
        "get_wifi_state", "play_volume_feedback",
    }

    def __init__(self, app_id: str, app_dir: Path, system_api: "SystemAPI"):
        self.app_id = app_id
        self.app_dir = app_dir
        self.system_api = system_api
        self._window = None
        self._proc: subprocess.Popen | None = None
        self._pending: dict[int, dict] = {}
        self._next_id = 1
        self._io_lock = threading.RLock()

    def attach_window(self, window) -> None:
        self._window = window

    # ----------------------------------------------------------
    # Запуск бэкенда
    # ----------------------------------------------------------
    def start_backend(self) -> None:
        backend = self.app_dir / "app_backend.py"
        if not backend.is_file():
            return

        env = os.environ.copy()
        env["SIAMBA_APP_ID"] = self.app_id
        env["SIAMBA_APP_DIR"] = str(self.app_dir)
        env["SIAMBA_APP_DATA"] = str(
            Path.home() / ".local" / "share" / "siamba-os" / "apps" / self.app_id
        )
        env["SIAMBA_OS_ROOT"] = str(BASE_DIR)
        env["PYTHONUNBUFFERED"] = "1"

        self._proc = subprocess.Popen(
            [sys.executable, str(backend)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
            cwd=str(self.app_dir),
            env=env,
            start_new_session=True, close_fds=True,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def stop_backend(self) -> None:
        with self._io_lock:
            proc = self._proc
            self._proc = None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try: proc.kill()
                except Exception: pass

    # ----------------------------------------------------------
    # Чтение из бэкенда
    # ----------------------------------------------------------
    def _read_stdout(self):
        try:
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._dispatch(msg)
        except Exception:
            pass
        # Бэкенд умер — закрываем окно приложения
        self._on_backend_exit()

    def _read_stderr(self):
        try:
            for line in self._proc.stderr:
                print(f"[{self.app_id}] {line.rstrip()}", file=sys.stderr, flush=True)
        except Exception:
            pass

    def _dispatch(self, msg: dict):
        kind = msg.get("type")
        if kind == "proxy":
            self._handle_proxy(msg)
        elif kind == "response":
            fut = self._pending.pop(msg.get("id"), None)
            if fut:
                fut["result"] = msg.get("result")
                fut["event"].set()

    def _handle_proxy(self, msg: dict):
        method = msg.get("method")
        params = msg.get("params") or {}
        req_id = msg.get("id")

        if method not in self.ALLOWED_PROXY:
            self._send({"type": "proxy_response", "id": req_id,
                        "error": f"method not allowed: {method}"})
            return

        try:
            fn = getattr(self.system_api, method)
            result = fn(**params) if isinstance(params, dict) else fn(*params)
            self._send({"type": "proxy_response", "id": req_id, "result": result})
        except Exception as e:
            self._send({"type": "proxy_response", "id": req_id,
                        "error": f"{type(e).__name__}: {e}"})

    def _send(self, msg: dict):
        with self._io_lock:
            proc = self._proc
            if not proc or proc.poll() is not None:
                return
            try:
                proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
                proc.stdin.flush()
            except Exception:
                pass

    def _on_backend_exit(self):
        try:
            if self._window:
                self._window.destroy()
        except Exception:
            pass

    # ----------------------------------------------------------
    # Методы, вызываемые из JS приложения через pywebview.api
    # ----------------------------------------------------------
    def call(self, method: str, params: dict | None = None):
        """UI приложения → его бэкенд. Возвращает ответ бэкенда."""
        if not self._proc or self._proc.poll() is not None:
            return {"ok": False, "error": "backend not running"}

        with self._io_lock:
            req_id = self._next_id
            self._next_id += 1

        evt = threading.Event()
        fut = {"event": evt, "result": None}
        self._pending[req_id] = fut

        self._send({"type": "call", "id": req_id,
                    "method": method, "params": params or {}})

        if not evt.wait(timeout=20):
            self._pending.pop(req_id, None)
            return {"ok": False, "error": "backend timeout"}
        return fut["result"]

    def get_app_info(self):
        """Метаданные окна — UI может показать имя, версию и т.п."""
        try:
            mf = json.loads((self.app_dir / "manifest.json").read_text(encoding="utf-8"))
        except Exception:
            mf = {}
        return {
            "id":      self.app_id,
            "name":    mf.get("name", self.app_id),
            "version": mf.get("version", "0.0.0"),
        }

    # --- оконные операции для frameless-режима ---
    def window_move(self, x: int, y: int):
        if self._window:
            try: self._window.move(int(x), int(y))
            except Exception: pass
        return {"ok": True}

    def window_get_position(self):
        if not self._window:
            return {"x": 0, "y": 0}
        try:
            return {"x": self._window.x or 0, "y": self._window.y or 0}
        except Exception:
            return {"x": 0, "y": 0}

    def window_minimize(self):
        if self._window:
            try: self._window.minimize()
            except Exception: pass
        return {"ok": True}

    def window_close(self):
        if self._window:
            try: self._window.destroy()
            except Exception: pass
        return {"ok": True}


# ==================================================================
# AppManager — установка / список / запуск siamba-приложений
# ==================================================================
class AppManager:
    def __init__(self, base_dir: Path, system_api: "SystemAPI"):
        self.base_dir = base_dir
        self.apps_dir = base_dir / "apps"
        self.apps_dir.mkdir(exist_ok=True)
        self.system_api = system_api
        self._windows: dict[str, object] = {}
        self._bridges: dict[str, AppBridge] = {}
        self._lock = threading.RLock()

    # ----------------------------------------------------------
    # Установка .sht
    # ----------------------------------------------------------
    def install_app(self, package_path: str) -> dict:
        p = Path(package_path)
        if not p.is_file():
            return {"ok": False, "error": "file not found"}

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Безопасная распаковка: никаких ../ и абсолютных путей
            try:
                with tarfile.open(p, "r:*") as tar:
                    for m in tar.getmembers():
                        target = (tmp_path / m.name).resolve()
                        if not str(target).startswith(str(tmp_path.resolve())):
                            return {"ok": False, "error": f"unsafe path: {m.name}"}
                    tar.extractall(tmp_path)
            except tarfile.TarError as e:
                return {"ok": False, "error": f"bad archive: {e}"}

            manifests = sorted(tmp_path.rglob("manifest.json"),
                               key=lambda x: len(x.parts))
            if not manifests:
                return {"ok": False, "error": "manifest.json not found"}

            manifest_path = manifests[0]
            app_root = manifest_path.parent

            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as e:
                return {"ok": False, "error": f"bad manifest: {e}"}

            app_id = (manifest.get("id") or "").strip()
            if not re.match(r"^[a-z0-9_\-]{2,64}$", app_id):
                return {"ok": False, "error": "invalid id (a-z 0-9 _ -, 2..64)"}

            for key in ("name", "version"):
                if not manifest.get(key):
                    return {"ok": False, "error": f"manifest missing '{key}'"}

            target = self.apps_dir / app_id
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(app_root), str(target))

        return {"ok": True, "app_id": app_id, "manifest": manifest}

    # ----------------------------------------------------------
    # Список установленных
    # ----------------------------------------------------------
    def list_apps(self) -> list:
        out = []
        for entry in sorted(self.apps_dir.iterdir()):
            if not entry.is_dir():
                continue
            mf_path = entry / "manifest.json"
            if not mf_path.is_file():
                continue
            try:
                mf = json.loads(mf_path.read_text(encoding="utf-8"))
            except Exception:
                continue

            app_id = mf.get("id") or entry.name

            icon_url = None
            icon = mf.get("icon")
            if icon:
                icon_file = entry / icon
                if icon_file.is_file():
                    icon_url = icon_file.as_uri()

            out.append({
                "id":           app_id,
                "kind":         "siamba",
                "bin":          f"siamba:{app_id}",       # ключ состояния
                "name":         mf.get("name", app_id),
                "version":      mf.get("version", "0.0.0"),
                "description":  mf.get("description", ""),
                "author":       mf.get("author", ""),
                "icon_path":    icon_url,
                "has_backend":  (entry / "app_backend.py").is_file(),
                "has_ui":       (entry / "ui" / "index.html").is_file(),
            })
        return out

    # ----------------------------------------------------------
    # Запуск приложения в отдельном окне
    # ----------------------------------------------------------
    def launch_my_app(self, app_id: str) -> dict:
        with self._lock:
            if app_id in self._windows:
                try:
                    self._windows[app_id].show()
                    self._windows[app_id].restore()
                except Exception:
                    pass
                return {"ok": True, "focused": True}

        app_dir = self.apps_dir / app_id
        if not app_dir.is_dir():
            return {"ok": False, "error": "app not installed"}

        manifest_path = app_dir / "manifest.json"
        ui_index      = app_dir / "ui" / "index.html"
        if not ui_index.is_file():
            return {"ok": False, "error": "ui/index.html missing"}

        try:
            mf = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            return {"ok": False, "error": f"bad manifest: {e}"}

        win_cfg = mf.get("window") or {}

        bridge = AppBridge(app_id, app_dir, self.system_api)
        bridge.start_backend()

        try:
            window = webview.create_window(
                title      = mf.get("name", app_id),
                url        = ui_index.as_uri(),
                js_api     = bridge,
                width      = int(win_cfg.get("width", 900)),
                height     = int(win_cfg.get("height", 600)),
                x          = win_cfg.get("x"),
                y          = win_cfg.get("y"),
                min_size   = (420, 320),
                frameless  = True,
                background_color = win_cfg.get("background", "#14141c"),
            )
        except Exception as e:
            bridge.stop_backend()
            return {"ok": False, "error": str(e)}

        bridge.attach_window(window)

        key = f"siamba:{app_id}"

        def on_closed():
            with self._lock:
                self._windows.pop(app_id, None)
                self._bridges.pop(app_id, None)
            self.system_api._unregister_siamba_app(key)
            bridge.stop_backend()

        try:
            window.events.closed += on_closed
        except Exception:
            pass

        with self._lock:
            self._windows[app_id] = window
            self._bridges[app_id] = bridge

        self.system_api._register_siamba_app(key, window)
        return {"ok": True, "app_id": app_id, "key": key}

    # ----------------------------------------------------------
    # Удаление
    # ----------------------------------------------------------
    def uninstall_app(self, app_id: str) -> dict:
        with self._lock:
            if app_id in self._windows:
                return {"ok": False, "error": "app is running"}
        target = self.apps_dir / app_id
        if not target.is_dir():
            return {"ok": False, "error": "not installed"}
        try:
            shutil.rmtree(target)
            return {"ok": True}
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
    app_manager = AppManager(BASE_DIR, api)
    api.set_app_manager(app_manager)

    webview.create_window(
        title=WINDOW_TITLE,
        url=UI_INDEX.as_uri(),
        js_api=api,
        width=1920, height=1080,
        fullscreen=not DEV_MODE,
        min_size=(800, 480),
        frameless=True,
    )
    webview.start(debug=DEVTOOLS)
    return 0


if __name__ == "__main__":
    sys.exit(main())