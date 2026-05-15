song_requests = []
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
from flask import Flask, request, jsonify
import threading

app = Flask(__name__)

@app.route("/")
def home():
    return """
    <h1>🎧 Black Sheep Radio Requests</h1>
    <form action="/request" method="POST">
        <input name="song" placeholder="Enter song request" required>
        <button type="submit">Send Request</button>
    </form>
    """
@app.route("/request", methods=["POST"])
def request_song():
    song = request.form.get("song")

    if song:
        song_requests.append(song)

    return "OK"

def run_web():
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080))
    )


# =========================
# LOAD ENV
# =========================

load_dotenv()

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
    raise ValueError("Missing Spotify environment variables")

if not DISCORD_TOKEN:
    raise ValueError("Missing Discord token")

# =========================
# CONFIG
# =========================

STREAM_URL = "https://streaming.live365.com/a97529"
BANNER_URL = "https://i.imgur.com/tdsxn4c.png"
OWNER_ID = 1041766723717693450

sp = spotipy.Spotify(
    auth_manager=SpotifyClientCredentials(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET
    )
)

# =========================
# DJ SCHEDULE
# =========================

DJ_SCHEDULE = [
    {"name": "DJ Kenny", "start_hour": 14, "end_hour": 16},
    {"name": "DJ Chrissy", "start_hour": 16, "end_hour": 20},
]
manual_dj = None
last_song = None

# guild_id -> message
last_messages = {}

# guild_id -> channel_id
radio_channels = {}

loop_started = False
song_task = None
web_started = False

# =========================
# SAVE / LOAD
# =========================

def save_channels():
    with open("radio_channels.json", "w") as f:
        json.dump(radio_channels, f, indent=4)

def load_channels():
    global radio_channels

    try:
        with open("radio_channels.json", "r") as f:
            radio_channels = json.load(f)
    except:
        radio_channels = {}

# =========================
# DISCORD SETUP
# =========================

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# =========================
# REQUEST BUTTON
# =========================

class RequestView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

        self.add_item(
            discord.ui.Button(
                label="🎵 Request Song",
                style=discord.ButtonStyle.link,
                url="https://worker-production-fc98.up.railway.app"
            )
        )

# =========================
# DJ SYSTEM
# =========================

def get_current_dj():

    if manual_dj:
        return manual_dj

    now = datetime.now().hour

    for dj in DJ_SCHEDULE:

        if dj["start_hour"] <= now < dj["end_hour"]:
            return dj["name"]

    return None

# =========================
# LIVE365 METADATA
# =========================

def get_now_playing():

    try:

        headers = {
            "Icy-MetaData": "1",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "VLC/3.0.18"
        }

        response = requests.get(
            STREAM_URL,
            headers=headers,
            stream=True,
            timeout=8
        )

        metaint = response.headers.get("icy-metaint")

        if not metaint:
            print("NO ICY METAINT")
            return "Unknown", "Unknown"

        metaint = int(metaint)

        stream = response.raw

        # skip audio block
        stream.read(metaint)

        # metadata size byte
        metadata_length = stream.read(1)

        if not metadata_length:
            return "Unknown", "Unknown"

        metadata_length = metadata_length[0] * 16

        if metadata_length <= 0:
            return "Unknown", "Unknown"

        metadata = stream.read(metadata_length)

        metadata = metadata.decode(
            "utf-8",
            errors="ignore"
        )

        print("RAW:", repr(metadata))

        match = re.search(
            r"StreamTitle='([^']*)';",
            metadata
        )

        if not match:
            return "Unknown", "Unknown"

        raw = match.group(1).strip()

        raw = raw.replace("\x00", "")
        raw = raw.strip()

        if not raw:
            return "Unknown", "Unknown"

        # Remove ads / station IDs
        bad_values = [
            "Live365",
            "Black Sheep Radio",
            "Advertisement"
        ]

        for bad in bad_values:
            if bad.lower() in raw.lower():
                return "Unknown", "Unknown"

        # normalize separators
        raw = (
            raw.replace(" – ", " - ")
               .replace(" — ", " - ")
               .replace(" ~ ", " - ")
        )

        print("CLEANED:", raw)

        if " - " in raw:

            artist, title = raw.split(" - ", 1)

            artist = artist.strip()
            title = title.strip()

            if artist and title:
                return artist, title

        return "Unknown", raw

    except Exception as e:

        print("METADATA ERROR:", repr(e))

        return "Unknown", "Unknown"

