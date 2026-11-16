import os
import time
import json
import requests
import numpy as np
import soundfile as sf
import pyloudnorm as pyln
import matplotlib.pyplot as plt
from io import BytesIO

GROUP_ID = "648972497"
WEBHOOK_URL = os.environ["WEBHOOKURL"]
SEEN_FILE = "seen_audios.json"

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen_set):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(list(seen_set)), f)

def generate_waveform(audio_data):
    fig, axes = plt.subplots(2, 1, figsize=(8, 2.5), facecolor="#0a0a0a")
    plt.subplots_adjust(hspace=0.15, left=0.02, right=0.98, top=0.92, bottom=0.08)
    channels = [audio_data, audio_data] if audio_data.ndim == 1 else [audio_data[:, 0], audio_data[:, 1]]
    step = max(1, len(channels[0]) // 2000)

    for ax, ch in zip(axes, channels):
        ax.set_facecolor("#0a0a0a")
        ax.plot(ch[::step], color="#e60026", linewidth=0.6)
        ax.set_ylim(-1.05, 1.05)
        ax.axis("off")

    buf = BytesIO()
    plt.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf

def analyze_and_send(asset):
    asset_id = str(asset["id"])
    name = asset.get("name", "Unknown")
    creator = asset.get("creator", {}).get("name", "Maleficioo")

    resp = requests.get(f"https://assetdelivery.roblox.com/v1/asset/?id={asset_id}", timeout=20)
    if resp.status_code != 200:
        return

    ogg_bytes = resp.content
    size_mb = len(ogg_bytes) / (1024 * 1024)
    data, rate = sf.read(BytesIO(ogg_bytes))
    mins, secs = divmod(int(len(data) / rate), 60)

    lufs = pyln.Meter(rate).integrated_loudness(data)
    peak_linear = np.max(np.abs(data))
    peak_db = 20 * np.log10(peak_linear) if peak_linear > 0 else -100.0
    waveform_buf = generate_waveform(data)

    embed = {
        "title": "🔊 Audio Discovered",
        "description": f"**{name}** by {creator}",
        "color": 15073318,
        "fields": [
            {"name": "Audio ID", "value": f"`{asset_id}`", "inline": True},
            {"name": "Duration", "value": f"`{mins}:{secs:02d}`", "inline": True},
            {"name": "File Size", "value": f"`{size_mb:.2f} MB`", "inline": True},
            {"name": "Loudness", "value": f"`{lufs:.2f} LUFS`", "inline": True},
            {"name": "Peak", "value": f"`{peak_db:.2f} dBFS`", "inline": True},
            {"name": "Links", "value": f"[View on Roblox](https://create.roblox.com/store/asset/{asset_id})", "inline": True},
        ],
        "image": {"url": "attachment://waveform.png"}
    }

    files = {
        "file1": ("waveform.png", waveform_buf, "image/png"),
    }
    requests.post(WEBHOOK_URL, data={"payload_json": json.dumps({"embeds": [embed]})}, files=files, timeout=20)

def main():
    seen = load_seen()
    url = "https://apis.roblox.com/toolbox-service/v2/assets:search"
    params = {
        "searchCategoryType": "Audio",
        "creatorTargetId": GROUP_ID,
        "creatorType": "Group",
        "limit": 100
    }

    r = requests.get(url, params=params, timeout=15)
    if r.status_code != 200:
        print("Failed to fetch:", r.status_code)
        return

    items = r.json().get("data", [])

    for item in reversed(items):
        asset = item.get("asset", {})
        aid = str(asset.get("id"))
        if aid and aid not in seen:
            analyze_and_send(asset)
            seen.add(aid)
            save_seen(seen)
            time.sleep(2)

if __name__ == "__main__":
    main()
