import os
import json
import asyncio
from datetime import datetime, timedelta

import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# SQLAlchemy
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Text,
)
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = "sqlite:///./weather_logs.db"

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class WeatherLog(Base):
    __tablename__ = "weather_logs"

    id = Column(Integer, primary_key=True, index=True)
    api_called_at = Column(DateTime, index=True)
    mode = Column(String(20))
    city = Column(String(100), nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    temp = Column(Float, nullable=True)
    feels_like = Column(Float, nullable=True)
    humidity = Column(Integer, nullable=True)
    description = Column(String(255), nullable=True)
    raw_json = Column(Text, nullable=True)


Base.metadata.create_all(bind=engine)

app = FastAPI()

# Serve index.html from project root (so visiting / shows dashboard)
# If you prefer serving from /static, adapt accordingly.
app.mount("/static", StaticFiles(directory="static"), name="static")

# Configuration
FALLBACK_API_KEY = "ca21257afebb7702df3c0497ccffa219"
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY") or FALLBACK_API_KEY

CITY = os.getenv("WEATHER_DEFAULT_CITY", "Pontianak")
COUNTRY_CODE = os.getenv("WEATHER_DEFAULT_COUNTRY", "ID")
# UPDATE_INTERVAL in seconds (how often server queries OpenWeather for Pontianak)
UPDATE_INTERVAL = int(os.getenv("UPDATE_INTERVAL", "60"))

# WIB offset
WIB_OFFSET = timedelta(hours=7)


def now_wib() -> datetime:
    return datetime.utcnow() + WIB_OFFSET


def format_weather(data: dict) -> dict:
    """Format OpenWeather JSON to simplified dict for frontend."""
    try:
        weather = data.get("weather", [{}])[0]
        icon = weather.get("icon")
        icon_url = f"https://openweathermap.org/img/wn/{icon}@2x.png" if icon else None
        return {
            "city": data.get("name"),
            "temp": data["main"]["temp"],
            "feels_like": data["main"]["feels_like"],
            "description": weather.get("description"),
            "humidity": data["main"]["humidity"],
            "icon": icon,
            "icon_url": icon_url,
            "updated_at": now_wib().strftime("%H:%M:%S WIB"),
        }
    except Exception as e:
        print("Error format_weather:", e, "DATA:", (list(data.keys()) if data else data))
        return {
            "city": data.get("name") if data else "Lokasi tidak diketahui",
            "temp": None,
            "feels_like": None,
            "description": "Tidak bisa ambil data",
            "humidity": None,
            "icon": None,
            "icon_url": None,
            "updated_at": now_wib().strftime("%H:%M:%S WIB"),
        }


def save_log(mode: str, api_data: dict, formatted: dict, lat=None, lon=None):
    """Save API call to SQLite (time stored as WIB)."""
    try:
        db = SessionLocal()
        log = WeatherLog(
            mode=mode,
            api_called_at=now_wib(),
            city=formatted.get("city"),
            lat=lat,
            lon=lon,
            temp=formatted.get("temp"),
            feels_like=formatted.get("feels_like"),
            humidity=formatted.get("humidity"),
            description=formatted.get("description"),
            raw_json=json.dumps(api_data, ensure_ascii=False),
        )
        db.add(log)
        db.commit()

        # === BATASI DATABASE MAKSIMAL 500 RECORD ===
        MAX_RECORDS = 500
        count = db.query(WeatherLog).count()
        if count > MAX_RECORDS:
            oldest = (
                db.query(WeatherLog)
                .order_by(WeatherLog.id.asc())
                .first()
            )
            db.delete(oldest)
            db.commit()
        # ============================================

    except Exception as e:
        print("Error save_log:", e)
    finally:
        db.close()



def _call_openweather(params: dict, mode: str, lat=None, lon=None) -> dict:
    url = "https://api.openweathermap.org/data/2.5/weather"
    final_params = {
        "appid": WEATHER_API_KEY,
        "units": "metric",
        "lang": "id",
        **params,
    }

    try:
        res = requests.get(url, params=final_params, timeout=6)
        data = res.json()
        print("OpenWeather status:", res.status_code, "keys:", list(data.keys()))
        if res.status_code != 200:
            msg = data.get("message", "Gagal ambil data dari API cuaca")
            formatted = {
                "city": data.get("name") or "Lokasi tidak diketahui",
                "temp": None,
                "feels_like": None,
                "description": f"Gagal ambil data: {msg}",
                "humidity": None,
                "icon": None,
                "icon_url": None,
                "updated_at": now_wib().strftime("%H:%M:%S WIB"),
            }
            save_log(mode, data, formatted, lat=lat, lon=lon)
            return formatted

        formatted = format_weather(data)
        save_log(mode, data, formatted, lat=lat, lon=lon)
        return formatted

    except requests.RequestException as e:
        print("Error saat memanggil OpenWeather:", e)
        formatted = {
            "city": "Lokasi tidak diketahui",
            "temp": None,
            "feels_like": None,
            "description": "Tidak bisa terhubung ke server cuaca",
            "humidity": None,
            "icon": None,
            "icon_url": None,
            "updated_at": now_wib().strftime("%H:%M:%S WIB"),
        }
        save_log(mode, {"error": str(e)}, formatted, lat=lat, lon=lon)
        return formatted


def get_weather_default() -> dict:
    """Default weather for Pontianak (used by WebSocket broadcaster)."""
    return _call_openweather({"q": f"{CITY},{COUNTRY_CODE}"}, mode="otomatis")


def get_weather_by_coords(lat: float, lon: float) -> dict:
    """Weather by coordinates for map lookup (manual, does NOT affect dashboard)."""
    return _call_openweather({"lat": lat, "lon": lon}, mode="manual", lat=lat, lon=lon)


from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# WebSocket clients registry
clients = set()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    try:
        while True:
            # We'll keep the connection alive; server pushes updates periodically.
            # WebSocket receive is optional; do a short receive to detect client closure.
            await websocket.receive_text()
    except WebSocketDisconnect:
        print("WebSocket client disconnected.")
    except Exception as e:
        print("WebSocket error:", e)
    finally:
        clients.discard(websocket)
        try:
            await websocket.close()
        except Exception:
            pass


@app.get("/weather", response_class=JSONResponse)
async def weather_endpoint(lat: float, lon: float):
    """HTTP endpoint used by map lookups."""
    weather = get_weather_by_coords(lat, lon)
    return JSONResponse(content=weather)


@app.get("/logs", response_class=JSONResponse)
async def get_logs(limit: int = 50):
    db = SessionLocal()
    try:
        rows = db.query(WeatherLog).order_by(WeatherLog.id.desc()).limit(limit).all()
        result = []
        for r in rows:
            result.append(
                {
                    "id": r.id,
                    "api_called_at": r.api_called_at.strftime("%Y-%m-%d %H:%M:%S WIB"),
                    "mode": r.mode,
                    "city": r.city,
                    "lat": r.lat,
                    "lon": r.lon,
                    "temp": r.temp,
                    "feels_like": r.feels_like,
                    "humidity": r.humidity,
                    "description": r.description,
                }
            )
        return JSONResponse(content=result)
    finally:
        db.close()


# Background broadcaster coroutine
async def broadcaster_loop():
    while True:
        try:
            data = get_weather_default()
            if data:
                disconnected = []
                for ws in list(clients):
                    try:
                        await ws.send_json(data)
                    except Exception:
                        disconnected.append(ws)
                for ws in disconnected:
                    clients.discard(ws)
        except Exception as e:
            print("Broadcaster loop error:", e)
        await asyncio.sleep(UPDATE_INTERVAL)


# Start background task when app starts
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(broadcaster_loop())
