import subprocess
import json
import os
import time
import requests
import logging
from urllib.parse import quote
from flask import Flask, jsonify, request, render_template

# Konfiguration
DATA_DIR = "/data"
DEVICES_FILE = os.path.join(DATA_DIR, "shelly_devices.json")
LOG_FILE = os.path.join(DATA_DIR, "add-on.log")

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)

# Globale Variablen
current_status = []
shelly_ip = '192.168.33.1'

app = Flask(__name__, template_folder=".")

# --- Hilfsfunktionen für Datenverwaltung und Netzwerk-Status ---
def get_shelly_devices():
    """Lädt die gespeicherte Liste der Shelly-Geräte."""
    if os.path.exists(DEVICES_FILE):
        try:
            with open(DEVICES_FILE, "r") as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            logging.error(f"Fehler beim Laden der Gerätedaten: {e}")
    return []

def save_shelly_devices(devices):
    """Speichert die Liste der Shelly-Geräte."""
    try:
        with open(DEVICES_FILE, "w") as f:
            json.dump(devices, f, indent=4)
    except IOError as e:
        logging.error(f"Fehler beim Speichern der Gerätedaten: {e}")

def get_current_network_id():
    """Ermittelt die SSID (ID) des aktuell verbundenen Netzwerks."""
    try:
        output = subprocess.check_output(
            ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],
            stderr=subprocess.DEVNULL,
        ).decode("utf-8")
        lines = output.strip().split("\n")
        for line in lines:
            if line.startswith("yes"):
                return line.split(":", 1)[1]
    except (subprocess.CalledProcessError, FileNotFoundError):
        logging.warning("Konnte das aktuelle Netzwerk nicht ermitteln.")
    return None

def restore_original_network(original_ssid):
    """Stellt die Verbindung zum ursprünglichen Netzwerk wieder her."""
    if original_ssid:
        try:
            logging.info(f"Versuche, die Verbindung zu '{original_ssid}' wiederherzustellen...")
            subprocess.check_output(["nmcli", "con", "up", "id", original_ssid], stderr=subprocess.DEVNULL)
            logging.info(f"Verbindung zu '{original_ssid}' erfolgreich wiederhergestellt.")
            return True
        except subprocess.CalledProcessError as e:
            logging.error(f"Fehler beim Wiederherstellen der Verbindung: {e}")
            return False
    return False

# --- API-Endpunkte für den Einrichtungsmodus ---
@app.route("/")
def index():
    return render_template("panel.html")

@app.route("/api/setup/scan", methods=["POST"])
def setup_scan():
    """Scannt nach allen WLANs für den Einrichtungsmodus."""
    global current_status
    current_status = []
    
    try:
        logging.info("Starte WLAN-Scan für Einrichtungsmodus...")
        output = subprocess.check_output(
            ["nmcli", "-t", "dev", "wifi", "list", "--rescan", "yes"],
            stderr=subprocess.STDOUT, timeout=45,
        ).decode("utf-8")
        
        networks = []
        lines = output.strip().split("\n")
        # Überspringt die Kopfzeile
        for line in lines[1:]:
            parts = line.split(":")
            if len(parts) >= 11:
                ssid = parts[1]
                bssid = parts[0]
                signal = parts[10]
                is_shelly = ssid.startswith('shelly-')
                networks.append({"ssid": ssid, "bssid": bssid, "signal": signal, "description": "", "selected": is_shelly})

        return jsonify({"status": "success", "data": networks})

    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        logging.error(f"Fehler beim Scan: {e}")
        return jsonify({"status": "error", "message": str(e)})

@app.route("/api/setup/save", methods=["POST"])
def setup_save():
    """Speichert die vom Benutzer ausgewählten Geräte."""
    devices_data = request.json.get("devices", [])
    selected_devices = [dev for dev in devices_data if dev.get("selected")]
    save_shelly_devices(selected_devices)
    return jsonify({"status": "success", "message": f"{len(selected_devices)} Geräte gespeichert."})

# --- API-Endpunkte für den Änderungsmodus ---
@app.route("/api/configure/get_saved_devices", methods=["GET"])
def configure_get_devices():
    """Gibt die gespeicherten Geräte für den Änderungsmodus zurück."""
    devices = get_shelly_devices()
    return jsonify({"status": "success", "data": devices})


@app.route("/api/configure/start", methods=["POST"])
def configure_start():
    """Startet den Konfigurationsprozess für die gespeicherten Geräte."""
    global current_status
    current_status = []
    data = request.json
    personal_ssid = data.get("ssid")
    personal_password = data.get("password")

    if not personal_ssid or not personal_password:
        return jsonify({"status": "error", "message": "WLAN-Daten fehlen."})

    devices = get_shelly_devices()
    if not devices:
        current_status.append({"type": "warning", "message": "Keine Geräte zum Konfigurieren gespeichert."})
        return jsonify(current_status)

    original_network = get_current_network_id()
    
    current_status.append({"type": "info", "message": f"Starte Konfiguration für {len(devices)} Geräte..."})
    
    encoded_ssid = quote(personal_ssid)
    encoded_password = quote(personal_password)
    
    for device in devices:
        ssid = device['ssid']
        bssid = device['bssid']
        
        try:
            current_status.append({"type": "progress", "message": f"Verbinde mich mit '{ssid}'...", "ssid": ssid})
            subprocess.check_output(
                ["nmcli", "dev", "wifi", "connect", ssid, "bssid", bssid],
                stderr=subprocess.DEVNULL, timeout=60,
            )
            time.sleep(10)

            url = f"http://{shelly_ip}/settings/sta?ssid={encoded_ssid}&password={encoded_password}"
            response = requests.get(url, timeout=15)
            
            if response.status_code == 200:
                current_status.append({"type": "success", "message": f"'{ssid}' erfolgreich konfiguriert.", "ssid": ssid})
            else:
                current_status.append({"type": "error", "message": f"'{ssid}' API-Fehler: HTTP {response.status_code}", "ssid": ssid})

        except (subprocess.CalledProcessError, requests.exceptions.RequestException, subprocess.TimeoutExpired) as e:
            error_message = f"Fehler bei der Konfiguration von '{ssid}': {e}"
            current_status.append({"type": "error", "message": error_message, "ssid": ssid})
            logging.error(error_message)
        
    if not restore_original_network(original_network):
        current_status.append({"type": "error", "message": f"Netzwerk '{original_network}' konnte nicht automatisch wiederhergestellt werden. Bitte manuell verbinden.", "ssid": ""})
            
    current_status.append({"type": "info", "message": "Konfiguration abgeschlossen."})
    return jsonify(current_status)

@app.route("/api/status")
def get_status():
    return jsonify(current_status)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)