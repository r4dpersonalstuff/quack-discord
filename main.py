import os
import random
import sqlite3
from datetime import datetime, timedelta
import discord
from discord.ext import commands, tasks

from market import OpenMarketRequestView, MarketReviewView, DB_NAME, RemovePostModal, get_setting, set_setting

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
        # Cek apakah tabel auto_messages masih memakai struktur lama
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
        conn.commit()

init_db()

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    msg = f"❌ There is an error: `{error}`"
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=False)
    else:
        await interaction.response.send_message(msg, ephemeral=False)

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


# AUTOMATED MESSAGES MANAGEMENT (RANDOM POOL)
@bot.tree.command(name="add_auto_msg", description="Add a message to a channel's auto-message pool")
@discord.app_commands.default_permissions(manage_messages=True)
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
        ephemeral=False
    )

@bot.tree.command(name="set_auto_interval", description="Change recurring interval delay for a channel")
@discord.app_commands.default_permissions(manage_messages=True)
@discord.app_commands.describe(channel="Target channel", delay_minutes="Interval delay in minutes")
async def set_auto_interval(interaction: discord.Interaction, channel: discord.TextChannel, delay_minutes: int):
    if delay_minutes < 1:
        return await interaction.response.send_message("❌ Minimum interval of 1 minute.", ephemeral=False)
    
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
        f"⏱️ Auto-message interval for {channel.mention} changed to every **{delay_minutes} minutes**.", 
        ephemeral=False
    )

@bot.tree.command(name="list_auto_msgs", description="View active recurring automated message pools")
@discord.app_commands.default_permissions(manage_messages=True)
async def list_auto_msgs(interaction: discord.Interaction):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        schedules = cursor.execute("SELECT channel_id, delay_minutes FROM channel_schedules").fetchall()
        
        if not schedules:
            return await interaction.response.send_message("ℹ️ There are no active auto-message schedules.", ephemeral=False)
        
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
    
    await interaction.response.send_message(embed=embed, ephemeral=False)

@bot.tree.command(name="delete_auto_msg", description="Delete an automated message from pool by ID")
@discord.app_commands.default_permissions(manage_messages=True)
async def delete_auto_msg(interaction: discord.Interaction, msg_id: int):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        row = cursor.execute("SELECT channel_id FROM auto_messages WHERE id = ?", (msg_id,)).fetchone()
        if not row:
            return await interaction.response.send_message(f"❌ Auto-message ID `#{msg_id}` not found.", ephemeral=False)
        
        ch_id = row[0]
        cursor.execute("DELETE FROM auto_messages WHERE id = ?", (msg_id,))
        
        remaining = cursor.execute("SELECT COUNT(*) FROM auto_messages WHERE channel_id = ?", (ch_id,)).fetchone()[0]
        if remaining == 0:
            cursor.execute("DELETE FROM channel_schedules WHERE channel_id = ?", (ch_id,))
        
        conn.commit()
    
    await interaction.response.send_message(f"🗑 Successfully deleted auto-message ID `#{msg_id}`.", ephemeral=False)

# UTILITY & FUN COMMANDS
@bot.tree.command(name="say", description="Send a custom message to a specific channel")
@discord.app_commands.default_permissions(manage_messages=True)
async def say_custom(interaction: discord.Interaction, message: str, channel: discord.TextChannel):
    await channel.send(message)
    await interaction.response.send_message(f"✅ Message sent to {channel.mention}!", ephemeral=False)

@bot.tree.command(name="devlog", description="Send a formatted update / devlog announcement")
@discord.app_commands.default_permissions(manage_messages=True)
async def devlog_command(interaction: discord.Interaction, version: str, title: str, changes: str, channel: discord.TextChannel):
    embed = discord.Embed(title=f"Devlog [{version}]: {title}", description=changes, color=discord.Color.blue(), timestamp=discord.utils.utcnow())
    embed.set_footer(text=f"Posted by {interaction.user.display_name}")
    await channel.send(embed=embed)
    await interaction.response.send_message(f"✅ Devlog posted to {channel.mention}!", ephemeral=False)

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

# ROLE PICKER (FORM + DROPDOWN SETUP)
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
            await interaction.response.send_message("❌ Failed to updated role! Make sure the bot's role placed ABOVE' role target and have the 'Manage Roles' permission.",ephemeral=False)
        except Exception as e:
            await interaction.response.send_message(f"❌ An error occurred: {e}", ephemeral=False)

class RolePickerDropdownView(discord.ui.View):
    def __init__(self, roles: list[discord.Role] = None):
        super().__init__(timeout=None)
        self.add_item(RoleSelectMenu(roles))

class RolePickerSetupRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder="Select the roles you want to add (max 25)...", min_values=1, max_values=25)

    async def callback(self, interaction: discord.Interaction):
        self.view.selected_roles = list(self.values)
        await interaction.response.defer()

class RolePickerSetupChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="Select the destination channel for delivery...",
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
            return await interaction.response.send_message("❌ Select at least 1 role first in the first dropdown!", ephemeral=False)
        if not self.target_channel_id:
            return await interaction.response.send_message("❌ Select the destination channel first in the second dropdown.!", ephemeral=False)

        target_ch = interaction.guild.get_channel(self.target_channel_id)
        if not target_ch:
            try:
                target_ch = await interaction.guild.fetch_channel(self.target_channel_id)
            except Exception as e:
                return await interaction.response.send_message(f"❌ Failed to access the target channel: `{e}`", ephemeral=False)

        embed = discord.Embed(title=self.embed_title, description=self.embed_description, color=discord.Color.blue())
        if self.image_url:
            embed.set_image(url=self.image_url)

        try:
            await target_ch.send(embed=embed, view=RolePickerDropdownView(self.selected_roles))
        except discord.Forbidden:
            return await interaction.response.send_message(f"❌ Bot does not have permission to send messages on {target_ch.mention}!", ephemeral=False)
        except Exception as e:
            return await interaction.response.send_message(f"❌ Error while sending `{e}`", ephemeral=False)

        done_embed = discord.Embed(
            title="✅ Role Picker Created Succeesfully!",
            description=f"The role picker panel has been sent to {target_ch.mention} with **{len(self.selected_roles)}** role selection.",
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
        placeholder="https://... (leave blank if not using an image)",
        required=False,
        max_length=300
    )

    async def on_submit(self, interaction: discord.Interaction):
        img_url = self.image_input.value.strip() or None
        preview_embed = discord.Embed(
            title="🛠 Step 2: Select Role & Destination Channel",
            description=(
                f"**Title:** {self.title_input.value}\n"
                f"**Description:** {self.desc_input.value[:200]}{'...' if len(self.desc_input.value) > 200 else ''}\n\n"
                "Please select **list of roles** (maximum 25) and the **destination channel** via the dropdown menu below, then click **Send Role Picker**."
            ),
            color=discord.Color.blue()
        )
        view = RolePickerSetupView(self.title_input.value, self.desc_input.value, img_url)
        await interaction.response.send_message(embed=preview_embed, view=view, ephemeral=False)

@bot.tree.command(name="rolepicker", description="Create a role picker embed with interactive form & dropdown")
@discord.app_commands.default_permissions(manage_roles=True)
@discord.app_commands.checks.has_permissions(manage_roles=True)
async def rolepicker_command(interaction: discord.Interaction):
    await interaction.response.send_modal(RolePickerModal())

# MARKETPLACE SETUP & MODERATION COMMANDS
@bot.tree.command(name="setup_market", description="Setup the Marketplace panel embed in the current channel")
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.checks.has_permissions(administrator=True)
async def setup_market_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 RaDS MARKETPLACE",
        description=(
            "Welcome to the RaDS Marketplace! Here you can:\n\n"
            "🛒 **Buy or Hire** — Find talented designers / developers for your project\n"
            "💼 **Sell Services** — Offer your skills to potential clients\n\n"
            "Click the button below to get started. The process takes place in your DMs for privacy.\n\n"
            "⚠️ **All marketplace transactions must occur in designated server channels. DMing about jobs is prohibited.**"
        ),
        color=discord.Color.blue()
    )
    embed.set_footer(text="RaDS Marketplace • Safe & Secure Transactions")
    await interaction.channel.send(embed=embed, view=OpenMarketRequestView())
    await interaction.response.send_message("✅ Marketplace panel created!", ephemeral=True)

@bot.tree.command(name="marketplaceban", description="Ban a user from accessing Marketplace channels")
@discord.app_commands.describe(user="Target user to ban", reason="Reason for marketplace ban")
@discord.app_commands.default_permissions(manage_roles=True)
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
@discord.app_commands.default_permissions(manage_messages=True)
@discord.app_commands.checks.has_permissions(manage_messages=True)
async def postremove_command(interaction: discord.Interaction, post_id: int):
    await interaction.response.send_modal(RemovePostModal(post_id))

# CONFIG & SETTINGS COMMANDS
@bot.tree.command(name="settings", description="Show Marketplace configuration status")
@discord.app_commands.default_permissions(administrator=True)
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
        f"Channels: {get_status(fhd_id)}"
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
@discord.app_commands.default_permissions(administrator=True)
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
        f"✅ **{name}** successfully set to {channel.mention} (`{channel.id}`).",
        ephemeral=True
    )

# BOT EVENTS & RUN
@bot.event
async def on_ready():
    print("===========================")
    print(f"Bot successfully online as: {bot.user}")
    print("===========================")
    
    bot.add_view(OpenMarketRequestView())
    bot.add_view(MarketReviewView())
    bot.add_view(RolePickerDropdownView())
    
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
