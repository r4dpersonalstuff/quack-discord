import sqlite3
import re
from datetime import datetime, timedelta
import discord

DB_NAME = "/data/auto_messages.db"

# DYNAMIC CONFIG HELPER FUNCTIONS
def get_setting(key: str, default: int = 0) -> int:
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS config_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        row = cursor.execute("SELECT value FROM config_settings WHERE key = ?", (key,)).fetchone()
        return int(row[0]) if row and row[0].isdigit() else default

def set_setting(key: str, value: int):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS config_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        cursor.execute(
            "INSERT INTO config_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value))
        )
        conn.commit()

# ==========================================
# MARKETPLACE UI COMPONENTS & VIEWS
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
        limit_msg = check_listing_limits(interaction.user.id)
        if limit_msg:
            blocked = discord.Embed(title="⛔ Listing Not Submitted", description=limit_msg, color=discord.Color.red())
            return await interaction.response.edit_message(embed=blocked, view=None)

        skills_str = ", ".join(self.selected_skills)
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO market_posts (author_id, listing_type, skills, title, description, rates_or_budget, portfolio, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (interaction.user.id, self.listing_type, skills_str, self.title_input.value, self.desc_input.value, self.rates_input.value, self.portfolio_input.value or "N/A", datetime.now().isoformat()))
            post_id = cursor.lastrowid
            conn.commit()

        embed_done = discord.Embed(
            title="✅ Listing Submitted!",
            description=f"Your listing **'{self.title_input.value}'** (ID: `#{post_id}`) has been sent for moderator review.",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed_done, view=None)

        review_ch_id = get_setting("POST_REVIEW_CHANNEL_ID")
        review_ch = interaction.client.get_channel(review_ch_id)
        if not review_ch and review_ch_id:
            try:
                review_ch = await interaction.client.fetch_channel(review_ch_id)
            except Exception:
                pass

        if review_ch:
            embed = discord.Embed(title=f"🔍 Market Review #{post_id}: {self.title_input.value}", description=self.desc_input.value, color=discord.Color.gold(), timestamp=discord.utils.utcnow())
            embed.add_field(name="Post ID", value=f"`#{post_id}`", inline=True)
            embed.add_field(name="Author", value=f"<@{interaction.user.id}>", inline=True)
            embed.add_field(name="Type", value=self.listing_type, inline=True)
            embed.add_field(name="Skills / Role", value=skills_str, inline=False)
            embed.add_field(name="Rates / Budget", value=self.rates_input.value, inline=True)
            embed.add_field(name="Portfolio / Contact", value=self.portfolio_input.value or "N/A", inline=True)
            
            target_id = get_setting("LFD_CHANNEL_ID") if self.listing_type == "Sell Services" else get_setting("FHD_CHANNEL_ID")
            target_mention = f"<#{target_id}>" if target_id else "Not Configured"
            embed.add_field(name="Target Channel", value=target_mention, inline=False)
            await review_ch.send(embed=embed, view=MarketReviewView(post_id))

