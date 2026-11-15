import os
import re
import random
import sqlite3
from datetime import datetime, timedelta
import discord
from discord.ext import commands, tasks

from market import (
    OpenMarketRequestView, MarketReviewView, DB_NAME, RemovePostModal, get_setting, set_setting,
    migrate_market_db, run_market_expiry, send_my_posts, send_market_search, format_market_limits,
    MARKET_SKILLS, LISTING_TYPES, ContactPosterView
)

# BOT INITIALIZATION & DATABASE SETUP
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
        
        # Check whether the auto_messages table still uses the old structure
        cursor.execute("PRAGMA table_info(auto_messages)")
        columns = [col[1] for col in cursor.fetchall()]
        if "delay_minutes" in columns:
            cursor.execute("""
                INSERT OR IGNORE INTO channel_schedules (channel_id, delay_minutes, next_run)
                SELECT channel_id, delay_minutes, next_run FROM auto_messages
            """)
            cursor.execute("ALTER TABLE auto_messages RENAME TO auto_messages_old")
            cursor.execute('''CREATE TABLE auto_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                message TEXT NOT NULL
            )''')
            cursor.execute("""
                INSERT INTO auto_messages (id, channel_id, message)
                SELECT id, channel_id, message FROM auto_messages_old
            """)
            cursor.execute("DROP TABLE auto_messages_old")
        
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
        migrate_market_db(cursor)
        conn.commit()

init_db()

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    msg = f"❌ There is an error: `{error}`"
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)

# BACKGROUND TASKS & DM ABORT LISTENER
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
                        print(f"Failed to retrieve channel {ch_id}: {e}")
                        ch = None
                
                if ch:
                    try:
                        await ch.send(chosen_msg)
                    except Exception as e:
                        print(f"Failed to send auto-message to the channel {ch_id}: {e}")
            
            next_run = (datetime.now() + timedelta(minutes=delay)).isoformat()
            cursor.execute(
                "UPDATE channel_schedules SET next_run = ? WHERE channel_id = ?",
                (next_run, ch_id)
            )
        conn.commit()

@check_scheduled_messages.before_loop
async def before_check_messages():
    await bot.wait_until_ready()

@tasks.loop(minutes=30)
async def check_market_expiry():
    try:
        await run_market_expiry(bot)
    except Exception as e:
        print(f"Market expiry check failed: {e}")

@check_market_expiry.before_loop
async def before_check_market_expiry():
    await bot.wait_until_ready()

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Abort feature via DM for the marketplace
    if message.guild is None:
        if message.content.strip().lower() == "abort":
            embed = discord.Embed(
                title="❌ Listing Cancelled",
                description="The marketplace listing process has been cancelled.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=embed)
            return

    if message.guild is not None:
        is_mentioned = f"<@{bot.user.id}>" in message.content or f"<@!{bot.user.id}>" in message.content
        clean_text = message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()

        has_name = bool(re.search(r"\brads\b", clean_text, re.IGNORECASE))
        has_hello = bool(re.search(r"\b(hello|halo|hi|hai|yo)\b", clean_text, re.IGNORECASE))

        # 1. Greeting trigger ("RaDS" or @mention + "Hello")
        if (is_mentioned or has_name) and has_hello:
            greetings = [
                f"Hello {message.author.mention}! Is there anything I can help?",
                f"Hi {message.author.mention}! Nice to meet you!",
                f"Yo! RaDS here!"
            ]
            await message.reply(random.choice(greetings))
            return

        # 2. 8-Ball feature when the bot is mentioned (@RaDS <question>)
        if is_mentioned:
            if not clean_text:
                await message.reply("Ask me anything!")
                return

            ans = random.choice([
                "It is certain.",
                "Without a doubt.",
                "Yes, definitely.",
                "Ask again later.",
                "Cannot predict now.",
                "Don't count on it.",
                "My reply is no.",
                "My reply is yes.",
                "Very doubtful."
            ])
            await message.reply(f"🎱 {ans}")
            return

    await bot.process_commands(message)

# AUTOMATED MESSAGES MANAGEMENT (RANDOM POOL)
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
                "❌ This channel does not yet have an interval schedule. Please provide the 'delay_minutes' parameter (minimum 1 minute).", 
                ephemeral=False
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
        f"✅ Message successfully added to the auto-message pool {channel.mention}!\n"
        f"⏱️ **Interval Channel:** Every **{current_delay} minutes** (1 random message will be sent).\n"
        f"📝 **Message:** {message}",
        ephemeral=True
    )

