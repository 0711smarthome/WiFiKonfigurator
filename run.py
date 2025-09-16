import os
import json
import socket
import zeroconf
import requests
import logging
import time
from urllib.parse import quote
from flask import Flask, jsonify, request, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

# Konfiguration
DATA_DIR = "/data"
DEVICES_FILE = os.path.join(DATA_DIR, "shelly_devices.json")
LOG_FILE = os.path.join(DATA_DIR, "add-on.log")

# Überprüfe die Umgebungsvariable für den Debug-Modus
# Der Wert '1' oder 'true' wird als aktiv betrachtet, alles andere als inaktiv
DEBUG_MODE = os.environ.get('FLASK_DEBUG', '0').lower() in ('1', 'true')

# Ersetze den alten logging-Block in run.py hiermit:
logging.basicConfig(
    level=logging.DEBUG if DEBUG_MODE else logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    # handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()], # DEAKTIVIERT
)

# Globale Variablen
current_status = []
shelly_ip = '192.168.33.1'

app = Flask(__name__, template_folder=".")

def get_shelly_devices():
    """Lädt die gespeicherte Liste der Shelly-Geräte auf eine sichere Weise."""
    if not os.path.exists(DEVICES_FILE):
        return []  # Datei existiert nicht, alles ok, leere Liste zurückgeben.

    try:
        # Prüfen, ob die Datei leer ist, da json.load() damit nicht umgehen kann
        if os.path.getsize(DEVICES_FILE) == 0:
            return [] # Datei ist leer, leere Liste zurückgeben.
            
        with open(DEVICES_FILE, "r") as f:
            return json.load(f)
            
    except (IOError, json.JSONDecodeError, OSError) as e:
        logging.error(f"Fehler beim Lesen oder Parsen der Gerätedatei ({DEVICES_FILE}): {e}")
        return [] # Bei JEDEM Fehler eine leere Liste zurückgeben, um Abstürze zu vermeiden.

def save_shelly_devices(devices):
    """Speichert die Liste der Shelly-Geräte."""
    try:
        with open(DEVICES_FILE, "w") as f:
            json.dump(devices, f, indent=4)
    except IOError as e:
        logging.error(f"Fehler beim Speichern der Gerätedaten: {e}")


# --- API-Endpunkte für den Einrichtungsmodus ---
@app.route("/")
def index():
    # Lies die Version aus der Umgebungsvariable, die HA bereitstellt
    # 'N/A' ist ein Standardwert, falls die Variable nicht gefunden wird
    addon_version = os.environ.get('VERSION', 'N/A')
    return render_template("panel.html", version=addon_version)

# Ersetze deine gesamte setup_scan Funktion hiermit:

@app.route("/setup/scan", methods=["POST"])
def setup_scan():
    logging.info("Starte mDNS-Scan nach Shelly-Geräten...")
    try:
        found_devices = []
        timeout = 10
        
        # Die innere Callback-Funktion bleibt unverändert
        def on_service_added(zeroconf_instance, type, name):
            nonlocal found_devices
            try:
                info = zeroconf_instance.get_service_info(type, name, 3000)
                if info and b'shelly' in info.properties.get(b'model', b''):
                    ip_address = socket.inet_ntoa(info.addresses[0])
                    device = {
                        "ssid": name.replace(type, "").strip('.'),
                        "ip": ip_address,
                        "description": info.properties.get(b'friendly_name', b'').decode('utf-8'),
                        "model": info.properties.get(b'model', b'').decode('utf-8')
                    }
                    found_devices.append(device)
                    logging.info(f"Shelly-Gerät gefunden: {device['ssid']} auf {device['ip']}")
            except Exception as e:
                logging.error(f"Fehler bei der Verarbeitung des mDNS-Dienstes: {e}")
        
        zeroconf_instance = zeroconf.Zeroconf()
        browser = zeroconf.ServiceBrowser(zeroconf_instance, "_http._tcp.local.", handlers=[on_service_added])
        
        time.sleep(timeout)
        zeroconf_instance.close()

        if not found_devices:
            return jsonify({"status": "error", "message": "Keine Shelly-Geräte gefunden (nach Scan)."})
        
        return jsonify({"status": "success", "data": found_devices})

    except Exception as e:
        # DIES IST DER ENTSCHEIDENDE TEIL:
        # Wir fangen JEDEN denkbaren Fehler ab und loggen ihn.
        logging.error("Ein kritischer Fehler ist im mDNS-Scan aufgetreten!", exc_info=True)
        # Wir geben eine saubere Fehlermeldung an das Frontend zurück, anstatt abzustürzen.
        return jsonify({"status": "error", "message": f"Serverfehler während des Scans: {e}"}), 500

@app.route("/setup/save", methods=["POST"])
def setup_save():
    """Speichert die vom Benutzer ausgewählten Geräte."""
    devices_data = request.json.get("devices", [])
    selected_devices = [dev for dev in devices_data if dev.get("selected")]
    save_shelly_devices(selected_devices)
    return jsonify({"status": "success", "message": f"{len(selected_devices)} Geräte gespeichert."})

# --- API-Endpunkte für den Änderungsmodus ---
@app.route("/configure/get_saved_devices", methods=["GET"])
def configure_get_devices():
    logging.info("TEST: Get-Devices-Endpunkt wurde aufgerufen, gebe leere Liste zurück.")
    return jsonify({"status": "success", "data": []})


@app.route("/configure/start", methods=["POST"])
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

    current_status.append({"type": "info", "message": f"Starte Konfiguration für {len(devices)} Geräte..."})

    encoded_ssid = quote(personal_ssid)
    encoded_password = quote(personal_password)
    
    for device in devices:
        ssid = device.get('ssid')
        ip = device.get('ip')  # Wir verwenden jetzt die IP
        
        if not ip:
            current_status.append({"type": "error", "message": f"Gerät '{ssid}' hat keine IP-Adresse. Überspringe...", "ssid": ssid})
            continue

        try:
            current_status.append({"type": "progress", "message": f"Konfiguriere '{ssid}' über IP-Adresse...", "ssid": ssid})
            
            url = f"http://{ip}/settings/sta?ssid={encoded_ssid}&password={encoded_password}"
            response = requests.get(url, timeout=15)
            
            if response.status_code == 200:
                current_status.append({"type": "success", "message": f"'{ssid}' erfolgreich konfiguriert.", "ssid": ssid})
            else:
                current_status.append({"type": "error", "message": f"'{ssid}' API-Fehler: HTTP {response.status_code}", "ssid": ssid})

        except requests.exceptions.RequestException as e:
            error_message = f"Fehler bei der Konfiguration von '{ssid}': {e}"
            current_status.append({"type": "error", "message": error_message, "ssid": ssid})
            logging.error(error_message)

    current_status.append({"type": "info", "message": "Konfiguration abgeschlossen."})
    return jsonify(current_status)
    
@app.route("/status")
def get_status():
    return jsonify(current_status)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=DEBUG_MODE)
