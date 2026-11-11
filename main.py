import os
import random
import sqlite3
from datetime import datetime, timedelta
import discord
from discord.ext import commands, tasks

from market import OpenMarketRequestView, MarketReviewView, DB_NAME, RemovePostModal, get_setting, set_setting

# ==========================================
# 1. BOT INITIALIZATION & DATABASE SETUP
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS channel_schedules (
            channel_id INTEGER PRIMARY KEY,
            delay_minutes INTEGER NOT NULL,
            next_run TEXT NOT NULL
        )''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS auto_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            message TEXT NOT NULL
        )''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS pending_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, author_id INTEGER NOT NULL,
            target_channel_id INTEGER NOT NULL, title TEXT NOT NULL,
            description TEXT NOT NULL, image_url TEXT, status TEXT DEFAULT 'PENDING'
        )''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS market_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, author_id INTEGER NOT NULL,
            listing_type TEXT NOT NULL, skills TEXT NOT NULL, title TEXT NOT NULL,
            description TEXT NOT NULL, rates_or_budget TEXT NOT NULL,
            portfolio TEXT, status TEXT DEFAULT 'PENDING', message_id INTEGER
        )''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS config_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )''')
        conn.commit()

init_db()

# ==========================================
# 2. BACKGROUND TASKS & DM ABORT LISTENER
# ==========================================
@tasks.loop(minutes=1)
async def check_scheduled_messages():
    now_str = datetime.now().isoformat()
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        due_channels = cursor.execute(
            "SELECT channel_id, delay_minutes FROM channel_schedules WHERE next_run <= ?",
            (now_str,)
        ).fetchall()
        
        for ch_id, delay in due_channels:
            msgs = cursor.execute(
                "SELECT message FROM auto_messages WHERE channel_id = ?",
                (ch_id,)
            ).fetchall()
            
            if msgs:
                chosen_msg = random.choice(msgs)[0]
                
                ch = bot.get_channel(ch_id)
                if not ch:
                    try:
                        ch = await bot.fetch_channel(ch_id)
                    except Exception as e:
                        print(f"Gagal mengambil channel {ch_id}: {e}")
                        ch = None
                
                if ch:
                    try:
                        await ch.send(chosen_msg)
                    except Exception as e:
                        print(f"Gagal mengirim auto-message ke channel {ch_id}: {e}")
            
            next_run = (datetime.now() + timedelta(minutes=delay)).isoformat()
            cursor.execute(
                "UPDATE channel_schedules SET next_run = ? WHERE channel_id = ?",
                (next_run, ch_id)
            )
        conn.commit()

@check_scheduled_messages.before_loop
async def before_check_messages():
    await bot.wait_until_ready()

@bot.event
async def on_message(message: discord.Message):
    if message.guild is None and not message.author.bot:
        if message.content.strip().lower() == "abort":
            embed = discord.Embed(
                title="❌ Listing Cancelled",
                description="The marketplace listing process has been cancelled.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=embed)
            return
    await bot.process_commands(message)

# ==========================================
# 3. AUTOMATED MESSAGES MANAGEMENT (RANDOM POOL)
# ==========================================
@bot.tree.command(name="add_auto_msg", description="Add a message to a channel's auto-message pool")
@discord.app_commands.describe(
    channel="Target channel", 
    message="Message content", 
    delay_minutes="Interval delay in minutes for this channel (optional if already set)"
)
async def add_auto_msg(
    interaction: discord.Interaction, 
    channel: discord.TextChannel, 
    message: str, 
    delay_minutes: int = None
):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        sched = cursor.execute("SELECT delay_minutes FROM channel_schedules WHERE channel_id = ?", (channel.id,)).fetchone()
        
        if not sched and (delay_minutes is None or delay_minutes < 1):
            return await interaction.response.send_message(
                "❌ Channel ini belum memiliki jadwal interval. Harap isi parameter `delay_minutes` (minimal 1 menit).", 
                ephemeral=True
            )
        
        if delay_minutes and delay_minutes >= 1:
            next_run = (datetime.now() + timedelta(minutes=delay_minutes)).isoformat()
            cursor.execute(
                "INSERT INTO channel_schedules (channel_id, delay_minutes, next_run) VALUES (?, ?, ?) "
                "ON CONFLICT(channel_id) DO UPDATE SET delay_minutes = excluded.delay_minutes",
                (channel.id, delay_minutes, next_run)
            )
        
        cursor.execute("INSERT INTO auto_messages (channel_id, message) VALUES (?, ?)", (channel.id, message))
        conn.commit()
        
        current_delay = delay_minutes if (delay_minutes and delay_minutes >= 1) else (sched[0] if sched else 0)

    await interaction.response.send_message(
        f"✅ Pesan berhasil ditambahkan ke pool auto-message {channel.mention}!\n"
        f"⏱️ **Interval Channel:** Setiap **{current_delay} menit** (1 pesan acak akan dikirim).\n"
        f"📝 **Pesan:** {message}",
        ephemeral=True
    )

@bot.tree.command(name="set_auto_interval", description="Change recurring interval delay for a channel")
@discord.app_commands.describe(channel="Target channel", delay_minutes="Interval delay in minutes")
async def set_auto_interval(interaction: discord.Interaction, channel: discord.TextChannel, delay_minutes: int):
    if delay_minutes < 1:
        return await interaction.response.send_message("❌ Interval minimal 1 menit.", ephemeral=True)
    
    next_run = (datetime.now() + timedelta(minutes=delay_minutes)).isoformat()
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO channel_schedules (channel_id, delay_minutes, next_run) VALUES (?, ?, ?) "
            "ON CONFLICT(channel_id) DO UPDATE SET delay_minutes = excluded.delay_minutes, next_run = excluded.next_run",
            (channel.id, delay_minutes, next_run)
        )
        conn.commit()
    
    await interaction.response.send_message(
        f"⏱️ Interval auto-message untuk {channel.mention} diubah menjadi setiap **{delay_minutes} menit**.", 
        ephemeral=True
    )

@bot.tree.command(name="list_auto_msgs", description="View active recurring automated message pools")
async def list_auto_msgs(interaction: discord.Interaction):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        schedules = cursor.execute("SELECT channel_id, delay_minutes FROM channel_schedules").fetchall()
        
        if not schedules:
            return await interaction.response.send_message("ℹ️ Tidak ada jadwal auto-message yang aktif.", ephemeral=True)
        
        embed = discord.Embed(title="📋 Active Automated Messages (Random Pool)", color=discord.Color.blue())
        for ch_id, delay in schedules:
            msgs = cursor.execute("SELECT id, message FROM auto_messages WHERE channel_id = ?", (ch_id,)).fetchall()
            if msgs:
                msg_list = "\n".join([f"• `[ID #{m_id}]` {msg}" for m_id, msg in msgs])
                embed.add_field(
                    name=f"Channel: <#{ch_id}> | Interval: Setiap {delay} min",
                    value=msg_list if len(msg_list) <= 1024 else msg_list[:1020] + "...",
                    inline=False
                )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="delete_auto_msg", description="Delete an automated message from pool by ID")
async def delete_auto_msg(interaction: discord.Interaction, msg_id: int):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        row = cursor.execute("SELECT channel_id FROM auto_messages WHERE id = ?", (msg_id,)).fetchone()
        if not row:
            return await interaction.response.send_message(f"❌ Auto-message ID `#{msg_id}` tidak ditemukan.", ephemeral=True)
        
        ch_id = row[0]
        cursor.execute("DELETE FROM auto_messages WHERE id = ?", (msg_id,))
        
        remaining = cursor.execute("SELECT COUNT(*) FROM auto_messages WHERE channel_id = ?", (ch_id,)).fetchone()[0]
        if remaining == 0:
            cursor.execute("DELETE FROM channel_schedules WHERE channel_id = ?", (ch_id,))
        
        conn.commit()
    
    await interaction.response.send_message(f"🗑️ Berhasil menghapus auto-message ID `#{msg_id}`.", ephemeral=True)

# =================================
# 4. UTILITY & FUN COMMANDS
# =================================
@bot.tree.command(name="say", description="Send a custom message to a specific channel")
async def say_custom(interaction: discord.Interaction, message: str, channel: discord.TextChannel):
    await channel.send(message)
    await interaction.response.send_message(f"✅ Message sent to {channel.mention}!", ephemeral=True)

@bot.tree.command(name="devlog", description="Send a formatted update / devlog announcement")
async def devlog_command(interaction: discord.Interaction, version: str, title: str, changes: str, channel: discord.TextChannel):
    embed = discord.Embed(title=f"🚀 Devlog [{version}]: {title}", description=changes, color=discord.Color.blue(), timestamp=discord.utils.utcnow())
    embed.set_footer(text=f"Posted by {interaction.user.display_name}")
    await channel.send(embed=embed)
    await interaction.response.send_message(f"✅ Devlog posted to {channel.mention}!", ephemeral=True)

@bot.tree.command(name="roll", description="Roll a random number or dice")
async def roll_command(interaction: discord.Interaction, max_value: int = 100):
    if max_value < 1:
        return await interaction.response.send_message("❌ Max value must be >= 1.", ephemeral=True)
    await interaction.response.send_message(f"🎲 **{interaction.user.display_name}** rolled **{random.randint(1, max_value)}** (1-{max_value})!")

@bot.tree.command(name="8ball", description="Ask the Magic 8-Ball a question")
async def eight_ball_command(interaction: discord.Interaction, question: str):
    ans = random.choice(["It is certain.", "Without a doubt.", "Yes, definitely.", "Ask again later.", "Cannot predict now.", "Don't count on it.", "My reply is no.", "Very doubtful."])
    embed = discord.Embed(title="🎱 Magic 8-Ball", color=discord.Color.purple())
    embed.add_field(name="Question", value=question, inline=False)
    embed.add_field(name="Answer", value=ans, inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="coinflip", description="Flip a virtual coin")
async def coinflip_command(interaction: discord.Interaction):
    await interaction.response.send_message(f"🪙 **{interaction.user.display_name}** flipped: **{random.choice(['Heads 🪙', 'Tails 🪙'])}**!")

@bot.tree.command(name="rate", description="Rate anything on a scale of 0% to 100%")
async def rate_command(interaction: discord.Interaction, thing: str):
    await interaction.response.send_message(f"⭐ I rate **{thing}** a **{random.randint(0, 100)}%**!")

# =================================
# 5. ROLE PICKER (DROPDOWN)
# =================================
class RoleSelectMenu(discord.ui.Select):
    def __init__(self, roles: list[discord.Role]):
        options = [discord.SelectOption(label=r.name, value=str(r.id), description=f"Toggle {r.name}") for r in roles]
        super().__init__(placeholder="Click here to select your roles!", min_values=0, max_values=len(options), options=options, custom_id="dropdown_role_picker")

    async def callback(self, interaction: discord.Interaction):
        selected = set(map(int, self.values))
        all_opts = {int(opt.value) for opt in self.options}
        to_add = [r for r_id in selected if (r := interaction.guild.get_role(r_id)) and r not in interaction.user.roles]
        to_rem = [r for r_id in (all_opts - selected) if (r := interaction.guild.get_role(r_id)) and r in interaction.user.roles]
        try:
            if to_add:
                await interaction.user.add_roles(*to_add)
            if to_rem:
                await interaction.user.remove_roles(*to_rem)
            await interaction.response.send_message("✅ Roles updated successfully!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Failed to update roles! Make sure the Bot's role is above the target roles in Server Settings and has 'Manage Roles' permission.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ An error occurred: {e}", ephemeral=True)

class RolePickerDropdownView(discord.ui.View):
    def __init__(self, roles: list[discord.Role]):
        super().__init__(timeout=None)
        self.add_item(RoleSelectMenu(roles))

@bot.tree.command(name="rolepicker", description="Create a role picker embed with dropdown menu")
async def rolepicker_command(interaction: discord.Interaction, title: str, description: str, role1: discord.Role, role2: discord.Role = None, role3: discord.Role = None, role4: discord.Role = None, role5: discord.Role = None, role6: discord.Role = None, role7: discord.Role = None, image_url: str = None):
    roles = [r for r in [role1, role2, role3, role4, role5, role6, role7] if r]
    embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
    if image_url:
        embed.set_image(url=image_url)
    await interaction.channel.send(embed=embed, view=RolePickerDropdownView(roles))
    await interaction.response.send_message("✅ Role picker created!", ephemeral=True)

# ==========================================
# 6. MARKETPLACE SETUP & MODERATION COMMANDS
# ==========================================
@bot.tree.command(name="setup_market", description="Setup the RAC Marketplace panel embed in the current channel")
async def setup_market_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 RAC MARKETPLACE",
        description=(
            "Welcome to the RAC Marketplace! Here you can:\n\n"
            "🛒 **Buy or Hire** — Find talented designers / developers for your project\n"
            "💼 **Sell Services** — Offer your skills to potential clients\n\n"
            "Click the button below to get started. The process takes place in your DMs for privacy.\n\n"
            "⚠️ **All marketplace transactions must occur in designated server channels. DMing about jobs is prohibited.**"
        ),
        color=discord.Color.blue()
    )
    embed.set_footer(text="RAC Marketplace • Safe & Secure Transactions")
    await interaction.channel.send(embed=embed, view=OpenMarketRequestView())
    await interaction.response.send_message("✅ Marketplace panel created!", ephemeral=True)

@bot.tree.command(name="marketplaceban", description="Ban a user from accessing RAC Marketplace channels")
@discord.app_commands.describe(user="Target user to ban", reason="Reason for marketplace ban")
@discord.app_commands.checks.has_permissions(manage_roles=True)
async def marketplaceban_command(interaction: discord.Interaction, user: discord.Member, reason: str = "Violation of marketplace rules"):
    lfd_id = get_setting("LFD_CHANNEL_ID")
    fhd_id = get_setting("FHD_CHANNEL_ID")
    
    for ch_id in [lfd_id, fhd_id]:
        if ch_id:
            ch = interaction.guild.get_channel(ch_id)
            if not ch:
                try:
                    ch = await interaction.guild.fetch_channel(ch_id)
                except Exception:
                    pass
            if ch:
                await ch.set_permissions(user, view_channel=False)
    
    try:
        embed = discord.Embed(
            title="⛔ Marketplace Access Revoked",
            description="You have been restricted from viewing and posting in RAC Marketplace channels.",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        await user.send(embed=embed)
    except Exception:
        pass

    await interaction.response.send_message(f"🚫 {user.mention} has been banned from RAC Marketplace channels.\n**Reason:** {reason}", ephemeral=True)

@bot.tree.command(name="postremove", description="Remove a marketplace post by ID with a reason prompt")
@discord.app_commands.describe(post_id="ID of the post to remove")
@discord.app_commands.checks.has_permissions(manage_messages=True)
async def postremove_command(interaction: discord.Interaction, post_id: int):
    await interaction.response.send_modal(RemovePostModal(post_id))

# ==========================================
# 7. DYNAMIC SETTINGS COMMAND
# ==========================================
@bot.tree.command(name="settings", description="Manage RAC Marketplace channel configurations dynamically")
@discord.app_commands.describe(
    action="Choose action: View current settings or Set new channel",
    target="Select which channel setting to update",
    channel="Select the new target text channel"
)
@discord.app_commands.choices(
    action=[
        discord.app_commands.Choice(name="👀 View Config", value="view"),
        discord.app_commands.Choice(name="⚙️ Set Channel", value="set")
    ],
    target=[
        discord.app_commands.Choice(name="🔍 Post Review Channel", value="POST_REVIEW_CHANNEL_ID"),
        discord.app_commands.Choice(name="💼 LFD Channel (Sell Services)", value="LFD_CHANNEL_ID"),
        discord.app_commands.Choice(name="🛒 FHD Channel (Buy / Hire)", value="FHD_CHANNEL_ID")
    ]
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def settings_command(
    interaction: discord.Interaction, 
    action: str, 
    target: str = None, 
    channel: discord.TextChannel = None
):
    if action == "view":
        review_id = get_setting("POST_REVIEW_CHANNEL_ID")
        lfd_id = get_setting("LFD_CHANNEL_ID")
        fhd_id = get_setting("FHD_CHANNEL_ID")

        embed = discord.Embed(
            title="⚙️ RAC Marketplace Configuration",
            description="Berikut adalah konfigurasi channel aktif saat ini:",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="🔍 Post Review Channel", value=f"<#{review_id}> (`{review_id}`)" if review_id else "❌ Belum Diatur", inline=False)
        embed.add_field(name="💼 LFD Channel (Sell Services)", value=f"<#{lfd_id}> (`{lfd_id}`)" if lfd_id else "❌ Belum Diatur", inline=False)
        embed.add_field(name="🛒 FHD Channel (Buy / Hire)", value=f"<#{fhd_id}> (`{fhd_id}`)" if fhd_id else "❌ Belum Diatur", inline=False)
        embed.set_footer(text="Gunakan `/settings set` untuk mengubah konfigurasi.")
        
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    elif action == "set":
        if not target or not channel:
            return await interaction.response.send_message(
                "❌ Untuk mengubah pengaturan (`set`), kamu harus memilih `target` dan `channel`!", 
                ephemeral=True
            )

        set_setting(target, channel.id)

        target_names = {
            "POST_REVIEW_CHANNEL_ID": "Post Review Channel",
            "LFD_CHANNEL_ID": "LFD Channel (Sell Services)",
            "FHD_CHANNEL_ID": "FHD Channel (Buy / Hire)"
        }
        
        name = target_names.get(target, target)
        await interaction.response.send_message(
            f"✅ **{name}** berhasil diubah ke {channel.mention} (`{channel.id}`).\n"
            f"💾 Perubahan telah disimpan ke database SQLite dan aman dari restart/redeploy.",
            ephemeral=True
        )

# ==========================================
# 8. BOT EVENTS & RUN
# ==========================================
@bot.event
async def on_ready():
    print("=================================")
    print(f"Bot successfully online as: {bot.user}")
    print("=================================")
    
    bot.add_view(OpenMarketRequestView())
    bot.add_view(MarketReviewView())
    
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
        
    if not check_scheduled_messages.is_running():
        check_scheduled_messages.start()

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    print("⚠️ DISCORD_TOKEN is missing in Environment Variables!")
else:
    bot.run(TOKEN)