@bot.tree.command(name="set_auto_interval", description="Change recurring interval delay for a channel")
@discord.app_commands.describe(channel="Target channel", delay_minutes="Interval delay in minutes")
async def set_auto_interval(interaction: discord.Interaction, channel: discord.TextChannel, delay_minutes: int):
    if delay_minutes < 1:
        return await interaction.response.send_message("❌ Minimum interval is 1 minute.", ephemeral=True)
    
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
        f"⏱️ Auto-message interval for {channel.mention} has been changed to every **{delay_minutes} minutes**.", 
        ephemeral=True
    )

@bot.tree.command(name="list_auto_msgs", description="View active recurring automated message pools")
async def list_auto_msgs(interaction: discord.Interaction):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        schedules = cursor.execute("SELECT channel_id, delay_minutes FROM channel_schedules").fetchall()
        
        if not schedules:
            return await interaction.response.send_message("ℹ️ There are no active auto-message schedules.", ephemeral=True)
        
        embed = discord.Embed(title="📋 Active Automated Messages (Random Pool)", color=discord.Color.blue())
        for ch_id, delay in schedules:
            msgs = cursor.execute("SELECT id, message FROM auto_messages WHERE channel_id = ?", (ch_id,)).fetchall()
            if msgs:
                msg_list = "\n".join([f"• `[ID #{m_id}]` {msg}" for m_id, msg in msgs])
                embed.add_field(
                    name=f"Channel: <#{ch_id}> | Interval: Every {delay} min",
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
            return await interaction.response.send_message(f"❌ Auto-message ID `#{msg_id}` was not found.", ephemeral=True)
        
        ch_id = row[0]
        cursor.execute("DELETE FROM auto_messages WHERE id = ?", (msg_id,))
        
        remaining = cursor.execute("SELECT COUNT(*) FROM auto_messages WHERE channel_id = ?", (ch_id,)).fetchone()[0]
        if remaining == 0:
            cursor.execute("DELETE FROM channel_schedules WHERE channel_id = ?", (ch_id,))
        
        conn.commit()
    
    await interaction.response.send_message(f"🗑️ Successfully deleted auto-message ID `#{msg_id}`.", ephemeral=True)

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

@bot.tree.command(name="coinflip", description="Flip a virtual coin")
async def coinflip_command(interaction: discord.Interaction):
    await interaction.response.send_message(f"🪙 **{interaction.user.display_name}** flipped: **{random.choice(['Heads 🪙', 'Tails 🪙'])}**!")

@bot.tree.command(name="rate", description="Rate anything on a scale of 0% to 100%")
async def rate_command(interaction: discord.Interaction, thing: str):
    await interaction.response.send_message(f"⭐ I rate **{thing}** a **{random.randint(0, 100)}%**!")

# ==========================================
# 5. ROLE PICKER (FORM + DROPDOWN SETUP)
# ==========================================
class RoleSelectMenu(discord.ui.Select):
    def __init__(self, roles: list[discord.Role] = None):
        if roles:
            options = [discord.SelectOption(label=r.name, value=str(r.id), description=f"Toggle {r.name}") for r in roles]
        else:
            options = [discord.SelectOption(label="Placeholder", value="0")]
        super().__init__(
            placeholder="Click here to select your roles!",
            min_values=0,
            max_values=len(options),
            options=options,
            custom_id="dropdown_role_picker"
        )

    async def callback(self, interaction: discord.Interaction):
        selected = set(map(int, self.values))
        all_opts = set()
        if interaction.message and interaction.message.components:
            for row in interaction.message.components:
                for child in row.children:
                    if hasattr(child, "options"):
                        for opt in child.options:
                            if opt.value.isdigit() and int(opt.value) != 0:
                                all_opts.add(int(opt.value))
        if not all_opts:
            all_opts = {int(opt.value) for opt in self.options if opt.value.isdigit() and int(opt.value) != 0}

        to_add = [r for r_id in selected if (r := interaction.guild.get_role(r_id)) and r not in interaction.user.roles]
        to_rem = [r for r_id in (all_opts - selected) if (r := interaction.guild.get_role(r_id)) and r in interaction.user.roles]
        try:
            if to_add:
                await interaction.user.add_roles(*to_add)
            if to_rem:
                await interaction.user.remove_roles(*to_rem)
            await interaction.response.send_message("✅ Roles updated successfully!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Failed to updated role! Make sure the bot's role placed ABOVE' target role and has the 'Manage Roles' permission.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ An error occurred: {e}", ephemeral=True)

class RolePickerDropdownView(discord.ui.View):
    def __init__(self, roles: list[discord.Role] = None):
        super().__init__(timeout=None)
        self.add_item(RoleSelectMenu(roles))

class RolePickerSetupRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder="1. Select the roles to include (max 25)...", min_values=1, max_values=25)

    async def callback(self, interaction: discord.Interaction):
        self.view.selected_roles = list(self.values)
        await interaction.response.defer()

class RolePickerSetupChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="2. Select the destination channel...",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.target_channel_id = self.values[0].id
        await interaction.response.defer()

class RolePickerSetupView(discord.ui.View):
    def __init__(self, title: str, description: str, image_url: str = None):
        super().__init__(timeout=300)
        self.embed_title = title
        self.embed_description = description
        self.image_url = image_url
        self.selected_roles: list[discord.Role] = []
        self.target_channel_id: int = None

        self.add_item(RolePickerSetupRoleSelect())
        self.add_item(RolePickerSetupChannelSelect())

    @discord.ui.button(label="Send Role Picker", style=discord.ButtonStyle.success, emoji="✅", row=2)
    async def publish_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_roles:
            return await interaction.response.send_message("❌ Select at least 1 role first in the first dropdown!", ephemeral=True)
        if not self.target_channel_id:
            return await interaction.response.send_message("❌ Select a destination channel first in the second dropdown!", ephemeral=True)

        target_ch = interaction.guild.get_channel(self.target_channel_id)
        if not target_ch:
            try:
                target_ch = await interaction.guild.fetch_channel(self.target_channel_id)
            except Exception as e:
                return await interaction.response.send_message(f"❌ Failed to access the destination channel: `{e}`", ephemeral=True)

        embed = discord.Embed(title=self.embed_title, description=self.embed_description, color=discord.Color.blue())
        if self.image_url:
            embed.set_image(url=self.image_url)

        try:
            await target_ch.send(embed=embed, view=RolePickerDropdownView(self.selected_roles))
        except discord.Forbidden:
            return await interaction.response.send_message(f"❌ The bot does not have permission to send messages in {target_ch.mention}!", ephemeral=True)
        except Exception as e:
            return await interaction.response.send_message(f"❌ Error while sending `{e}`", ephemeral=False)

        done_embed = discord.Embed(
            title="✅ Role Picker Created Succeesfully!",
            description=f"The role picker panel has been sent to {target_ch.mention} with **{len(self.selected_roles)}** role options.",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=done_embed, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️", row=2)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cancel_embed = discord.Embed(title="❌ Role Picker Creation Cancelled", color=discord.Color.red())
        await interaction.response.edit_message(embed=cancel_embed, view=None)

class RolePickerModal(discord.ui.Modal, title="Setup Role Picker"):
    title_input = discord.ui.TextInput(
        label="Title",
        placeholder="Example: Choose Your Role!",
        required=True,
        max_length=100
    )
    desc_input = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        placeholder="Explain the function of each role here...",
        required=True,
        max_length=2000
    )
    image_input = discord.ui.TextInput(
        label="Image URL (Optional)",
        placeholder="https://... (leave empty if not using an image)",
        required=False,
        max_length=300
    )

    async def on_submit(self, interaction: discord.Interaction):
        img_url = self.image_input.value.strip() or None
        preview_embed = discord.Embed(
            title="🛠️ Step 2: Select Roles & Destination Channel",
            description=(
                f"**Title:** {self.title_input.value}\n"
                f"**Description:** {self.desc_input.value[:200]}{'...' if len(self.desc_input.value) > 200 else ''}\n\n"
                "Please select the **list of roles** (maximum 25) and the **destination channel** using the dropdown menus below, then click **Send Role Picker**."
            ),
            color=discord.Color.blue()
        )
        view = RolePickerSetupView(self.title_input.value, self.desc_input.value, img_url)
        await interaction.response.send_message(embed=preview_embed, view=view, ephemeral=True)

