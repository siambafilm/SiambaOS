"""
Бэкенд приложения «Настройки».

Все привилегированные действия (пользователи, тема) идут через
siamba_runtime.proxy(...) → AppBridge → SystemAPI. Прямого доступа
к системе тут нет.
"""
import sys
from pathlib import Path

# Подключаем siamba_runtime из корня SIamba OS
ROOT = Path(__file__).resolve().parents[2]   # apps/settings_app/ -> apps/ -> root
sys.path.insert(0, str(ROOT))

import siamba_runtime as rt   # noqa: E402


# ------------------------------------------------------------------
# Пользователи
# ------------------------------------------------------------------
@rt.method("users.list")
def users_list():
    return {"ok": True, "users": rt.proxy("get_users")}


@rt.method("users.delete")
def users_delete(username: str):
    return rt.proxy("delete_user", username=username)


@rt.method("users.create")
def users_create(username: str,
                 password: str = "",
                 display_name: str = "",
                 require_password: bool = True):
    return rt.proxy("create_user",
                    username=username,
                    password=password,
                    display_name=display_name or username,
                    require_password=bool(require_password))


@rt.method("users.update")
def users_update(username: str,
                 display_name: str | None = None,
                 password: str | None = None,
                 require_password: bool | None = None):
    kwargs = {"username": username}
    if display_name is not None:
        kwargs["display_name"] = display_name
    if password is not None and password != "":
        kwargs["password"] = password
    if require_password is not None:
        kwargs["require_password"] = bool(require_password)
    return rt.proxy("update_user", **kwargs)


# ------------------------------------------------------------------
# Тема
# ------------------------------------------------------------------
@rt.method("theme.get")
def theme_get():
    return {"ok": True, **rt.proxy("get_theme")}


@rt.method("theme.set")
def theme_set(theme: str | None = None, wallpaper: str | None = None):
    kwargs = {}
    if theme is not None:      kwargs["theme"] = theme
    if wallpaper is not None:  kwargs["wallpaper"] = wallpaper
    return rt.proxy("set_theme", **kwargs)


if __name__ == "__main__":
    rt.run()