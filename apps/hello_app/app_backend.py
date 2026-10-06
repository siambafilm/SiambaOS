import os
import sys

sys.path.insert(0, os.environ.get("SIAMBA_OS_ROOT", ""))
import siamba_runtime as rt


@rt.method("greet")
def greet(name="мир"):
    return {"ok": True, "text": f"Привет, {name}!"}


@rt.method("get_stats")
def get_stats():
    """Прокси-вызов к ОС. Прямого доступа к системе у приложения нет."""
    try:
        stats = rt.proxy("get_system_stats")
        return {"ok": True, "stats": stats}
    except Exception as e:
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    rt.run()