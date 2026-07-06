"""
FIBA 3x3 Data Feed — simple GUI.

Lets the user edit the three things that change between events (API Key, Event ID,
WebSocket URL), saves them to config.json, and shows a live status. Everything
else in config.json (waitInterval, fastUpdates, ...) is preserved untouched.
"""
import json
import os
import tkinter as tk
from tkinter import ttk, messagebox

from feed_engine import FeedEngine

CONFIG_PATH = "config.json"

DEFAULT_CONFIG = {
    "subscription": {
        "apiName": "TvFeedApiV4",
        "apiCommand": "subscribe",
        "apiKey": "",
        "requestId": "RANDOM_UUID",
        "eventId": "",
        "fastUpdates": True,
    },
    "webSocketUrl": "",
    "waitInterval": 0,
}

# Status colors
GREY = "#9aa0a6"
ORANGE = "#e8a33d"
GREEN = "#2e9e5b"
RED = "#d64545"


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
        except Exception:
            cfg = dict(DEFAULT_CONFIG)
    else:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    # Make sure the expected keys exist.
    cfg.setdefault("subscription", {})
    cfg["subscription"].setdefault("apiName", "TvFeedApiV4")
    cfg["subscription"].setdefault("apiCommand", "subscribe")
    cfg["subscription"].setdefault("requestId", "RANDOM_UUID")
    cfg["subscription"].setdefault("fastUpdates", True)
    cfg["subscription"].setdefault("apiKey", "")
    cfg["subscription"].setdefault("eventId", "")
    cfg.setdefault("webSocketUrl", "")
    cfg.setdefault("waitInterval", 0)
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def status_view(s):
    """Map a status snapshot to (text, color)."""
    if not s["running"]:
        return "Stopped", GREY
    if s["auth"] == "failed" or s["connection"] == "error":
        return s["detail"], RED
    if s["auth"] == "ok":
        return "Connected — receiving data", GREEN
    if s["connection"] == "connected":
        return "Connected — authenticating...", ORANGE
    return "Connecting...", ORANGE


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.engine = None

        root.title("FIBA 3x3 Data Feed")
        root.resizable(False, False)
        try:
            root.configure(bg="#f4f5f7")
        except Exception:
            pass

        pad = {"padx": 14, "pady": 6}
        frm = ttk.Frame(root, padding=16)
        frm.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frm, text="FIBA 3x3 Data Feed",
                  font=("Segoe UI", 15, "bold")).grid(row=0, column=0, columnspan=2,
                                                      sticky="w", pady=(0, 10))

        # --- editable fields ---
        self.api_key = tk.StringVar(value=self.cfg["subscription"]["apiKey"])
        self.event_id = tk.StringVar(value=self.cfg["subscription"]["eventId"])
        self.ws_url = tk.StringVar(value=self.cfg["webSocketUrl"])

        self.entries = []
        self._field(frm, 1, "API Key", self.api_key)
        self._field(frm, 2, "Event ID", self.event_id)
        self._field(frm, 3, "WebSocket URL", self.ws_url)

        # --- buttons ---
        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 6))
        self.save_btn = ttk.Button(btns, text="Save", command=self.on_save)
        self.save_btn.grid(row=0, column=0, padx=(0, 8))
        self.start_btn = ttk.Button(btns, text="Start", command=self.on_start_stop)
        self.start_btn.grid(row=0, column=1)

        # --- status ---
        status = ttk.Frame(frm)
        status.grid(row=5, column=0, columnspan=2, sticky="we", pady=(10, 0))
        self.dot = tk.Canvas(status, width=16, height=16, highlightthickness=0,
                             bg="#f4f5f7")
        self.dot.grid(row=0, column=0, padx=(0, 8))
        self._dot_id = self.dot.create_oval(3, 3, 13, 13, fill=GREY, outline="")
        self.status_lbl = ttk.Label(status, text="Stopped", font=("Segoe UI", 10, "bold"))
        self.status_lbl.grid(row=0, column=1, sticky="w")

        self.score_lbl = ttk.Label(frm, text="", font=("Segoe UI", 11))
        self.score_lbl.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.path_lbl = ttk.Label(
            frm, text="Writes to: {}".format(os.path.abspath("data.xml")),
            foreground="#777", font=("Segoe UI", 8))
        self.path_lbl.grid(row=7, column=0, columnspan=2, sticky="w", pady=(12, 0))

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._poll()

    def _field(self, parent, row, label, var):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=6)
        e = ttk.Entry(parent, textvariable=var, width=46)
        e.grid(row=row, column=1, sticky="we", pady=6, padx=(10, 0))
        self.entries.append(e)

    # ---------------------------------------------------------- actions
    def _apply_fields_to_cfg(self):
        self.cfg["subscription"]["apiKey"] = self.api_key.get().strip()
        self.cfg["subscription"]["eventId"] = self.event_id.get().strip()
        self.cfg["webSocketUrl"] = self.ws_url.get().strip()

    def on_save(self):
        self._apply_fields_to_cfg()
        try:
            save_config(self.cfg)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            return
        messagebox.showinfo("Saved", "Settings saved to config.json.")

    def _validate(self):
        url = self.ws_url.get().strip()
        if not self.api_key.get().strip():
            return "Please enter the API Key."
        if not self.event_id.get().strip():
            return "Please enter the Event ID."
        if not (url.startswith("ws://") or url.startswith("wss://")):
            return "WebSocket URL must start with ws:// or wss://"
        return None

    def on_start_stop(self):
        if self.engine is not None:
            self._stop()
            return
        problem = self._validate()
        if problem:
            messagebox.showerror("Cannot start", problem)
            return
        # Save first so what runs is what's on screen.
        self._apply_fields_to_cfg()
        try:
            save_config(self.cfg)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            return
        self.engine = FeedEngine(
            ws_url=self.cfg["webSocketUrl"],
            api_key=self.cfg["subscription"]["apiKey"],
            event_id=self.cfg["subscription"]["eventId"],
            min_write_interval=max(self.cfg.get("waitInterval", 0), 0) / 1000.0,
            fast_updates=self.cfg["subscription"].get("fastUpdates", True),
        )
        self.engine.start()
        self._set_running_ui(True)

    def _stop(self):
        if self.engine is not None:
            self.engine.stop()
            self.engine = None
        self._set_running_ui(False)

    def _set_running_ui(self, running):
        state = "disabled" if running else "normal"
        for e in self.entries:
            e.configure(state=state)
        self.save_btn.configure(state=state)
        self.start_btn.configure(text="Stop" if running else "Start")

    def on_close(self):
        try:
            if self.engine is not None:
                self.engine.stop()
        finally:
            self.root.destroy()

    # ------------------------------------------------------------- polling
    def _poll(self):
        if self.engine is not None:
            s = self.engine.get_status()
            text, color = status_view(s)
            self.status_lbl.configure(text=text)
            self.dot.itemconfigure(self._dot_id, fill=color)
            if s["auth"] == "ok" and s["time"] is not None:
                self.score_lbl.configure(
                    text="Score {} - {}   |   Fouls {} - {}   |   Time {}   |   writes {}".format(
                        s["scoreA"], s["scoreB"], s["foulsA"], s["foulsB"],
                        s["time"], s["writes"]))
            else:
                self.score_lbl.configure(text="")
        else:
            self.status_lbl.configure(text="Stopped")
            self.dot.itemconfigure(self._dot_id, fill=GREY)
            self.score_lbl.configure(text="")
        self.root.after(300, self._poll)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
