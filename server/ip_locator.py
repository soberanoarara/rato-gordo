"""Consulta à API do IP2Location com chave de autenticação."""
import os
import httpx
import logging

# A chave deve vir do ambiente (ou de um .env carregado no main.py)
API_KEY = os.environ.get("IP2LOCATION_API_KEY", "2A092478E760E054478A1DD48F19C6B0")

async def get_location_by_ip(ip: str) -> dict | None:
    """Consulta o IP2Location e devolve um dicionário com os detalhes."""
    if not ip:
        return None
        
    url = f"https://api.ip2location.io/?key={API_KEY}&ip={ip}"
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                lat = data.get("latitude")
                lon = data.get("longitude")
                if lat and lon:
                    return {
                        "lat": float(lat),
                        "lon": float(lon),
                        "city": data.get("city_name", "Desconhecida"),
                        "region": data.get("region_name", "Desconhecida"),
                        "country": data.get("country_name", "Desconhecido")
                    }
    except Exception as e:
        logging.warning(f"IP2Location falhou para {ip}: {e}")
    return None
