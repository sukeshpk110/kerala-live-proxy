import os
import requests
import yt_dlp
from urllib.parse import urljoin, quote
from flask import Flask, Response, jsonify, redirect, request

app = Flask(__name__)

# Existing YouTube Live Channel IDs
CHANNELS = {
    "amrita": "0JE1N_HQc1M",
    "kannur": "ldYhjkWYNDE",
    "keralavision": "3446A6FzW3o"
}

# 1. Home / Health check route for Render and cron-job.org
@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "channels": list(CHANNELS.keys()),
        "proxy_usage": "/proxy?url=YOUR_M3U8_URL"
    })

@app.route("/health")
def health():
    return "OK", 200

# 2. Existing YouTube Live Extractor Route
@app.route("/channel/<name>.m3u8")
@app.route("/<name>.m3u8")
def youtube_channel(name):
    video_id = CHANNELS.get(name.lower())
    if not video_id:
        return "Channel not found", 404

    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            stream_url = info.get("url")
            if stream_url:
                return redirect(stream_url)
            return "Stream URL not found", 404
    except Exception as e:
        return str(e), 500

# 3. Header-Injecting Proxy Route for Smart TV (myTV Best)
@app.route("/proxy")
def proxy_stream():
    target_url = request.args.get("url")
    if not target_url:
        return "Missing 'url' parameter", 400

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://bhoomtv.org/",
        "Origin": "https://bhoomtv.org"
    }

    try:
        req = requests.get(target_url, headers=headers, stream=True, timeout=15)
        content_type = req.headers.get("content-type", "")

        # Rewrite internal M3U8 links so sub-playlists and .ts chunks also use /proxy
        if ".m3u8" in target_url or "mpegurl" in content_type.lower():
            base_proxy = f"{request.scheme}://{request.host}/proxy?url="
            lines = []
            for line in req.text.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    full_url = urljoin(target_url, stripped)
                    lines.append(base_proxy + quote(full_url, safe=""))
                else:
                    lines.append(line)
            return Response("\n".join(lines), status=req.status_code, content_type="application/vnd.apple.mpegurl")

        # Stream video segments (.ts) directly
        def generate():
            for chunk in req.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk

        return Response(generate(), status=req.status_code, content_type=content_type)

    except Exception as e:
        return str(e), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
