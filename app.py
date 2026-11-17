
from flask import Flask, jsonify
import os
import yt_dlp

app = Flask(__name__)

CHANNELS = {
    "amrita": "https://www.youtube.com/amritanews/live",
    "kannur": "https://www.youtube.com/@KannurVisionOnlinenews/live",
    "keralavision": "https://www.youtube.com/@keralavisionnews24x7/live"
}

@app.route("/")
def home():
    return jsonify({
        "service": "Kerala Live Channel Checker",
        "status": "online",
        "channels": list(CHANNELS.keys())
    })

@app.route("/check/<channel>")
def check(channel):
    if channel not in CHANNELS:
        return jsonify({"error": "Unknown channel"}), 404

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                CHANNELS[channel],
                download=False
            )

        return jsonify({
            "channel": channel,
            "title": info.get("title"),
            "video_id": info.get("id"),
            "is_live": info.get("is_live"),
            "status": "detected"
        })

    except Exception as error:
        return jsonify({
            "channel": channel,
            "status": "error",
            "message": str(error)
        }), 502


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