class MarketSkillSelect(discord.ui.Select):
    def __init__(self, listing_type: str):
        self.listing_type = listing_type
        profs = [
            ("Animator", "2D/3D Animation, Rigging, Motion Graphics"),
            ("Artist", "Concept Art, Pixel Art, Textures, 2D Sprites"),
            ("Builder", "Level Design, World Building, Map Environment"),
            ("Scripting", "Gameplay Programming, Systems, Luau"),
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
            description=f"Selected Skills: **{', '.join(selected)}**\nClick the button below to complete your listing details.", 
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
    reason = discord.ui.TextInput(
        label="Reason for Denial", 
        style=discord.TextStyle.paragraph, 
        placeholder="Provide a reason why this post is denied...", 
        required=True, 
        max_length=500
    )

    def __init__(self, post_id: int, review_msg: discord.Message):
        super().__init__()
        self.post_id = post_id
        self.review_msg = review_msg

    async def on_submit(self, interaction: discord.Interaction):
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            row = cursor.execute("SELECT author_id, title FROM market_posts WHERE id = ?", (self.post_id,)).fetchone()
            if row:
                author_id, title = row
                cursor.execute("UPDATE market_posts SET status = 'DENIED' WHERE id = ?", (self.post_id,))
                conn.commit()

                try:
                    author = await interaction.client.fetch_user(author_id)
                    if author:
                        embed = discord.Embed(
                            title="❌ Market Post Denied", 
                            description=f"Sorry, your post **'{title}'** (ID: `#{self.post_id}`) was denied by the moderators.", 
                            color=discord.Color.red(), 
                            timestamp=discord.utils.utcnow()
                        )
                        embed.add_field(name="Reason for Denial", value=self.reason.value, inline=False)
                        await author.send(embed=embed)
                except Exception as e:
                    print(f"Failed to send DM to user {author_id}: {e}")

        embed = self.review_msg.embeds[0]
        embed.color = discord.Color.red()
        embed.add_field(name="Status", value=f"❌ **Denied by {interaction.user.mention}**\n**Reason:** {self.reason.value}", inline=False)
        await self.review_msg.edit(embed=embed, view=None)
        await interaction.response.send_message(f"❌ Market post #{self.post_id} denied.", ephemeral=True)

class MarketReviewView(discord.ui.View):
    def __init__(self, post_id: int = 0):
        super().__init__(timeout=None)
        self.post_id = post_id

    def _get_post_id(self, interaction: discord.Interaction) -> int:
        if self.post_id and self.post_id != 0:
            return self.post_id
        try:
            import re
            title = interaction.message.embeds[0].title
            match = re.search(r'#(\d+)', title)
            if match:
                return int(match.group(1))
        except Exception:
            pass
        return 0

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅", custom_id="btn_accept_market")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        post_id = self._get_post_id(interaction)
        if not post_id:
            return await interaction.response.send_message("❌ Post ID not found.", ephemeral=True)

        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("ALTER TABLE market_posts ADD COLUMN message_id INTEGER")
            except sqlite3.OperationalError:
                pass

            row = cursor.execute("SELECT author_id, listing_type, skills, title, description, rates_or_budget, portfolio, status FROM market_posts WHERE id = ?", (post_id,)).fetchone()
            if not row or row[7] != 'PENDING':
                return await interaction.response.send_message("❌ This post has been reviewed previously.", ephemeral=True)
            author_id, listing_type, skills, title, desc, rates, portfolio, _ = row

            target_id = get_setting("LFD_CHANNEL_ID") if listing_type == "Sell Services" else get_setting("FHD_CHANNEL_ID")
            
            ch = interaction.guild.get_channel(target_id)
            if not ch and target_id:
                try:
                    ch = await interaction.guild.fetch_channel(target_id)
                except Exception as e:
                    print(f"Failed to retrieve the target channel {target_id}: {e}")

            published_msg_id = None
            if ch:
                p_embed = build_published_embed(post_id, listing_type, title, desc, skills, rates, portfolio, author_id)
                
                sent_msg = await ch.send(embed=p_embed, view=ContactPosterView())
                published_msg_id = sent_msg.id

            cursor.execute("UPDATE market_posts SET status = 'ACCEPTED', message_id = ?, published_at = ?, expiry_warned = 0 WHERE id = ?", (published_msg_id, datetime.now().isoformat(), post_id))
            conn.commit()

        try:
            author = await interaction.client.fetch_user(author_id)
            if author:
                dm_embed = discord.Embed(title="🎉 Market Post Approved!", description=f"Your post **'{title}'** has been approved and published in <#{target_id}>!", color=discord.Color.green())
                await author.send(embed=dm_embed)
        except Exception:
            pass

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.add_field(name="Status", value=f"✅ **Accepted by {interaction.user.mention}** (<#{target_id}>)", inline=False)
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="❌", custom_id="btn_deny_market")
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        post_id = self._get_post_id(interaction)
        if not post_id:
            return await interaction.response.send_message("❌ Post ID not found.", ephemeral=True)

        with sqlite3.connect(DB_NAME) as conn:
            row = conn.cursor().execute("SELECT status FROM market_posts WHERE id = ?", (post_id,)).fetchone()
        if not row or row[0] != 'PENDING':
            return await interaction.response.send_message("❌ This post has been reviewed previously.", ephemeral=True)
        await interaction.response.send_modal(MarketDenyModal(post_id, interaction.message))

class OpenMarketRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Request", style=discord.ButtonStyle.primary, emoji="📝", custom_id="btn_open_market_request")
    async def open_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        limit_msg = check_listing_limits(interaction.user.id)
        if limit_msg:
            return await interaction.response.send_message(limit_msg, ephemeral=True)
        embed = discord.Embed(title="📝 NEW LISTING", description="Hello! I'm here to help you with your journey into the RaDS Marketplace.\n\nFirst, what would you like to post?", color=discord.Color.blue())
        embed.set_footer(text="Step 1 of 3 • Send 'abort' or click Cancel to exit")
        try:
            await interaction.user.send(embed=embed, view=MarketTypeView())
            await interaction.response.send_message("📩 Sent you a DM to get started with your marketplace listing!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Failed to send DM. Please enable Direct Messages from server members in your Privacy Settings!", ephemeral=True)

