import re
import random
import sqlite3
import time
from datetime import datetime, timedelta
import discord
from discord.ext import tasks

from market import DB_NAME, get_setting, set_setting

_bot = None

# ==========================================
# DATABASE
# ==========================================
def migrate_community_db(cursor):
    cursor.execute('''CREATE TABLE IF NOT EXISTS polls (
        id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER, channel_id INTEGER,
        creator_id INTEGER NOT NULL, question TEXT NOT NULL, options TEXT NOT NULL,
        ends_at TEXT, closed INTEGER DEFAULT 0
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS poll_votes (
        poll_id INTEGER NOT NULL, user_id INTEGER NOT NULL, option_idx INTEGER NOT NULL,
        PRIMARY KEY (poll_id, user_id)
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        channel_id INTEGER, message TEXT NOT NULL, remind_at TEXT NOT NULL
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS user_xp (
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, xp INTEGER DEFAULT 0,
        PRIMARY KEY (guild_id, user_id)
    )''')

# ==========================================
# POLLS
# ==========================================
BAR_LEN = 10

def load_poll(poll_id=None, message_id=None):
    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()
        col, val = ("id", poll_id) if poll_id is not None else ("message_id", message_id)
        row = cur.execute(
            f"SELECT id, message_id, channel_id, creator_id, question, options, ends_at, closed FROM polls WHERE {col} = ?",
            (val,)
        ).fetchone()
        if not row:
            return None
        options = row[5].split("\n")
        counts = [0] * len(options)
        for idx, n in cur.execute("SELECT option_idx, COUNT(*) FROM poll_votes WHERE poll_id = ? GROUP BY option_idx", (row[0],)):
            if idx < len(counts):
                counts[idx] = n
    return {"id": row[0], "message_id": row[1], "channel_id": row[2], "creator_id": row[3],
            "question": row[4], "options": options, "counts": counts, "ends_at": row[6], "closed": bool(row[7])}

def poll_embed(poll):
    counts, total = poll["counts"], sum(poll["counts"])
    best = max(counts)
    lines = []
    for i, opt in enumerate(poll["options"]):
        filled = round(BAR_LEN * counts[i] / total) if total else 0
        pct = round(counts[i] * 100 / total) if total else 0
        crown = " 🏆" if poll["closed"] and total and counts[i] == best else ""
        lines.append(f"**{i + 1}. {opt}**{crown}\n{'▰' * filled}{'▱' * (BAR_LEN - filled)} {counts[i]} vote(s) ({pct}%)")
    if poll["closed"]:
        status = "🔒 Poll closed"
    elif poll["ends_at"]:
        status = f"⏳ Ends <t:{int(datetime.fromisoformat(poll['ends_at']).timestamp())}:R>"
    else:
        status = "🗳️ Open until closed by its creator"
    embed = discord.Embed(
        title=f"📊 {poll['question']}",
        description="\n\n".join(lines) + f"\n\n{status}",
        color=discord.Color.gold() if poll["closed"] else discord.Color.blue()
    )
    embed.set_footer(text=f"Poll #{poll['id']} • {total} vote(s) • Tap a button again to remove your vote")
    return embed

async def handle_vote(interaction: discord.Interaction, idx: int):
    poll = load_poll(message_id=interaction.message.id)
    if not poll or poll["closed"]:
        return await interaction.response.send_message("❌ This poll is closed.", ephemeral=True)
    if idx >= len(poll["options"]):
        return await interaction.response.send_message("❌ Invalid option.", ephemeral=True)
    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()
        current = cur.execute("SELECT option_idx FROM poll_votes WHERE poll_id = ? AND user_id = ?",
                              (poll["id"], interaction.user.id)).fetchone()
        if current and current[0] == idx:
            cur.execute("DELETE FROM poll_votes WHERE poll_id = ? AND user_id = ?", (poll["id"], interaction.user.id))
        else:
            cur.execute("INSERT OR REPLACE INTO poll_votes (poll_id, user_id, option_idx) VALUES (?, ?, ?)",
                        (poll["id"], interaction.user.id, idx))
        conn.commit()
    await interaction.response.edit_message(embed=poll_embed(load_poll(poll_id=poll["id"])))

def _vote_callback(idx):
    async def callback(interaction: discord.Interaction):
        await handle_vote(interaction, idx)
    return callback

