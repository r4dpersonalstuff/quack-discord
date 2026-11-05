import os
import sqlite3
from datetime import datetime, timedelta
import discord
from discord.ext import commands, tasks

# 1. Initialize Intents & Bot
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 2. Database Initialization (SQLite)
DB_NAME = "auto_messages.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS auto_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            delay_minutes INTEGER NOT NULL,
            message TEXT NOT NULL,
            next_run TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ==========================================
# BACKGROUND TASK (CHECKS DB EVERY MINUTE)
# ==========================================
@tasks.loop(minutes=1)
async def check_scheduled_messages():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    now_str = datetime.now().isoformat()
    # Find all messages that are due to be sent
    cursor.execute("SELECT id, channel_id, delay_minutes, message FROM auto_messages WHERE next_run <= ?", (now_str,))
    records = cursor.fetchall()

    for record_id, channel_id, delay_minutes, message in records:
        channel = bot.get_channel(channel_id)
        if channel:
            try:
                await channel.send(message)
            except Exception as e:
                print(f"Error sending message to channel {channel_id}: {e}")
        
        # Calculate and update the next run time
        next_run_time = (datetime.now() + timedelta(minutes=delay_minutes)).isoformat()
        cursor.execute("UPDATE auto_messages SET next_run = ? WHERE id = ?", (next_run_time, record_id))
    
    conn.commit()
    conn.close()

@check_scheduled_messages.before_loop
async def before_check_scheduled_messages():
    await bot.wait_until_ready()

# ==========================================
# SLASH COMMANDS FOR DISCORD MANAGEMENT
# ==========================================

# Command 1: Add a new automated message schedule
@bot.tree.command(name="add_auto_msg", description="Set up a recurring message for a specific channel")
@discord.app_commands.describe(
    channel="Select the target text channel",
    delay_minutes="Interval delay in minutes (e.g., 60 for 1 hour, 120 for 2 hours)",
    message="The message content to send automatically"
)
async def add_auto_msg(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    delay_minutes: int,
    message: str
):
    if delay_minutes < 1:
        await interaction.response.send_message("❌ Delay must be at least 1 minute.", ephemeral=True)
        return

    next_run = (datetime.now() + timedelta(minutes=delay_minutes)).isoformat()
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO auto_messages (channel_id, delay_minutes, message, next_run) VALUES (?, ?, ?, ?)",
        (channel.id, delay_minutes, message, next_run)
    )
    conn.commit()
    conn.close()

    await interaction.response.send_message(
        f"✅ Auto-message scheduled for {channel.mention} every {delay_minutes} minute(s)!\n**Message:** {message}",
        ephemeral=True
    )

# Command 2: List all active automated messages
@bot.tree.command(name="list_auto_msgs", description="View all active recurring automated messages")
async def list_auto_msgs(interaction: discord.Interaction):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, channel_id, delay_minutes, message FROM auto_messages")
    records = cursor.fetchall()
    conn.close()

    if not records:
        await interaction.response.send_message("ℹ️ No active auto-messages found.", ephemeral=True)
        return

    embed = discord.Embed(
        title="📋 Active Automated Messages",
        color=discord.Color.blue()
    )
    
    for record_id, channel_id, delay_minutes, message in records:
        embed.add_field(
            name=f"ID #{record_id} | Channel: <#{channel_id}>",
            value=f"**Interval:** Every {delay_minutes} min\n**Message:** {message}",
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)

# Command 3: Delete an automated message schedule by ID
@bot.tree.command(name="delete_auto_msg", description="Delete an automated message schedule by ID")
@discord.app_commands.describe(msg_id="The ID of the auto-message to delete (find ID via /list_auto_msgs)")
async def delete_auto_msg(interaction: discord.Interaction, msg_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM auto_messages WHERE id = ?", (msg_id,))
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted_count > 0:
        await interaction.response.send_message(f"🗑️ Successfully deleted auto-message ID #{msg_id}.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Auto-message ID #{msg_id} not found.", ephemeral=True)

# BOT EVENTS
@bot.event
async def on_ready():
    print(f"Bot successfully online as: {bot.user}")
    
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
        
    if not check_scheduled_messages.is_running():
        check_scheduled_messages.start()

# RUN BOT
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    print("ERROR: DISCORD_TOKEN is missing!")
else:
    bot.run(TOKEN)
