FROM python:3.9-slim

# Installiere notwendige Pakete für die Netzwerkverwaltung
RUN apt-get update && apt-get install -y \
    network-manager \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

# Setze das Arbeitsverzeichnis
WORKDIR /app

# Kopiere die Abhängigkeiten und installiere sie
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiere die Anwendungsdateien
COPY . .

# Setze den Startbefehl
CMD ["python", "run.py"]