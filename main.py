import os
import re
import time
import json
import requests
import numpy as np
import soundfile as sf
import pyloudnorm as pyln
import matplotlib.pyplot as plt
from io import BytesIO
from datetime import datetime

GROUP_ID = "648972497"
WEBHOOK_URL = os.environ["WEBHOOKURL"]
ROBLOX_COOKIE = os.environ["ROBLOXCOOKIE"]
SEEN_FILE = "seen_audios.json"
BATCH_SIZE = 50

SESSION = requests.Session()
SESSION.cookies[".ROBLOSECURITY"] = ROBLOX_COOKIE
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://create.roblox.com/"
})

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen_set):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(list(seen_set)), f)

def get_thumbnail(asset_id):
    try:
        r = SESSION.get(
            f"https://thumbnails.roblox.com/v1/assets?assetIds={asset_id}&returnPolicy=PlaceHolder&size=420x420&format=Png",
            timeout=10
        )
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data and data[0].get("imageUrl"):
                return data[0]["imageUrl"]
    except Exception:
        pass
    return None

def get_asset_details(asset_id):
    """Consulta la API individual para obtener la fecha de creación real y oficial"""
    try:
        r = SESSION.get(f"https://itemconfiguration.roblox.com/v1/assets/{asset_id}", timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("created") or data.get("updated")
    except Exception:
        pass
    return None

def generate_waveform(audio_data, rate):
    duration = len(audio_data) / rate
    fig, axes = plt.subplots(2, 1, figsize=(9, 3.2), facecolor="#0e0f13")
    plt.subplots_adjust(hspace=0.32, left=0.06, right=0.96, top=0.90, bottom=0.18)
    
    channels = [audio_data, audio_data] if audio_data.ndim == 1 else [audio_data[:, 0], audio_data[:, 1]]
    labels = ["L", "R"]
    num_bins = 1800

    for ax, ch, lbl in zip(axes, channels, labels):
        ax.set_facecolor("#14151b")
        
        chunk_size = max(1, len(ch) // num_bins)
        trimmed_len = len(ch) - (len(ch) % chunk_size)
        ch_trimmed = ch[:trimmed_len]
        
        reshaped = ch_trimmed.reshape(-1, chunk_size)
        peaks_max = np.max(reshaped, axis=1)
        peaks_min = np.min(reshaped, axis=1)
        
        time_axis = np.linspace(0, duration, len(peaks_max))
        
        ax.plot(time_axis, peaks_max, color="#ff2a38", linewidth=0.7, alpha=0.9)
        ax.plot(time_axis, peaks_min, color="#ff2a38", linewidth=0.7, alpha=0.9)
        ax.fill_between(time_axis, peaks_min, peaks_max, color="#ff2a38", alpha=0.25)
        
        ax.set_ylim(-1.05, 1.05)
        ax.set_xlim(0, duration if duration > 0 else 1)
        
        ax.text(0.01, 0.78, lbl, transform=ax.transAxes, color="#8a8d96", fontsize=9, fontweight="bold", family="monospace")
        
        for spine in ax.spines.values():
            spine.set_edgecolor("#2f3136")
            spine.set_linewidth(1.2)
        
        ax.tick_params(colors="#8a8d96", labelsize=8)
        if duration > 0:
            tick_interval = max(30, int(duration / 6))
            ticks = list(range(0, int(duration) + 1, tick_interval))
            ax.set_xticks(ticks)
            ax.set_xticklabels([f"{t//60}:{t%60:02d}" for t in ticks], color="#8a8d96", fontsize=8)
        
        ax.yaxis.set_ticks([])

    buf = BytesIO()
    plt.savefig(buf, format="png", facecolor=fig.get_facecolor(), dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf

def fetch_all_group_audios():
    audios = []
    cursor = ""
    page_num = 1
    while True:
        url = f"https://itemconfiguration.roblox.com/v1/creations/get-assets?assetType=Audio&groupId={GROUP_ID}&limit=100"
        if cursor:
            url += f"&cursor={cursor}"
            
        r = SESSION.get(url, timeout=15)
        if r.status_code != 200:
            break
            
        payload = r.json()
        page_items = payload.get("data", [])
        if not page_items:
            break
            
        audios.extend(page_items)
        cursor = payload.get("nextPageCursor") or payload.get("cursor")
        if not cursor:
            break
            
        page_num += 1
        time.sleep(0.3)
        
    return audios

def download_private_audio(asset_id):
    r = SESSION.get(f"https://assetdelivery.roblox.com/v1/asset/?id={asset_id}", timeout=20)
    if r.status_code == 200 and len(r.content) > 100:
        return r.content
    return None

def post_to_discord(embed, waveform_buf, ogg_bytes, safe_name, size_mb):
    for attempt in range(3):
        waveform_buf.seek(0)
        files = {
            "file1": ("waveform.png", waveform_buf, "image/png"),
        }
        if size_mb < 9.5:
            files["file2"] = (f"{safe_name}.ogg", BytesIO(ogg_bytes), "audio/ogg")

        res = requests.post(
            WEBHOOK_URL,
            data={"payload_json": json.dumps({"embeds": [embed]})},
            files=files,
            timeout=30
        )
        if res.status_code in (200, 204):
            return True
        if res.status_code == 429:
            time.sleep(5)
        else:
            break
    return False

def analyze_and_send(asset):
    asset_id = str(asset["assetId"])
    name = asset.get("name", "Unknown")
    
    created_raw = asset.get("resolved_created") or asset.get("created") or asset.get("updated") or ""
    upload_date = "Unknown date"
    if created_raw:
        try:
            dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            upload_date = dt.strftime("%B %d, %Y %I:%M %p")
        except Exception:
            upload_date = str(created_raw)[:16]

    ogg_bytes = download_private_audio(asset_id)
    if not ogg_bytes:
        return False

    size_mb = len(ogg_bytes) / (1024 * 1024)
    data, rate = sf.read(BytesIO(ogg_bytes))
    mins, secs = divmod(int(len(data) / rate), 60)

    lufs = pyln.Meter(rate).integrated_loudness(data)
    peak_linear = np.max(np.abs(data))
    peak_db = 20 * np.log10(peak_linear) if peak_linear > 0 else -100.0
    
    waveform_buf = generate_waveform(data, rate)
    thumb_url = get_thumbnail(asset_id)

    embed = {
        "title": f"🔊 {name}",
        "description": "*No description*",
        "color": 16711680,
        "fields": [
            {"name": "ID", "value": f"`{asset_id}`", "inline": True},
            {"name": "Status", "value": "✅ Approved", "inline": True},
            {"name": "Creator", "value": "Maleficioo", "inline": True},
            {"name": "Uploaded by", "value": "`@maleficio`", "inline": True},
            {"name": "Duration", "value": f"{mins}:{secs:02d}", "inline": True},
            {"name": "Size", "value": f"{size_mb:.2f} MB", "inline": True},
            {"name": "Format", "value": f"OGG {rate//1000}kHz", "inline": True},
            {"name": "Loudness", "value": f"{lufs:.1f} LUFS", "inline": True},
            {"name": "Peak", "value": f"{peak_db:.1f} dBFS", "inline": True},
            {"name": "Uploaded", "value": upload_date, "inline": False},
            {"name": "Links", "value": f"[View on Roblox](https://create.roblox.com/store/asset/{asset_id})", "inline": False},
        ],
        "image": {"url": "attachment://waveform.png"}
    }

    if thumb_url:
        embed["thumbnail"] = {"url": thumb_url}

    safe_name = re.sub(r'[^a-zA-Z0-9_\-\. ]', '', name).strip() or asset_id
    return post_to_discord(embed, waveform_buf, ogg_bytes, safe_name, size_mb)

def main():
    seen = load_seen()
    items = fetch_all_group_audios()
    print(f"Total de audios encontrados en bruto: {len(items)}")

    # Asignar la fecha oficial a cada audio para poder ordenarlos bien
    print("Obteniendo fechas reales de creación para ordenar cronológicamente...")
    for asset in items:
        aid = str(asset.get("assetId"))
        c_date = asset.get("created") or get_asset_details(aid) or "2000-01-01T00:00:00Z"
        asset["resolved_created"] = c_date
        time.sleep(0.1) # Pausa pequeña para no saturar la API

    # Ordenar estrictamente del MÁS VIEJO al MÁS NUEVO (ascendente)
    items.sort(key=lambda x: x.get("resolved_created", ""))

    sent_this_run = 0
    for asset in items:
        if sent_this_run >= BATCH_SIZE:
            print(f"Límite de lote alcanzado ({BATCH_SIZE}). El resto se procesará en la próxima ejecución.")
            break
        aid = str(asset.get("assetId"))
        if aid and aid not in seen:
            print(f"Procesando {aid} ({asset.get('name')}) - Fecha: {asset.get('resolved_created')}...")
            if analyze_and_send(asset):
                seen.add(aid)
                save_seen(seen)
                sent_this_run += 1
            time.sleep(2.5)

if __name__ == "__main__":
    main()
