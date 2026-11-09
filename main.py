import os
import random
import sqlite3
from datetime import datetime, timedelta
import discord
from discord.ext import commands, tasks

# ==========================================
# 1. BOT & DATABASE INITIALIZATION
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

DB_NAME = "/data/auto_messages.db"

# Channel IDs for GD Marketplace (Replace with your server's actual Channel IDs!)
POST_REVIEW_CHANNEL_ID = 123456789012345678
LFD_CHANNEL_ID = 123456789012345678
FHD_CHANNEL_ID = 123456789012345678

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
                try: await ch.send(msg)
                except Exception as e: print(f"Error sending msg: {e}")
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
    await interaction.response.send_message(f"✅ Auto-message scheduled for {channel.mention} every {delay_minutes} min!
**Message:** {message}", ephemeral=True)

@bot.tree.command(name="list_auto_msgs", description="View active recurring automated messages")
async def list_auto_msgs(interaction: discord.Interaction):
    with sqlite3.connect(DB_NAME) as conn:
        records = conn.cursor().execute("SELECT id, channel_id, delay_minutes, message FROM auto_messages").fetchall()
    if not records:
        return await interaction.response.send_message("ℹ️ No active auto-messages found.", ephemeral=True)
    embed = discord.Embed(title="📋 Active Automated Messages", color=discord.Color.blue())
    for r_id, ch_id, delay, msg in records:
        embed.add_field(name=f"ID #{r_id} | Channel: <#{ch_id}>", value=f"**Interval:** Every {delay} min
**Message:** {msg}", inline=False)
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
    if max_value < 1: return await interaction.response.send_message("❌ Max value must be >= 1.", ephemeral=True)
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
            if to_add: await interaction.user.add_roles(*to_add)
            if to_rem: await interaction.user.remove_roles(*to_rem)
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
    if image_url: embed.set_image(url=image_url)
    await interaction.channel.send(embed=embed, view=RolePickerDropdownView(roles))
    await interaction.response.send_message("✅ Role picker created!", ephemeral=True)

# ==========================================
# 6. GENERIC POST & REVIEW SYSTEM
# ==========================================
class DenyReasonModal(discord.ui.Modal, title="Deny Post Request"):
    reason = discord.ui.TextInput(label="Reason", style=discord.TextStyle.paragraph, required=True, max_length=500)
    def __init__(self, post_id: int, review_msg: discord.Message):
        super().__init__()
        self.post_id, self.review_msg = post_id, review_msg

    async def on_submit(self, interaction: discord.Interaction):
        with sqlite3.connect(DB_NAME) as conn:
            conn.cursor().execute("UPDATE pending_posts SET status = 'DENIED' WHERE id = ?", (self.post_id,))
            conn.commit()
        embed = self.review_msg.embeds[0]
        embed.color = discord.Color.red()
        embed.add_field(name="Status", value=f"❌ **Denied by {interaction.user.mention}**
**Reason:** {self.reason.value}", inline=False)
        await self.review_msg.edit(embed=embed, view=None)
        await interaction.response.send_message(f"❌ Post #{self.post_id} denied.", ephemeral=True)

class PostReviewView(discord.ui.View):
    def __init__(self, post_id: int):
        super().__init__(timeout=None)
        self.post_id = post_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="btn_accept_post")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            row = cursor.execute("SELECT target_channel_id, title, description, image_url, status FROM pending_posts WHERE id = ?", (self.post_id,)).fetchone()
            if not row or row[4] != 'PENDING':
                return await interaction.response.send_message("❌ Post not pending.", ephemeral=True)
            target_id, title, desc, img, _ = row
            cursor.execute("UPDATE pending_posts SET status = 'ACCEPTED' WHERE id = ?", (self.post_id,))
            conn.commit()
        ch = interaction.guild.get_channel(target_id)
        if ch:
            pub_embed = discord.Embed(title=title, description=desc, color=discord.Color.blue(), timestamp=discord.utils.utcnow())
            if img: pub_embed.set_image(url=img)
            await ch.send(embed=pub_embed)
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.add_field(name="Status", value=f"✅ **Accepted by {interaction.user.mention}** (<#{target_id}>)", inline=False)
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="btn_deny_post")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        with sqlite3.connect(DB_NAME) as conn:
            row = conn.cursor().execute("SELECT status FROM pending_posts WHERE id = ?", (self.post_id,)).fetchone()
        if not row or row[0] != 'PENDING':
            return await interaction.response.send_message("❌ Post not pending.", ephemeral=True)
        await interaction.response.send_modal(DenyReasonModal(self.post_id, interaction.message))

@bot.tree.command(name="post", description="Create an embedded post submission for admin review")
async def post_command(interaction: discord.Interaction, title: str, description: str, target_channel: discord.TextChannel, image_url: str = None):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO pending_posts (author_id, target_channel_id, title, description, image_url) VALUES (?, ?, ?, ?, ?)",
                       (interaction.user.id, target_channel.id, title, description, image_url))
        post_id = cursor.lastrowid
        conn.commit()
    await interaction.response.send_message(f"📝 Post submitted! ID: `#{post_id}`", ephemeral=True)

