import os
import json
import asyncio
from datetime import datetime, timezone, timedelta

import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ==== SQLALCHEMY (SQLite) ====
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
    api_called_at = Column(DateTime, default=datetime.utcnow, index=True)
    mode = Column(String(20))          # "otomatis" / "manual"
    city = Column(String(100), nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    temp = Column(Float, nullable=True)
    feels_like = Column(Float, nullable=True)
    humidity = Column(Integer, nullable=True)
    description = Column(String(255), nullable=True)
    raw_json = Column(Text, nullable=True)  # respon asli dari OpenWeather


Base.metadata.create_all(bind=engine)

# =============================

app = FastAPI()

# folder static & templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ==== KONFIG CUACA ====
FALLBACK_API_KEY = "ca21257afebb7702df3c0497ccffa219"
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY") or FALLBACK_API_KEY

CITY = os.getenv("WEATHER_DEFAULT_CITY", "Pontianak")
COUNTRY_CODE = os.getenv("WEATHER_DEFAULT_COUNTRY", "ID")
UPDATE_INTERVAL = int(os.getenv("UPDATE_INTERVAL", "10"))
# =======================

# ==== WAKTU WIB (Asia/Pontianak, UTC+7) ====
WIB = timezone(timedelta(hours=7))


def now_wib_str() -> str:
    return datetime.now(WIB).strftime("%H:%M:%S WIB")


def format_weather(data: dict) -> dict:
    """
    Format JSON dari OpenWeather ke bentuk sederhana untuk frontend.
    Mirip versi awalmu, ditambah condition + condition_id.
    """
    try:
        weather_main = data["weather"][0]["main"]   # ex: "Clouds"
        weather_id = data["weather"][0]["id"]       # ex: 803

        return {
            "city": data.get("name"),
            "temp": data["main"]["temp"],
            "feels_like": data["main"]["feels_like"],
            "description": data["weather"][0]["description"],
            "humidity": data["main"]["humidity"],
            "condition": weather_main,
            "condition_id": weather_id,
            "updated_at": now_wib_str(),
        }
    except Exception as e:
        print("Error format_weather:", e, "DATA:", data)
        return {
            "city": data.get("name") if data else "Lokasi tidak diketahui",
            "temp": None,
            "feels_like": None,
            "description": "Tidak bisa ambil data",
            "humidity": None,
            "condition": None,
            "condition_id": None,
            "updated_at": now_wib_str(),
        }


def save_log(mode: str, api_data: dict, formatted: dict, lat=None, lon=None):
    """Simpan rekaman pemanggilan API ke database SQLite."""
    try:
        db = SessionLocal()
        log = WeatherLog(
            mode=mode,
            api_called_at=datetime.utcnow(),  # log di DB tetap pakai UTC
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
    except Exception as e:
        print("Error save_log:", e)
    finally:
        db.close()


def _call_openweather(params: dict, mode: str, lat=None, lon=None) -> dict:
    """
    Panggil API OpenWeather dan simpan ke DB.
    - Dipakai baik otomatis (Pontianak) maupun manual (klik peta / lat, lon).
    - Kalau API error, tetap balikin data yang bisa ditampilkan.
    """
    url = "https://api.openweathermap.org/data/2.5/weather"
    final_params = {
        "appid": WEATHER_API_KEY,
        "units": "metric",
        "lang": "id",
        **params,
    }

    try:
        res = requests.get(url, params=final_params, timeout=5)
        data = res.json()
        print("STATUS CODE:", res.status_code)
        print("DATA DARI OPENWEATHER:", data)

        if res.status_code != 200:
            # Tangani error dengan pesan yang lebih manusiawi
            raw_msg = data.get("message", "")
            msg_lower = raw_msg.lower() if isinstance(raw_msg, str) else ""

            if "city not found" in msg_lower:
                desc = "Data cuaca tidak tersedia di titik ini (kemungkinan laut / di luar jangkauan)."
            elif raw_msg:
                desc = f"Gagal ambil data: {raw_msg}"
            else:
                desc = "Gagal ambil data dari API cuaca."

            formatted = {
                "city": data.get("name") or "Lokasi tidak diketahui",
                "temp": None,
                "feels_like": None,
                "description": desc,
                "humidity": None,
                "condition": None,
                "condition_id": None,
                "updated_at": now_wib_str(),
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
            "condition": None,
            "condition_id": None,
            "updated_at": now_wib_str(),
        }
        save_log(mode, {"error": str(e)}, formatted, lat=lat, lon=lon)
        return formatted


def get_weather_default() -> dict:
    """Cuaca default kota Pontianak (mode WebSocket / otomatis)."""
    return _call_openweather(
        {"q": f"{CITY},{COUNTRY_CODE}"},
        mode="otomatis",
    )


def get_weather_by_coords(lat: float, lon: float) -> dict:
    """Cuaca berdasarkan koordinat (mode manual / klik peta)."""
    return _call_openweather(
        {"lat": lat, "lon": lon},
        mode="manual",
        lat=lat,
        lon=lon,
    )


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    """WebSocket untuk update cuaca Pontianak berkala (mode otomatis)."""
    await websocket.accept()
    try:
        while True:
            weather = get_weather_default()
            await websocket.send_json(weather)
            await asyncio.sleep(UPDATE_INTERVAL)
    except WebSocketDisconnect:
        print("WebSocket client disconnected.")
    except Exception as e:
        print("WebSocket closed by error:", e)
    finally:
        await websocket.close()


@app.get("/weather", response_class=JSONResponse)
async def weather_endpoint(lat: float, lon: float):
    """
    Endpoint HTTP:
    /weather?lat=...&lon=...
    Dipanggil saat user klik peta (mode manual).
    """
    weather = get_weather_by_coords(lat, lon)
    return JSONResponse(content=weather)
