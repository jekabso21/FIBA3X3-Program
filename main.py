import websocket
import json
import xml.etree.ElementTree as ET
import threading
import time
import uuid
import signal
import sys
import os

def load_config():
    config_path = "config.json"
    if not os.path.exists(config_path):
        config_path = input("Enter the path to config.json: ")
    with open(config_path, 'r') as f:
        return json.load(f)

config = load_config()

ws_url = config["webSocketUrl"]

subscribe_message = {
    "apiName": "TvFeedApiV4",
    "apiCommand": "subscribe",
    "apiKey": config["subscription"]["apiKey"],
    "requestId": str(uuid.uuid4()),
    "eventId": config["subscription"]["eventId"],
    "fastUpdates": True
}

print(subscribe_message)

wait_interval = config["waitInterval"] / 1000.0

def on_message(ws, message):
    data = json.loads(message)
    # print("Received data:", data)
    
    if "data" in data and "messageType" in data and data["messageType"] == "game-status-update":
        game_data = data["data"]
        
        for game_id, game_info in game_data.items():
            team_ids = list(game_info["currentTeamScore"].keys())
            if len(team_ids) == 2:
                home_team_id, away_team_id = team_ids
                
                formatted_data = {
                    "homeTeamName": "Team #1",  # Static name for example, should map from actual team data
                    "awayTeamName": "New team name #75",  # Static name for example, should map from actual team data
                    "time": game_info.get("timeRemainingFormatted", "0.0"),
                    "scoreA": game_info["currentTeamScore"].get(home_team_id, 0),
                    "foulsA": game_info["currentTeamFouls"].get(home_team_id, 0),
                    "scoreB": game_info["currentTeamScore"].get(away_team_id, 0),
                    "foulsB": game_info["currentTeamFouls"].get(away_team_id, 0)
                }
                
                xml_data = convert_to_xml(formatted_data)
                # print(xml_data)
                save_to_file(xml_data)
            else:
                print("Unexpected number of teams in the data.")

def on_error(ws, error):
    print("Error:", error)

def on_close(ws, close_status_code, close_msg):
    print("Connection closed:", close_msg)

def on_open(ws):
    print("Connection established")
    def send_subscription():
        ws.send(json.dumps(subscribe_message))
        threading.Timer(wait_interval, send_subscription).start()
    
    send_subscription()

def convert_to_xml(data):
    root = ET.Element("root")
    
    home_team_name = ET.SubElement(root, "homeTeamName")
    home_team_name.text = data.get("homeTeamName", "")
    
    away_team_name = ET.SubElement(root, "awayTeamName")
    away_team_name.text = data.get("awayTeamName", "")
    
    time = ET.SubElement(root, "time")
    time.text = str(data.get("time", "0.0"))
    
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
    with open("data.xml", "w") as file:
        file.write(data)
        # print("Data saved to data.xml")

def run_websocket():
    websocket.enableTrace(True)
    ws = websocket.WebSocketApp(ws_url,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    ws.on_open = on_open

    # Run WebSocket in the main thread
    ws.run_forever()

def signal_handler(sig, frame):
    print("Interrupt received, stopping...")
    sys.exit(0)

if __name__ == "__main__":
    # Register the signal handler
    signal.signal(signal.SIGINT, signal_handler)
    
    # Run WebSocket in a separate thread
    ws_thread = threading.Thread(target=run_websocket)
    ws_thread.start()
    
    # Keep the main thread running, otherwise, the script will exit
    while True:
        time.sleep(1)
