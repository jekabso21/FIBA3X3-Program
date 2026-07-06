import websocket
import json
import xml.etree.ElementTree as ET
import threading
import time
import uuid
import signal
import sys
import os
import traceback

def load_config():
    config_path = "config.json"
    if not os.path.exists(config_path):
        config_path = input("Enter the path to config.json: ")
    with open(config_path, 'r') as f:
        return json.load(f)

# Fixed WebSocket keep-alive ping (seconds). Unrelated to waitInterval.
PING_INTERVAL = 20
PING_TIMEOUT = 10

# ANSI colors for console output (stripped from the log file).
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RESET = "\033[0m"

def enable_ansi_colors():
    """Enable ANSI escape processing in the Windows console so colored output
    (red/green) renders instead of showing raw escape codes."""
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # -11 = STD_OUTPUT_HANDLE; mode 7 = ENABLE_PROCESSED_OUTPUT |
            # ENABLE_WRAP_AT_EOL_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING.
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

# Config-derived settings, populated by start(). Kept as module globals so the
# handler functions below can read them at call time.
ws_url = None
subscribe_message = None
debug_trace = False
auth_max_retries = 5       # how many re-auth attempts before giving up
auth_retry_delay = 5.0     # seconds between re-auth attempts

# Writer state. This tool tracks a SINGLE game, and writes data.xml directly from
# the WebSocket thread the moment a change arrives - no polling timer - so the
# file reflects the feed with the lowest possible latency.
last_written_xml = None
last_write_time = 0.0
writer_lock = threading.Lock()
sample_dumped = False        # dump the first populated update in full, once
last_rendered = None         # last rendered field dict, for the heartbeat display
min_write_interval = 0.0     # min seconds between disk writes (0 = every change)

# Diagnostics: count of each received messageType and how many times we've
# written data.xml. Used by the heartbeat so the operator can see what's flowing.
msg_counts = {}
write_count = 0
stats_lock = threading.Lock()

# Authentication state. The server accepts the subscribe command (returns
# "subscribed") but may then reject the API key with an Unauthorized error. We
# retry re-subscribing a few times; if all fail we surface a red banner.
auth_lock = threading.Lock()
auth_attempts = 0          # how many re-auth attempts have been made
auth_ok = False            # True once real data proves we're authorized
auth_gave_up = False       # True once retries are exhausted