@bot.tree.command(name="rolepicker", description="Create a role picker embed with interactive form & dropdown")
@discord.app_commands.checks.has_permissions(manage_roles=True)
async def rolepicker_command(interaction: discord.Interaction):
    await interaction.response.send_modal(RolePickerModal())

# MARKETPLACE SETUP & MODERATION COMMANDS
@bot.tree.command(name="setup_market", description="Setup the Marketplace panel embed in the current channel")
async def setup_market_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 RaDS MARKETPLACE",
        description=(
            "Welcome to the RaDS Marketplace! Here you can:\n\n"
            "🛒 **Buy or Hire** — Find talented designers / developers for your project\n"
            "💼 **Sell Services** — Offer your skills to potential clients\n\n"
            "Click the button below to get started. The process takes place in your DMs for privacy."
        ),
        color=discord.Color.blue()
    )
    embed.set_footer(text="RaDS Marketplace • Safe & Secure Transactions")
    await interaction.channel.send(embed=embed, view=OpenMarketRequestView())
    await interaction.response.send_message("✅ Marketplace panel created!", ephemeral=True)

@bot.tree.command(name="marketplaceban", description="Ban a user from accessing Marketplace channels")
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
            description="You have been restricted from viewing and posting in the Marketplace channels.",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        await user.send(embed=embed)
    except Exception:
        pass

    await interaction.response.send_message(f"🚫 {user.mention} has been banned from the Marketplace channels.\n**Reason:** {reason}", ephemeral=False)

@bot.tree.command(name="postremove", description="Remove a marketplace post by ID with a reason prompt")
@discord.app_commands.describe(post_id="ID of the post to remove")
@discord.app_commands.checks.has_permissions(manage_messages=True)
async def postremove_command(interaction: discord.Interaction, post_id: int):
    await interaction.response.send_modal(RemovePostModal(post_id))

@bot.tree.command(name="marketplaceunban", description="Restore a user's access to the Marketplace channels")
@discord.app_commands.describe(user="Target user to unban")
@discord.app_commands.checks.has_permissions(manage_roles=True)
async def marketplaceunban_command(interaction: discord.Interaction, user: discord.Member):
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
                await ch.set_permissions(user, view_channel=None)

    try:
        embed = discord.Embed(
            title="✅ Marketplace Access Restored",
            description="You can view and post in the Marketplace channels again.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        await user.send(embed=embed)
    except Exception:
        pass

    await interaction.response.send_message(f"✅ {user.mention} can access the Marketplace channels again.", ephemeral=False)

@bot.tree.command(name="mypost", description="View, edit, renew, or close your own marketplace listings")
async def mypost_command(interaction: discord.Interaction):
    await send_my_posts(interaction)

@bot.tree.command(name="market_search", description="Search live marketplace listings")
@discord.app_commands.describe(
    skill="Filter by skill / role",
    listing_type="Filter by listing type",
    keyword="Search in the title and description"
)
@discord.app_commands.choices(
    skill=[discord.app_commands.Choice(name=s, value=s) for s in MARKET_SKILLS],
    listing_type=[discord.app_commands.Choice(name=t, value=t) for t in LISTING_TYPES]
)
async def market_search_command(interaction: discord.Interaction, skill: str = None, listing_type: str = None, keyword: str = None):
    await send_market_search(interaction, skill, listing_type, keyword)

# CONFIG & SETTINGS COMMANDS
@bot.tree.command(name="settings", description="Show Marketplace configuration status")
@discord.app_commands.checks.has_permissions(administrator=True)
async def settings_command(interaction: discord.Interaction):
    review_id = get_setting("POST_REVIEW_CHANNEL_ID")
    lfd_id = get_setting("LFD_CHANNEL_ID")
    fhd_id = get_setting("FHD_CHANNEL_ID")

    def get_status(ch_id):
        return f"✅ <#{ch_id}>" if ch_id else "❌ **Not Set**"

    embed = discord.Embed(
        title=f"Marketplace config for {interaction.guild.name}",
        color=discord.Color.dark_theme()
    )

    embed.description = (
        "**Post Review Channel**\n"
        f"Channels: {get_status(review_id)}\n\n"
        "**LFD Channel (Sell Services)**\n"
        f"Channels: {get_status(lfd_id)}\n\n"
        "**FHD Channel (Buy / Hire)**\n"
        f"Channels: {get_status(fhd_id)}\n\n"
        "**Posting Limits** (change with `/market_limits`)\n"
        f"{format_market_limits()}"
    )

    embed.set_footer(text="Use the /set_channel command to change the configuration.")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="set_channel", description="Set a target channel for the Marketplace")