class RemovePostModal(discord.ui.Modal, title="Remove Marketplace Post"):
    reason = discord.ui.TextInput(
        label="Reason for Removal",
        style=discord.TextStyle.paragraph,
        placeholder="Provide a reason why this post is being removed...",
        required=True,
        max_length=500
    )

    def __init__(self, post_id: int):
        super().__init__()
        self.post_id = post_id

    async def on_submit(self, interaction: discord.Interaction):
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("ALTER TABLE market_posts ADD COLUMN message_id INTEGER")
            except sqlite3.OperationalError:
                pass

            row = cursor.execute(
                "SELECT author_id, listing_type, title, status, message_id FROM market_posts WHERE id = ?", 
                (self.post_id,)
            ).fetchone()
            
            if not row:
                return await interaction.response.send_message(f"❌ Post `#{self.post_id}` was not found in the database.", ephemeral=True)
            
            author_id, listing_type, title, status, msg_id = row
            
            if status == "REMOVED":
                return await interaction.response.send_message(f"ℹ️ Post `#{self.post_id}` was previously marked as deleted.", ephemeral=False)

            cursor.execute("UPDATE market_posts SET status = 'REMOVED' WHERE id = ?", (self.post_id,))
            conn.commit()

        target_id = get_setting("LFD_CHANNEL_ID") if listing_type == "Sell Services" else get_setting("FHD_CHANNEL_ID")
        msg_deleted = False
        delete_error = ""

        if msg_id:
            try:
                ch = interaction.guild.get_channel(target_id)
                if not ch and target_id:
                    ch = await interaction.guild.fetch_channel(target_id)
                
                if ch:
                    msg = await ch.fetch_message(msg_id)
                    await msg.delete()
                    msg_deleted = True
            except discord.NotFound:
                delete_error = "The message has been manually deleted or cannot be found in the channel."
            except discord.Forbidden:
                delete_error = "The bot does not have the 'Manage Messages' permission in that channel."
            except Exception as e:
                delete_error = f"Error while deleting: {e}"
        else:
            delete_error = "Empty message ID in the database (old post or failed publication)."

        try:
            author = await interaction.client.fetch_user(author_id)
            if author:
                embed = discord.Embed(
                    title="🗑 Marketplace Post Removed",
                    description=f"Your listing **'{title}'** (ID: `#{self.post_id}`) has been removed by moderators.",
                    color=discord.Color.orange(),
                    timestamp=discord.utils.utcnow()
                )
                embed.add_field(name="Reason for Removal", value=self.reason.value, inline=False)
                await author.send(embed=embed)
        except Exception as e:
            print(f"Failed to send DM to user {author_id}: {e}")

        if msg_deleted:
            await interaction.response.send_message(
                f"✅ Post `#{self.post_id}` (**{title}**) has been successfully **DELETED** from <#{target_id}> & a DM has been sent to the user.\n**Reason:** {self.reason.value}", 
                ephemeral=False
            )
        else:
            await interaction.response.send_message(
                f"⚠️ DB status updated & DM sent, **but physical message in channel NOT deleted**.\n**Cause:** {delete_error}\n**Reason:** {self.reason.value}", 
                ephemeral=False
                
			)


# ==========================================
# MARKETPLACE LIMITS, EXPIRY, SELF-SERVICE & SEARCH
# ==========================================
DEFAULT_COOLDOWN_HOURS = 24
DEFAULT_MAX_ACTIVE_POSTS = 3
DEFAULT_EXPIRY_DAYS = 14
EXPIRY_WARNING_DAYS = 2

MARKET_SKILLS = ["Animator", "Artist", "Builder", "Scripting", "Sound Composer", "UI/UX", "VFX", "Modeler"]
LISTING_TYPES = ["Sell Services", "Buy or Hire"]
STATUS_LABELS = {
    "PENDING": "⏳ Pending review",
    "ACCEPTED": "✅ Live",
    "DENIED": "❌ Denied",
    "REMOVED": "🗑 Removed",
    "CLOSED": "🔒 Closed",
    "EXPIRED": "⌛ Expired",
}

