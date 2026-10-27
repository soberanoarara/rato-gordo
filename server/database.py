"""Base de dados SQLite para registo persistente de dispositivos e histórico."""
from datetime import datetime, timezone
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
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    distance_from_prev: Optional[float] = None

def init_db():
    SQLModel.metadata.create_all(engine)
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

def add_location(serial: str, lat: float, lon: float, source: str = "gps"):
    with Session(engine) as session:
        last = session.exec(
            select(LocationHistory).where(LocationHistory.serial == serial).order_by(LocationHistory.timestamp.desc())
        ).first()
        
        dist = None
        if last:
            R = 6371000
            phi1, phi2 = math.radians(last.lat), math.radians(lat)
            dphi = math.radians(lat - last.lat)
            dlambda = math.radians(lon - last.lon)
            a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
            dist = 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))

        hist = LocationHistory(serial=serial, lat=lat, lon=lon, source=source, distance_from_prev=dist)
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

def get_history(serial: str, limit: int = 50) -> list[LocationHistory]:
    with Session(engine) as session:
        return list(session.exec(
            select(LocationHistory).where(LocationHistory.serial == serial).order_by(LocationHistory.timestamp.desc()).limit(limit)
        ).all())


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
