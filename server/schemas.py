from pydantic import BaseModel, Field


class LocationReport(BaseModel):
    device: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy: float = Field(ge=0, le=100000)
    ts: float | None = None  # sobrescrito pelo servidor
    source: str = "gps"
    ip_publico: str | None = None

class TokenRequest(BaseModel):
    device: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    secret: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    token: str
    expires_at: float


class CommandRequest(BaseModel):
    action: str = Field(pattern=r"^(type|tap|swipe|key)$")
    text: str | None = Field(default=None, max_length=200)
    x: int | None = Field(default=None, ge=0, le=9999)
    y: int | None = Field(default=None, ge=0, le=9999)
    x1: int | None = Field(default=None, ge=0, le=9999)
    y1: int | None = Field(default=None, ge=0, le=9999)
    x2: int | None = Field(default=None, ge=0, le=9999)
    y2: int | None = Field(default=None, ge=0, le=9999)
    ms: int | None = Field(default=None, ge=1, le=5000)
    keycode: int | None = None