class PollView(discord.ui.View):
    """Persistent: buttons use fixed custom_ids (poll_vote_0..4); the poll is found by message ID."""
    def __init__(self, options=None):
        super().__init__(timeout=None)
        for i in range(len(options) if options else 5):
            label = f"{i + 1}. {options[i]}"[:80] if options else f"Option {i + 1}"
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, custom_id=f"poll_vote_{i}")
            btn.callback = _vote_callback(i)
            self.add_item(btn)

async def fetch_channel(channel_id):
    if not channel_id:
        return None
    ch = _bot.get_channel(channel_id)
    if not ch:
        try:
            ch = await _bot.fetch_channel(channel_id)
        except Exception:
            ch = None
    return ch

async def close_poll(poll_id: int) -> bool:
    poll = load_poll(poll_id=poll_id)
    if not poll or poll["closed"]:
        return False
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("UPDATE polls SET closed = 1 WHERE id = ?", (poll_id,))
        conn.commit()
    poll["closed"] = True
    try:
        ch = await fetch_channel(poll["channel_id"])
        msg = await ch.fetch_message(poll["message_id"])
        await msg.edit(embed=poll_embed(poll), view=None)
    except Exception as e:
        print(f"Failed to update the closed poll #{poll_id}: {e}")
    return True

# ==========================================
# REMINDERS
# ==========================================
MAX_REMINDERS = 10

def parse_duration(text: str):
    text = text.strip().lower().replace(" ", "")
    if not re.fullmatch(r"(\d{1,4}[dhm])+", text):
        return None
    units = {"d": "days", "h": "hours", "m": "minutes"}
    total = timedelta()
    for n, unit in re.findall(r"(\d{1,4})([dhm])", text):
        total += timedelta(**{units[unit]: int(n)})
    return total if timedelta(minutes=1) <= total <= timedelta(days=30) else None

async def send_due_reminders():
    with sqlite3.connect(DB_NAME) as conn:
        rows = conn.execute("SELECT id, user_id, channel_id, message FROM reminders WHERE remind_at <= ?",
                            (datetime.now().isoformat(),)).fetchall()
        for row in rows:
            conn.execute("DELETE FROM reminders WHERE id = ?", (row[0],))
        conn.commit()
    for _, user_id, channel_id, message in rows:
        embed = discord.Embed(title="⏰ Reminder", description=message, color=discord.Color.gold())
        try:
            user = await _bot.fetch_user(user_id)
            await user.send(embed=embed)
        except Exception:
            ch = await fetch_channel(channel_id)
            if ch:
                try:
                    await ch.send(f"<@{user_id}>", embed=embed)
                except Exception as e:
                    print(f"Failed to deliver reminder to {user_id}: {e}")

# ==========================================
# LEVELS / XP
# ==========================================
XP_COOLDOWN = 60
_xp_last = {}

def level_from_xp(xp: int) -> int:
    return int((xp / 100) ** 0.5)

async def award_xp(message: discord.Message):
    try:
        if message.guild is None or message.author.bot or get_setting("LEVELS_ENABLED", 1) == 0:
            return
        key = (message.guild.id, message.author.id)
        now = time.time()
        if now - _xp_last.get(key, 0) < XP_COOLDOWN:
            return
        _xp_last[key] = now
        gain = random.randint(15, 25)
        with sqlite3.connect(DB_NAME) as conn:
            cur = conn.cursor()
            cur.execute("INSERT OR IGNORE INTO user_xp (guild_id, user_id, xp) VALUES (?, ?, 0)", key)
            old = cur.execute("SELECT xp FROM user_xp WHERE guild_id = ? AND user_id = ?", key).fetchone()[0]
            cur.execute("UPDATE user_xp SET xp = ? WHERE guild_id = ? AND user_id = ?", (old + gain, *key))
            conn.commit()
        new_level = level_from_xp(old + gain)
        if new_level > level_from_xp(old) and get_setting("LEVELS_ANNOUNCE", 1) == 1:
            await message.channel.send(f"🎉 {message.author.mention} reached **level {new_level}**!")
    except Exception as e:
        print(f"XP award failed: {e}")

# ==========================================
# BACKGROUND LOOP
# ==========================================
@tasks.loop(seconds=30)
async def community_tick():
    try:
        with sqlite3.connect(DB_NAME) as conn:
            due = [r[0] for r in conn.execute("SELECT id FROM polls WHERE closed = 0 AND ends_at IS NOT NULL AND ends_at <= ?",
                                              (datetime.now().isoformat(),))]
        for poll_id in due:
            await close_poll(poll_id)
        await send_due_reminders()
    except Exception as e:
        print(f"Community tick failed: {e}")

