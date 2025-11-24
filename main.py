import os
import asyncio
from datetime import datetime

import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# -------------------------------------------------------------------
# KONFIGURASI APLIKASI
# -------------------------------------------------------------------

app = FastAPI()

# folder static & templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ==== KONFIGURASI CUACA ====
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "").strip()  # API key dari environment
CITY = os.getenv("WEATHER_DEFAULT_CITY", "Pontianak")       # kota default untuk mode WebSocket
COUNTRY_CODE = os.getenv("WEATHER_DEFAULT_COUNTRY", "ID")   # kode negara
UPDATE_INTERVAL = int(os.getenv("UPDATE_INTERVAL", "10"))   # interval update WebSocket (detik)
# ============================


# -------------------------------------------------------------------
# FUNGSI UTILITAS CUACA
# -------------------------------------------------------------------

def _base_error(description: str, city: str | None = None) -> dict:
    """Template respon kalau terjadi error."""
    return {
        "city": city or "Lokasi tidak diketahui",
        "temp": None,
        "feels_like": None,
        "description": description,
        "humidity": None,
        "updated_at": datetime.now().strftime("%H:%M:%S"),
    }


def format_weather(data: dict) -> dict:
    """Format JSON dari OpenWeather ke bentuk sederhana untuk frontend."""
    try:
        return {
            "city": data.get("name"),
            "temp": data["main"]["temp"],
            "feels_like": data["main"]["feels_like"],
            "description": data["weather"][0]["description"],
            "humidity": data["main"]["humidity"],
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }
    except Exception as e:
        print("Error format_weather:", e, "DATA:", data)
        return _base_error("Tidak bisa ambil data", data.get("name"))


def _call_openweather(params: dict) -> dict:
    """
    Panggil API OpenWeather dengan parameter tertentu.
    Handle:
    - API key tidak di-set
    - response error (status code != 200)
    """
    if not WEATHER_API_KEY:
        print("[ERROR] WEATHER_API_KEY tidak diset di server.")
        return _base_error("API key belum diset di server")

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
            # Contoh error: {"cod":401,"message":"Invalid API key"}
            msg = data.get("message", "Gagal ambil data dari API cuaca")
            return _base_error(f"Gagal ambil data: {msg}")

        return format_weather(data)

    except requests.RequestException as e:
        print("Error saat memanggil OpenWeather:", e)
        return _base_error("Tidak bisa terhubung ke server cuaca")


def get_weather_default() -> dict:
    """Cuaca default berbasis nama kota (untuk WebSocket)."""
    return _call_openweather({"q": f"{CITY},{COUNTRY_CODE}"})


def get_weather_by_coords(lat: float, lon: float) -> dict:
    """Cuaca berdasarkan koordinat lat/lon (untuk klik peta)."""
    return _call_openweather({"lat": lat, "lon": lon})


# -------------------------------------------------------------------
# ROUTES
# -------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Halaman utama: render templates/index.html."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    """
    WebSocket: kirim data cuaca default berkala ke client.
    Dipakai untuk mode otomatis (kota default).
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
