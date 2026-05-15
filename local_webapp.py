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
  <title>Amethyst Webapp</title>
  <link rel="stylesheet" href="/styles.css">
</head>
<body>
  <aside class="sidebar">
    <div class="brand"><div class="logo">A</div><span>Amethyst</span></div>
    <nav>
      <button class="nav active" data-view="dashboard"><span>#</span><b data-i18n="dashboard">Dashboard</b></button>
      <button class="nav" data-view="multi"><span>∞</span><b>Multi-Instance</b></button>
      <button class="nav" data-view="brawlers"><span>=</span><b data-i18n="brawlers">Brawlers</b></button>
      <button class="nav" data-view="playstyles"><span>*</span><b data-i18n="playstyles">Playstyles</b></button>
      <button class="nav" data-view="history"><span>+</span><b data-i18n="history">History</b></button>
      <button class="nav" data-view="logging"><span>@</span><b data-i18n="logging">Logging</b></button>
      <button class="nav" data-view="settings"><span>:</span><b data-i18n="settings">Settings</b></button>
    </nav>
  </aside>

  <main class="app">
    <header class="topbar">
      <div><h1 id="pageTitle">Dashboard</h1></div>
      <div class="top-actions"><span id="webappStatus" class="pill">Connecting...</span></div>
    </header>

    <section id="dashboard" class="view active">
      <div class="grid two">
        <div class="panel hero-run">
          <div class="runbox">
            <div class="eyebrow">RUNTIME</div>
            <button id="startBtn" class="start">&gt; <span>START</span></button>
            <p id="startHint">Runtime controls are connected to the local bot state.</p>
          </div>
        </div>
        <div class="panel" id="activePlaystyle"></div>
      </div>
      <div id="runtimePanel" class="session-panel"></div>
    </section>

    <section id="multi" class="view">
      <div class="multi-toolbar panel">
        <div>
          <div class="eyebrow">MULTI-INSTANCE HUB</div>
          <h2>LDPlayer windows</h2>
          <p>Local dashboard compatibility view. Multi-worker management can be wired to the same API later.</p>
        </div>
        <div class="multi-actions">
          <button id="multiScan" class="secondary">Scan ADB</button>
          <button id="multiStartNext" class="primary">Start Next</button>
          <button id="multiResumeAll" class="secondary">Resume All</button>
          <button id="multiPauseAll" class="secondary">Pause All</button>
          <button id="multiStopAll" class="secondary danger-soft">Stop All</button>
        </div>
      </div>
      <div id="multiDevices" class="multi-devices"></div>
      <div id="multiGrid" class="multi-grid"></div>
      <div class="panel multi-log-panel">
        <div class="panel-head"><div><div class="eyebrow">INSTANCE LOGS</div><h2 id="multiLogTitle">Select instance</h2></div><button id="multiCopyLog" class="secondary">Copy Log</button></div>
        <pre id="multiLogs" class="multi-logs">Logs will appear here...</pre>
      </div>
    </section>

    <section id="brawlers" class="view">
      <div class="grid brawler-layout">
        <div class="panel">
          <div class="panel-head"><div><div class="eyebrow">BRAWLER QUEUE</div><h2>Select a brawler and add it to the run order</h2></div><div class="player-card"><b id="playerName">Player</b><small id="playerTag"></small></div></div>
          <label>SEARCH BRAWLERS</label>
          <input id="brawlerSearch" class="input wide" placeholder="Search by brawler name">
          <label>PLAYER TAG</label>
          <input id="playerTagInput" class="input wide" placeholder="#PLAYER">
          <div class="player-actions"><button id="loadQueue" class="secondary">Sync Player</button><span class="player-actions-spacer"></span><button id="pushAllBtn" type="button" class="secondary glow-action">Push all</button></div>
          <label class="check multi-brawler-toggle"><input id="brawlersMultiMode" type="checkbox"><span><b>Multi Instance</b><br>Separate tag, sync and queue for each LDPlayer window.</span></label>
          <div id="brawlerInstanceTabs" class="brawler-instance-tabs" hidden></div>
          <p id="playerLoadStatus" class="tiny-status"></p>
          <div id="brawlerGrid" class="brawler-grid"></div>
        </div>
        <aside class="panel sticky" id="brawlerEditor"></aside>
      </div>
    </section>

    <section id="playstyles" class="view">
      <div class="panel selected-zone">
        <div class="eyebrow">SELECTED</div>
        <div id="selectedPlaystyle" class="drop-zone">Runtime monitor</div>
      </div>
      <div class="toolbar playstyle-toolbar">
        <input id="playstyleSearch" class="input" placeholder="Search by playstyle, brawler, or gamemode">
        <button id="importPlaystyle" class="secondary">Import</button>
        <input id="playstyleFile" type="file" accept=".pyla" hidden>
      </div>
      <div class="eyebrow muted">LIBRARY</div>
      <div id="playstyleGrid" class="playstyle-grid"></div>
    </section>

    <section id="history" class="view">
      <div class="panel">
        <div class="panel-head">
          <div><div class="eyebrow">MATCH HISTORY</div><h2 id="historyTotal">0 total matches</h2><p id="historySummary"></p></div>
          <div class="segmented"><input id="historySearch" class="input" placeholder="Filter by brawler"><button class="active" data-sort="matches">Matches</button><button data-sort="rate">Win Rate</button><button data-sort="name">Name</button></div>
        </div>
        <div id="historyGrid" class="history-grid"></div>
      </div>
    </section>

    <section id="logging" class="view">
      <div id="loggingGrid" class="settings-grid"></div>
    </section>

    <section id="settings" class="view">
      <div id="settingsGrid" class="settings-grid"></div>
    </section>
  </main>

  <footer class="queuebar" id="queuebar">
    <div id="queueResizeHandle" class="queue-resize-handle" title="Drag to resize queue" aria-label="Drag to resize queue"></div>
    <div class="queue-content"><div class="eyebrow">QUEUE</div><small id="queueCount">0 brawlers ready</small><div id="queueItems" class="queue-items"></div></div>
    <button id="clearQueue" class="secondary">Clear Queue</button>
  </footer>

  <div id="pushAllModal" class="modal-backdrop" hidden>
    <div class="modal-card">
      <button id="pushAllClose" class="modal-close" aria-label="Close">×</button>
      <div class="eyebrow">PUSH ALL</div>
      <h2>Choose trophy target</h2>
      <p>This compatibility dashboard keeps queue editing local-only for now.</p>
      <div class="target-presets">
        <button data-push-target="250">250</button>
        <button data-push-target="500">500</button>
        <button data-push-target="750">750</button>
        <button data-push-target="1000" class="hot-target">1000</button>
      </div>
      <label>CUSTOM TARGET</label>
      <div class="custom-target-row">
        <input id="pushAllTarget" class="input wide" value="1000" inputmode="numeric">
        <button id="pushAllApply" class="primary">Build Queue</button>
      </div>
      <p id="pushAllStatus" class="tiny-status"></p>
    </div>
  </div>

  <script src="/app.js?v=20260513_onnx_provider_visibility"></script>
