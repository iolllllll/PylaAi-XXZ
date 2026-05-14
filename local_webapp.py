from __future__ import annotations

import hashlib
import json
import mimetypes
import socket
import threading
import time
import tomllib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from runtime_control import PAUSED, RUNNING, read_state, write_state

DEFAULT_WEBAPP_PORT = 8765


def _config_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def load_toml_as_dict(path: str | Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except OSError:
        return {}


def load_brawlers_info() -> dict[str, Any]:
    try:
        with open("cfg/brawlers_info.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def calculate_sha256(file_path: str | Path) -> str:
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def current_pyla_version() -> str:
    return str(load_toml_as_dict("cfg/general_config.toml").get("pyla_version", ""))


SECRET_KEYWORDS = ("token", "password", "webhook", "secret", "api_key", "developer_email", "key")


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PylaAi-XXZ Web Runtime</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, Arial, sans-serif; background: #101014; color: #f4f4f5; }
    body { margin: 0; padding: 24px; }
    main { max-width: 1120px; margin: 0 auto; display: grid; gap: 16px; }
    .card { background: #1b1b22; border: 1px solid #2f2f3a; border-radius: 14px; padding: 18px; box-shadow: 0 10px 30px #0005; }
    h1, h2 { margin: 0 0 12px; }
    .status { font-size: clamp(28px, 6vw, 56px); font-weight: 800; line-height: 1.05; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }
    .metric { background: #24242d; border-radius: 10px; padding: 12px; }
    .label { color: #a1a1aa; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }
    .value { font-size: 22px; font-weight: 700; margin-top: 4px; word-break: break-word; }
    button { border: 0; border-radius: 10px; padding: 11px 16px; font-weight: 800; color: #111; background: #a7f3d0; cursor: pointer; margin-right: 8px; margin-bottom: 8px; }
    button.stop { background: #fecaca; }
    button.restart { background: #fde68a; }
    pre { white-space: pre-wrap; word-break: break-word; max-height: 420px; overflow: auto; background: #101014; padding: 12px; border-radius: 10px; }
    a { color: #93c5fd; }
  </style>
</head>
<body>
<main>
  <section class="card">
    <h1>PylaAi-XXZ Web Runtime</h1>
    <div id="performance" class="status">Loading...</div>
  </section>
  <section class="card">
    <h2>Control</h2>
    <button onclick="control('resume')">Resume</button>
    <button class="stop" onclick="control('pause')">Pause</button>
    <button class="restart" onclick="control('restart_game')">Restart game</button>
    <span id="controlResult"></span>
  </section>
  <section class="card">
    <h2>Runtime</h2>
    <div class="grid" id="metrics"></div>
  </section>
  <section class="card">
    <h2>All local parameters</h2>
    <pre id="config">Loading...</pre>
  </section>
</main>
<script>
const metricKeys = ['state', 'runtimeControl', 'ips', 'feed_fps', 'feedFps', 'onnxBackend', 'emulator', 'adb_device', 'brawler', 'target'];
function renderMetrics(data) {
  const metrics = document.getElementById('metrics');
  metrics.innerHTML = '';
  metricKeys.forEach((key) => {
    const value = data[key];
    if (value === undefined || value === null || value === '') return;
    const el = document.createElement('div');
    el.className = 'metric';
    el.innerHTML = `<div class="label">${key}</div><div class="value">${value}</div>`;
    metrics.appendChild(el);
  });
}
async function refresh() {
  try {
    const runtime = await fetch('/api/runtime').then((r) => r.json());
    document.getElementById('performance').textContent = runtime.performanceStatus || `${runtime.ips || '0.00'} IPS | ONNX: ${runtime.onnxBackend || 'unknown'}`;
    renderMetrics(runtime);
  } catch (e) {
    document.getElementById('performance').textContent = 'Runtime unavailable';
  }
}
async function refreshConfig() {
  try {
    const cfg = await fetch('/api/config').then((r) => r.json());
    document.getElementById('config').textContent = JSON.stringify(cfg, null, 2);
  } catch (e) {
    document.getElementById('config').textContent = 'Config unavailable';
  }
}
async function control(action) {
  const result = document.getElementById('controlResult');
  result.textContent = 'Sending...';
  try {
    const response = await fetch('/api/control', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action})});
    const data = await response.json();
    result.textContent = data.ok ? `OK: ${data.state || data.action}` : `Error: ${data.error}`;
    refresh();
  } catch (e) {
    result.textContent = `Error: ${e}`;
  }
}
refresh();
refreshConfig();
setInterval(refresh, 1000);
</script>
</body>
</html>
"""


def _json_default(value: Any):
    return str(value)


def _safe_path(root: Path, requested: str) -> Path | None:
    requested_path = (root / unquote(requested).lstrip("/")).resolve()
    try:
        requested_path.relative_to(root.resolve())
    except ValueError:
        return None
    return requested_path


def _redact_config(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {nested_key: _redact_config(nested_value, nested_key) for nested_key, nested_value in value.items()}
    if any(secret in key.lower() for secret in SECRET_KEYWORDS) and value not in ("", None):
        return "***redacted***"
    return value


def _lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def webapp_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config if config is not None else load_toml_as_dict("cfg/general_config.toml")
    allow_lan = _config_bool(config.get("webapp_allow_lan"), False)
    host = str(config.get("webapp_host") or "").strip()
    if not host:
        host = "0.0.0.0" if allow_lan else "127.0.0.1"
    try:
        port = int(config.get("webapp_port", DEFAULT_WEBAPP_PORT))
    except (TypeError, ValueError):
        port = DEFAULT_WEBAPP_PORT
    return {
        "enabled": _config_bool(config.get("webapp_enabled"), True),
        "host": host,
        "port": port,
        "allow_lan": allow_lan or host in ("0.0.0.0", "::"),
    }


class LocalWebAppServer:
    def __init__(
            self,
            state_path: str | Path | None = None,
            status_provider: Callable[[], dict[str, Any]] | None = None,
            restart_game_callback: Callable[[], Any] | None = None,
            config_loader: Callable[[], dict[str, Any]] | None = None,
    ):
        self.state_path = Path(state_path) if state_path else None
        self.status_provider = status_provider
        self.restart_game_callback = restart_game_callback
        self.config_loader = config_loader or (lambda: load_toml_as_dict("cfg/general_config.toml"))
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.url: str | None = None
        self.lan_url: str | None = None

    def start(self) -> bool:
        settings = webapp_settings(self.config_loader())
        if not settings["enabled"]:
            return False
        if self.thread and self.thread.is_alive():
            return True

        handler = self._handler_class()
        try:
            self.server = ThreadingHTTPServer((settings["host"], settings["port"]), handler)
        except OSError as e:
            print(f"Local webapp failed to start on {settings['host']}:{settings['port']}: {e}")
            return False

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        bound_port = self.server.server_address[1]
        display_host = "127.0.0.1" if settings["host"] in ("0.0.0.0", "::", "") else settings["host"]
        self.url = f"http://{display_host}:{bound_port}"
        self.lan_url = f"http://{_lan_ip()}:{bound_port}" if settings["allow_lan"] else None
        print(f"Local webapp running at {self.url}")
        if self.lan_url:
            print(f"Local network webapp URL: {self.lan_url}")
        return True

    def close(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self.server = None
        self.thread = None

    def runtime_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        runtime_path = Path("logs/web_runtime.json")
        if runtime_path.exists():
            try:
                payload.update(json.loads(runtime_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
        if self.status_provider:
            try:
                payload.update(self.status_provider() or {})
            except Exception as e:
                payload["statusProviderError"] = str(e)
        if self.state_path:
            payload["runtimeControl"] = read_state(self.state_path)
        payload.setdefault("onnxBackend", "unknown")
        payload.setdefault("performanceStatus", f"{payload.get('ips') or '0.00'} IPS | ONNX: {payload.get('onnxBackend')}")
        payload["updatedAt"] = time.time()
        return payload

    def config_payload(self) -> dict[str, Any]:
        cfg_dir = Path("cfg")
        configs: dict[str, Any] = {}
        if cfg_dir.exists():
            for config_path in sorted(cfg_dir.glob("*.toml")):
                try:
                    configs[config_path.name] = _redact_config(load_toml_as_dict(str(config_path)))
                except Exception as e:
                    configs[config_path.name] = {"error": str(e)}
        return {
            "version": current_pyla_version(),
            "webapp": webapp_settings(self.config_loader()),
            "configs": configs,
        }

    def control(self, action: str) -> dict[str, Any]:
        action = str(action or "").strip().lower()
        if action in ("pause", "stop"):
            if not self.state_path:
                return {"ok": False, "error": "runtime control state path is unavailable"}
            write_state(self.state_path, PAUSED)
            return {"ok": True, "state": PAUSED}
        if action in ("resume", "start"):
            if not self.state_path:
                return {"ok": False, "error": "runtime control state path is unavailable"}
            write_state(self.state_path, RUNNING)
            return {"ok": True, "state": RUNNING}
        if action in ("restart_game", "restart"):
            if not self.restart_game_callback:
                return {"ok": False, "error": "restart callback is unavailable"}
            result = self.restart_game_callback()
            return {"ok": bool(result), "action": action}
        return {"ok": False, "error": f"unknown action: {action}"}

    def _handler_class(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "PylaWebApp/1.0"

            def log_message(self, fmt: str, *args: Any) -> None:
                return

            def _send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
                body = json.dumps(data, default=_json_default).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _send_bytes(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self) -> dict[str, Any]:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length <= 0:
                    return {}
                raw = self.rfile.read(length).decode("utf-8")
                if not raw:
                    return {}
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    parsed = parse_qs(raw)
                    return {key: values[-1] if values else "" for key, values in parsed.items()}

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/") or "/"
                if path == "/":
                    self._send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
                    return
                if path in ("/health", "/api/health"):
                    self._send_json({"ok": True, "version": current_pyla_version()})
                    return
                if path in ("/runtime", "/api/runtime"):
                    self._send_json(app.runtime_payload())
                    return
                if path in ("/config", "/api/config"):
                    self._send_json(app.config_payload())
                    return
                if path in ("/check_version", "/api/check_version"):
                    self._send_json({"version": current_pyla_version()})
                    return
                if path in ("/get_discord_link", "/api/get_discord_link"):
                    self._send_json({"link": "https://discord.gg/xUusk3fw4A"})
                    return
                if path in ("/get_wall_model_hash", "/api/get_wall_model_hash"):
                    model_path = Path("models/tileDetector.onnx")
                    self._send_json({"hash": calculate_sha256(model_path) if model_path.exists() else ""})
                    return
                if path in ("/get_wall_model_classes", "/api/get_wall_model_classes"):
                    classes = load_toml_as_dict("cfg/bot_config.toml").get("wall_model_classes", [])
                    self._send_json({"classes": classes})
                    return
                if path in ("/get_wall_model_file", "/api/get_wall_model_file"):
                    model_path = Path("models/tileDetector.onnx")
                    if not model_path.exists():
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    self._send_bytes(model_path.read_bytes(), "application/octet-stream")
                    return
                if path.startswith("/assets/"):
                    asset_path = _safe_path(Path("api/assets"), path.removeprefix("/assets/"))
                    if not asset_path or not asset_path.is_file():
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    content_type = mimetypes.guess_type(str(asset_path))[0] or "application/octet-stream"
                    self._send_bytes(asset_path.read_bytes(), content_type)
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/") or "/"
                data = self._read_json()
                if path in ("/api/control", "/control"):
                    self._send_json(app.control(data.get("action")))
                    return
                if path in ("/get_brawler_list", "/api/get_brawler_list"):
                    self._send_json({"brawlers": list(load_brawlers_info().keys())}, HTTPStatus.CREATED)
                    return
                if path in ("/get_brawler_info", "/api/get_brawler_info"):
                    brawler_name = str(data.get("brawler_name") or "")
                    self._send_json({"info": load_brawlers_info().get(brawler_name, {})})
                    return
                if path in ("/check_user", "/api/check_user"):
                    self._send_json({"exists": True})
                    return
                if path in ("/api/brawlers", "/brawlers"):
                    results_path = Path("logs/web_match_results.json")
                    results_path.parent.mkdir(exist_ok=True)
                    existing = []
                    if results_path.exists():
                        try:
                            existing = json.loads(results_path.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError):
                            existing = []
                    existing.append({"receivedAt": time.time(), "data": data})
                    results_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
                    self._send_json({"ok": True})
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

        return Handler