@community_tick.before_loop
async def before_community_tick():
    await _bot.wait_until_ready()

def start_community_tasks():
    if not community_tick.is_running():
        community_tick.start()

# ==========================================
# COMMANDS
# ==========================================
def setup_community(bot):
    global _bot
    _bot = bot

    # ---------- /poll ----------
    @bot.tree.command(name="poll", description="Create a poll with voting buttons")
    @discord.app_commands.describe(
        question="What do you want to ask?",
        option1="First option", option2="Second option", option3="Third option (optional)",
        option4="Fourth option (optional)", option5="Fifth option (optional)",
        duration_minutes="Auto-close after this many minutes (optional, max 10080 = 7 days)"
    )
    async def poll_command(interaction: discord.Interaction, question: str, option1: str, option2: str,
                           option3: str = None, option4: str = None, option5: str = None, duration_minutes: int = None):
        options = [o.strip() for o in (option1, option2, option3, option4, option5) if o and o.strip()]
        if len(question) > 200 or any(len(o) > 70 for o in options):
            return await interaction.response.send_message("❌ The question must be 200 characters or fewer, and each option 70 or fewer.", ephemeral=True)
        if len(options) < 2:
            return await interaction.response.send_message("❌ A poll needs at least 2 options.", ephemeral=True)
        if duration_minutes is not None and not 1 <= duration_minutes <= 10080:
            return await interaction.response.send_message("❌ Duration must be between 1 and 10080 minutes.", ephemeral=True)
        ends_at = (datetime.now() + timedelta(minutes=duration_minutes)).isoformat() if duration_minutes else None
        with sqlite3.connect(DB_NAME) as conn:
            poll_id = conn.execute(
                "INSERT INTO polls (creator_id, question, options, ends_at) VALUES (?, ?, ?, ?)",
                (interaction.user.id, question, "\n".join(options), ends_at)
            ).lastrowid
            conn.commit()
        await interaction.response.send_message(embed=poll_embed(load_poll(poll_id=poll_id)), view=PollView(options))
        msg = await interaction.original_response()
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("UPDATE polls SET message_id = ?, channel_id = ? WHERE id = ?", (msg.id, msg.channel.id, poll_id))
            conn.commit()

    @bot.tree.command(name="poll_end", description="Close a poll early (creator or staff only)")
    @discord.app_commands.describe(poll_id="The number shown in the poll's footer")
    async def poll_end_command(interaction: discord.Interaction, poll_id: int):
        poll = load_poll(poll_id=poll_id)
        if not poll or poll["closed"]:
            return await interaction.response.send_message("❌ Poll not found or already closed.", ephemeral=True)
        if interaction.user.id != poll["creator_id"] and not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message("❌ Only the poll's creator or staff can close it.", ephemeral=True)
        await close_poll(poll_id)
        await interaction.response.send_message(f"🔒 Poll `#{poll_id}` closed.", ephemeral=True)

    # ---------- /remind ----------
    @bot.tree.command(name="remind", description="Get a reminder later, e.g. time: 30m, 2h, 1d, 1h30m")
    @discord.app_commands.describe(time="When to remind you: 30m, 2h, 1d, 1h30m (1 minute to 30 days)", message="What should I remind you about?")
    async def remind_command(interaction: discord.Interaction, time: str, message: str):
        delta = parse_duration(time)
        if not delta:
            return await interaction.response.send_message("❌ Use a time like `30m`, `2h`, `1d` or `1h30m` (1 minute to 30 days).", ephemeral=True)
        if len(message) > 500:
            return await interaction.response.send_message("❌ The message must be 500 characters or fewer.", ephemeral=True)
        with sqlite3.connect(DB_NAME) as conn:
            count = conn.execute("SELECT COUNT(*) FROM reminders WHERE user_id = ?", (interaction.user.id,)).fetchone()[0]
            if count >= MAX_REMINDERS:
                return await interaction.response.send_message(f"❌ You already have {MAX_REMINDERS} pending reminders. Cancel one with `/remind_cancel`.", ephemeral=True)
            remind_at = datetime.now() + delta
            rid = conn.execute("INSERT INTO reminders (user_id, channel_id, message, remind_at) VALUES (?, ?, ?, ?)",
                               (interaction.user.id, interaction.channel_id, message, remind_at.isoformat())).lastrowid
            conn.commit()
        await interaction.response.send_message(f"⏰ Reminder `#{rid}` set. I'll remind you <t:{int(remind_at.timestamp())}:R>.", ephemeral=True)

    @bot.tree.command(name="reminders", description="List your pending reminders")
    async def reminders_command(interaction: discord.Interaction):
        with sqlite3.connect(DB_NAME) as conn:
            rows = conn.execute("SELECT id, message, remind_at FROM reminders WHERE user_id = ? ORDER BY remind_at LIMIT 10",
                                (interaction.user.id,)).fetchall()
        if not rows:
            return await interaction.response.send_message("ℹ️ You have no pending reminders.", ephemeral=True)
        lines = [f"`#{rid}` <t:{int(datetime.fromisoformat(at).timestamp())}:R> — {msg[:80]}" for rid, msg, at in rows]
        await interaction.response.send_message("**Your reminders**\n" + "\n".join(lines), ephemeral=True)

    @bot.tree.command(name="remind_cancel", description="Cancel one of your reminders by ID")
    async def remind_cancel_command(interaction: discord.Interaction, reminder_id: int):
        with sqlite3.connect(DB_NAME) as conn:
            deleted = conn.execute("DELETE FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, interaction.user.id)).rowcount
            conn.commit()
        msg = f"🗑️ Reminder `#{reminder_id}` cancelled." if deleted else "❌ Reminder not found."
        await interaction.response.send_message(msg, ephemeral=True)

    # ---------- levels ----------
    @bot.tree.command(name="rank", description="Show your level and XP (or another member's)")
    async def rank_command(interaction: discord.Interaction, member: discord.Member = None):
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
        member = member or interaction.user
        with sqlite3.connect(DB_NAME) as conn:
            row = conn.execute("SELECT xp FROM user_xp WHERE guild_id = ? AND user_id = ?", (interaction.guild.id, member.id)).fetchone()
            xp = row[0] if row else 0
            position = conn.execute("SELECT COUNT(*) + 1 FROM user_xp WHERE guild_id = ? AND xp > ?", (interaction.guild.id, xp)).fetchone()[0]
        level = level_from_xp(xp)
        floor_xp, next_xp = 100 * level ** 2, 100 * (level + 1) ** 2
        filled = round(BAR_LEN * (xp - floor_xp) / (next_xp - floor_xp))
        embed = discord.Embed(title=f"🏅 {member.display_name}", color=discord.Color.blue())
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="Rank", value=f"#{position}" if row else "Unranked", inline=True)
        embed.add_field(name="XP", value=f"{xp} / {next_xp}\n{'▰' * filled}{'▱' * (BAR_LEN - filled)}", inline=False)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="leaderboard", description="Show the top 10 most active members")
    async def leaderboard_command(interaction: discord.Interaction):
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
        with sqlite3.connect(DB_NAME) as conn:
            rows = conn.execute("SELECT user_id, xp FROM user_xp WHERE guild_id = ? ORDER BY xp DESC LIMIT 10", (interaction.guild.id,)).fetchall()
        if not rows:
            return await interaction.response.send_message("ℹ️ Nobody has earned XP yet. Start chatting!", ephemeral=True)
        medals = ["🥇", "🥈", "🥉"]
        lines = [f"{medals[i] if i < 3 else f'`{i + 1}.`'} <@{uid}> — level {level_from_xp(xp)} ({xp} XP)" for i, (uid, xp) in enumerate(rows)]
        await interaction.response.send_message(embed=discord.Embed(title="🏆 Leaderboard", description="\n".join(lines), color=discord.Color.gold()))

    @bot.tree.command(name="level_config", description="Turn XP gain and level-up announcements on or off")
    @discord.app_commands.describe(xp_enabled="Members earn XP for chatting", announce="Announce level-ups in the channel")
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def level_config_command(interaction: discord.Interaction, xp_enabled: bool = None, announce: bool = None):
        if xp_enabled is not None:
            set_setting("LEVELS_ENABLED", int(xp_enabled))
        if announce is not None:
            set_setting("LEVELS_ANNOUNCE", int(announce))
        on, ann = get_setting("LEVELS_ENABLED", 1), get_setting("LEVELS_ANNOUNCE", 1)
        await interaction.response.send_message(
            f"⚙️ **Levels**\nXP gain: **{'On' if on else 'Off'}**\nLevel-up announcements: **{'On' if ann else 'Off'}**", ephemeral=True)