def migrate_market_db(cursor):
    """Add the columns used by posting limits / expiry to older databases."""
    for col_def in ("message_id INTEGER", "created_at TEXT", "published_at TEXT", "expiry_warned INTEGER DEFAULT 0"):
        try:
            cursor.execute(f"ALTER TABLE market_posts ADD COLUMN {col_def}")
        except sqlite3.OperationalError:
            pass
    cursor.execute('''CREATE TABLE IF NOT EXISTS market_contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )''')
    # Listings that were already live before expiry existed start their timer now
    cursor.execute(
        "UPDATE market_posts SET published_at = ? WHERE status = 'ACCEPTED' AND published_at IS NULL",
        (datetime.now().isoformat(),)
    )

def get_market_limits():
    """Returns (cooldown_hours, max_active_posts, expiry_days). 0 means the limit is disabled."""
    return (
        get_setting("POST_COOLDOWN_HOURS", DEFAULT_COOLDOWN_HOURS),
        get_setting("MAX_ACTIVE_POSTS", DEFAULT_MAX_ACTIVE_POSTS),
        get_setting("POST_EXPIRY_DAYS", DEFAULT_EXPIRY_DAYS),
    )

def format_market_limits() -> str:
    cooldown, max_active, expiry = get_market_limits()
    def fmt(value, unit):
        return f"**{value} {unit}**" if value else "**Disabled**"
    return (
        f"Cooldown between listings: {fmt(cooldown, 'hour(s)')}\n"
        f"Max pending + live listings per user: {fmt(max_active, 'listing(s)')}\n"
        f"Live listing lifetime: {fmt(expiry, 'day(s)')}"
    )

def check_listing_limits(user_id: int):
    """Returns an error message if the user may not create a listing right now, otherwise None."""
    cooldown_h, max_active, _ = get_market_limits()
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        if max_active > 0:
            active = cursor.execute(
                "SELECT COUNT(*) FROM market_posts WHERE author_id = ? AND status IN ('PENDING', 'ACCEPTED')",
                (user_id,)
            ).fetchone()[0]
            if active >= max_active:
                return f"❌ You already have **{active}** pending/live listing(s) (limit: **{max_active}**). Close one with `/mypost` first."
        if cooldown_h > 0:
            last = cursor.execute(
                "SELECT MAX(created_at) FROM market_posts WHERE author_id = ? AND status != 'DENIED' AND created_at IS NOT NULL",
                (user_id,)
            ).fetchone()[0]
            if last:
                ready_at = datetime.fromisoformat(last) + timedelta(hours=cooldown_h)
                if ready_at > datetime.now():
                    return f"⏳ You can post again <t:{int(ready_at.timestamp())}:R> (cooldown: {cooldown_h}h)."
    return None

def get_target_channel_id(listing_type: str) -> int:
    return get_setting("LFD_CHANNEL_ID") if listing_type == "Sell Services" else get_setting("FHD_CHANNEL_ID")

def build_published_embed(post_id, listing_type, title, desc, skills, rates, portfolio, author_id):
    p_embed = discord.Embed(
        title=f"📢 [{listing_type.upper()}] {title}",
        description=desc,
        color=discord.Color.green() if listing_type == "Sell Services" else discord.Color.blue(),
        timestamp=discord.utils.utcnow()
    )
    p_embed.add_field(name="Poster", value=f"<@{author_id}>", inline=True)
    p_embed.add_field(name="Skills / Role", value=skills, inline=True)
    p_embed.add_field(name="Rates / Budget", value=rates, inline=True)
    if portfolio and portfolio != "N/A":
        p_embed.add_field(name="Portfolio / Contact", value=portfolio, inline=False)
    p_embed.set_footer(text=f"RaDS Marketplace • Post ID: #{post_id} • Safe & Secure Transactions")
    return p_embed

async def fetch_text_channel(client, channel_id: int):
    if not channel_id:
        return None
    ch = client.get_channel(channel_id)
    if not ch:
        try:
            ch = await client.fetch_channel(channel_id)
        except Exception:
            ch = None
    return ch

async def delete_published_message(client, listing_type: str, msg_id) -> bool:
    if not msg_id:
        return False
    try:
        ch = await fetch_text_channel(client, get_target_channel_id(listing_type))
        if not ch:
            return False
        msg = await ch.fetch_message(msg_id)
        await msg.delete()
        return True
    except Exception as e:
        print(f"Failed to delete the published message {msg_id}: {e}")
        return False

async def dm_user(client, user_id: int, embed: discord.Embed):
    try:
        user = await client.fetch_user(user_id)
        if user:
            await user.send(embed=embed)
    except Exception:
        pass

