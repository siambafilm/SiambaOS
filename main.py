"""
SIamba OS — точка входа и бэкенд SystemAPI.

ВАЖНО: НЕ вызываем window.evaluate_js() из фоновых тредов — на GTK-бэкенде
pywebview это приводит к дедлокам и падениям. Вместо этого фронтенд
опрашивает get_running_apps() раз в ~1.5 секунды.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
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
DEVTOOLS     = True
WINDOW_TITLE = "SIamba OS"
BASE_DIR     = Path(__file__).resolve().parent
UI_INDEX     = BASE_DIR / "ui" / "index.html"
LOGIN_INDEX  = BASE_DIR / "ui" / "login.html"

WATCH_INTERVAL = 0.5
FORK_GRACE     = 1.5


def _log(*args):
    print("[SIamba OS]", *args, file=sys.stderr, flush=True)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


# ==================================================================
# SystemAPI
# ==================================================================
class SystemAPI:
    DEFAULT_DOCK = [
        "xed", "gnome-terminal", "firefox", "thunderbird", "rhythmbox",
        "eog", "gnome-calendar", "gnome-control-center", "nautilus",
    ]

    USERNAME_RE = re.compile(r"^[a-z][a-z0-9_\-]{1,31}$")

    def __init__(self):
        self._apps: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._apps_cache: tuple[float, list[dict]] | None = None
        self._apps_cache_ttl = 60.0
        self._dock_config_path  = BASE_DIR / "dock.json"
        self._users_root        = BASE_DIR / "users"
        self._theme_config_path = BASE_DIR / "theme.json"
        self._session_path      = BASE_DIR / "session.json"

        self._siamba_apps: dict[str, object] = {}
        self._app_manager = None
        self._main_window = None

        threading.Thread(target=self._watch_loop, daemon=True).start()

    # ------------------------------------------------------------------
    # Служебное
    # ------------------------------------------------------------------
    @staticmethod
    def _run(args, timeout=3, check=False):
        try:
            return subprocess.run(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=timeout, check=check,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError,
                subprocess.CalledProcessError, PermissionError, OSError):
            return None

    def set_main_window(self, window):
        self._main_window = window

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
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True, close_fds=True,
            )
        except FileNotFoundError:
            return {"ok": False, "error": f"not found: {argv[0]}"}
        except PermissionError:
            return {"ok": False, "error": f"permission denied: {argv[0]}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

        with self._lock:
            self._apps[app_id] = {
                "proc": proc, "started_at": time.time(), "argv": argv,
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
    # Выход из системы и переходы между экранами
    # ------------------------------------------------------------------
    def logout(self) -> dict:
        if self._app_manager:
            try:
                self._app_manager.shutdown_all()
            except Exception as e:
                _log(f"shutdown_all failed: {e}")
        self.clear_session()
        if self._main_window is not None:
            try:
                self._main_window.load_url(LOGIN_INDEX.as_uri())
            except Exception as e:
                _log(f"load_url(login) failed: {e}")
                return {"ok": False, "error": str(e)}
        return {"ok": True}

    def goto_desktop(self) -> dict:
        if self._main_window is not None:
            try:
                self._main_window.load_url(UI_INDEX.as_uri())
            except Exception as e:
                _log(f"load_url(desktop) failed: {e}")
                return {"ok": False, "error": str(e)}
        return {"ok": True}

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
        r = self._run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{v}%"],
                      timeout=2)
        if r and r.returncode == 0:
            return {"ok": True, "volume": v}
        return {"ok": False, "error": "pactl failed"}

    def toggle_mute(self) -> dict:
        r = self._run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
                      timeout=2)
        if r and r.returncode == 0:
            return self.get_volume()
        return {"ok": False, "error": "pactl failed"}

    def play_volume_feedback(self) -> dict:
        r = self._run(["canberra-gtk-play", "-i", "audio-volume-change"],
                      timeout=2)
        if r is not None and r.returncode == 0:
            return {"ok": True, "via": "canberra"}
        for path in [
            "/usr/share/sounds/freedesktop/stereo/audio-volume-change.oga",
            "/usr/share/sounds/freedesktop/stereo/bell.oga",
            "/usr/share/sounds/gnome/default/alerts/glass.ogg",
            "/usr/share/sounds/linuxmint/stereo/audio-volume-change.oga",
        ]:
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
        r = self._run(["nmcli", "--version"], timeout=2)
        if r is None or r.returncode != 0:
            return result
        result["available"] = True

        r = self._run(["nmcli", "radio", "wifi"], timeout=2)
        if r is None or r.returncode != 0:
            return result
        result["enabled"] = r.stdout.strip().lower().endswith("enabled")
        if not result["enabled"]:
            return result

        r = self._run(
            ["nmcli", "-m", "multiline",
             "-f", "IN-USE,SSID,SIGNAL,SECURITY",
             "device", "wifi", "list", "--rescan", "auto"],
            timeout=8,
        )
        if r is None or r.returncode != 0:
            return result

        records: list[dict] = []
        current: dict = {}
        for line in r.stdout.splitlines():
            if not line.strip():
                if current:
                    records.append(current); current = {}
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
        return result

    def toggle_wifi(self) -> dict:
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
        r = self._run(["nmcli", "device", "wifi", "connect", ssid], timeout=20)
        if r is None:
            return {"ok": False, "error": "timeout or nmcli missing"}
        if r.returncode == 0:
            return {"ok": True}
        return {"ok": False,
                "error": (r.stderr or r.stdout or "connect failed").strip()}

    # ------------------------------------------------------------------
    # Сканирование Linux-приложений
    # ------------------------------------------------------------------
    def scan_linux_apps(self) -> list[dict]:
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
        return results

    @staticmethod
    def _localized(fields: dict, base: str) -> str:
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
            content = path.read_text(encoding="utf-8-sig", errors="replace")
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
            if k not in fields:
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

        exec_clean = re.sub(r"%[fFuUdDnNickvm]", "", exec_line)
        exec_clean = re.sub(r"\s+", " ", exec_clean).strip()
        if not exec_clean:
            return None

        if fields.get("Terminal", "").lower() == "true":
            exec_clean = "x-terminal-emulator -e " + exec_clean

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
            for ext in exts:
                f = base / (icon + ext)
                if f.is_file():
                    return str(f)
            for size in sizes:
                for ext in exts:
                    f = base / size / "apps" / (icon + ext)
                    if f.is_file():
                        return str(f)
        return None

    # ------------------------------------------------------------------
    # Конфигурация дока
    # ------------------------------------------------------------------
    def get_dock_config(self) -> list[str]:
        try:
            if self._dock_config_path.exists():
                data = _read_json(self._dock_config_path)
                if isinstance(data, list) and all(isinstance(x, str) for x in data):
                    return data
        except Exception as e:
            _log(f"dock config read failed: {type(e).__name__}: {e}")
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
    # Регистрация siamba-окон
    # ------------------------------------------------------------------
    def _register_siamba_app(self, key: str, window) -> None:
        with self._lock:
            self._siamba_apps[key] = window

    def _unregister_siamba_app(self, key: str) -> None:
        with self._lock:
            self._siamba_apps.pop(key, None)

    # ==================================================================
    # ВИРТУАЛЬНЫЕ ПОЛЬЗОВАТЕЛИ SIAMBA
    # ==================================================================
    #
    # users/<username>/
    #     profile.json    — username, display_name, password_hash,
    #                       require_password, avatar, created
    #     settings.json   — theme, wallpaper, locale
    #     home/

    @staticmethod
    def _hash_password(password: str, salt: bytes | None = None) -> str:
        if salt is None:
            salt = secrets.token_bytes(16)
        iterations = 200_000
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )
        return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"

    @staticmethod
    def _verify_password(password: str, stored: str) -> bool:
        try:
            algo, iters, salt_hex, hash_hex = stored.split("$")
            if algo != "pbkdf2_sha256":
                return False
            dk = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"),
                bytes.fromhex(salt_hex), int(iters),
            )
            return hmac.compare_digest(dk.hex(), hash_hex)
        except Exception:
            return False

    def _user_dir(self, username: str) -> Path:
        return self._users_root / username

    def get_users(self) -> list[dict]:
        root = self._users_root
        if not root.is_dir():
            return []

        out: list[dict] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            profile = entry / "profile.json"
            if not profile.is_file():
                continue
            try:
                data = _read_json(profile)
            except Exception:
                continue

            out.append({
                "username":         entry.name,
                "display_name":     data.get("display_name") or entry.name,
                "avatar":           data.get("avatar"),
                "created":          data.get("created"),
                "require_password": bool(data.get("require_password", True)),
            })
        return out

    def create_user(self, username: str, password: str = "",
                    display_name: str | None = None,
                    require_password: bool = True) -> dict:
        if not isinstance(username, str) or not self.USERNAME_RE.match(username):
            return {"ok": False,
                    "error": "invalid username (a-z, 0-9, _, -, 2..32)"}

        user_dir = self._user_dir(username)
        if user_dir.exists():
            return {"ok": False, "error": "user already exists"}

        require_password = bool(require_password)

        if require_password:
            if not isinstance(password, str) or len(password) < 4:
                return {"ok": False, "error": "password too short (min 4)"}
            pwd_hash = self._hash_password(password)
        else:
            pwd_hash = ""

        try:
            (user_dir / "home").mkdir(parents=True, exist_ok=False)
        except OSError as e:
            return {"ok": False, "error": f"mkdir failed: {e}"}

        try:
            profile = {
                "username":         username,
                "display_name":     (display_name or username).strip(),
                "password_hash":    pwd_hash,
                "require_password": require_password,
                "avatar":           None,
                "created":          time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            settings = {"theme": "dark", "wallpaper": "aurora", "locale": "ru-RU"}
            (user_dir / "profile.json").write_text(
                json.dumps(profile, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (user_dir / "settings.json").write_text(
                json.dumps(settings, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            shutil.rmtree(user_dir, ignore_errors=True)
            return {"ok": False, "error": f"write failed: {e}"}

        _log(f"user created: {username} (require_password={require_password})")
        return {"ok": True, "username": username}

    def delete_user(self, username: str) -> dict:
        if not isinstance(username, str) or not username:
            return {"ok": False, "error": "empty username"}
        user_dir = self._user_dir(username)
        if not user_dir.is_dir():
            return {"ok": False, "error": "user not found"}
        try:
            if user_dir.resolve() == self._users_root.resolve():
                return {"ok": False, "error": "refusing to delete users root"}
        except Exception:
            pass
        try:
            shutil.rmtree(user_dir)
        except OSError as e:
            return {"ok": False, "error": f"rmtree failed: {e}"}
        _log(f"user deleted: {username}")
        return {"ok": True}

    def update_user(self, username: str, *,
                    display_name: str | None = None,
                    password: str | None = None,
                    require_password: bool | None = None) -> dict:
        if not isinstance(username, str) or not username:
            return {"ok": False, "error": "empty username"}

        profile_path = self._user_dir(username) / "profile.json"
        if not profile_path.is_file():
            return {"ok": False, "error": "user not found"}

        try:
            profile = _read_json(profile_path)
        except Exception as e:
            return {"ok": False, "error": f"read failed: {e}"}

        if display_name is not None:
            dn = str(display_name).strip()
            if not dn:
                return {"ok": False, "error": "empty display_name"}
            profile["display_name"] = dn

        if password is not None and password != "":
            if len(password) < 4:
                return {"ok": False, "error": "password too short (min 4)"}
            profile["password_hash"] = self._hash_password(password)
            profile["require_password"] = True

        if require_password is not None:
            require_password = bool(require_password)
            if require_password and not profile.get("password_hash"):
                return {"ok": False,
                        "error": "cannot require password without one set"}
            profile["require_password"] = require_password

        try:
            profile_path.write_text(
                json.dumps(profile, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            return {"ok": False, "error": f"write failed: {e}"}
        return {"ok": True}

    def authenticate_user(self, username: str, password: str = "") -> dict:
        profile_path = self._user_dir(username) / "profile.json"
        if not profile_path.is_file():
            return {"ok": False, "error": "user not found"}
        try:
            profile = _read_json(profile_path)
        except Exception as e:
            return {"ok": False, "error": f"read failed: {e}"}

        if not profile.get("require_password", True):
            return {"ok": True, "passwordless": True}

        stored = profile.get("password_hash", "")
        if self._verify_password(password, stored):
            return {"ok": True}
        return {"ok": False, "error": "wrong password"}

    # ------------------------------------------------------------------
    # Сессия
    # ------------------------------------------------------------------
    def get_current_session(self) -> dict:
        try:
            if self._session_path.exists():
                data = _read_json(self._session_path)
                if isinstance(data, dict):
                    return {"username": data.get("username")}
        except Exception as e:
            _log(f"session read failed: {type(e).__name__}: {e}")
        return {"username": None}

    def set_current_session(self, username: str) -> dict:
        if not isinstance(username, str) or not username:
            return {"ok": False, "error": "empty username"}
        if not self._user_dir(username).is_dir():
            return {"ok": False, "error": "user not found"}
        try:
            self._session_path.write_text(
                json.dumps({"username": username}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return {"ok": True, "username": username}
        except OSError as e:
            return {"ok": False, "error": str(e)}

    def clear_session(self) -> dict:
        try:
            if self._session_path.exists():
                self._session_path.unlink()
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True}

    # ------------------------------------------------------------------
    # Тема и обои
    # ------------------------------------------------------------------
    def get_theme(self) -> dict:
        default = {"theme": "dark", "wallpaper": "aurora"}
        try:
            if self._theme_config_path.exists():
                data = _read_json(self._theme_config_path)
                if isinstance(data, dict):
                    default.update({k: v for k, v in data.items()
                                    if k in ("theme", "wallpaper")})
        except Exception as e:
            _log(f"theme config read failed: {type(e).__name__}: {e}")
        return default

    def set_theme(self, theme: str | None = None,
                  wallpaper: str | None = None) -> dict:
        cur = self.get_theme()
        if theme in ("dark", "light"):
            cur["theme"] = theme
        if isinstance(wallpaper, str) and wallpaper:
            cur["wallpaper"] = wallpaper
        try:
            self._theme_config_path.write_text(
                json.dumps(cur, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, **cur}


# ==================================================================
# AppBridge
# ==================================================================
class AppBridge:
    ALLOWED_PROXY = {
        "get_system_stats",
        "get_volume", "set_volume", "toggle_mute",
        "get_wifi_state", "play_volume_feedback",
        "get_users", "create_user", "delete_user", "update_user",
        "authenticate_user",
        "get_current_session", "set_current_session", "clear_session",
        "get_theme", "set_theme",
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
        # Alt+F4, клик по системному крестику и т.п. → GTK delete-event
        # → pywebview events.closing. Возвращаем False — отменяем закрытие.
        # Программный window.destroy() (кнопка закрытия в titlebar)
        # через это событие не проходит, поэтому UI-кнопка работает как обычно.
        try:
            window.events.closing += self._block_wm_close
        except Exception as e:
            _log(f"closing handler not attached: {e}")

    def _block_wm_close(self):
        return False

    def start_backend(self) -> None:
        backend = self.app_dir / "app_backend.py"
        if not backend.is_file():
            return
        env = os.environ.copy()
        env["SIAMBA_APP_ID"]   = self.app_id
        env["SIAMBA_APP_DIR"]  = str(self.app_dir)
        env["SIAMBA_APP_DATA"] = str(
            Path.home() / ".local" / "share" / "siamba-os" / "apps" / self.app_id
        )
        env["SIAMBA_OS_ROOT"] = str(BASE_DIR)
        env["PYTHONUNBUFFERED"] = "1"

        self._proc = subprocess.Popen(
            [sys.executable, str(backend)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, cwd=str(self.app_dir), env=env,
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
                proc.terminate(); proc.wait(timeout=2)
            except Exception:
                try: proc.kill()
                except Exception: pass

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
        self._on_backend_exit()

    def _read_stderr(self):
        try:
            for line in self._proc.stderr:
                print(f"[{self.app_id}] {line.rstrip()}",
                      file=sys.stderr, flush=True)
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
        except AttributeError:
            self._send({"type": "proxy_response", "id": req_id,
                        "error": f"SystemAPI has no method: {method}"})
            return

        try:
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

    def call(self, method: str, params: dict | None = None):
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
        try:
            mf = _read_json(self.app_dir / "manifest.json")
        except Exception:
            mf = {}
        return {"id": self.app_id,
                "name": mf.get("name", self.app_id),
                "version": mf.get("version", "0.0.0")}

    def window_move(self, x, y):
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
# AppManager
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

    def install_app(self, package_path: str) -> dict:
        p = Path(package_path)
        if not p.is_file():
            return {"ok": False, "error": "file not found"}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
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
                manifest = _read_json(manifest_path)
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

    def list_apps(self) -> list:
        out = []
        for entry in sorted(self.apps_dir.iterdir()):
            if not entry.is_dir():
                continue
            mf_path = entry / "manifest.json"
            if not mf_path.is_file():
                continue
            try:
                mf = _read_json(mf_path)
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
                "bin":          f"siamba:{app_id}",
                "name":         mf.get("name", app_id),
                "version":      mf.get("version", "0.0.0"),
                "description":  mf.get("description", ""),
                "author":       mf.get("author", ""),
                "icon_path":    icon_url,
                "has_backend":  (entry / "app_backend.py").is_file(),
                "has_ui":       (entry / "ui" / "index.html").is_file(),
            })
        return out

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
            mf = _read_json(manifest_path)
        except Exception as e:
            return {"ok": False, "error": f"bad manifest: {e}"}

        win_cfg = mf.get("window") or {}
        bridge = AppBridge(app_id, app_dir, self.system_api)
        bridge.start_backend()

        try:
            window = webview.create_window(
                title=mf.get("name", app_id),
                url=ui_index.as_uri(),
                js_api=bridge,
                width=int(win_cfg.get("width", 900)),
                height=int(win_cfg.get("height", 600)),
                x=win_cfg.get("x"), y=win_cfg.get("y"),
                min_size=(420, 320), frameless=True,
                background_color=win_cfg.get("background", "#14141c"),
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

    def shutdown_all(self) -> None:
        """Закрывает все siamba-окна. Используется при logout."""
        with self._lock:
            wins    = list(self._windows.values())
            bridges = list(self._bridges.values())
            self._windows.clear()
            self._bridges.clear()
            self.system_api._siamba_apps.clear()
        for w in wins:
            try: w.destroy()
            except Exception: pass
        for b in bridges:
            try: b.stop_backend()
            except Exception: pass


# ==================================================================
# Точка входа
# ==================================================================
def main() -> int:
    if not LOGIN_INDEX.exists():
        _log(f"Login UI not found: {LOGIN_INDEX}")
        return 1

    api = SystemAPI()
    app_manager = AppManager(BASE_DIR, api)
    api.set_app_manager(app_manager)

    window = webview.create_window(
        title=WINDOW_TITLE,
        url=LOGIN_INDEX.as_uri(),
        js_api=api,
        width=1920, height=1080,
        fullscreen=not DEV_MODE,
        min_size=(800, 480),
        frameless=True,
    )
    api.set_main_window(window)
    
    def _block_main_close():
        return False

    try:
        window.events.closing += _block_main_close
    except Exception as e:
        _log(f"main closing handler not attached: {e}")

    webview.start(debug=DEVTOOLS)
    return 0


if __name__ == "__main__":
    sys.exit(main())