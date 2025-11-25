import os
import json
import asyncio
from datetime import datetime, timedelta

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
    api_called_at = Column(DateTime, index=True)  # akan diisi dengan waktu WIB
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

# ==== KONFIGURASI CUACA ====


# API key: pakai ENV kalau ada, kalau tidak pakai fallback
FALLBACK_API_KEY = "ca21257afebb7702df3c0497ccffa219"  # <-- API key kamu di sini
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY") or FALLBACK_API_KEY

CITY = os.getenv("WEATHER_DEFAULT_CITY", "Pontianak")      # kota default untuk mode WebSocket
COUNTRY_CODE = os.getenv("WEATHER_DEFAULT_COUNTRY", "ID")  # kode negara
UPDATE_INTERVAL = int(os.getenv("UPDATE_INTERVAL", "60"))  # interval update WebSocket (detik) -> 60 = 1 menit

# Waktu WIB (UTC+7)
WIB_OFFSET = timedelta(hours=7)


def now_wib() -> datetime:
    """Mengembalikan waktu sekarang dalam WIB."""
    return datetime.utcnow() + WIB_OFFSET


# ============================


def format_weather(data: dict) -> dict:
    """Format JSON dari OpenWeather ke bentuk sederhana untuk frontend."""
    try:
        return {
            "city": data.get("name"),
            "temp": data["main"]["temp"],
            "feels_like": data["main"]["feels_like"],
            "description": data["weather"][0]["description"],
            "humidity": data["main"]["humidity"],
            "updated_at": now_wib().strftime("%H:%M:%S WIB"),
        }
    except Exception as e:
        print("Error format_weather:", e, "DATA:", data)
        return {
            "city": data.get("name") if data else "Lokasi tidak diketahui",
            "temp": None,
            "feels_like": None,
            "description": "Tidak bisa ambil data",
            "humidity": None,
            "updated_at": now_wib().strftime("%H:%M:%S WIB"),
        }


