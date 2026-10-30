"""Base de dados SQLite para registo persistente de dispositivos e histórico."""
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlmodel import SQLModel, Field, create_engine, Session, select
import math
import os
from security import get_password_hash, verify_password

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sqlite_url = f"sqlite:///{os.path.join(BASE_DIR, 'lab.db')}"
engine = create_engine(sqlite_url, echo=False, connect_args={"check_same_thread": False})

class DeviceRegistry(SQLModel, table=True):
    serial: str = Field(primary_key=True)
    brand: Optional[str] = None
    model: Optional[str] = None
    android_version: Optional[str] = None
    first_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_lat: Optional[float] = None
    last_lon: Optional[float] = None
    last_source: Optional[str] = None

class LocationHistory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    serial: str = Field(index=True)
    lat: float
    lon: float
    source: str = "gps"
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True
    )
    distance_from_prev: Optional[float] = None
    ip_details: Optional[str] = None


def migrate_location_history():
    from sqlalchemy import inspect, text

    inspector = inspect(engine)

    if not inspector.has_table("locationhistory"):
        return

    columns = {
        column["name"]
        for column in inspector.get_columns("locationhistory")
    }

    if "ip_details" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE locationhistory "
                    "ADD COLUMN ip_details TEXT"
                )
            )



def init_db():
    SQLModel.metadata.create_all(engine)
    migrate_location_history()
    create_default_admin()

def register_device(serial: str, brand: str, model: str, android: str):
    with Session(engine) as session:
        dev = session.get(DeviceRegistry, serial)
        if not dev:
            dev = DeviceRegistry(serial=serial, brand=brand, model=model, android_version=android)
            session.add(dev)
        else:
            dev.brand = brand
            dev.model = model
            dev.android_version = android
            dev.last_seen = datetime.now(timezone.utc)
        session.commit()

def add_location(
    serial: str,
    lat: float,
    lon: float,
    source: str = "gps",
    ip_details: Optional[dict] = None,
):
    import json

    if not serial:
        raise ValueError("O identificador do dispositivo é obrigatório.")

    lat = float(lat)
    lon = float(lon)

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Coordenadas inválidas.")

    with Session(engine) as session:
        last = session.exec(
            select(LocationHistory)
            .where(LocationHistory.serial == serial)
            .order_by(LocationHistory.timestamp.desc())
        ).first()

        dist = None

        if last:
            radius = 6371000
            phi1 = math.radians(last.lat)
            phi2 = math.radians(lat)
            dphi = math.radians(lat - last.lat)
            dlambda = math.radians(lon - last.lon)

            a = (
                math.sin(dphi / 2) ** 2
                + math.cos(phi1)
                * math.cos(phi2)
                * math.sin(dlambda / 2) ** 2
            )

            # Evita pequenos erros de arredondamento no cálculo.
            a = max(0.0, min(1.0, a))
            dist = 2 * radius * math.atan2(
                math.sqrt(a),
                math.sqrt(1 - a)
            )

        details_json = (
            json.dumps(ip_details, ensure_ascii=False)
            if ip_details is not None
            else None
        )

        hist = LocationHistory(
            serial=serial,
            lat=lat,
            lon=lon,
            source=source,
            distance_from_prev=dist,
            ip_details=details_json,
        )

        session.add(hist)

        dev = session.get(DeviceRegistry, serial)

        if dev:
            dev.last_lat = lat
            dev.last_lon = lon
            dev.last_source = source
            dev.last_seen = datetime.now(timezone.utc)

        session.commit()

def get_registered_devices() -> list[DeviceRegistry]:
    with Session(engine) as session:
        return list(session.exec(select(DeviceRegistry)).all())

def get_history(
    serial: str,
    limit: int = 5000,
    hours: Optional[int] = None,
) -> list[LocationHistory]:
    with Session(engine) as session:
        query = select(LocationHistory).where(
            LocationHistory.serial == serial
        )

        if hours is not None:
            if hours <= 0:
                raise ValueError("O período deve ser positivo.")

            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            query = query.where(LocationHistory.timestamp >= cutoff)

        query = query.order_by(
            LocationHistory.timestamp.asc(),
            LocationHistory.id.asc()
        ).limit(limit)

        return list(session.exec(query).all())


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    hashed_password: str

def create_default_admin():
    """Cria o utilizador 'admin' com a password 'labdemo' se não existir."""
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == "aidem")).first()
        if not user:
            print("[DB] A criar utilizador aidem padrão (password: labdemo1)")
            admin = User(username="aidem", hashed_password=get_password_hash("labdemo1"))
            session.add(admin)
            session.commit()

def authenticate_user(username: str, password: str) -> bool:
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == username)).first()
        if not user:
            return False
        return verify_password(password, user.hashed_password)