@bot.tree.command(name="postsreview", description="Review a submitted post embed")
async def postsreview_command(interaction: discord.Interaction, post_id: int):
    with sqlite3.connect(DB_NAME) as conn:
        row = conn.cursor().execute("SELECT author_id, target_channel_id, title, description, image_url, status FROM pending_posts WHERE id = ?", (post_id,)).fetchone()
    if not row: return await interaction.response.send_message(f"❌ Post ID `#{post_id}` not found.", ephemeral=True)
    author_id, target_id, title, desc, img, status = row
    if status != 'PENDING': return await interaction.response.send_message(f"ℹ️ Post `#{post_id}` already {status}.", ephemeral=True)

    embed = discord.Embed(title=f"🔍 Review Post #{post_id}: {title}", description=desc, color=discord.Color.gold(), timestamp=discord.utils.utcnow())
    embed.add_field(name="Author", value=f"<@{author_id}>", inline=True)
    embed.add_field(name="Target Channel", value=f"<#{target_id}>", inline=True)
    if img: embed.set_image(url=img)
    await interaction.response.send_message(embed=embed, view=PostReviewView(post_id))

# ==========================================
# 7. ADVANCED MARKETPLACE DM FLOW (ENGLISH)
# ==========================================
class CancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")

    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="❌ Listing Cancelled",
            description="The marketplace listing creation process has been cancelled.",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=None)

class MarketDetailsModal(discord.ui.Modal, title="Marketplace Listing Details"):
    title_input = discord.ui.TextInput(label="Title / Headline", placeholder="e.g., 3D Modeler for Hire / Need Developer", required=True, max_length=100)
    desc_input = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, placeholder="Describe your services, project scope, requirements...", required=True, max_length=1000)
    rates_input = discord.ui.TextInput(label="Rates / Budget", placeholder="e.g., $500/project, $30/hr, or Negotiable", required=True, max_length=100)
    portfolio_input = discord.ui.TextInput(label="Portfolio / Contact Info", placeholder="ArtStation, GitHub, Drive link, preferred contact", required=False, max_length=200)

    def __init__(self, listing_type: str, selected_skills: list[str]):
        super().__init__()
        self.listing_type, self.selected_skills = listing_type, selected_skills

    async def on_submit(self, interaction: discord.Interaction):
        skills_str = ", ".join(self.selected_skills)
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO market_posts (author_id, listing_type, skills, title, description, rates_or_budget, portfolio) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (interaction.user.id, self.listing_type, skills_str, self.title_input.value, self.desc_input.value, self.rates_input.value, self.portfolio_input.value or "N/A"))
            post_id = cursor.lastrowid
            conn.commit()

        embed_done = discord.Embed(
            title="✅ Listing Submitted!",
            description=f"Your listing **'{self.title_input.value}'** (ID: `#{post_id}`) has been sent for moderator review.",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed_done, view=None)

        review_ch = interaction.client.get_channel(POST_REVIEW_CHANNEL_ID)
        if review_ch:
            embed = discord.Embed(title=f"🔍 Market Review #{post_id}: {self.title_input.value}", description=self.desc_input.value, color=discord.Color.gold(), timestamp=discord.utils.utcnow())
            embed.add_field(name="Author", value=f"<@{interaction.user.id}>", inline=True)
            embed.add_field(name="Type", value=self.listing_type, inline=True)
            embed.add_field(name="Skills / Role", value=skills_str, inline=False)
            embed.add_field(name="Rates / Budget", value=self.rates_input.value, inline=True)
            embed.add_field(name="Portfolio / Contact", value=self.portfolio_input.value or "N/A", inline=True)
            target_mention = f"<#{LFD_CHANNEL_ID}>" if self.listing_type == "Sell Services" else f"<#{FHD_CHANNEL_ID}>"
            embed.add_field(name="Target Channel", value=target_mention, inline=False)
            await review_ch.send(embed=embed, view=MarketReviewView(post_id))

