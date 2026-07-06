"""
FIBA 3x3 TV feed engine.

Encapsulates the WebSocket connection to the FIBA TvFeedApiV4: subscribe once,
retry authentication on rejection, parse the single game's status, and write it
to data.xml. Exposes a thread-safe status snapshot (get_status) that a GUI can
poll to show what's happening. No console/printing - this is meant to be driven
by a UI.
"""
import json
import os
import threading
import time
import uuid
import traceback
import xml.etree.ElementTree as ET

import websocket

PING_INTERVAL = 20
PING_TIMEOUT = 10


class FeedEngine:
    def __init__(self, ws_url, api_key, event_id, out_path="data.xml",
                 min_write_interval=0.0, auth_max_retries=5, auth_retry_delay=5.0,
                 fast_updates=True):
        self.ws_url = ws_url
        self.out_path = out_path
        self.min_write_interval = min_write_interval
        self.auth_max_retries = auth_max_retries
        self.auth_retry_delay = auth_retry_delay
        self.subscribe_message = {
            "apiName": "TvFeedApiV4",
            "apiCommand": "subscribe",
            "apiKey": api_key,
            "requestId": str(uuid.uuid4()),
            "eventId": event_id,
            "fastUpdates": bool(fast_updates),
        }

        self._ws = None
        self._thread = None
        self._stopping = False
        self._auth_attempts = 0
        self._last_written = None
        self._last_write_time = 0.0

        self._lock = threading.Lock()
        self._status = {
            "running": False,
            "connection": "disconnected",   # disconnected | connecting | connected | error
            "auth": "idle",                 # idle | pending | ok | failed
            "detail": "Stopped",
            "writes": 0,
            "time": None,
            "scoreA": None, "scoreB": None,
            "foulsA": None, "foulsB": None,
        }

    # ---------------------------------------------------------------- status
    def get_status(self):
        with self._lock:
            return dict(self._status)

    def _set(self, **kw):
        with self._lock:
            self._status.update(kw)

    def _auth(self):
        with self._lock:
            return self._status["auth"]

    # ------------------------------------------------------------- lifecycle
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stopping = False
        self._auth_attempts = 0
        self._last_written = None
        self._set(running=True, connection="connecting", auth="idle",
                  detail="Connecting...", writes=0,
                  time=None, scoreA=None, scoreB=None, foulsA=None, foulsB=None)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stopping = True
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        self._set(running=False, connection="disconnected", auth="idle",
                  detail="Stopped")

    def _run(self):
        try:
            self._ws = websocket.WebSocketApp(
                self.ws_url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            self._ws.run_forever(reconnect=5, ping_interval=PING_INTERVAL,
                                 ping_timeout=PING_TIMEOUT)
        except Exception as e:
            self._set(connection="error", detail="Connection error: {}".format(e))

    # -------------------------------------------------------- ws callbacks
    def _on_open(self, ws):
        self._auth_attempts = 0
        self._set(connection="connected", auth="pending",
                  detail="Connected - authenticating...")
        self._safe_send(ws)

    def _on_error(self, ws, error):
        if not self._stopping:
            self._set(connection="error", detail="Connection error: {}".format(error))

    def _on_close(self, ws, code, msg):
        if not self._stopping:
            self._set(connection="connecting", detail="Disconnected - reconnecting...")

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
        except Exception:
            return

        # Error messages carry errorMessage and no messageType.
        if "errorMessage" in data:
            err = str(data.get("errorMessage", ""))
            if "unauthorized" in err.lower() or "api key" in err.lower():
                self._handle_auth_error(ws)
            else:
                self._set(detail="Server error: {}".format(err))
            return

        message_type = data.get("messageType")
        # Real data proves the key is accepted.
        if message_type in ("game-status-update", "event-data-update"):
            if self._auth() != "ok":
                self._auth_attempts = 0
                self._set(auth="ok", detail="Connected - receiving data")

        if message_type == "game-status-update":
            self._process_game(data)

    # ------------------------------------------------------------- helpers
    def _safe_send(self, ws):
        try:
            ws.send(json.dumps(self.subscribe_message))
        except Exception:
            pass

    def _handle_auth_error(self, ws):
        if self._stopping:
            return
        if self._auth_attempts >= self.auth_max_retries:
            self._set(auth="failed",
                      detail="Authentication failed - check API Key, Event ID and URL")
            return
        self._auth_attempts += 1
        self._set(auth="pending", detail="Key rejected; retrying ({}/{})...".format(
            self._auth_attempts, self.auth_max_retries))
        threading.Timer(self.auth_retry_delay, lambda: self._safe_send(ws)).start()

    def _process_game(self, data):
        try:
            game_data = data.get("data") or {}
            if not game_data:
                return  # inactivity; nothing to update
            # Single game: take the one game in the update.
            _game_id, info = next(iter(game_data.items()))
            scores = info.get("currentTeamScore") or {}
            team_ids = list(scores.keys())
            if len(team_ids) != 2:
                return
            home, away = team_ids
            fouls = info.get("currentTeamFouls", {})
            rendered = {
                "homeTeamName": "Team #1",
                "awayTeamName": "New team name #75",
                "time": info.get("timeRemainingFormatted", "0.0"),
                "scoreA": scores.get(home, 0),
                "foulsA": fouls.get(home, 0),
                "scoreB": scores.get(away, 0),
                "foulsB": fouls.get(away, 0),
            }
            self._write_if_changed(self._to_xml(rendered))
            self._set(time=rendered["time"],
                      scoreA=rendered["scoreA"], scoreB=rendered["scoreB"],
                      foulsA=rendered["foulsA"], foulsB=rendered["foulsB"])
        except Exception:
            self._set(detail="Error processing update:\n" + traceback.format_exc())

    def _to_xml(self, d):
        root = ET.Element("root")
        for tag in ("homeTeamName", "awayTeamName", "time",
                    "scoreA", "foulsA", "scoreB", "foulsB"):
            el = ET.SubElement(root, tag)
            el.text = str(d.get(tag, ""))
        return ET.tostring(root, encoding="unicode")

    def _write_if_changed(self, xml_data):
        now = time.monotonic()
        if xml_data == self._last_written:
            return
        if self.min_write_interval > 0 and (now - self._last_write_time) < self.min_write_interval:
            return
        tmp = self.out_path + ".tmp"
        try:
            with open(tmp, "w") as f:
                f.write(xml_data)
            os.replace(tmp, self.out_path)
        except Exception:
            self._set(detail="Error writing {}:\n{}".format(
                self.out_path, traceback.format_exc()))
            return
        self._last_written = xml_data
        self._last_write_time = now
        with self._lock:
            self._status["writes"] += 1