# AUTO-EXPIRE
async def run_market_expiry(client):
    expiry_days = get_setting("POST_EXPIRY_DAYS", DEFAULT_EXPIRY_DAYS)
    if expiry_days <= 0:
        return

    now = datetime.now()
    expire_before = (now - timedelta(days=expiry_days)).isoformat()
    to_warn = []
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        to_expire = cursor.execute(
            "SELECT id, author_id, listing_type, title, message_id FROM market_posts "
            "WHERE status = 'ACCEPTED' AND published_at IS NOT NULL AND published_at <= ?",
            (expire_before,)
        ).fetchall()
        for post_id, *_ in to_expire:
            cursor.execute("UPDATE market_posts SET status = 'EXPIRED' WHERE id = ?", (post_id,))

        if expiry_days > EXPIRY_WARNING_DAYS:
            warn_before = (now - timedelta(days=expiry_days - EXPIRY_WARNING_DAYS)).isoformat()
            to_warn = cursor.execute(
                "SELECT id, author_id, title, published_at FROM market_posts "
                "WHERE status = 'ACCEPTED' AND expiry_warned = 0 AND published_at IS NOT NULL AND published_at <= ?",
                (warn_before,)
            ).fetchall()
            for post_id, *_ in to_warn:
                cursor.execute("UPDATE market_posts SET expiry_warned = 1 WHERE id = ?", (post_id,))
        conn.commit()

    for post_id, author_id, listing_type, title, msg_id in to_expire:
        await delete_published_message(client, listing_type, msg_id)
        embed = discord.Embed(
            title="⌛ Marketplace Listing Expired",
            description=f"Your listing **'{title}'** (ID: `#{post_id}`) was live for {expiry_days} days and has expired. You can create a new listing any time.",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        await dm_user(client, author_id, embed)

    for post_id, author_id, title, published_at in to_warn:
        expires_at = datetime.fromisoformat(published_at) + timedelta(days=expiry_days)
        embed = discord.Embed(
            title="⚠️ Your Listing Expires Soon",
            description=f"Your listing **'{title}'** (ID: `#{post_id}`) expires <t:{int(expires_at.timestamp())}:R>.\nUse `/mypost` → select the listing → **Renew** to keep it up.",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )
        await dm_user(client, author_id, embed)

# MY POSTS (EDIT / RENEW / CLOSE)
def get_post(post_id: int):
    with sqlite3.connect(DB_NAME) as conn:
        return conn.cursor().execute(
            "SELECT id, author_id, listing_type, skills, title, description, rates_or_budget, portfolio, status, message_id, published_at "
            "FROM market_posts WHERE id = ?",
            (post_id,)
        ).fetchone()

def fetch_user_posts(user_id: int):
    with sqlite3.connect(DB_NAME) as conn:
        return conn.cursor().execute(
            "SELECT id, title, status, listing_type FROM market_posts "
            "WHERE author_id = ? AND status IN ('PENDING', 'ACCEPTED') ORDER BY id DESC LIMIT 25",
            (user_id,)
        ).fetchall()

def build_my_posts_embed(posts):
    lines = [f"• `#{pid}` **{title}** — {STATUS_LABELS.get(status, status)}" for pid, title, status, _ in posts]
    return discord.Embed(
        title="📂 My Marketplace Listings",
        description="\n".join(lines) + "\n\nSelect a listing below to edit, renew, or close it.",
        color=discord.Color.blue()
    )

def build_post_detail_embed(post):
    pid, _, listing_type, skills, title, desc, rates, portfolio, status, _, published_at = post
    embed = discord.Embed(title=f"📄 Listing #{pid}: {title}", description=desc, color=discord.Color.blue())
    embed.add_field(name="Type", value=listing_type, inline=True)
    embed.add_field(name="Status", value=STATUS_LABELS.get(status, status), inline=True)
    embed.add_field(name="Skills / Role", value=skills, inline=False)
    embed.add_field(name="Rates / Budget", value=rates, inline=True)
    embed.add_field(name="Portfolio / Contact", value=portfolio or "N/A", inline=True)
    _, _, expiry_days = get_market_limits()
    if status == "ACCEPTED" and published_at and expiry_days > 0:
        expires_at = datetime.fromisoformat(published_at) + timedelta(days=expiry_days)
        embed.add_field(name="Expires", value=f"<t:{int(expires_at.timestamp())}:R>", inline=False)
    return embed

class EditListingModal(discord.ui.Modal, title="Edit Your Listing"):
    def __init__(self, post_id: int, title: str, description: str, rates: str, portfolio: str):
        super().__init__()
        self.post_id = post_id
        self.title_input = discord.ui.TextInput(label="Title / Headline", default=title, required=True, max_length=100)
        self.desc_input = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, default=description, required=True, max_length=1000)
        self.rates_input = discord.ui.TextInput(label="Rates / Budget", default=rates, required=True, max_length=100)
        self.portfolio_input = discord.ui.TextInput(label="Portfolio / Contact Info", default="" if portfolio == "N/A" else portfolio, required=False, max_length=200)
        for item in (self.title_input, self.desc_input, self.rates_input, self.portfolio_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        new_portfolio = self.portfolio_input.value.strip() or "N/A"
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT author_id, listing_type, skills, status, message_id FROM market_posts WHERE id = ?",
                (self.post_id,)
            ).fetchone()
            if not row or row[0] != interaction.user.id or row[3] != "ACCEPTED":
                return await interaction.response.send_message("❌ This listing can no longer be edited.", ephemeral=True)
            _, listing_type, skills, _, msg_id = row
            cursor.execute(
                "UPDATE market_posts SET title = ?, description = ?, rates_or_budget = ?, portfolio = ? WHERE id = ?",
                (self.title_input.value, self.desc_input.value, self.rates_input.value, new_portfolio, self.post_id)
            )
            conn.commit()

        updated = False
        if msg_id:
            try:
                ch = await fetch_text_channel(interaction.client, get_target_channel_id(listing_type))
                if ch:
                    msg = await ch.fetch_message(msg_id)
                    await msg.edit(embed=build_published_embed(
                        self.post_id, listing_type, self.title_input.value, self.desc_input.value,
                        skills, self.rates_input.value, new_portfolio, interaction.user.id
                    ), view=ContactPosterView())
                    updated = True
            except Exception as e:
                print(f"Failed to update the published message {msg_id}: {e}")

        # Let moderators know about the change (edits skip a new review)
        review_ch = await fetch_text_channel(interaction.client, get_setting("POST_REVIEW_CHANNEL_ID"))
        if review_ch:
            try:
                notice = discord.Embed(
                    title=f"✏️ Listing #{self.post_id} Edited: {self.title_input.value}",
                    description=self.desc_input.value,
                    color=discord.Color.orange(),
                    timestamp=discord.utils.utcnow()
                )
                notice.add_field(name="Author", value=f"<@{interaction.user.id}>", inline=True)
                notice.add_field(name="Rates / Budget", value=self.rates_input.value, inline=True)
                notice.add_field(name="Portfolio / Contact", value=new_portfolio, inline=True)
                notice.set_footer(text="Use /postremove if this edit violates the rules")
                await review_ch.send(embed=notice)
            except Exception as e:
                print(f"Failed to send the edit notice: {e}")

        msg = f"✅ Listing `#{self.post_id}` updated!" if updated else f"⚠️ Listing `#{self.post_id}` was saved, but the public post could not be updated automatically."
        await interaction.response.send_message(msg, ephemeral=True)

