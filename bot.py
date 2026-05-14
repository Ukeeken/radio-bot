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

song_requests = []

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
        print("NEW REQUEST:", song)

    return """
    <h2>✅ Request sent!</h2>
    <a href="/">Back</a>
    """
@app.route("/requests")
def get_requests():
    return jsonify(song_requests)

def run_web():
    app.run(host="0.0.0.0", port=8080)


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

        response = requests.get(
            "https://streaming.live365.com/a97529",
            headers={
                "Icy-MetaData": "1",
                "User-Agent": "Mozilla/5.0"
            },
            stream=True,
            timeout=10
        )

        metaint_header = response.headers.get("icy-metaint")

        if not metaint_header:
            print("No icy-metaint header found")
            return None

        metaint = int(metaint_header)

        stream = response.raw

        stream.read(metaint)

        metadata_length = stream.read(1)

        if not metadata_length:
            print("No metadata length")
            return None

        metadata_length = metadata_length[0] * 16

        metadata = stream.read(metadata_length).decode(
            "utf-8",
            errors="ignore"
        )

        print("RAW METADATA:", metadata)

        match = re.search(
            r"StreamTitle='([^']*)';",
            metadata
        )

        if match:

            song = match.group(1).strip()

            if song:
                return song

        return None

    except Exception as e:

        print(f"ERROR in {guild.name}")
        print(type(e))
        print(e)

        return None

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

def create_embed(song, dj, album_art):

    embed = discord.Embed(
        title="🔴 ON AIR • LIVE BROADCAST",
        description=f"🎵 **{song}**",
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

    old_msg = last_messages.get(guild_id)

    if old_msg:

        try:
            await old_msg.delete()
        except:
            pass

        last_messages.pop(guild_id, None)

# =========================
# POST SCROLLER
# =========================

async def post_scroller(song):

    dj = get_current_dj()

    if not dj:
        return

    album_art = get_album_art(song)

    embed = create_embed(
        song,
        dj,
        album_art
    )

    for guild in client.guilds:
        print(f"CHECKING GUILD: {guild.name}")

        channel_id = radio_channels.get(str(guild.id))
        print(f"CHANNEL ID: {channel_id}")

        if not channel_id:
            print(f"No setup for {guild.name}")
            continue

        channel = await client.fetch_channel(int(channel_id))

        if not channel:
            print(f"Cannot find channel for {guild.name}")
            continue

        try:

            await delete_old_message(guild.id)

            print(f"Attempting send in: {channel.name}")

            msg = await channel.send(
                embed=embed,
                view=RequestView()
            )

            last_messages[guild.id] = msg

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
    global last_messages

    await interaction.response.defer(ephemeral=True)

    manual_dj = name

    # FORCE repost to all servers
    last_song = None

    # Optional: clear cached messages
    last_messages.clear()

    song = get_now_playing()

    print("CURRENT SONG:", song)

    # Immediately post if song exists
    if song:
        await post_scroller(song)

    await interaction.followup.send(
        f"🎙 DJ LIVE: **{name}** is now on air globally!",
        ephemeral=True
    )

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

                song = get_now_playing()

                if song and song != last_song:

                    print("New song:", song)

                    last_song = song

                    await post_scroller(song)

            else:

                if last_messages:
                    await clear_all_scrollers()

            await asyncio.sleep(15)

        except Exception as e:

            print("Loop error:", e)

            await asyncio.sleep(15)

# =========================
# READY EVENT
# =========================

@client.event
async def on_ready():

    global loop_started

    load_channels()

    client.add_view(RequestView())

    threading.Thread(target=run_web).start()

    await tree.sync()

    print(f"Logged in as {client.user}")

    print("Loaded radio channels:", radio_channels)

    if not loop_started:

        asyncio.create_task(song_loop())

        loop_started = True

# =========================
# RUN BOT
# =========================

client.run(DISCORD_TOKEN)