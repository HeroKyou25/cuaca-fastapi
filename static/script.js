const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
const wsUrl = `${wsProtocol}://${window.location.host}/ws`;

const cityEl = document.getElementById("city");
const tempEl = document.getElementById("temp");
const descEl = document.getElementById("description");
const feelsEl = document.getElementById("feels_like");
const humEl = document.getElementById("humidity");
const statusEl = document.getElementById("status");

let socket = null;

function connectWebSocket() {
  statusEl.textContent = "Status: menghubungkan WebSocket...";

  socket = new WebSocket(wsUrl);

  socket.onopen = () => {
    statusEl.textContent = "Status: WebSocket terhubung ✅";
  };

  socket.onmessage = (event) => {
    const data = JSON.parse(event.data);

    cityEl.textContent = data.city || "Kota tidak diketahui";
    tempEl.textContent = data.temp !== null ? `${data.temp}°C` : "--°C";
    descEl.textContent = data.description || "-";
    feelsEl.textContent = data.feels_like !== null ? `Feels like: ${data.feels_like}°C` : "Feels like: --°C";
    humEl.textContent = data.humidity !== null ? `Humidity: ${data.humidity}%` : "Humidity: --%";
  };

  socket.onclose = () => {
    statusEl.textContent = "Status: koneksi putus, mencoba ulang...";
    setTimeout(connectWebSocket, 3000);
  };

  socket.onerror = () => {
    statusEl.textContent = "Status: error WebSocket";
  };
}

connectWebSocket();