class ConfirmCloseView(discord.ui.View):
    def __init__(self, post_id: int):
        super().__init__(timeout=60)
        self.post_id = post_id

    @discord.ui.button(label="Yes, close it", style=discord.ButtonStyle.danger, emoji="🔒")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT listing_type, title, status, message_id FROM market_posts WHERE id = ? AND author_id = ?",
                (self.post_id, interaction.user.id)
            ).fetchone()
            if not row or row[2] not in ("PENDING", "ACCEPTED"):
                return await interaction.response.edit_message(content="❌ This listing can no longer be closed.", embed=None, view=None)
            listing_type, title, status, msg_id = row
            cursor.execute("UPDATE market_posts SET status = 'CLOSED' WHERE id = ?", (self.post_id,))
            conn.commit()

        note = ""
        if status == "ACCEPTED":
            if not await delete_published_message(interaction.client, listing_type, msg_id):
                note = "\n⚠️ The public post could not be deleted automatically. Please ask a moderator to remove it."
        embed = discord.Embed(
            title="🔒 Listing Closed",
            description=f"Your listing **'{title}'** (ID: `#{self.post_id}`) has been closed.{note}",
            color=discord.Color.orange()
        )
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Keep it", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def keep_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        post = get_post(self.post_id)
        if not post or post[1] != interaction.user.id or post[8] not in ("PENDING", "ACCEPTED"):
            return await interaction.response.edit_message(content="❌ This listing is no longer available.", embed=None, view=None)
        await interaction.response.edit_message(embed=build_post_detail_embed(post), view=MyPostActionView(post[0], post[8]))

