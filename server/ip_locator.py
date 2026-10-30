"""Consulta à API do IP2Location com chave de autenticação."""
import os
import httpx
import logging

# A chave deve vir do ambiente (ou de um .env carregado no main.py)
API_KEY = os.environ.get("IP2LOCATION_API_KEY", "2A092478E760E054478A1DD48F19C6B0")

async def get_location_by_ip(ip: str) -> dict | None:
    """Consulta o IP2Location e devolve coordenadas e detalhes geográficos."""
    import ipaddress

    if not ip or not API_KEY:
        logging.warning("IP2Location: IP ou chave da API em falta.")
        return None

    try:
        # Validar o endereço IP antes de consultar o serviço.
        ip = str(ipaddress.ip_address(ip.strip()))

        url = "https://api.ip2location.io/"

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                url,
                params={"key": API_KEY, "ip": ip}
            )

        if response.status_code != 200:
            logging.warning(
                "IP2Location respondeu com HTTP %s.", response.status_code
            )
            return None

        data = response.json()

        if not isinstance(data, dict):
            logging.warning("IP2Location devolveu uma resposta inválida.")
            return None

        lat = data.get("latitude")
        lon = data.get("longitude")

        # Não usar "if lat and lon": zero é uma coordenada válida.
        if lat is None or lon is None:
            return None

        lat = float(lat)
        lon = float(lon)

        # Validar os limites geográficos.
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            logging.warning("IP2Location devolveu coordenadas inválidas.")
            return None

        return {
            "lat": lat,
            "lon": lon,
            "city": data.get("city_name"),
            "region": data.get("region_name"),
            "country": data.get("country_name"),
            "district": data.get("district"),
            "zip_code": data.get("zip_code"),
            "time_zone": data.get("time_zone"),
            "ip": ip,
        }

    except (ValueError, TypeError):
        logging.warning("IP2Location: IP, JSON ou coordenadas inválidas.")
    except httpx.HTTPError as exc:
        logging.warning("Falha de comunicação com IP2Location: %s", exc)
    except Exception:
        logging.exception("Erro inesperado na consulta IP2Location.")

    return None
