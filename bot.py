# =========================
# IMPORTS
# =========================
from dotenv import load_dotenv
import os
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import discord
from discord import app_commands
import asyncio
import requests
import re
from datetime import datetime
import json
from flask import Flask, request
import threading

# =========================
# FLASK APP
# =========================
app = Flask(__name__)

song_requests = []
radio_channels = {}

requests_updated = False
force_refresh = False

manual_dj = None
last_song = None

last_messages = {}

# THREAD SAFE QUEUE (IMPORTANT FIX)
import queue
dj_command_queue = queue.Queue()

lock = threading.Lock()

# =========================
# FLASK ROUTES
# =========================

@app.route("/")
def home():
    return """
    <h1>🎧 Radio Requests</h1>
    <form action="/request" method="POST">
        <input name="song" placeholder="Song" required>
        <input name="user" placeholder="Name">
        <input name="server" placeholder="Server">
        <button type="submit">Send</button>
    </form>
    """

@app.route("/request", methods=["POST"])
def request_song():
    global requests_updated, force_refresh

    song = request.form.get("song")
    user = request.form.get("user", "Web User")
    server = request.form.get("server", "Web")

    with lock:
        song_requests.append({
            "song": song,
            "user": user,
            "server": server
        })

        song_requests[:] = song_requests[-3:]

        requests_updated = True
        force_refresh = True

    return "OK"


@app.route("/dj")
def dj_panel():
    return """
    <h1>🎛 DJ PANEL</h1>

    <form action="/dj/start" method="POST">
        <input name="name" placeholder="DJ Name">
        <button type="submit">Start DJ</button>
    </form>

    <form action="/dj/end" method="POST">
        <button type="submit">End DJ</button>
    </form>

    <form action="/dj/clear" method="POST">
        <button type="submit">Clear Requests</button>
    </form>

    <form action="/dj/refresh" method="POST">
        <button type="submit">Refresh Scroller</button>
    </form>
    """

@app.route("/dj/start", methods=["POST"])
def dj_start_web():
    dj_command_queue.put({
        "type": "dj_start",
        "name": request.form.get("name", "DJ Web")
    })
    return "DJ started"

@app.route("/dj/end", methods=["POST"])
def dj_end_web():
    dj_command_queue.put({"type": "dj_end"})
    return "DJ ended"

@app.route("/dj/clear", methods=["POST"])
def dj_clear_web():
    dj_command_queue.put({"type": "clear"})
    return "cleared"

@app.route("/dj/refresh", methods=["POST"])
def dj_refresh_web():
    dj_command_queue.put({"type": "refresh"})
    return "refreshed"


def run_web():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), threaded=True)

# =========================
# LOAD ENV
# =========================
load_dotenv()

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET
))

STREAM_URL = "https://streaming.live365.com/a97529"
BANNER_URL = "https://i.imgur.com/tdsxn4c.png"

# =========================
# DISCORD
# =========================
intents = discord.Intents.default()
intents.guilds = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# =========================
# DJ SYSTEM
# =========================

DJ_SCHEDULE = [
    {"name": "DJ Kenny", "start": 14, "end": 16},
    {"name": "DJ Chrissy", "start": 16, "end": 20}
]

def get_current_dj():
    if manual_dj:
        return manual_dj

    hour = datetime.now().hour

    for dj in DJ_SCHEDULE:
        if dj["start"] <= hour < dj["end"]:
            return dj["name"]

    return None

# =========================
# METADATA
# =========================

def get_now_playing():
    try:
        r = requests.get(STREAM_URL, headers={
            "Icy-MetaData": "1",
            "User-Agent": "VLC"
        }, stream=True, timeout=5)

        metaint = int(r.headers.get("icy-metaint", 0))
        if not metaint:
            return "Unknown", "Unknown"

        r.raw.read(metaint)
        length = ord(r.raw.read(1)) * 16
        metadata = r.raw.read(length).decode("utf-8", errors="ignore")

        match = re.search(r"StreamTitle='([^']*)';", metadata)
        if not match:
            return "Unknown", "Unknown"

        raw = match.group(1)

        if " - " in raw:
            a, t = raw.split(" - ", 1)
            return a.strip(), t.strip()

        return "Unknown", raw

    except:
        return "Unknown", "Unknown"

# =========================
# EMBED
# =========================

def create_embed(artist, title, dj):
    embed = discord.Embed(
        title="🔴 ON AIR",
        description=f"**{title}**\n{artist}",
        color=0xff0000
    )

    embed.add_field(name="DJ", value=dj or "None")

    if song_requests:
        text = "\n".join([f"{r['song']} ({r['user']})" for r in song_requests[-3:]])
    else:
        text = "No requests"

    embed.add_field(name="Requests", value=text, inline=False)

    return embed

# =========================
# SCROLLER
# =========================

async def post_scroller():
    dj = get_current_dj()
    if not dj:
        return

    artist, title = get_now_playing()

    embed = create_embed(artist, title, dj)

    for guild in client.guilds:
        channel_id = radio_channels.get(str(guild.id))
        if not channel_id:
            continue

        channel = client.get_channel(int(channel_id))
        if not channel:
            continue

        await channel.send(embed=embed)

# =========================
# DJ LOOP (FIXED)
# =========================

async def dj_panel_loop():
    await client.wait_until_ready()

    global manual_dj, requests_updated, force_refresh, song_requests

    while not client.is_closed():
        try:
            try:
                cmd = dj_command_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.5)
                continue

            if cmd["type"] == "dj_start":
                manual_dj = cmd["name"]
                await post_scroller()

            elif cmd["type"] == "dj_end":
                manual_dj = None

            elif cmd["type"] == "clear":
                song_requests.clear()

            elif cmd["type"] == "refresh":
                await post_scroller()

        except Exception as e:
            print("DJ PANEL ERROR:", e)

# =========================
# LOOP
# =========================

async def song_loop():
    await client.wait_until_ready()

    global last_song

    while not client.is_closed():
        try:
            dj = get_current_dj()
            if not dj:
                await asyncio.sleep(10)
                continue

            artist, title = get_now_playing()
            key = f"{artist}-{title}"

            if key != last_song:
                last_song = key
                await post_scroller()

            await asyncio.sleep(30)

        except:
            await asyncio.sleep(30)

# =========================
# READY
# =========================

@client.event
async def on_ready():
    print("Bot ready")

    threading.Thread(target=run_web, daemon=True).start()

    client.loop.create_task(song_loop())
    client.loop.create_task(dj_panel_loop())

# =========================
# RUN
# =========================
client.run(DISCORD_TOKEN)