def spotify_enrich(artist, title):

    try:
        query = f"{artist} {title}".strip()

        results = sp.search(q=query, type="track", limit=1)

        items = results["tracks"]["items"]

        if not items:
            return artist, title

        track = items[0]

        return artist, title

    except:
        return artist, title

# =========================
# SPOTIFY ALBUM ART
# =========================

def get_album_art(song_query):

    try:

        results = sp.search(
            q=song_query,
            type="track",
            limit=1
        )

        items = results["tracks"]["items"]

        if not items:
            return None

        images = items[0]["album"].get("images", [])

        if not images:
            return None

        return images[0]["url"]

    except Exception as e:
        print("Spotify error:", e)
        return None

# =========================
# CREATE EMBED
# =========================

def create_embed(artist, title, dj, album_art):

    embed = discord.Embed(
        title="🔴 ON AIR • Black Sheep Radio",
        description=f"🎵 **{title}**\n👤 {artist}",
        color=0xff0033
    )

    embed.add_field(
        name="🎙 DJ",
        value=dj,
        inline=True
    )

    embed.add_field(
        name="📻 Listen Live",
        value="[▶ Click Here To Listen](https://thechatbarcommunity.org/radio-player/)",
        inline=True
    )
        # LIVE REQUESTS
    if song_requests:
        latest_requests = song_requests[-5:]  # last 5 requests

        embed.add_field(
            name="🎧 Live Requests",
            value="\n".join(f"• {r}" for r in latest_requests),
            inline=False
        )
    else:
        embed.add_field(
            name="🎧 Live Requests",
            value="No requests yet 🎵",
            inline=False
        )

    embed.set_thumbnail(
        url=album_art or BANNER_URL
    )

    embed.set_footer(
        text="Live365 Stream • Auto-updating"
    )

    return embed

# =========================
# DELETE OLD SCROLLER
# =========================

async def delete_old_message(guild_id):

    message_id = last_messages.get(guild_id)

    if not message_id:
        return

    channel_id = radio_channels.get(str(guild_id))

    if not channel_id:
        return

    try:

        channel = await client.fetch_channel(int(channel_id))

        msg = await channel.fetch_message(message_id)

        await msg.delete()

        print(f"Deleted old message in guild {guild_id}")

    except discord.NotFound:
        print("Old message already deleted")

    except Exception as e:
        print("Delete error:", e)

    last_messages.pop(guild_id, None)

# =========================
# POST SCROLLER
# =========================

async def post_scroller(artist, title):

    dj = get_current_dj()

    if not dj:
        return

    album_art = BANNER_URL

    embed = create_embed(artist, title, dj, album_art)

    for guild in client.guilds:

        print(f"CHECKING GUILD: {guild.name}")

        channel_id = radio_channels.get(str(guild.id))

        print(f"CHANNEL ID: {channel_id}")

        if not channel_id:
            print(f"No setup for {guild.name}")
            continue

        try:

            channel = await client.fetch_channel(int(channel_id))

            if not channel:
                print(f"Cannot find channel for {guild.name}")
                continue

            await delete_old_message(guild.id)

            print(f"Attempting send in: {channel.name}")

            msg = await channel.send(
                embed=embed,
                view=RequestView()
            )

            last_messages[guild.id] = msg.id

            print(f"Posted in {guild.name}")

        except Exception as e:

            print("STREAM ERROR")
            print(type(e))
            print(e)

# =========================
# REMOVE ALL SCROLLERS
# =========================

async def clear_all_scrollers():

    for guild_id in list(last_messages.keys()):

        try:
            await delete_old_message(guild_id)
        except:
            pass

