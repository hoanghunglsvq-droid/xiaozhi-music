import os
import json
import asyncio
import threading
import subprocess
import urllib.parse
from flask import Flask, request, Response
import yt_dlp
from duckduckgo_search import DDGS
import websockets

XIAOZHI_MCP_WSS = "wss://api.xiaozhi.me/mcp/?token=eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOjkxMTE3OSwiYWdlbnRJZCI6MTc1Njc0MiwiZW5kcG9pbnRJZCI6ImFnZW50XzE3NTY3NDIiLCJwdXJwb3NlIjoibWNwLWVuZHBvaW50IiwiaWF0IjoxNzk1MDA0NTcwLCJleHAiOjE4MjY1NjIxNzB9.7XfqkhRWB-tB-GCW-YiX0p9IzYPI6mynRBJLVgPT728rlDXllZB4CBPAiPAzIR-BT-_q4bJR9xMtxSBozH-Jgw"

BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:10000").rstrip("/")

flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Xiaozhi Cloud Music Server is Running 24/7!"

def get_youtube_stream_info(query):
    lower_q = query.lower()
    if not any(w in lower_q for w in ["remix", "karaoke", "cover", "live", "lofi", "beat"]):
        search_target = f"ytsearch5:{query} official audio"
    else:
        search_target = f"ytsearch5:{query}"

    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'quiet': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(search_target, download=False)
        entries = info.get('entries', [info])
        selected = None
        for video in entries:
            if not video:
                continue
            dur = video.get('duration', 0)
            title = video.get('title', '').lower()
            if 60 <= dur <= 900 and "reaction" not in title:
                if "karaoke" in lower_q or "karaoke" not in title:
                    selected = video
                    break
        if not selected and entries:
            selected = entries[0]

        webpage_url = selected.get('webpage_url') or selected.get('url')
        encoded_url = urllib.parse.quote(webpage_url, safe='')
        return {
            "title": selected.get('title', query),
            "stream_url": f"{BASE_URL}/stream?url={encoded_url}"
        }

@flask_app.route('/stream')
def stream_audio():
    target_url = request.args.get('url', '')
    def generate():
        cmd = f'yt-dlp -f bestaudio -o - "{target_url}" | ffmpeg -i pipe:0 -f mp3 -ab 128k -ar 24000 -ac 1 pipe:1'
        proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            while True:
                data = proc.stdout.read(4096)
                if not data:
                    break
                yield data
        finally:
            proc.kill()
    return Response(generate(), mimetype="audio/mpeg")

def do_web_search(query):
    with DDGS() as ddgs:
        results = list(ddgs.text(query, region='vn-vi', max_results=3))
        if not results:
            return "Không tìm thấy thông tin."
        return "\n".join([f"- {r['title']}: {r['body']}" for r in results])

async def mcp_bridge_loop():
    while True:
        try:
            async with websockets.connect(XIAOZHI_MCP_WSS, ping_interval=20, ping_timeout=20) as ws:
                print("✔ Đã kết nối thẳng tới Xiaozhi MCP Server!", flush=True)
                async for raw_msg in ws:
                    req = json.loads(raw_msg)
                    req_id = req.get("id")
                    method = req.get("method", "")

                    if method == "initialize":
                        resp = {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "protocolVersion": "2024-11-05",
                                "capabilities": {"tools": {}},
                                "serverInfo": {"name": "Cloud-Vietnam-Music", "version": "1.0.0"}
                            }
                        }
                        await ws.send(json.dumps(resp))

                    elif method == "tools/list":
                        resp = {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "tools": [
                                    {
                                        "name": "play_youtube_music",
                                        "description": "Tìm kiếm và lấy link MP3 phát nhạc YouTube tiếng Việt.",
                                        "inputSchema": {
                                            "type": "object",
                                            "properties": {
                                                "song_name": {"type": "string", "description": "Tên bài hát và ca sĩ"}
                                            },
                                            "required": ["song_name"]
                                        }
                                    },
                                    {
                                        "name": "search_vietnam_web",
                                        "description": "Tra cứu tin tức, thời tiết, thông tin mới nhất bằng tiếng Việt.",
                                        "inputSchema": {
                                            "type": "object",
                                            "properties": {
                                                "query": {"type": "string", "description": "Từ khóa cần tìm"}
                                            },
                                            "required": ["query"]
                                        }
                                    }
                                ]
                            }
                        }
                        await ws.send(json.dumps(resp))

                    elif method == "tools/call":
                        params = req.get("params", {})
                        tool_name = params.get("name")
                        args = params.get("arguments", {})

                        if tool_name == "play_youtube_music":
                            song = args.get("song_name", "")
                            print(f"🎵 Đang tìm nhạc: {song}", flush=True)
                            data = await asyncio.to_thread(get_youtube_stream_info, song)
                            text_out = f"Đã tìm thấy '{data['title']}'. Link phát nhạc MP3: {data['stream_url']}"
                        elif tool_name == "search_vietnam_web":
                            q = args.get("query", "")
                            text_out = await asyncio.to_thread(do_web_search, q)
                        else:
                            text_out = "Không hỗ trợ."

                        resp = {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {"content": [{"type": "text", "text": text_out}]}
                        }
                        await ws.send(json.dumps(resp))
        except Exception as e:
            print(f"⏳ Đang kết nối lại Xiaozhi MCP... ({e})", flush=True)
            await asyncio.sleep(5)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=lambda: asyncio.run(mcp_bridge_loop()), daemon=True).start()
    flask_app.run(host="0.0.0.0", port=port)