class MarketSkillSelect(discord.ui.Select):
    def __init__(self, listing_type: str):
        self.listing_type = listing_type
        profs = [
            ("Animator", "2D/3D Animation, Rigging, Motion Graphics"),
            ("Artist", "Concept Art, Pixel Art, Textures, 2D Sprites"),
            ("Builder", "Level Design, World Building, Map Environment"),
            ("Scripting", "Gameplay Programming, Systems, GDScript/Luau/C#"),
            ("Sound Composer", "Music Production, SFX, Audio Design"),
            ("UI/UX", "User Interface, Layout Design, HUD Systems"),
            ("VFX", "Particle Systems, Shaders, Visual Effects"),
            ("Modeler", "3D Asset Modeling, Texturing, Sculpting")
        ]
        options = [discord.SelectOption(label=name, description=desc, value=name) for name, desc in profs]
        super().__init__(placeholder="Pick up to 3 skills", min_values=1, max_values=3, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected = self.values
        embed = discord.Embed(
            title="📝 Almost Done!", 
            description=f"Selected Skills: **{', '.join(selected)}**
Click the button below to complete your listing details.", 
            color=discord.Color.blue()
        )
        embed.set_footer(text="Step 3 of 3 • Send 'abort' or click Cancel to exit")
        await interaction.response.edit_message(embed=embed, view=MarketFillDetailsView(self.listing_type, selected))

class MarketSkillView(discord.ui.View):
    def __init__(self, listing_type: str):
        super().__init__(timeout=None)
        self.add_item(MarketSkillSelect(listing_type))
        self.add_item(CancelButton())

class MarketFillDetailsView(discord.ui.View):
    def __init__(self, listing_type: str, selected_skills: list[str]):
        super().__init__(timeout=None)
        self.listing_type, self.selected_skills = listing_type, selected_skills
        self.add_item(CancelButton())

    @discord.ui.button(label="Fill Details Form", style=discord.ButtonStyle.primary, emoji="✍️")
    async def fill_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(MarketDetailsModal(self.listing_type, self.selected_skills))

class MarketTypeSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Sell Services", description="Offer your skills to potential clients", emoji="💼"),
            discord.SelectOption(label="Buy or Hire", description="Hire designers or developers for your project", emoji="🛒")
        ]
        super().__init__(placeholder="Select listing type", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🛠️ SKILLS", 
            description="Select up to 3 skills/professions that best describe your service or request.", 
            color=discord.Color.blue()
        )
        embed.set_footer(text="Step 2 of 3 • Send 'abort' or click Cancel to exit")
        await interaction.response.edit_message(embed=embed, view=MarketSkillView(self.values[0]))

class MarketTypeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(MarketTypeSelect())
        self.add_item(CancelButton())