class MyPostActionView(discord.ui.View):
    def __init__(self, post_id: int, status: str):
        super().__init__(timeout=300)
        self.post_id = post_id
        live = status == "ACCEPTED"
        self.edit_button.disabled = not live
        self.renew_button.disabled = not live

    def _owned_post(self, interaction: discord.Interaction):
        post = get_post(self.post_id)
        if not post or post[1] != interaction.user.id or post[8] not in ("PENDING", "ACCEPTED"):
            return None
        return post

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, emoji="✏️")
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        post = self._owned_post(interaction)
        if not post or post[8] != "ACCEPTED":
            return await interaction.response.send_message("❌ This listing can no longer be edited.", ephemeral=True)
        await interaction.response.send_modal(EditListingModal(post[0], post[4], post[5], post[6], post[7]))

    @discord.ui.button(label="Renew", style=discord.ButtonStyle.success, emoji="🔄")
    async def renew_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        with sqlite3.connect(DB_NAME) as conn:
            cur = conn.cursor().execute(
                "UPDATE market_posts SET published_at = ?, expiry_warned = 0 WHERE id = ? AND author_id = ? AND status = 'ACCEPTED'",
                (datetime.now().isoformat(), self.post_id, interaction.user.id)
            )
            conn.commit()
            renewed = cur.rowcount > 0
        post = get_post(self.post_id)
        if not renewed or not post:
            return await interaction.response.send_message("❌ This listing can no longer be renewed.", ephemeral=True)
        embed = build_post_detail_embed(post)
        embed.set_footer(text="🔄 Listing renewed!")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, emoji="🔒")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        post = self._owned_post(interaction)
        if not post:
            return await interaction.response.send_message("❌ This listing can no longer be closed.", ephemeral=True)
        embed = discord.Embed(
            title="🔒 Close this listing?",
            description=f"**#{post[0]} — {post[4]}**\nThis cannot be undone. A live post will be deleted from the channel.",
            color=discord.Color.orange()
        )
        await interaction.response.edit_message(embed=embed, view=ConfirmCloseView(post[0]))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="⬅️")
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        posts = fetch_user_posts(interaction.user.id)
        if not posts:
            return await interaction.response.edit_message(content="ℹ️ You have no pending or live listings.", embed=None, view=None)
        await interaction.response.edit_message(embed=build_my_posts_embed(posts), view=MyPostsView(posts))

