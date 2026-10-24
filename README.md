Manifesty1
Plataforma integrada para demonstração de:

Localização de dispositivos móveis
Espelhamento Android utilizando Scrcpy
Backend FastAPI
Painel Web em tempo real
Comparação de mecanismos de localização (GPS, Wi‑Fi e IP)
Componentes
Android App
FastAPI Server
Web Dashboard
Scrcpy Integration
Requisitos
Python 3.13+
FastAPI
Android SDK
ADB
Scrcpy
Execução
Servidor
cd server
source venv/bin/activate
uvicorn server:app --host 0.0.0.0 --port 8000