def log(message, color=None):
    """Print to the console AND append to fiba3x3.log next to the program, so a
    double-clicked run still leaves a record after the window closes. Optional
    color applies only to the console; the log file stays plain text."""
    line = "[{}] {}".format(time.strftime("%Y-%m-%d %H:%M:%S"), message)
    if color:
        print(color + line + RESET, flush=True)
    else:
        print(line, flush=True)
    try:
        with open("fiba3x3.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def is_auth_error(data):
    """True if a received message is an authentication/authorization error."""
    err = str(data.get("errorMessage", "")).lower()
    return "unauthorized" in err or "api key" in err

def schedule_reauth(ws):
    """Retry authentication by re-sending the subscribe command, up to
    auth_max_retries times. On the final failure, show a red banner."""
    global auth_attempts, auth_gave_up
    with auth_lock:
        if auth_ok or auth_gave_up:
            return
        if auth_attempts >= auth_max_retries:
            auth_gave_up = True
            log("=" * 56, color=RED)
            log("FAILED TO AUTHENTICATE after {} attempts.".format(auth_attempts), color=RED)
            log("The server rejected the API key. Check that apiKey, eventId and", color=RED)
            log("webSocketUrl in config.json all belong to the SAME current", color=RED)
            log("Venue Server session.", color=RED)
            log("=" * 56, color=RED)
            return
        auth_attempts += 1
        attempt = auth_attempts
    log("Authentication failed; retrying (attempt {}/{}) in {:.0f}s...".format(
        attempt, auth_max_retries, auth_retry_delay), color=YELLOW)

    def _resend():
        try:
            ws.send(json.dumps(subscribe_message))
            log("Re-sent subscribe (re-auth attempt {}/{}).".format(attempt, auth_max_retries),
                color=YELLOW)
        except Exception:
            log("Re-auth send failed:\n" + traceback.format_exc(), color=RED)

    threading.Timer(auth_retry_delay, _resend).start()

def mark_authenticated():
    """Called when real data proves the API key is accepted."""
    global auth_ok
    with auth_lock:
        already = auth_ok
        auth_ok = True
    if not already:
        log("Authentication successful - receiving live data.", color=GREEN)

def on_message(ws, message):
    try:
        data = json.loads(message)
    except Exception as e:
        log("Received a non-JSON message ({}): {}".format(e, message[:200]))
        return

    # Error messages have no messageType but carry an errorMessage field.
    if "errorMessage" in data:
        if is_auth_error(data):
            log("Server rejected the API key: {} (context: {})".format(
                data.get("errorMessage"), data.get("errorContext")), color=RED)
            schedule_reauth(ws)
        else:
            log("Server error: {} (context: {})".format(
                data.get("errorMessage"), data.get("errorContext")), color=RED)
        return

    message_type = data.get("messageType", "<no messageType>")
    with stats_lock:
        first_seen = message_type not in msg_counts
        msg_counts[message_type] = msg_counts.get(message_type, 0) + 1
    if first_seen:
        # Log the whole first message of each type so we can see its structure.
        log("First '{}' message received: {}".format(message_type, message[:600]))

    # Real data (game or event updates) proves the API key was accepted.
    if message_type in ("game-status-update", "event-data-update"):
        mark_authenticated()

    if message_type != "game-status-update":
        return

    try:
        game_data = data.get("data") or {}
        if not game_data:
            # Empty updates happen during inactivity (no game being scored). This
            # is normal, so we don't touch the output or spam the log.
            return

        # Dump the first populated update in full so we can see the real structure.
        global sample_dumped
        if not sample_dumped:
            log("Sample populated game-status-update (full): {}".format(message))
            sample_dumped = True

        # Single game: take the one game in the update.
        game_id, game_info = next(iter(game_data.items()))
        scores = game_info.get("currentTeamScore") or {}
        team_ids = list(scores.keys())
        if len(team_ids) != 2:
            log("game {}: expected 2 teams but got {} ({})".format(
                game_id, len(team_ids), team_ids))
            return

        home_team_id, away_team_id = team_ids
        fouls = game_info.get("currentTeamFouls", {})
        rendered = {
            "homeTeamName": "Team #1",  # Static name for example, should map from actual team data
            "awayTeamName": "New team name #75",  # Static name for example, should map from actual team data
            "time": game_info.get("timeRemainingFormatted", "0.0"),
            "scoreA": scores.get(home_team_id, 0),
            "foulsA": fouls.get(home_team_id, 0),
            "scoreB": scores.get(away_team_id, 0),
            "foulsB": fouls.get(away_team_id, 0),
        }
        global last_rendered
        last_rendered = rendered
        write_if_changed(convert_to_xml(rendered))
    except Exception:
        # Never let one bad message kill the handler silently.
        log("Error processing game-status-update:\n" + traceback.format_exc())

def on_error(ws, error):
    log("WebSocket error: {}".format(error))

def on_close(ws, close_status_code, close_msg):
    log("Connection closed (code={}, msg={})".format(close_status_code, close_msg))

def heartbeat_loop():
    """Every 10s, report status. NOTE: this 10s cadence is only the logging
    interval - data.xml itself is written far more often (see 'writes' below)."""
    while True:
        time.sleep(10)
        with stats_lock:
            counts = dict(msg_counts)
            writes = write_count
        if counts:
            summary = ", ".join("{}={}".format(k, v) for k, v in counts.items())
        else:
            summary = "no messages received yet"
        with auth_lock:
            gave_up = auth_gave_up
            ok = auth_ok
        disp = last_rendered
        note = ""
        color = None
        if gave_up:
            note = "  <-- AUTH FAILED; fix apiKey/eventId/webSocketUrl in config.json"
            color = RED
        elif disp is not None:
            note = "  | game: time={} score {}-{}".format(
                disp["time"], disp["scoreA"], disp["scoreB"])
        elif ok and "game-status-update" not in counts:
            note = "  <-- authenticated; waiting for a game to be scored"
        elif "game-status-update" not in counts:
            note = "  <-- no game-status-update yet; data.xml is only written when one arrives"
        log("heartbeat: messages [{}]; data.xml writes={}{}".format(summary, writes, note),
            color=color)

def on_open(ws):
    # Fresh connection: reset the auth retry state so a reconnect gets a clean
    # set of re-auth attempts.
    global auth_attempts, auth_ok, auth_gave_up
    with auth_lock:
        auth_attempts = 0
        auth_ok = False
        auth_gave_up = False

    log("Connection established; sending subscribe (eventId={})".format(
        subscribe_message.get("eventId")))
    # Subscribe once. This is a push-based feed: after the server acknowledges
    # with messageType "subscribed", it streams "game-status-update" messages on
    # its own. Re-sending "subscribe" repeatedly only makes the server re-ack the
    # subscription and never lets us settle into receiving the game data.
    ws.send(json.dumps(subscribe_message))

def convert_to_xml(data):
    root = ET.Element("root")
    
    home_team_name = ET.SubElement(root, "homeTeamName")
    home_team_name.text = data.get("homeTeamName", "")
    
    away_team_name = ET.SubElement(root, "awayTeamName")
    away_team_name.text = data.get("awayTeamName", "")
    
    time_el = ET.SubElement(root, "time")
    time_el.text = str(data.get("time", "0.0"))

    score_a = ET.SubElement(root, "scoreA")
    score_a.text = str(data.get("scoreA", "0"))
    
    fouls_a = ET.SubElement(root, "foulsA")
    fouls_a.text = str(data.get("foulsA", "0"))
    
    score_b = ET.SubElement(root, "scoreB")
    score_b.text = str(data.get("scoreB", "0"))
    
    fouls_b = ET.SubElement(root, "foulsB")
    fouls_b.text = str(data.get("foulsB", "0"))
    
    return ET.tostring(root, encoding='unicode')

def save_to_file(data):
    # Atomic write: write to a temp file then replace, so a consumer (e.g. the
    # graphics/overlay app) never reads a half-written or empty data.xml.
    # No fsync: durability doesn't matter for a live scoreboard, and skipping it
    # keeps each write fast. os.replace() is still atomic.
    tmp_path = "data.xml.tmp"
    with open(tmp_path, "w") as file:
        file.write(data)
    os.replace(tmp_path, "data.xml")

def write_if_changed(xml_data):
    """Write data.xml immediately when the content changed. Called straight from
    the WebSocket thread so the file tracks the feed with minimal latency. An
    optional min_write_interval (from config waitInterval) throttles disk writes;
    0 = write on every change."""
    global last_written_xml, last_write_time, write_count
    now = time.monotonic()
    with writer_lock:
        if xml_data == last_written_xml:
            return
        if min_write_interval > 0 and (now - last_write_time) < min_write_interval:
            return  # too soon since last write; a later update will flush it
        try:
            save_to_file(xml_data)
        except Exception:
            log("Error writing data.xml:\n" + traceback.format_exc())
            return
        last_written_xml = xml_data
        last_write_time = now
    with stats_lock:
        write_count += 1
        first_write = write_count == 1
    if first_write:
        log("First data.xml written next to the program. Path: {}".format(
            os.path.abspath("data.xml")))

def run_websocket():
    websocket.enableTrace(debug_trace)
    ws = websocket.WebSocketApp(ws_url,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    ws.on_open = on_open

    # run_forever auto-reconnects (every 5s) if the connection drops, so a single
    # network blip doesn't silently freeze the feed for the rest of the event.
    # ping_interval keeps the connection alive between game-status-update pushes.
    ws.run_forever(reconnect=5, ping_interval=PING_INTERVAL, ping_timeout=PING_TIMEOUT)

def signal_handler(sig, frame):
    print("Interrupt received, stopping...")
    sys.exit(0)

def start():
    global ws_url, subscribe_message, min_write_interval, debug_trace
    global auth_max_retries, auth_retry_delay

    enable_ansi_colors()
    config = load_config()

    ws_url = config["webSocketUrl"]
    api_key = config["subscription"]["apiKey"]
    event_id = config["subscription"]["eventId"]

    # Validate config before connecting so an un-edited template gives a clear
    # message instead of a raw "url is invalid" traceback from a worker thread.
    placeholders = {"YOUR_API_KEY_HERE", "Network_Interface_Websokcet_API_URL",
                    "RANDOM_UUID", "", None}
    problems = []
    if ws_url in placeholders or not str(ws_url).startswith(("ws://", "wss://")):
        problems.append("webSocketUrl must be a ws:// or wss:// address "
                        "(currently: {!r})".format(ws_url))
    if api_key in placeholders:
        problems.append("apiKey is not set (currently: {!r})".format(api_key))
    if event_id in placeholders:
        problems.append("eventId is not set (currently: {!r})".format(event_id))
    if problems:
        log("config.json is not filled in yet:", color=RED)
        for p in problems:
            log("  - " + p, color=RED)
        log("Edit config.json next to the program, set the real values from your "
            "Venue Server session, then run again.", color=RED)
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass
        sys.exit(1)

    subscribe_message = {
        "apiName": "TvFeedApiV4",
        "apiCommand": "subscribe",
        "apiKey": api_key,
        "requestId": str(uuid.uuid4()),
        "eventId": event_id,
        "fastUpdates": True
    }
    print(subscribe_message)

    # data.xml is written the instant a change arrives (lowest latency). The feed
    # itself sends at most one update per 100 ms, so that is effectively the
    # update rate. "waitInterval" is optional and only acts as a MINIMUM interval
    # between disk writes (0 = write on every change, the fastest).
    min_write_interval = max(config.get("waitInterval", 0), 0) / 1000.0  # ms -> s
    # Verbose raw-frame tracing is noisy; off unless explicitly enabled in config.
    debug_trace = bool(config.get("debug", False))
    # Optional re-authentication tuning (defaults: 5 attempts, 5s apart).
    auth_max_retries = int(config.get("authRetries", 5))
    auth_retry_delay = max(config.get("authRetryDelay", 5000), 500) / 1000.0  # ms -> s

    log("Starting. Connecting to {}".format(ws_url))
    log("Writing data.xml on every change (min {} ms apart) to: {}".format(
        int(min_write_interval * 1000), os.path.abspath("data.xml")))

    # Register the signal handler
    signal.signal(signal.SIGINT, signal_handler)

    # Daemon threads so Ctrl+C (sys.exit in the handler) exits cleanly instead of
    # hanging on a still-running worker.
    ws_thread = threading.Thread(target=run_websocket, daemon=True)
    ws_thread.start()

    # Heartbeat thread: periodic status so the operator can see what's arriving.
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    # Keep the main thread running, otherwise, the script will exit
    while True:
        time.sleep(1)

if __name__ == "__main__":
    try:
        start()
    except SystemExit:
        raise
    except BaseException:
        # When launched by double-clicking the .exe, an unhandled error would
        # otherwise close the terminal instantly. Show it and wait for a keypress.
        traceback.print_exc()
        try:
            input("\nProgram stopped due to an error. Press Enter to close...")
        except EOFError:
            pass
        sys.exit(1)
