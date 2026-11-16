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

GROUP_ID = "648972497"
WEBHOOK_URL = os.environ["WEBHOOKURL"]
ROBLOX_COOKIE = os.environ["ROBLOXCOOKIE"]
SEEN_FILE = "seen_audios.json"
BATCH_SIZE = 50  # Sends up to 50 audios per run so runs never overlap

SESSION = requests.Session()
SESSION.cookies[".ROBLOSECURITY"] = ROBLOX_COOKIE
SESSION.headers.update({"User-Agent": "Mozilla/5.0"})

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

def fetch_all_group_audios():
    audios = []
    cursor = ""
    while True:
        url = f"https://itemconfiguration.roblox.com/v1/creations/get-assets?assetType=Audio&groupId={GROUP_ID}&limit=50&cursor={cursor}"
        r = SESSION.get(url, timeout=15)
        if r.status_code != 200:
            print(f"Failed to fetch group assets ({r.status_code}): {r.text}")
            break
        payload = r.json()
        page_items = payload.get("data", [])
        audios.extend(page_items)
        cursor = payload.get("nextPageCursor")
        if not cursor:
            break
        time.sleep(0.3)
    return audios

def download_private_audio(asset_id):
    r = SESSION.get(f"https://assetdelivery.roblox.com/v1/asset/?id={asset_id}", timeout=20)
    if r.status_code == 200 and len(r.content) > 100:
        return r.content
    print(f"Download failed for {asset_id}: status {r.status_code}")
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
            retry_after = res.json().get("retry_after", 5)
            print(f"Discord rate limit hit, waiting {retry_after}s...")
            time.sleep(float(retry_after) + 0.5)
        else:
            print(f"Discord error {res.status_code}: {res.text}")
            break
    return False

def analyze_and_send(asset):
    asset_id = str(asset["assetId"])
    name = asset.get("name", "Unknown")

    ogg_bytes = download_private_audio(asset_id)
    if not ogg_bytes:
        return False

    size_mb = len(ogg_bytes) / (1024 * 1024)
    data, rate = sf.read(BytesIO(ogg_bytes))
    mins, secs = divmod(int(len(data) / rate), 60)

    lufs = pyln.Meter(rate).integrated_loudness(data)
    peak_linear = np.max(np.abs(data))
    peak_db = 20 * np.log10(peak_linear) if peak_linear > 0 else -100.0
    waveform_buf = generate_waveform(data)
    thumb_url = get_thumbnail(asset_id)

    embed = {
        "title": "🔊 Audio Discovered",
        "description": f"**{name}** by Maleficioo",
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

    if thumb_url:
        embed["thumbnail"] = {"url": thumb_url}

    safe_name = re.sub(r'[^a-zA-Z0-9_\-\. ]', '', name).strip() or asset_id
    return post_to_discord(embed, waveform_buf, ogg_bytes, safe_name, size_mb)

def main():
    seen = load_seen()
    items = fetch_all_group_audios()
    print(f"Found {len(items)} total group audios.")

    sent_this_run = 0
    for asset in reversed(items):
        if sent_this_run >= BATCH_SIZE:
            print(f"Reached batch limit ({BATCH_SIZE}). Remaining audios will continue on the next run.")
            break
        aid = str(asset.get("assetId"))
        if aid and aid not in seen:
            print(f"[{sent_this_run + 1}/{BATCH_SIZE}] Processing {aid} ({asset.get('name')})...")
            if analyze_and_send(asset):
                seen.add(aid)
                save_seen(seen)
                sent_this_run += 1
            time.sleep(2.5)

if __name__ == "__main__":
    main()
