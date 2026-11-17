
from flask import Flask, jsonify
import yt_dlp

app = Flask(__name__)

CHANNELS = {
    "amrita": "0JE1N_HQc1M",
    "kannur": "ldYhjkWYNDE",
    "keralavision": "3446A6FzW3o"
}

@app.route("/")
def home():
    return jsonify({
        "service": "Kerala Live Checker",
        "status": "online",
        "channels": list(CHANNELS.keys())
    })

@app.route("/check/<channel>")
def check(channel):
    video_id = CHANNELS.get(channel)

    if not video_id:
        return jsonify({"error": "Unknown channel"}), 404

   options = {
    "quiet": True,
    "no_warnings": False,
    "skip_download": True,
    "noplaylist": True,
    "socket_timeout": 20,
    "extractor_retries": 1,
    "retries": 1,
    "extractor_args": {
        "youtube": {
            "player_client": ["web"]
        }
    }
}

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}",
                download=False
            )

        formats = info.get("formats", [])

        return jsonify({
            "channel": channel,
            "title": info.get("title"),
            "video_id": video_id,
            "is_live": info.get("is_live"),
            "live_status": info.get("live_status"),
            "format_count": len(formats),
            "available_formats": [
                {
                    "format_id": f.get("format_id"),
                    "ext": f.get("ext"),
                    "protocol": f.get("protocol"),
                    "height": f.get("height")
                }
                for f in formats[-15:]
            ]
        })

    except Exception as error:
        return jsonify({
            "channel": channel,
            "status": "error",
            "message": str(error)
        }), 502
