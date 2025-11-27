import os
import json
import asyncio
from datetime import datetime

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

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

FALLBACK_API_KEY = "ca21257afebb7702df3c0497ccffa219"
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY") or FALLBACK_API_KEY

CITY = os.getenv("WEATHER_DEFAULT_CITY", "Pontianak")
COUNTRY_CODE = os.getenv("WEATHER_DEFAULT_COUNTRY", "ID")
UPDATE_INTERVAL = int(os.getenv("UPDATE_INTERVAL", "10"))


def format_weather(data: dict) -> dict:
    try:
        weather_main = data["weather"][0]["main"]
        weather_id = data["weather"][0]["id"]

        return {
            "city": data.get("name"),
            "temp": data["main"]["temp"],
            "feels_like": data["main"]["feels_like"],
            "description": data["weather"][0]["description"],
            "humidity": data["main"]["humidity"],
            "condition": weather_main,
            "condition_id": weather_id,
            "updated_at": datetime.now().strftime("%H:%M:%S"),
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
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }


def save_log(mode: str, api_data: dict, formatted: dict, lat=None, lon=None):
    try:
        db = SessionLocal()
        log = WeatherLog(
            mode=mode,
            api_called_at=datetime.utcnow(),
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
        print("DATA:", data)

        if res.status_code != 200:
            msg = data.get("message", "Gagal ambil data dari API")
            formatted = {
                "city": data.get("name") or "Lokasi tidak diketahui",
                "temp": None,
                "feels_like": None,
                "description": f"Gagal ambil data: {msg}",
                "humidity": None,
                "condition": None,
                "condition_id": None,
                "updated_at": datetime.now().strftime("%H:%M:%S"),
            }
            save_log(mode, data, formatted, lat, lon)
            return formatted

        formatted = format_weather(data)
        save_log(mode, data, formatted, lat, lon)
        return formatted

    except Exception as e:
        print("Error API:", e)
        formatted = {
            "city": "Lokasi tidak diketahui",
            "temp": None,
            "feels_like": None,
            "description": "Tidak bisa terhubung ke server",
            "humidity": None,
            "condition": None,
            "condition_id": None,
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }
        save_log(mode, {"error": str(e)}, formatted, lat, lon)
        return formatted


def get_weather_default():
    return _call_openweather(
        {"q": f"{CITY},{COUNTRY_CODE}"},
        mode="otomatis",
    )


def get_weather_by_coords(lat: float, lon: float):
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
    await websocket.accept()
    try:
        while True:
            weather = get_weather_default()
            await websocket.send_json(weather)
            await asyncio.sleep(UPDATE_INTERVAL)
    except WebSocketDisconnect:
        print("Client disconnected")
    finally:
        await websocket.close()


@app.get("/weather", response_class=JSONResponse)
async def weather_endpoint(lat: float, lon: float):
    weather = get_weather_by_coords(lat, lon)
    return JSONResponse(content=weather)