def save_log(mode: str, api_data: dict, formatted: dict, lat=None, lon=None):
    """Simpan rekaman pemanggilan API ke database SQLite (waktu disimpan sebagai WIB)."""
    try:
        db = SessionLocal()
        log = WeatherLog(
            mode=mode,
            api_called_at=now_wib(),  # simpan langsung WIB
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
    Panggil API OpenWeather dan SELALU simpan ke DB
    (baik otomatis maupun manual).
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
            msg = data.get("message", "Gagal ambil data dari API cuaca")
            formatted = {
                "city": data.get("name") or "Lokasi tidak diketahui",
                "temp": None,
                "feels_like": None,
                "description": f"Gagal ambil data: {msg}",
                "humidity": None,
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
            "updated_at": now_wib().strftime("%H:%M:%S WIB"),
        }
        save_log(mode, {"error": str(e)}, formatted, lat=lat, lon=lon)
        return formatted


def get_weather_default() -> dict:
    """Cuaca default berbasis nama kota (untuk WebSocket, mode otomatis)."""
    return _call_openweather(
        {"q": f"{CITY},{COUNTRY_CODE}"},
        mode="otomatis",
    )


def get_weather_by_coords(lat: float, lon: float) -> dict:
    """Cuaca berdasarkan koordinat lat/lon (mode manual / klik peta)."""
    return _call_openweather(
        {"lat": lat, "lon": lon},
        mode="manual",
        lat=lat,
        lon=lon,
    )


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Halaman utama: render templates/index.html."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    """
    WebSocket: kirim data cuaca default berkala ke client.
    Dipakai untuk mode otomatis (kota default).
    Interval diatur oleh UPDATE_INTERVAL (detik).
    """
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
    Endpoint HTTP biasa:
    /weather?lat=...&lon=...
    Dipanggil saat user klik peta.
    """
    weather = get_weather_by_coords(lat, lon)
    return JSONResponse(content=weather)


# ========= ENDPOINT LIHAT LOG SEBAGAI JSON =========

@app.get("/logs", response_class=JSONResponse)
async def get_logs(limit: int = 50):
    """
    Lihat rekapan pemanggilan API cuaca (JSON).
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(WeatherLog)
            .order_by(WeatherLog.id.desc())
            .limit(limit)
            .all()
        )
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


# ========= ENDPOINT LIHAT LOG DALAM BENTUK DUA TABEL HTML =========

@app.get("/logs-view", response_class=HTMLResponse)
async def logs_view(limit: int = 50):
    """
    Viewer HTML sederhana untuk melihat log:
    - Tabel 1: mode otomatis
    - Tabel 2: mode manual
    """
    db = SessionLocal()
    try:
        auto_rows = (
            db.query(WeatherLog)
            .filter(WeatherLog.mode == "otomatis")
            .order_by(WeatherLog.id.desc())
            .limit(limit)
            .all()
        )
        manual_rows = (
            db.query(WeatherLog)
            .filter(WeatherLog.mode == "manual")
            .order_by(WeatherLog.id.desc())
            .limit(limit)
            .all()
        )

        def build_rows(rows):
            html = ""
            for r in rows:
                html += f"""
                <tr>
                  <td>{r.id}</td>
                  <td>{r.api_called_at.strftime('%Y-%m-%d %H:%M:%S WIB')}</td>
                  <td>{r.city or ""}</td>
                  <td>{r.lat or ""}</td>
                  <td>{r.lon or ""}</td>
                  <td>{r.temp or ""}</td>
                  <td>{r.feels_like or ""}</td>
                  <td>{r.humidity or ""}</td>
                  <td>{r.description or ""}</td>
                </tr>
                """
            return html

        auto_rows_html = build_rows(auto_rows)
        manual_rows_html = build_rows(manual_rows)

        html = f"""
        <!DOCTYPE html>
        <html lang="id">
        <head>
          <meta charset="utf-8" />
          <title>Log Pemanggilan API Cuaca</title>
          <style>
            body {{
              font-family: Arial, sans-serif;
              background: #f5f5f5;
              padding: 20px;
            }}
            h1 {{
              margin-bottom: 4px;
            }}
            h2 {{
              margin-top: 24px;
              margin-bottom: 8px;
            }}
            table {{
              border-collapse: collapse;
              width: 100%;
              background: #fff;
              border-radius: 8px;
              overflow: hidden;
              box-shadow: 0 3px 8px rgba(0,0,0,0.1);
              font-size: 0.9rem;
              margin-bottom: 16px;
            }}
            thead {{
              background: #f0f0f0;
            }}
            th, td {{
              padding: 6px 8px;
              border-bottom: 1px solid #e0e0e0;
              text-align: left;
            }}
            tr:nth-child(even) td {{
              background: #fafafa;
            }}
          </style>
        </head>
        <body>
          <h1>Log Pemanggilan API Cuaca</h1>
          <p>Waktu pada tabel sudah dikonversi ke WIB. Menampilkan maks {limit} data terakhir per mode.</p>

          <h2>Mode Otomatis (WebSocket, kota default)</h2>
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Waktu (WIB)</th>
                <th>Kota</th>
                <th>Lat</th>
                <th>Lon</th>
                <th>Temp</th>
                <th>Feels Like</th>
                <th>Humidity</th>
                <th>Deskripsi</th>
              </tr>
            </thead>
            <tbody>
              {auto_rows_html}
            </tbody>
          </table>

          <h2>Mode Manual (klik peta)</h2>
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Waktu (WIB)</th>
                <th>Kota</th>
                <th>Lat</th>
                <th>Lon</th>
                <th>Temp</th>
                <th>Feels Like</th>
                <th>Humidity</th>
                <th>Deskripsi</th>
              </tr>
            </thead>
            <tbody>
              {manual_rows_html}
            </tbody>
          </table>
        </body>
        </html>
        """

        return HTMLResponse(content=html)
    finally:
        db.close()
