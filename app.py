
import os
import requests
import yt_dlp

from importlib.metadata import version, PackageNotFoundError
from urllib.parse import urljoin, quote, urlparse
from flask import Flask, Response, jsonify, redirect, request

app = Flask(__name__)

# YouTube Live channel IDs
CHANNELS = {
    "amrita": "0JE1N_HQc1M",
    "kannur": "ldYhjkWYNDE",
    "keralavision": "3446A6FzW3o"
}

POT_SERVER = "http://127.0.0.1:4416"


# 1. Home route
@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "channels": list(CHANNELS.keys()),
        "diagnostics": "/diagnostics",
        "health": "/health"
    })


# 2. Health check for Render
@app.route("/health")
def health():
    return "OK", 200


# 3. Diagnostics: yt-dlp, plugin and POT server
@app.route("/diagnostics")
def diagnostics():
    result = {
        "status": "running",
        "yt_dlp_version": yt_dlp.version.__version__,
        "pot_plugin": "unknown",
        "pot_server": "unknown"
    }

    try:
        result["pot_plugin"] = version(
            "bgutil-ytdlp-pot-provider"
        )
    except PackageNotFoundError:
        result["pot_plugin"] = "NOT INSTALLED"

    try:
        r = requests.get(
            POT_SERVER + "/ping",
            timeout=5
        )

        try:
            server_info = r.json()
        except ValueError:
            server_info = r.text[:500]

        result["pot_server"] = {
            "http_status": r.status_code,
            "response": server_info
        }

    except Exception as e:
        result["pot_server"] = {
            "error": str(e)
        }

    return jsonify(result)


# 4. YouTube Live extractor
@app.route("/channel/<name>.m3u8")
@app.route("/<name>.m3u8")
def youtube_channel(name):

    video_id = CHANNELS.get(name.lower())

    if not video_id:
        return "Channel not found", 404

    youtube_url = (
        f"https://www.youtube.com/watch?v={video_id}"
    )

    ydl_opts = {
        "format": "best",
        "quiet": False,
        "verbose": True,
        "no_warnings": False,
        "noplaylist": True,
        "extractor_args": {
            "youtubepot-bgutilhttp": {
                "base_url": POT_SERVER
            }
        }
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:

            info = ydl.extract_info(
                youtube_url,
                download=False
            )

            stream_url = info.get("url")

            if not stream_url:
                return "Stream URL not found", 404

            return redirect(
                stream_url,
                code=302,
                Response=Response
            )

    except Exception as e:
        app.logger.exception(
            "YouTube extraction failed for %s",
            name
        )
        return str(e), 500


# 5. Proxy for HLS playlists and segments
@app.route("/proxy")
def proxy_stream():

    target_url = request.args.get("url")

    if not target_url:
        return "Missing url parameter", 400

    parsed = urlparse(target_url)

    if parsed.scheme not in ("http", "https"):
        return "Only HTTP and HTTPS URLs are allowed", 400

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": "https://bhoomtv.org/",
        "Origin": "https://bhoomtv.org"
    }

    try:
        req = requests.get(
            target_url,
            headers=headers,
            stream=True,
            timeout=20
        )

        content_type = req.headers.get(
            "content-type", ""
        )

        is_playlist = (
            ".m3u8" in parsed.path.lower()
            or "mpegurl" in content_type.lower()
        )

        if is_playlist:

            lines = []

            base_proxy = (
                request.host_url.rstrip("/")
                + "/proxy?url="
            )

            for line in req.text.splitlines():

                stripped = line.strip()

                if stripped and not stripped.startswith("#"):

                    full_url = urljoin(
                        target_url,
                        stripped
                    )

                    lines.append(
                        base_proxy
                        + quote(full_url, safe="")
                    )

                else:
                    lines.append(line)

            req.close()

            response = Response(
                "\n".join(lines) + "\n",
                status=req.status_code,
                content_type="application/vnd.apple.mpegurl"
            )

            response.headers["Cache-Control"] = "no-store"
            return response

        def generate():
            try:
                for chunk in req.iter_content(
                    chunk_size=8192
                ):
                    if chunk:
                        yield chunk
            finally:
                req.close()

        response = Response(
            generate(),
            status=req.status_code,
            content_type=content_type
        )

        response.headers["Cache-Control"] = "no-store"
        return response

    except Exception as e:
        app.logger.exception("Proxy request failed")
        return str(e), 500


# 6. Local development
if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