class MarketDenyModal(discord.ui.Modal, title="Deny Marketplace Post"):
    reason = discord.ui.TextInput(label="Reason for Denial", style=discord.TextStyle.paragraph, placeholder="Provide a reason why this post is denied...", required=True, max_length=500)
    def __init__(self, post_id: int, review_msg: discord.Message):
        super().__init__()
        self.post_id, self.review_msg = post_id, review_msg

    async def on_submit(self, interaction: discord.Interaction):
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            row = cursor.execute("SELECT author_id, title FROM market_posts WHERE id = ?", (self.post_id,)).fetchone()
            if row:
                author_id, title = row
                cursor.execute("UPDATE market_posts SET status = 'DENIED' WHERE id = ?", (self.post_id,))
                conn.commit()
                author = interaction.client.get_user(author_id)
                if author:
                    try:
                        embed = discord.Embed(title="❌ Market Post Denied", description=f"Sorry, your post **'{title}'** (ID: `#{self.post_id}`) was denied by the moderators.", color=discord.Color.red(), timestamp=discord.utils.utcnow())
                        embed.add_field(name="Reason for Denial", value=self.reason.value, inline=False)
                        await author.send(embed=embed)
                    except Exception: pass
        embed = self.review_msg.embeds[0]
        embed.color = discord.Color.red()
        embed.add_field(name="Status", value=f"❌ **Denied by {interaction.user.mention}**
**Reason:** {self.reason.value}", inline=False)
        await self.review_msg.edit(embed=embed, view=None)
        await interaction.response.send_message(f"❌ Market post #{self.post_id} denied.", ephemeral=True)

class MarketReviewView(discord.ui.View):
    def __init__(self, post_id: int):
        super().__init__(timeout=None)
        self.post_id = post_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅", custom_id="btn_accept_market")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            row = cursor.execute("SELECT author_id, listing_type, skills, title, description, rates_or_budget, portfolio, status FROM market_posts WHERE id = ?", (self.post_id,)).fetchone()
            if not row or row[7] != 'PENDING':
                return await interaction.response.send_message("❌ This post has already been reviewed.", ephemeral=True)
            author_id, listing_type, skills, title, desc, rates, portfolio, _ = row
            cursor.execute("UPDATE market_posts SET status = 'ACCEPTED' WHERE id = ?", (self.post_id,))
            conn.commit()

        target_id = LFD_CHANNEL_ID if listing_type == "Sell Services" else FHD_CHANNEL_ID
        ch = interaction.guild.get_channel(target_id)
        if ch:
            p_embed = discord.Embed(title=f"📢 {title}", description=desc, color=discord.Color.green() if listing_type == "Sell Services" else discord.Color.blue(), timestamp=discord.utils.utcnow())
            p_embed.add_field(name="Poster", value=f"<@{author_id}>", inline=True)
            p_embed.add_field(name="Skills / Role", value=skills, inline=True)
            p_embed.add_field(name="Rates / Budget", value=rates, inline=True)
            if portfolio and portfolio != "N/A": p_embed.add_field(name="Portfolio / Contact", value=portfolio, inline=False)
            p_embed.set_footer(text="GD Marketplace • Safe & Secure Transactions")
            await ch.send(embed=p_embed)

        author = interaction.client.get_user(author_id)
        if author:
            try:
                dm_embed = discord.Embed(title="🎉 Market Post Approved!", description=f"Your post **'{title}'** has been approved and published in <#{target_id}>!", color=discord.Color.green())
                await author.send(embed=dm_embed)
            except Exception: pass

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.add_field(name="Status", value=f"✅ **Accepted by {interaction.user.mention}** (<#{target_id}>)", inline=False)
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="❌", custom_id="btn_deny_market")
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        with sqlite3.connect(DB_NAME) as conn:
            row = conn.cursor().execute("SELECT status FROM market_posts WHERE id = ?", (self.post_id,)).fetchone()
        if not row or row[0] != 'PENDING':
            return await interaction.response.send_message("❌ This post has already been reviewed.", ephemeral=True)
        await interaction.response.send_modal(MarketDenyModal(self.post_id, interaction.message))

class OpenMarketRequestView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Open Request", style=discord.ButtonStyle.primary, emoji="📝", custom_id="btn_open_market_request")
    async def open_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="📝 NEW LISTING", description="Hello! I'm here to help you with your journey into the RAC Marketplace.

First, what would you like to post?", color=discord.Color.blue())
        embed.set_footer(text="Step 1 of 3 • Send 'abort' or click Cancel to exit")
        try:
            await interaction.user.send(embed=embed, view=MarketTypeView())
            await interaction.response.send_message("📩 Sent you a Direct Message to get started with your marketplace listing!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Failed to send DM. Please enable Direct Messages from server members in your Privacy Settings!", ephemeral=True)

@bot.tree.command(name="setup_market", description="Setup the RAC Marketplace panel embed in the current channel")
async def setup_market_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 GD MARKETPLACE",
        description=(
            "Welcome to the RAC Marketplace! Here you can:

"
            "🛒 **Buy or Hire** — Find talented designers / developers for your project
"
            "💼 **Sell Services** — Offer your skills to potential clients

"
            "Click the button below to get started. The process takes place in your DMs for privacy.

"
            "⚠️ **All marketplace transactions must occur in designated server channels. DMing about jobs is prohibited.**"
        ),
        color=discord.Color.blue()
    )
    embed.set_footer(text="RAC Marketplace • Safe & Secure Transactions")
    await interaction.channel.send(embed=embed, view=OpenMarketRequestView())
    await interaction.response.send_message("✅ Marketplace panel created!", ephemeral=True)

# ==========================================
# 8. BOT EVENTS & RUN
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

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("ERROR: DISCORD_TOKEN is missing!")
else:
    bot.run(TOKEN)