# =========================
# SLASH COMMANDS
# =========================

@tree.command(
    name="setup_radio",
    description="Set radio channel"
)
async def setup_radio(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):

    if (
        not interaction.user.guild_permissions.administrator
        and interaction.user.id != OWNER_ID
    ):

        await interaction.response.send_message(
            "❌ Admin only.",
            ephemeral=True
        )

        return

    radio_channels[str(interaction.guild.id)] = channel.id

    save_channels()

    print("SAVED CHANNELS:", radio_channels)

    await interaction.response.send_message(
        f"🎧 Radio channel set to {channel.mention}",
        ephemeral=True
    )

@tree.command(
    name="dj_start",
    description="Start DJ session globally"
)
async def dj_start(
    interaction: discord.Interaction,
    name: str
):

    global manual_dj
    global last_song

    try:

        await interaction.response.defer(ephemeral=True)

        manual_dj = name
        last_song = None

        artist, title = get_now_playing()

        print("CURRENT SONG:", artist, "-", title)

        if title != "Unknown":
            await post_scroller(artist, title)

        await interaction.followup.send(
            f"🎙 DJ LIVE: **{name}** is now on air globally!",
            ephemeral=True
        )

    except Exception as e:

        print("DJ_START ERROR:", e)

        try:
            await interaction.followup.send(
                f"❌ Error starting DJ session:\n{e}",
                ephemeral=True
            )
        except:
            pass

@tree.command(
    name="dj_end",
    description="End DJ session globally"
)
async def dj_end(interaction: discord.Interaction):

    global manual_dj
    global last_song

    manual_dj = None
    last_song = None

    await clear_all_scrollers()

    await interaction.response.send_message(
        "🔴 DJ session ended globally.",
        ephemeral=True
    )

@tree.command(name="clear_requests", description="Clear song requests")
async def clear_requests(interaction: discord.Interaction):

    song_requests.clear()

    await interaction.response.send_message(
        "🧹 Song requests cleared.",
        ephemeral=True
    )

# =========================
# SONG LOOP
# =========================

async def song_loop():

    global last_song

    await client.wait_until_ready()

    while not client.is_closed():

        try:

            current_dj = get_current_dj()

            if current_dj:

                artist, title = get_now_playing()

                # only enrich if we got something usable
                if title == "Unknown":
                    await asyncio.sleep(30)
                    continue

                # optional enrichment (safe fallback)
                # artist, title = spotify_enrich(artist, title)

                song_key = f"{artist} - {title}"

                if song_key != last_song:
                    last_song = song_key
                    await post_scroller(artist, title)

            else:

                if last_messages:
                    await clear_all_scrollers()

            await asyncio.sleep(30)

        except Exception as e:

            print("Loop error:")
            traceback.print_exc()

            await asyncio.sleep(30)

# =========================
# READY EVENT
# =========================

@client.event
async def on_ready():

    global loop_started
    global web_started
    global song_task

    try:

        load_channels()

        client.add_view(RequestView())

        if not web_started:

            threading.Thread(
                target=run_web,
                daemon=True
            ).start()

            web_started = True

        synced = await tree.sync()

        print(f"Synced {len(synced)} command(s)")

        print(f"Logged in as {client.user}")

        print("Loaded radio channels:", radio_channels)

        if not loop_started:

            if song_task is None:
                song_task = asyncio.create_task(song_loop())

            loop_started = True

            print("Song loop started")

    except Exception as e:

        import traceback

        print("ON_READY ERROR:")
        traceback.print_exc()

@client.event
async def on_disconnect():
    print("Bot disconnected from Discord")

@client.event
async def on_resumed():
    print("Discord session resumed") 

@client.event
async def on_error(event, *args, **kwargs):

    import traceback

    print(f"ERROR IN EVENT: {event}")

    traceback.print_exc()

import traceback

# =========================
# RUN BOT
# =========================

client.run(
    DISCORD_TOKEN,
    reconnect=True
)