@discord.app_commands.describe(
    target="Select which channel setting to update",
    channel="Select the target text channel"
)
@discord.app_commands.choices(
    target=[
        discord.app_commands.Choice(name="Post Review Channel", value="POST_REVIEW_CHANNEL_ID"),
        discord.app_commands.Choice(name="LFD Channel (Sell Services)", value="LFD_CHANNEL_ID"),
        discord.app_commands.Choice(name="FHD Channel (Buy / Hire)", value="FHD_CHANNEL_ID")
    ]
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def set_channel_command(interaction: discord.Interaction, target: str, channel: discord.TextChannel):
    set_setting(target, channel.id)

    target_names = {
        "POST_REVIEW_CHANNEL_ID": "Post Review Channel",
        "LFD_CHANNEL_ID": "LFD Channel (Sell Services)",
        "FHD_CHANNEL_ID": "FHD Channel (Buy / Hire)"
    }
    
    name = target_names.get(target, target)
    await interaction.response.send_message(
        f"✅ **{name}** has been successfully set to {channel.mention} (`{channel.id}`).",
        ephemeral=True
    )

@bot.tree.command(name="market_limits", description="Configure marketplace posting limits (0 = disabled)")
@discord.app_commands.describe(
    cooldown_hours="Hours a user must wait between listings (0 = disabled)",
    max_active_posts="Max pending + live listings per user (0 = disabled)",
    expiry_days="Days before a live listing expires (0 = never expires)"
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def market_limits_command(interaction: discord.Interaction, cooldown_hours: int = None, max_active_posts: int = None, expiry_days: int = None):
    updates = {
        "POST_COOLDOWN_HOURS": cooldown_hours,
        "MAX_ACTIVE_POSTS": max_active_posts,
        "POST_EXPIRY_DAYS": expiry_days
    }
    if any(v is not None and v < 0 for v in updates.values()):
        return await interaction.response.send_message("❌ Values must be 0 or higher.", ephemeral=True)

    for key, value in updates.items():
        if value is not None:
            set_setting(key, value)

    await interaction.response.send_message(f"⚙️ **Marketplace posting limits**\n{format_market_limits()}", ephemeral=True)

# BOT EVENTS & RUN
@bot.event
async def on_ready():
    print("===========================")
    print(f"Bot successfully online as: {bot.user}")
    print("===========================")
    
    bot.add_view(OpenMarketRequestView())
    bot.add_view(MarketReviewView())
    bot.add_view(ContactPosterView())
    
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
        
    if not check_scheduled_messages.is_running():
        check_scheduled_messages.start()

    if not check_market_expiry.is_running():
        check_market_expiry.start()

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    print("⚠️ DISCORD_TOKEN is missing in Environment Variables!")
else:
    bot.run(TOKEN)
