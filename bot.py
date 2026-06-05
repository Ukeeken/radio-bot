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
import traceback
from flask_cors import CORS
 
flask_thread = None
 
requests_enabled = True
 
app = Flask(__name__)
CORS(app)  # ✅ AFTER app is created
 
lock = threading.Lock()
 
@app.route("/")
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
 
        <style>
 
        body{
            font-family:sans-serif;
            background:#111;
            color:white;
            padding:20px;
            margin:0;
        }
 
        .container{
            max-width:500px;
            margin:auto;
        }
 
        h1{
            text-align:center;
        }
 
        input{
            width:100%;
            padding:12px;
            border-radius:8px;
            border:none;
            box-sizing:border-box;
            font-size:16px;
        }
 
        p{
            margin-bottom:12px;
        }
 
        button{
            width:100%;
            padding:14px;
            background:#ff0033;
            color:white;
            border:none;
            border-radius:8px;
            font-size:18px;
            cursor:pointer;
        }
 
        button:hover{
            opacity:0.9;
        }
 
        </style>
    </head>
 
    <body>
 
    <div class="container">
 
        <h1>🎧 Black Sheep Radio Requests</h1>
 
        <form action="/request" method="POST">
 
            <p>
                <input
                    type="text"
                    name="song"
                    placeholder="Song Title"
                    required
                >
            </p>
 
            <p>
                <input
                    type="text"
                    name="artist"
                    placeholder="Artist"
                    required
                >
            </p>
 
            <p>
                <input
                    type="text"
                    name="user"
                    placeholder="Requested By"
                    required
                >
            </p>
 
            <p>
                <input
                    type="text"
                    name="server"
                    placeholder="Discord Server"
                    required
                >
            </p>
 
            <button type="submit">
                🎵 Send Request
            </button>
 
        </form>
 
    </div>
 
    </body>
    </html>
    """
 
@app.route("/status")
def status():
    artist = last_song.split(" - ")[0] if last_song and " - " in last_song else "Unknown"
    title = last_song.split(" - ", 1)[1] if last_song and " - " in last_song else "Unknown"
    return jsonify({
        "dj": manual_dj,
        "artist": artist,
        "title": title,
        "requests": song_requests[-3:],
        "recent": recent_songs[:-1],
        "album_art": last_album_art  # use cached value instead of calling Spotify
    })
 
@app.route("/request", methods=["POST"])
def request_song():
 
    try:
        global requests_updated
        global force_refresh
        global requests_enabled
        global song_requests
 
        if not requests_enabled:
            return """
            <h2>❌ Song requests are currently disabled by the DJ.</h2>
            """
 
        artist = request.form.get("artist") or "Unknown Artist"
        song = request.form.get("song")
 
        user = request.form.get("user") or "Website User"
        server = request.form.get("server") or "Website"
 
        if not song or not artist:
            return "Missing song or artist", 400
 
        with lock:
 
            song_requests.append({
                "artist": artist,
                "song": song,
                "user": user,
                "server": server,
                "source": "web"
            })
 
            song_requests[:] = song_requests[-3:]
 
            requests_updated = True
            force_refresh = True
 
        return f"""
        <h2>✅ Request submitted!</h2>
 
        <p>
        🎵 {artist} - {song}
        </p>
        """
    except Exception as e:
        print("FLASK ERROR:", e)
        traceback.print_exc()
        return "Internal Server Error", 500
 
def run_web():
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        debug=False,
        use_reloader=False
    )
 
@app.route("/player")
def player():
    return open("radio-player.html").read()
 
@app.route("/schedule")
def schedule():
    return open("dj-schedule.html").read()
 
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
 
# FIX: retries=0 prevents spotipy from calling time.sleep() on rate limit,
# which would block the async event loop and kill the Discord heartbeat.
sp = spotipy.Spotify(
    auth_manager=SpotifyClientCredentials(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET
    ),
    requests_timeout=5,
    retries=0
)
 
# =========================
# DJ STATUS
# =========================
 
manual_dj = None
last_song = None
recent_songs = []
 
requests_updated = False
force_refresh = False
 
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
intents.members = True
 
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
 
# =========================
# DJ PERMISSIONS
# =========================
 
async def is_dj_or_admin(interaction: discord.Interaction):
 
    try:
 
        # Bot owner
        if interaction.user.id == OWNER_ID:
            return True
 
        # Must be in a server
        if not interaction.guild:
            return False
 
        member = interaction.guild.get_member(interaction.user.id)
 
        # fallback fetch
        if member is None:
            member = await interaction.guild.fetch_member(interaction.user.id)
 
        if not member:
            return False
 
        # Admins
        if member.guild_permissions.administrator:
            return True
 
        # Allowed DJ roles
        allowed_roles = [
            "djs",
            "radio dj",
            "moderator"
        ]
 
        member_role_names = [
            role.name.lower()
            for role in member.roles
        ]
 
        return any(
            role in allowed_roles
            for role in member_role_names
        )
 
    except Exception as e:
 
        print("PERMISSION ERROR:", e)
        traceback.print_exc()
 
        return False
 
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
                url="https://blacksheepradio.up.railway.app"
            )
        )
 
class DJPanel(discord.ui.View):
 
    def __init__(self):
        super().__init__(timeout=None)
 
    @discord.ui.button(
        label="▶ Start DJ",
        style=discord.ButtonStyle.green,
        custom_id="djpanel_start"
    )
    async def start_dj(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not await is_dj_or_admin(interaction):
            await interaction.response.send_message(
                "❌ DJs or admins only.",
                ephemeral=True
            )
            return
 
        global manual_dj
        global last_song
 
        # FIX: defer first so Discord doesn't time out while we fetch metadata
        await interaction.response.defer(ephemeral=True)
 
        manual_dj = interaction.user.display_name
        last_song = None
 
        try:
            artist, title = get_now_playing()
            if title != "Unknown":
                await post_scroller(artist, title)
        except Exception as e:
            print("start_dj post_scroller error:", e)
 
        await interaction.followup.send(
            f"🎙 DJ session started by {interaction.user.display_name}",
            ephemeral=True
        )
 
    @discord.ui.button(
        label="⏹ End DJ",
        style=discord.ButtonStyle.red,
        custom_id="djpanel_end"
    )
    async def end_dj(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not await is_dj_or_admin(interaction):
 
            await interaction.response.send_message(
                "❌ DJs or admins only.",
                ephemeral=True
            )
            return
    
        global manual_dj
        global last_song
 
        manual_dj = None
        last_song = None
 
        await clear_all_scrollers()
 
        await interaction.response.send_message(
            "🔴 DJ session ended.",
            ephemeral=True
        )
 
    @discord.ui.button(
        label="🧹 Clear Requests",
        style=discord.ButtonStyle.gray,
        custom_id="djpanel_clear"
    )
    async def clear_requests_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not await is_dj_or_admin(interaction):
 
            await interaction.response.send_message(
                "❌ DJs or admins only.",
                ephemeral=True
            )
            return
        
        global requests_updated
        global force_refresh
 
        song_requests.clear()
 
        requests_updated = True
        force_refresh = True
 
        await interaction.response.send_message(
            "🧹 Requests cleared.",
            ephemeral=True
        )
 
    @discord.ui.button(
        label="🎵 Toggle Requests",
        style=discord.ButtonStyle.blurple,
        custom_id="djpanel_toggle_requests"
    )
    async def toggle_requests(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not await is_dj_or_admin(interaction):
 
            await interaction.response.send_message(
                "❌ DJs or admins only.",
                ephemeral=True
            )
            return
        
        global requests_enabled
 
        requests_enabled = not requests_enabled
 
        status = (
            "ENABLED ✅"
            if requests_enabled
            else "DISABLED ❌"
        )
 
        await interaction.response.send_message(
            f"🎵 Song requests are now {status}",
            ephemeral=True
        )
 
def get_current_dj():
 
    return manual_dj
 
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
            timeout=5
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
            r"StreamTitle='(.*?)';StreamUrl=",
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
            "Advertisement",
            "OFF AIR"
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
 
# FIX: Run get_album_art in a thread executor so Spotify network calls
# can never block the async event loop and kill the Discord heartbeat.
async def get_album_art_async(song_query):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_album_art, song_query)
 
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
        value="[▶ Click Here To Listen](https://blacksheepradio.up.railway.app/player)",
        inline=True
    )
    # LIVE REQUESTS
    if song_requests:
        latest_requests = song_requests[-3:]
 
        request_lines = []
 
        for r in latest_requests:
            song = r.get("song", "Unknown Song")
            artist = r.get("artist", "Unknown Artist")
            user = r.get("user", "Unknown")
            server = r.get("server", "Unknown")
 
            request_lines.append(
                f"• 🎵 **{artist} - {song}**\n"
                f"  👤 {user}\n"
                f"  🌐 {server}"
            )
 
        embed.add_field(
            name="🎧 Live Requests",
            value="\n\n".join(request_lines)[:1020],
            inline=False
        )
    else:
        embed.add_field(
            name="🎧 Live Requests",
            value="No requests yet 🎵",
            inline=False
        )
 
    embed.set_image(
        url=album_art or BANNER_URL
    )
 
    embed.set_thumbnail(
        text="Live365 Stream • Auto-updating"
    )
 
    return embed
 
# =========================
# DELETE OLD SCROLLER
# =========================
 
async def delete_old_message(guild_id):
 
    message_id = last_messages.get(str(guild_id))
 
    if not message_id:
        return
 
    channel_id = radio_channels.get(str(guild_id))
 
    if not channel_id:
        return
 
    try:
        channel = client.get_channel(int(channel_id))
 
        if channel is None:
            channel = await client.fetch_channel(int(channel_id))
 
        msg = await channel.fetch_message(message_id)
        await msg.delete()
 
        print(f"Deleted old message in guild {guild_id}")
 
    except Exception as e:
        print("Delete error:", e)
 
    last_messages.pop(str(guild_id), None)
 
# =========================
# POST SCROLLER
# =========================
last_album_art = None  # add with other globals

async def post_scroller(artist, title):
 
    dj = get_current_dj()
 
    if not dj:
        return
 
    # FIX: use async version so Spotify never blocks the event loop
    album_art = await get_album_art_async(f"{artist} {title}") or BANNER_URL
 
    embed = create_embed(artist, title, dj, album_art)
 
    for guild in client.guilds:
 
        print(f"CHECKING GUILD: {guild.name}")
 
        channel_id = radio_channels.get(str(guild.id))
 
        print(f"CHANNEL ID: {channel_id}")
 
        if not channel_id:
            print(f"No setup for {guild.name}")
            continue
 
        try:
 
            channel = client.get_channel(int(channel_id))
 
            if channel is None:
                channel = await client.fetch_channel(int(channel_id))
 
            await delete_old_message(guild.id)
 
            print(f"Attempting send in: {channel.name}")
 
            msg = await channel.send(
                embed=embed,
                view=RequestView()
            )
 
            last_messages[str(guild.id)] = msg.id
 
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
    if not await is_dj_or_admin(interaction):
 
        await interaction.response.send_message(
            "❌ DJs or admins only.",
            ephemeral=True
        )
        return
 
    global manual_dj
    global last_song
 
    await interaction.response.defer(ephemeral=True)
 
    manual_dj = name
    last_song = None
 
    try:
        artist, title = get_now_playing()
        print("CURRENT SONG:", artist, "-", title)
        if title != "Unknown":
            await post_scroller(artist, title)
    except Exception as e:
        print("DJ_START post_scroller error:", e)
 
    await interaction.followup.send(
        f"🎙 DJ LIVE: **{name}** is now on air globally!",
        ephemeral=True
    )
 
@tree.command(
    name="dj_end",
    description="End DJ session globally"
)
async def dj_end(interaction: discord.Interaction):
    if not await is_dj_or_admin(interaction):
 
        await interaction.response.send_message(
            "❌ DJs or admins only.",
            ephemeral=True
        )
        return
    global manual_dj
    global last_song
 
    manual_dj = None
    last_song = None
 
    await clear_all_scrollers()
 
    await asyncio.sleep(2)
 
    await clear_all_scrollers()
 
    await interaction.response.send_message(
        "🔴 DJ session ended globally.",
        ephemeral=True
    )
 
@tree.command(
    name="clear_requests",
    description="Clear all song requests"
)
async def clear_requests(interaction: discord.Interaction):
    if not await is_dj_or_admin(interaction):
 
        await interaction.response.send_message(
            "❌ DJs or admins only.",
            ephemeral=True
        )
        return
    global requests_updated, force_refresh
 
    song_requests.clear()
 
    requests_updated = True
    force_refresh = True
 
    await interaction.response.send_message(
        "🧹 Song requests cleared and scroller updated.",
        ephemeral=True
    )
 
@tree.command(name="request", description="Request a song")
async def request_song_command(
    interaction: discord.Interaction,
    song: str,
    artist: str
):
 
    global requests_enabled
    global requests_updated
    global force_refresh
 
    if not requests_enabled:
 
        await interaction.response.send_message(
            "❌ Song requests are currently disabled by the DJ.",
            ephemeral=True
        )
        return
 
    song_requests.append({
        "song": song,
        "artist": artist,
        "user": interaction.user.display_name,
        "user_id": interaction.user.id,
        "server": interaction.guild.name if interaction.guild else "DM",
        "server_id": interaction.guild.id if interaction.guild else 0,
        "source": "discord"
    })
 
    song_requests[:] = song_requests[-3:]
 
    requests_updated = True
    force_refresh = True
 
    await interaction.response.send_message(
        f"🎵 Request added: **{artist} - {song}**",
        ephemeral=True
    )
 
@tree.command(
    name="dj_panel",
    description="Post DJ control panel"
)
async def dj_panel(interaction: discord.Interaction):
 
    try:
 
        await interaction.response.defer(ephemeral=True)
 
        embed = discord.Embed(
            title="🎛 Black Sheep Radio DJ Panel",
            description=(
                "Control the radio bot here.\n\n"
                "▶ Start DJ\n"
                "⏹ End DJ\n"
                "🧹 Clear Requests"
            ),
            color=0xff0033
        )
 
        await interaction.followup.send(
            embed=embed,
            view=DJPanel()
        )
 
        print("DJ panel posted")
 
    except Exception as e:
 
        print("DJ PANEL ERROR:")
        traceback.print_exc()
 
        try:
            await interaction.followup.send(
                f"❌ DJ panel failed:\n{e}",
                ephemeral=True
            )
        except:
            pass
 
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
 
                if title == "Unknown":
                    await asyncio.sleep(30)
                    continue
 
                song_key = f"{artist} - {title}"
 
                global requests_updated, force_refresh
 
                should_update = False
 
                if song_key != last_song:
                    recent_songs.append({"artist": artist, "title": title})
                    recent_songs[:] = recent_songs[-5:]
                    last_song = song_key
                    should_update = True
 
                if requests_updated or force_refresh:
                    should_update = True
 
                if should_update:
                    requests_updated = False
                    force_refresh = False
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
        client.add_view(DJPanel())
 
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
 
    print(f"ERROR IN EVENT: {event}")
 
    traceback.print_exc()
 
 
# =========================
# RUN BOT
# =========================
 
client.run(
    DISCORD_TOKEN,
    reconnect=True
)