class MyPostSelect(discord.ui.Select):
    def __init__(self, posts):
        options = [
            discord.SelectOption(label=f"#{pid} • {title}"[:100], value=str(pid), description=STATUS_LABELS.get(status, status)[:100])
            for pid, title, status, _ in posts
        ]
        super().__init__(placeholder="Select a listing to manage", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        post = get_post(int(self.values[0]))
        if not post or post[1] != interaction.user.id or post[8] not in ("PENDING", "ACCEPTED"):
            return await interaction.response.send_message("❌ This listing is no longer available.", ephemeral=True)
        await interaction.response.edit_message(embed=build_post_detail_embed(post), view=MyPostActionView(post[0], post[8]))

class MyPostsView(discord.ui.View):
    def __init__(self, posts):
        super().__init__(timeout=300)
        self.add_item(MyPostSelect(posts))

async def send_my_posts(interaction: discord.Interaction):
    posts = fetch_user_posts(interaction.user.id)
    if not posts:
        return await interaction.response.send_message(
            "ℹ️ You have no pending or live listings. Use the marketplace panel to create one.", ephemeral=True
        )
    await interaction.response.send_message(embed=build_my_posts_embed(posts), view=MyPostsView(posts), ephemeral=True)

# SEARCH
def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

def search_market_posts(skill=None, listing_type=None, keyword=None, limit: int = 8):
    query = ("SELECT id, author_id, listing_type, skills, title, rates_or_budget, message_id "
             "FROM market_posts WHERE status = 'ACCEPTED'")
    params = []
    if skill:
        query += " AND skills LIKE ? ESCAPE '\\'"
        params.append(_like(skill))
    if listing_type:
        query += " AND listing_type = ?"
        params.append(listing_type)
    if keyword:
        query += " AND (title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')"
        params.extend([_like(keyword), _like(keyword)])
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with sqlite3.connect(DB_NAME) as conn:
        return conn.cursor().execute(query, params).fetchall()

async def send_market_search(interaction: discord.Interaction, skill=None, listing_type=None, keyword=None):
    rows = search_market_posts(skill, listing_type, keyword)
    if not rows:
        return await interaction.response.send_message("ℹ️ No live listings match your search.", ephemeral=True)

    guild_id = interaction.guild.id if interaction.guild else None
    entries = []
    for pid, author_id, ltype, skills, title, rates, msg_id in rows:
        ch_id = get_target_channel_id(ltype)
        link = f" • [Jump to post](https://discord.com/channels/{guild_id}/{ch_id}/{msg_id})" if guild_id and ch_id and msg_id else ""
        entries.append(f"**#{pid} • {title}**\n{ltype} • {skills}\n💰 {rates} • <@{author_id}>{link}")

    filters = [f for f in (skill, listing_type, f'"{keyword}"' if keyword else None) if f]
    embed = discord.Embed(
        title=f"🔎 Marketplace Search ({len(rows)} result(s))",
        description="\n\n".join(entries),
        color=discord.Color.blue()
    )
    embed.set_footer(text="Filters: " + (", ".join(filters) if filters else "none (latest listings)"))
    await interaction.response.send_message(embed=embed, ephemeral=True)


# CONTACT POSTER
CONTACT_COOLDOWN_MINUTES = 30

def get_post_id_from_message(message):
    """Reads the post ID from the footer of a published listing embed."""
    try:
        footer = message.embeds[0].footer.text
        match = re.search(r"Post ID: #(\d+)", footer)
        return int(match.group(1)) if match else 0
    except Exception:
        return 0

def check_contact_cooldown(sender_id: int, post_id: int):
    with sqlite3.connect(DB_NAME) as conn:
        last = conn.cursor().execute(
            "SELECT MAX(created_at) FROM market_contacts WHERE sender_id = ? AND post_id = ?",
            (sender_id, post_id)
        ).fetchone()[0]
    if last:
        ready_at = datetime.fromisoformat(last) + timedelta(minutes=CONTACT_COOLDOWN_MINUTES)
        if ready_at > datetime.now():
            return f"⏳ You already contacted this poster. You can send another message <t:{int(ready_at.timestamp())}:R>."
    return None

class ContactPosterModal(discord.ui.Modal, title="Contact the Poster"):
    def __init__(self, post_id: int):
        super().__init__()
        self.post_id = post_id
        self.message_input = discord.ui.TextInput(
            label="Your message",
            style=discord.TextStyle.paragraph,
            placeholder="Introduce yourself and describe what you need or offer...",
            required=True,
            max_length=1000
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        post = get_post(self.post_id)
        if not post or post[8] != "ACCEPTED":
            return await interaction.response.send_message("❌ This listing is no longer available.", ephemeral=True)

        cooldown_msg = check_contact_cooldown(interaction.user.id, self.post_id)
        if cooldown_msg:
            return await interaction.response.send_message(cooldown_msg, ephemeral=True)

        embed = discord.Embed(
            title=f"📩 New Inquiry: {post[4]}"[:256],
            description=self.message_input.value,
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="From", value=f"<@{interaction.user.id}>", inline=True)
        embed.add_field(name="Your Listing", value=f"`#{self.post_id}` {post[4]}", inline=True)
        embed.set_footer(text="Reply by messaging them directly • RaDS Marketplace")

        try:
            poster = await interaction.client.fetch_user(post[1])
            await poster.send(embed=embed)
        except Exception as e:
            print(f"Failed to deliver the inquiry for post {self.post_id}: {e}")
            return await interaction.response.send_message(
                "❌ Your message could not be delivered. The poster may have their DMs closed.", ephemeral=True
            )

        with sqlite3.connect(DB_NAME) as conn:
            conn.cursor().execute(
                "INSERT INTO market_contacts (post_id, sender_id, created_at) VALUES (?, ?, ?)",
                (self.post_id, interaction.user.id, datetime.now().isoformat())
            )
            conn.commit()
        await interaction.response.send_message("✅ Your message was sent to the poster!", ephemeral=True)

class ContactPosterView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Contact Poster", style=discord.ButtonStyle.primary, emoji="📩", custom_id="btn_contact_poster")
    async def contact_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        post_id = get_post_id_from_message(interaction.message)
        post = get_post(post_id) if post_id else None
        if not post or post[8] != "ACCEPTED":
            return await interaction.response.send_message("❌ This listing is no longer available.", ephemeral=True)
        if post[1] == interaction.user.id:
            return await interaction.response.send_message("❌ This is your own listing.", ephemeral=True)

        cooldown_msg = check_contact_cooldown(interaction.user.id, post_id)
        if cooldown_msg:
            return await interaction.response.send_message(cooldown_msg, ephemeral=True)
        await interaction.response.send_modal(ContactPosterModal(post_id))
