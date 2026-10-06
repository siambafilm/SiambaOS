"""
siamba_runtime — минимальный JSON-RPC-рантайм для бэкендов SIamba-приложений.

Протокол с родителем (main.py) через stdin/stdout:
  Родитель → app: {"type":"call","id":N,"method":"name","params":{...}}
  App → родитель: {"type":"response","id":N,"result":...}

  App → родитель: {"type":"proxy","id":M,"method":"name","params":{...}}
  Родитель → app: {"type":"proxy_response","id":M,"result":... | "error":...}
"""

import json
import sys
import threading

_handlers: dict[str, callable] = {}
_proxy_pending: dict[int, dict] = {}
_proxy_next_id = 1
_stdout_lock = threading.Lock()


def method(name: str):
    """Декоратор регистрации RPC-метода, доступного из UI приложения."""
    def deco(fn):
        _handlers[name] = fn
        return fn
    return deco


def proxy(method_name: str, **params):
    """
    Вызвать привилегированный метод ОС. Apps НЕ имеют прямого доступа
    к Linux — все системные вызовы идут через этот мост и фильтруются
    на стороне родителя (AppBridge.ALLOWED).
    """
    global _proxy_next_id
    with _stdout_lock:
        req_id = _proxy_next_id
        _proxy_next_id += 1

    evt = threading.Event()
    fut = {"event": evt, "result": None}
    _proxy_pending[req_id] = fut

    _write({"type": "proxy", "id": req_id,
            "method": method_name, "params": params})

    if not evt.wait(timeout=10):
        _proxy_pending.pop(req_id, None)
        raise RuntimeError(f"proxy timeout: {method_name}")

    resp = fut["result"]
    if resp.get("error") and "result" not in resp:
        raise RuntimeError(resp["error"])
    return resp.get("result")


def log(*args):
    """Лог в stderr родителя — виден в терминале."""
    print("[APP]", *args, file=sys.stderr, flush=True)


def _write(msg: dict):
    with _stdout_lock:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _reader_loop():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        _handle(msg)


def _handle(msg: dict):
    kind = msg.get("type")

    if kind == "call":
        req_id = msg.get("id")
        handler = _handlers.get(msg.get("method"))
        if not handler:
            _write({"type": "response", "id": req_id,
                    "result": {"ok": False,
                               "error": f"unknown method: {msg.get('method')}"}})
            return
        try:
            params = msg.get("params") or {}
            result = handler(**params) if isinstance(params, dict) else handler(*params)
        except Exception as e:
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        _write({"type": "response", "id": req_id, "result": result})

    elif kind == "proxy_response":
        fut = _proxy_pending.pop(msg.get("id"), None)
        if fut:
            fut["result"] = msg
            fut["event"].set()


def run():
    """Блокирующий цикл. Вызывается в конце app_backend.py."""
    threading.Thread(target=_reader_loop, daemon=True).start()
    try:
        while True:
            threading.Event().wait(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()