</body>
</html>
"""


STYLES_CSS = """:root {
  color-scheme: dark;
  --bg: #0a0515;
  --panel: #1a0d2a;
  --panel2: #251538;
  --line: #403056;
  --red: #d75cff;
  --red2: #8d2bbf;
  --hot: #ff4fb8;
  --pink: #ff6b9d;
  --purple: #9d4edd;
  --text: #f7f7f8;
  --muted: #b2a6c4;
  --green: #71f5a2;
  --queue-height: 132px;
  --queue-max-height: min(38vh, 360px);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background:
    radial-gradient(circle at 20% 30%, rgba(157,78,221,0.15) 0%, transparent 50%),
    radial-gradient(circle at 80% 20%, rgba(215,92,255,0.12) 0%, transparent 40%),
    radial-gradient(circle at 40% 80%, rgba(255,107,157,0.10) 0%, transparent 45%),
    radial-gradient(circle at 90% 90%, rgba(255,79,184,0.08) 0%, transparent 35%),
    linear-gradient(135deg, #0a0515 0%, #1a0d2a 50%, #251538 100%);
  color: var(--text);
  font: 15px/1.45 Inter, Segoe UI, Arial, sans-serif;
  position: relative;
  overflow-x: hidden;
}
body::before, body::after {
  content: '';
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: -1;
}
body::before {
  background:
    radial-gradient(circle at 25% 25%, rgba(215,92,255,0.03) 0%, transparent 30%),
    radial-gradient(circle at 75% 75%, rgba(255,107,157,0.03) 0%, transparent 30%);
  animation: float 20s ease-in-out infinite;
}
body::after {
  background:
    radial-gradient(circle at 50% 50%, rgba(157,78,221,0.02) 0%, transparent 40%),
    radial-gradient(circle at 10% 90%, rgba(255,79,184,0.02) 0%, transparent 35%);
  animation: float-reverse 25s ease-in-out infinite;
}
@keyframes float { 0%,100% { transform: translate(0,0) scale(1); } 33% { transform: translate(30px,-30px) scale(1.05); } 66% { transform: translate(-20px,20px) scale(.95); } }
@keyframes float-reverse { 0%,100% { transform: translate(0,0) scale(1); } 33% { transform: translate(-25px,25px) scale(1.03); } 66% { transform: translate(15px,-15px) scale(.97); } }
button, input, select { font: inherit; }
.sidebar { position: fixed; inset: 0 auto 0 0; width: 224px; background: #0b0712; border-right: 1px solid #2d2140; display: flex; flex-direction: column; z-index: 10; }
.brand { height: 82px; display: flex; align-items: center; gap: 12px; padding: 0 22px; border-bottom: 1px solid #2d2140; font-size: 25px; font-weight: 900; }
.logo { width: 34px; height: 34px; display: grid; place-items: center; background: linear-gradient(135deg, #241236, #5d174d); border: 1px solid #7c3ca8; box-shadow: 0 0 24px rgba(215,92,255,.38); }
nav { padding: 18px 12px; display: grid; gap: 7px; }
.nav { height: 44px; color: var(--muted); background: rgba(26,18,62,0.3); border: 1px solid rgba(215,92,255,0.2); border-radius: 8px; display: flex; align-items: center; gap: 14px; padding: 0 16px; cursor: pointer; text-align: left; transition: all .3s cubic-bezier(.4,0,.2,1); position: relative; overflow: hidden; }
.nav::before { content: ''; position: absolute; inset: 0 auto 0 -100%; width: 100%; background: linear-gradient(90deg, transparent, rgba(215,92,255,0.1), transparent); transition: left .4s; }
.nav:hover::before { left: 100%; }
.nav:hover { color: var(--text); background: rgba(26,18,62,.5); border-color: rgba(215,92,255,.4); transform: translateX(2px); }
.nav.active { color: white; background: linear-gradient(135deg, rgba(215,92,255,.3), rgba(157,78,221,.2)); border-color: var(--red); box-shadow: inset 4px 0 0 rgba(215,92,255,.3), 0 0 20px rgba(215,92,255,.2); }
.app { margin-left: 224px; padding: 24px 30px calc(var(--queue-height) + 34px); }
.topbar { height: 76px; display: flex; justify-content: space-between; align-items: start; }
.eyebrow { color: var(--red); font-size: 12px; font-weight: 900; letter-spacing: 3px; }
h1 { margin: 8px 0 0; font-size: 34px; line-height: 1; }
h2 { margin: 8px 0 0; font-size: 22px; }
p { color: var(--muted); margin: 8px 0 0; }
.top-actions { display: flex; gap: 10px; align-items: center; }
.pill { border: 1px solid var(--line); border-radius: 999px; padding: 8px 16px; color: var(--muted); background: #100a19; font-weight: 800; }
.view { display: none; opacity: 0; transform: translateY(20px); transition: all .3s cubic-bezier(.4,0,.2,1); }
.view.active { display: block; opacity: 1; transform: translateY(0); }
.grid { display: grid; gap: 24px; }
.two { grid-template-columns: minmax(0, 1fr) minmax(360px, 1fr); }
.brawler-layout { grid-template-columns: minmax(0, 1fr) 340px; align-items: start; }
.panel { background: rgba(26,13,42,.9); border: 1px solid rgba(215,92,255,.2); border-radius: 16px; padding: 24px; box-shadow: 0 22px 80px rgba(0,0,0,.3), 0 0 0 1px rgba(215,92,255,.1), inset 0 0 20px rgba(255,255,255,.05); transition: all .3s cubic-bezier(.4,0,.2,1); }
.panel:hover { border-color: rgba(215,92,255,.3); box-shadow: 0 25px 90px rgba(0,0,0,.4), 0 0 0 1px rgba(215,92,255,.2), inset 0 0 25px rgba(255,255,255,.08); }
.hero-run { min-height: 362px; border-color: rgba(215,92,255,.52); background: radial-gradient(circle at 50% 52%, rgba(215,92,255,.22), transparent 34%), rgba(18,13,29,.9); display: grid; place-items: center; }
.runbox { text-align: center; }
.start { width: 315px; height: 108px; border: 0; border-radius: 18px; color: white; background: linear-gradient(135deg, var(--hot) 0%, var(--red) 50%, var(--purple) 100%); font-size: 38px; font-weight: 950; cursor: pointer; box-shadow: 0 18px 45px rgba(215,92,255,.35), inset 0 0 20px rgba(255,255,255,.1); transition: all .3s cubic-bezier(.4,0,.2,1); position: relative; overflow: hidden; }
.start::before { content: ''; position: absolute; inset: 0 auto 0 -100%; width: 100%; background: linear-gradient(90deg, transparent, rgba(255,255,255,.2), transparent); transition: left .5s; }
.start:hover::before { left: 100%; }
.start:hover { transform: translateY(-2px); box-shadow: 0 20px 50px rgba(215,92,255,.4), inset 0 0 25px rgba(255,255,255,.15); }
.start:disabled, .disabled { background: #383a42 !important; color: #9da3af !important; box-shadow: none !important; cursor: not-allowed; }
.panel-head { display: flex; justify-content: space-between; gap: 20px; align-items: start; padding-bottom: 18px; border-bottom: 1px solid var(--line); margin-bottom: 18px; }
.player-card { min-width: 210px; background: #1b1230; border: 1px solid #62338a; border-radius: 8px; padding: 12px; }
.player-card small { display: block; color: var(--muted); }
label { display: block; color: var(--muted); font-weight: 900; font-size: 12px; letter-spacing: 1px; margin: 12px 0 7px; }
.input { height: 46px; background: #0d0714; color: white; border: 1px solid var(--line); border-radius: 6px; padding: 0 13px; font-weight: 800; }
.wide { width: 100%; }
.secondary { height: 46px; padding: 0 18px; margin-top: 12px; color: white; background: #211333; border: 1px solid #5e4078; border-radius: 6px; font-weight: 900; cursor: pointer; }
.primary { min-height: 46px; border: 0; border-radius: 7px; background: linear-gradient(135deg, var(--hot), var(--red)); color: white; font-weight: 950; cursor: pointer; box-shadow: 0 14px 34px rgba(215,92,255,.25); }
.brawler-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(88px, 1fr)); gap: 12px; margin-top: 18px; max-height: 560px; overflow: auto; padding-right: 6px; }
.brawler-card { border: 1px solid var(--line); background: #171022; color: white; border-radius: 8px; padding: 8px; text-align: center; cursor: pointer; font-weight: 900; }
.brawler-card.active { border-color: var(--red); box-shadow: inset 0 0 0 1px var(--red); }
.brawler-card img { width: 72px; height: 72px; object-fit: cover; border-radius: 7px; display: block; margin: 0 auto 6px; }
.sticky { position: sticky; top: 24px; }
.selected-zone { min-height: 260px; border-color: rgba(215,92,255,.55); display: grid; place-items: center; text-align: center; }
.drop-zone { width: min(540px, 100%); min-height: 170px; border: 1px solid rgba(215,92,255,.42); border-radius: 12px; display: grid; place-items: center; padding: 18px; color: var(--muted); }
.toolbar { margin: 24px 0; padding: 12px 18px; border: 1px solid var(--line); border-radius: 10px; background: rgba(16,17,22,.9); }
.playstyle-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 18px; }
.playstyle-grid, .history-grid, .settings-grid, .multi-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; margin-top: 16px; }
.setting-group, .history-card, .playstyle-card, .empty-card, .mi-card { background: #171022; border: 1px solid var(--line); border-radius: 12px; padding: 14px; }
.setting { display: grid; grid-template-columns: minmax(0,1fr) minmax(140px, 220px); gap: 16px; align-items: center; padding: 12px 14px; border: 1px solid var(--line); border-radius: 8px; margin-top: 12px; background: #171022; }
.queuebar { position: fixed; left: 224px; right: 0; bottom: 0; height: min(var(--queue-height), var(--queue-max-height)); min-height: 86px; max-height: var(--queue-max-height); background: rgba(26,13,42,.8); border-top: 1px solid rgba(215,92,255,.3); display: flex; justify-content: space-between; align-items: start; gap: 18px; padding: 18px 30px; z-index: 5; overflow: hidden; box-shadow: 0 -10px 40px rgba(0,0,0,.2); }
.queue-resize-handle { position: absolute; left: 0; right: 0; top: -7px; height: 14px; cursor: ns-resize; z-index: 2; }
.queue-resize-handle::before { content: ''; position: absolute; left: 30px; right: 30px; top: 4px; height: 10px; border-radius: 999px; background: linear-gradient(90deg, transparent, rgba(215,92,255,.85), transparent); box-shadow: 0 0 12px rgba(215,92,255,.45); opacity: .65; }
.queue-content { min-width: 0; flex: 1; overflow: hidden; padding-right: 8px; }
.queue-items { display: flex; gap: 10px; margin-top: 10px; flex-wrap: wrap; max-height: calc(var(--queue-height) - 72px); overflow: auto; padding: 0 6px 10px 0; }
.queue-item { min-width: 190px; display: grid; grid-template-columns: 44px 1fr; gap: 9px; padding: 9px 28px 9px 9px; border: 1px solid rgba(215,92,255,.3); border-radius: 8px; background: rgba(26,13,42,.7); position: relative; }
.queue-remove { position: absolute; top: 6px; right: 6px; width: 20px; height: 20px; border: 1px solid #6f4f8c; border-radius: 50%; background: #2a1740; color: white; cursor: pointer; font-weight: 900; line-height: 16px; }
.queue-item img { width: 44px; height: 44px; border-radius: 6px; }
.segmented { display: flex; gap: 10px; align-items: center; }
.segmented .input { width: 220px; }
.segmented button { border: 1px solid var(--line); padding: 0 16px; border-radius: 6px; height: 42px; color: var(--muted); background: #0d0714; font-weight: 900; cursor: pointer; }
.segmented button.active { color: white; background: #2a1740; }
.session-panel { margin-top: 24px; border: 1px solid #69458a; border-left: 3px solid var(--hot); border-radius: 10px; background: linear-gradient(180deg, #160d24, #0f0918); padding: 14px; box-shadow: 0 22px 70px rgba(0,0,0,.28); }
.session-cards { display: grid; grid-template-columns: 1.05fr 1fr 1fr 1fr; gap: 8px; }
.metric { min-height: 58px; border: 1px solid #4a3561; border-radius: 8px; padding: 10px 12px; background: #1a1028; }
.metric small { display: block; color: #bdaed2; font-size: 11px; letter-spacing: 1px; }
.metric b { display: block; margin-top: 5px; font-size: 16px; word-break: break-word; }
.wide-metric { grid-row: span 2; }
.progress { height: 5px; background: #2d1f3b; border-radius: 99px; margin: 10px 0 6px; overflow: hidden; }
.progress span { display: block; height: 100%; background: linear-gradient(90deg, var(--hot), var(--red)); border-radius: inherit; }
.tiny-status { min-height: 20px; font-size: 13px; color: var(--muted); }
.player-actions { width: 100%; display: grid; grid-template-columns: auto minmax(16px,1fr) auto; align-items: center; gap: 12px; margin-top: 12px; }
.player-actions .secondary { margin-top: 0; }
.glow-action { margin-left: auto; border-color: rgba(215,92,255,.72); background: linear-gradient(135deg, #26143d, #34133a); box-shadow: 0 0 18px rgba(215,92,255,.18); }
.modal-backdrop { position: fixed; inset: 0; z-index: 20; background: rgba(5,3,10,.72); backdrop-filter: blur(8px); display: grid; place-items: center; padding: 24px; }
.modal-backdrop[hidden] { display: none; }
.modal-card { position: relative; width: min(560px,100%); border: 1px solid rgba(215,92,255,.55); border-radius: 18px; padding: 26px; background: radial-gradient(circle at 80% 0%, rgba(255,79,184,.16), transparent 34%), linear-gradient(180deg, #1a1028, #0d0714); box-shadow: 0 30px 90px rgba(0,0,0,.55), 0 0 45px rgba(215,92,255,.18); }
.modal-close { position: absolute; right: 16px; top: 14px; width: 34px; height: 34px; border: 1px solid #6b4d87; border-radius: 9px; background: #211333; color: white; font-size: 24px; cursor: pointer; }
.target-presets { display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; margin: 22px 0 14px; }
.target-presets button { height: 58px; border: 1px solid #6b4d87; border-radius: 12px; background: #171022; color: white; cursor: pointer; font-weight: 950; font-size: 18px; }
.target-presets button:hover, .target-presets .hot-target { border-color: var(--red); background: linear-gradient(135deg, #30164a, #431743); }
.custom-target-row { display: grid; grid-template-columns: 1fr 150px; gap: 12px; align-items: center; }
.multi-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 18px; margin-bottom: 16px; }
.multi-actions { display: flex; flex-wrap: wrap; gap: 10px; justify-content: flex-end; }
.danger-soft { border-color: #8f3546 !important; color: #ffd1d8 !important; background: #32131d !important; }
.multi-devices { display: flex; flex-wrap: wrap; gap: 10px; margin: 0 0 16px; }
.multi-logs { min-height: 220px; max-height: 420px; overflow: auto; white-space: pre-wrap; word-break: break-word; padding: 14px; border: 1px solid #2d3848; border-radius: 10px; background: #070a0f; color: #cbd5e1; font-size: 12px; }
@media (max-width: 960px) { .sidebar { position: static; width: auto; } .app, .queuebar { margin-left: 0; left: 0; } .two, .brawler-layout, .settings-grid, .session-cards { grid-template-columns: 1fr; } .playstyle-toolbar, .multi-toolbar { align-items: stretch; flex-direction: column; } .queuebar { position: static; height: auto; max-height: none; } .app { padding-bottom: 30px; } }
"""


APP_JS = """let state = { runtime: {}, brawlers: [], queue: [], config: null };
let selectedBrawler = null;
let selectedPlaystyle = "Runtime monitor";
const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const res = await fetch(path, { cache: "no-store", headers: { "Content-Type": "application/json" }, ...options });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) throw new Error(data.error || "Request failed");
  return data;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
}

function switchView(view) {
  document.querySelectorAll('.view').forEach(el => el.classList.toggle('active', el.id === view));
  document.querySelectorAll('.nav').forEach(el => el.classList.toggle('active', el.dataset.view === view));
  const titles = { dashboard: 'Dashboard', multi: 'Multi-Instance', brawlers: 'Brawlers', playstyles: 'Playstyles', history: 'History', logging: 'Logging', settings: 'Settings' };
  $('pageTitle').textContent = titles[view] || view;
}

function runtimeValue(...keys) {
  for (const key of keys) {
    const value = state.runtime?.[key];
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return '';
}

function renderRuntimePanel() {
  const runtime = state.runtime || {};
  const status = runtime.performanceStatus || `${runtime.ips || '0.00'} IPS | ONNX: ${runtime.onnxBackend || 'unknown'}`;
  const running = runtime.runtimeControl !== 'paused';
  $('webappStatus').textContent = running ? 'Running' : 'Paused';
  $('startBtn').querySelector('span').textContent = running ? 'STOP' : 'START';
  $('startHint').textContent = running ? 'Bot is running. Stop pauses movement safely.' : 'Bot is paused. Start resumes movement.';
  $('activePlaystyle').innerHTML = `<div class="eyebrow">ACTIVE STATUS</div><h2>${escapeHtml(status)}</h2><p>Live values are read from PylaAi-XXZ runtime.</p>`;
  $('runtimePanel').innerHTML = `
    <div class="session-cards">
      <div class="metric wide-metric"><small>ACTIVE SESSION</small><b>${escapeHtml(status)}</b><div class="progress"><span style="width:${running ? 100 : 25}%"></span></div><strong>${escapeHtml(runtime.runtimeControl || 'running')}</strong></div>
      <div class="metric"><small>CURRENT BRAWLER</small><b>${escapeHtml(runtime.brawler || runtime.currentBrawler || 'none')}</b></div>
      <div class="metric"><small>STATE</small><b>${escapeHtml(runtime.state || 'unknown')}</b></div>
      <div class="metric"><small>IPS</small><b>${escapeHtml(runtime.ips || '0.00')}</b></div>
      <div class="metric"><small>ONNX BACKEND</small><b>${escapeHtml(runtime.onnxBackend || 'unknown')}</b></div>
      <div class="metric"><small>FEED FPS</small><b>${escapeHtml(runtime.feed_fps || runtime.feedFps || '0.00')}</b></div>
      <div class="metric"><small>EMULATOR</small><b>${escapeHtml(runtime.emulator || 'unknown')}</b></div>
    </div>`;
}

function brawlerIcon(name) {
  const clean = String(name || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  return `/assets/brawler_icons/${clean}.png`;
}

function renderBrawlers() {
  const query = ($('brawlerSearch')?.value || '').toLowerCase();
  const brawlers = (state.brawlers || []).filter(name => String(name).toLowerCase().includes(query));
  $('brawlerGrid').innerHTML = brawlers.map(name => `<button class="brawler-card ${selectedBrawler === name ? 'active' : ''}" data-brawler="${escapeHtml(name)}"><img src="${brawlerIcon(name)}" onerror="this.style.visibility='hidden'"><span>${escapeHtml(name)}</span></button>`).join('') || `<div class="empty-card"><b>No brawlers found</b><p>Local brawler list is empty.</p></div>`;
  document.querySelectorAll('[data-brawler]').forEach(btn => btn.onclick = () => { selectedBrawler = btn.dataset.brawler; renderBrawlers(); renderBrawlerEditor(); });
}

function renderBrawlerEditor() {
  const host = $('brawlerEditor');
  if (!selectedBrawler) {
    host.innerHTML = `<div class="eyebrow">SELECTED BRAWLER</div><h2>No brawler selected</h2><p>Choose a brawler from the list. Queue editing in this compatibility view is local to the browser.</p>`;
    return;
  }
  host.innerHTML = `<div class="eyebrow">SELECTED BRAWLER</div><h2>${escapeHtml(selectedBrawler)}</h2><p>Add this brawler to the local visual queue.</p><label>TARGET TROPHIES</label><input id="targetAmount" class="input wide" value="1000"><button id="updateQueue" class="primary">Update Queue Entry</button>`;
  $('updateQueue').onclick = () => {
    state.queue = state.queue.filter(row => row.brawler !== selectedBrawler);
    state.queue.push({ brawler: selectedBrawler, push_until: Number($('targetAmount').value) || 1000, type: 'trophies', trophies: 0 });
    renderQueue();
  };
}

function renderQueue() {
  $('queueCount').textContent = `${state.queue.length} brawler${state.queue.length === 1 ? '' : 's'} ready`;
  $('queueItems').innerHTML = state.queue.map(row => `<div class="queue-item"><button class="queue-remove" data-remove="${escapeHtml(row.brawler)}">x</button><img src="${brawlerIcon(row.brawler)}"><div><b>${escapeHtml(row.brawler)}</b><small>Current ${escapeHtml(row.type)}: ${escapeHtml(row[row.type] || 0)}</small><small>Target ${escapeHtml(row.type)}: ${escapeHtml(row.push_until)}</small></div></div>`).join('');
  document.querySelectorAll('[data-remove]').forEach(btn => btn.onclick = () => { state.queue = state.queue.filter(row => row.brawler !== btn.dataset.remove); renderQueue(); });
}

function renderSettings() {
  const configs = state.config?.configs || {};
  $('settingsGrid').innerHTML = Object.entries(configs).map(([name, data]) => `<div class="setting-group"><div class="eyebrow">${escapeHtml(name.toUpperCase())}</div>${Object.entries(data || {}).map(([key, value]) => `<div class="setting"><div><b>${escapeHtml(key.replaceAll('_', ' '))}</b><p>Local config value</p></div><input class="input" value="${escapeHtml(typeof value === 'object' ? JSON.stringify(value) : value)}" readonly></div>`).join('')}</div>`).join('');
}

function renderLogging() {
  const configs = state.config?.configs || {};
  const logging = { ...(configs['discord_config.toml'] || {}), ...(configs['telegram_config.toml'] || {}) };
  $('loggingGrid').innerHTML = `<div class="setting-group"><div class="eyebrow">LOGGING</div>${Object.entries(logging).map(([key, value]) => `<div class="setting"><div><b>${escapeHtml(key.replaceAll('_', ' '))}</b><p>Notification setting</p></div><input class="input" value="${escapeHtml(typeof value === 'object' ? JSON.stringify(value) : value)}" readonly></div>`).join('') || '<p>No logging config found.</p>'}</div>`;
}

function renderPlaystyles() {
  $('selectedPlaystyle').textContent = selectedPlaystyle;
  $('playstyleGrid').innerHTML = `<div class="playstyle-card active"><h2>Runtime monitor</h2><p>Built-in web runtime dashboard style.</p><div class="mode-tags"><span class="tag">all</span></div></div>`;
}

function renderHistory() {
  $('historyTotal').textContent = 'Local runtime history';
  $('historySummary').textContent = 'Match result posts are stored by the local API when received.';
  $('historyGrid').innerHTML = `<div class="history-card"><h2>Waiting for match data</h2><p>The bot can post results to /api/brawlers.</p></div>`;
}

function renderMulti() {
  $('multiDevices').innerHTML = `<div class="empty-devices">Multi-instance controls are visible for design compatibility.</div>`;
  $('multiGrid').innerHTML = `<div class="empty-card"><b>No active bot instances</b><p>This local server currently controls the main bot runtime.</p></div>`;
}

async function refreshRuntime() {
  try {
    state.runtime = await api('/api/runtime');
    renderRuntimePanel();
  } catch (err) {
    $('webappStatus').textContent = 'Offline';
    $('runtimePanel').innerHTML = `<div class="empty-card"><b>Runtime unavailable</b><p>${escapeHtml(err.message)}</p></div>`;
  }
}

async function loadConfig() {
  state.config = await api('/api/config');
  renderSettings();
  renderLogging();
}

async function loadBrawlers() {
  try {
    const data = await api('/api/get_brawler_list', { method: 'POST', body: '{}' });
    state.brawlers = data.brawlers || [];
  } catch (_) {
    state.brawlers = [];
  }
  renderBrawlers();
  renderBrawlerEditor();
}

async function control(action) {
  const data = await api('/api/control', { method: 'POST', body: JSON.stringify({ action }) });
  await refreshRuntime();
  return data;
}

async function startBot() {
  const paused = state.runtime?.runtimeControl === 'paused';
  $('startBtn').disabled = true;
  try {
    await control(paused ? 'resume' : 'pause');
  } finally {
    $('startBtn').disabled = false;
  }
}

function bindUi() {
  document.addEventListener('click', ev => {
    const nav = ev.target.closest('[data-view]');
    if (nav) switchView(nav.dataset.view);
  });
  $('startBtn').onclick = startBot;
  $('brawlerSearch').oninput = renderBrawlers;
  $('clearQueue').onclick = () => { state.queue = []; renderQueue(); };
  $('pushAllBtn').onclick = () => { $('pushAllModal').hidden = false; $('pushAllStatus').textContent = 'Push all is visual-only in this local compatibility page.'; };
  $('pushAllClose').onclick = () => { $('pushAllModal').hidden = true; };
  $('pushAllApply').onclick = () => { $('pushAllStatus').textContent = 'Queue builder requires synced player data in the full Amethyst API.'; };
  $('multiPauseAll').onclick = () => control('pause');
  $('multiResumeAll').onclick = () => control('resume');
  $('multiStopAll').onclick = () => control('pause');
  $('multiScan').onclick = renderMulti;
  $('multiStartNext').onclick = () => control('resume');
}

async function init() {
  bindUi();
  renderPlaystyles();
  renderHistory();
  renderMulti();
  renderQueue();
  await Promise.allSettled([refreshRuntime(), loadConfig(), loadBrawlers()]);
  setInterval(refreshRuntime, 1000);
}

init().catch(err => alert(err.message));
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
                if path == "/styles.css":
                    self._send_bytes(STYLES_CSS.encode("utf-8"), "text/css; charset=utf-8")
                    return
                if path == "/app.js":
                    self._send_bytes(APP_JS.encode("utf-8"), "application/javascript; charset=utf-8")
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
