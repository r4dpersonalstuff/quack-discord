import os
import random
import sqlite3
from datetime import datetime, timedelta
import discord
from discord.ext import commands, tasks

# Import modul marketplace
from market import OpenMarketRequestView, DB_NAME, RemovePostModal, LFD_CHANNEL_ID, FHD_CHANNEL_ID

# ==========================================
# 1. BOT INITIALIZATION & DATABASE SETUP
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS auto_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER NOT NULL,
            delay_minutes INTEGER NOT NULL, message TEXT NOT NULL, next_run TEXT NOT NULL
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
            portfolio TEXT, status TEXT DEFAULT 'PENDING'
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
        records = cursor.execute("SELECT id, channel_id, delay_minutes, message FROM auto_messages WHERE next_run <= ?", (now_str,)).fetchall()
        for rec_id, ch_id, delay, msg in records:
            ch = bot.get_channel(ch_id)
            if ch:
                try:
                    await ch.send(msg)
                except Exception as e:
                    print(f"Error sending msg: {e}")
            next_run = (datetime.now() + timedelta(minutes=delay)).isoformat()
            cursor.execute("UPDATE auto_messages SET next_run = ? WHERE id = ?", (next_run, rec_id))
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
# 3. AUTOMATED MESSAGES MANAGEMENT
# ==========================================
@bot.tree.command(name="add_auto_msg", description="Set up a recurring message for a specific channel")
@discord.app_commands.describe(channel="Target channel", delay_minutes="Interval delay in minutes", message="Message content")
async def add_auto_msg(interaction: discord.Interaction, channel: discord.TextChannel, delay_minutes: int, message: str):
    if delay_minutes < 1:
        return await interaction.response.send_message("❌ Delay must be at least 1 minute.", ephemeral=True)
    next_run = (datetime.now() + timedelta(minutes=delay_minutes)).isoformat()
    with sqlite3.connect(DB_NAME) as conn:
        conn.cursor().execute("INSERT INTO auto_messages (channel_id, delay_minutes, message, next_run) VALUES (?, ?, ?, ?)",
                              (channel.id, delay_minutes, message, next_run))
        conn.commit()
    await interaction.response.send_message(f"✅ Auto-message scheduled for {channel.mention} every {delay_minutes} min!\n**Message:** {message}", ephemeral=True)

@bot.tree.command(name="list_auto_msgs", description="View active recurring automated messages")
async def list_auto_msgs(interaction: discord.Interaction):
    with sqlite3.connect(DB_NAME) as conn:
        records = conn.cursor().execute("SELECT id, channel_id, delay_minutes, message FROM auto_messages").fetchall()
    if not records:
        return await interaction.response.send_message("ℹ️ No active auto-messages found.", ephemeral=True)
    embed = discord.Embed(title="📋 Active Automated Messages", color=discord.Color.blue())
    for r_id, ch_id, delay, msg in records:
        embed.add_field(name=f"ID #{r_id} | Channel: <#{ch_id}>", value=f"**Interval:** Every {delay} min\n**Message:** {msg}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="delete_auto_msg", description="Delete an automated message schedule by ID")
async def delete_auto_msg(interaction: discord.Interaction, msg_id: int):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM auto_messages WHERE id = ?", (msg_id,))
        deleted = cursor.rowcount
        conn.commit()
    msg = f"🗑️ Deleted auto-message ID #{msg_id}." if deleted else f"❌ Auto-message ID #{msg_id} not found."
    await interaction.response.send_message(msg, ephemeral=True)

# ==========================================
# 4. UTILITY & FUN COMMANDS
# ==========================================

@bot.tree.command(name="marketplaceban", description="Ban a user from accessing RAC Marketplace channels")
@discord.app_commands.describe(user="Target user to ban", reason="Reason for marketplace ban")
@discord.app_commands.checks.has_permissions(manage_roles=True)
async def marketplaceban_command(interaction: discord.Interaction, user: discord.Member, reason: str = "Violation of marketplace rules"):
    # Sembunyikan channel LFD dan FHD dari user
    for ch_id in [LFD_CHANNEL_ID, FHD_CHANNEL_ID]:
        ch = interaction.guild.get_channel(ch_id)
        if ch:
            await ch.set_permissions(user, view_channel=False)
    
    # Kirim notifikasi DM ke user
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
    # Membuka pop-up form modal untuk memasukkan alasan penghapusan
    await interaction.response.send_modal(RemovePostModal(post_id))

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

# ==========================================
# 5. ROLE PICKER (DROPDOWN)
# ==========================================
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

# ==========================================
# 6. BOT EVENTS & RUN
# ==========================================
@bot.event
async def on_ready():
    print("=================================")
    print(f"Bot successfully online as: {bot.user}")
    print("=================================")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    if not check_scheduled_messages.is_running():
        check_scheduled_messages.start()

# Tempelkan Token Bot Discord kamu di bawah ini
TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN == "TEMPEL TOKEN BOT KAMU DISINI":
    print("⚠️ HARAP MASUKKAN TOKEN BOT DISCORD KAMU PADA VARIABEL TOKEN AT THE BOTTOM!")
else:
    bot.run(TOKEN)
