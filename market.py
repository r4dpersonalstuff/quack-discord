import sqlite3
import discord

DB_NAME = "/data/auto_messages.db"

# ==========================================
# DYNAMIC CONFIG HELPER FUNCTIONS
# ==========================================
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
    title_input = discord.ui.TextInput(label="Title / Headline", placeholder="e.g., 3D Modeler for Hire / Need Godot Developer", required=True, max_length=100)
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

        review_ch_id = get_setting("POST_REVIEW_CHANNEL_ID")
        review_ch = interaction.client.get_channel(review_ch_id)
        if not review_ch and review_ch_id:
            try:
                review_ch = await interaction.client.fetch_channel(review_ch_id)
            except Exception:
                pass

        if review_ch:
            embed = discord.Embed(title=f"🔍 Market Review #{post_id}: {self.title_input.value}", description=self.desc_input.value, color=discord.Color.gold(), timestamp=discord.utils.utcnow())
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
                    print(f"Gagal mengirim DM ke user {author_id}: {e}")

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
            return await interaction.response.send_message("❌ ID postingan tidak ditemukan.", ephemeral=True)

        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("ALTER TABLE market_posts ADD COLUMN message_id INTEGER")
            except sqlite3.OperationalError:
                pass

            row = cursor.execute("SELECT author_id, listing_type, skills, title, description, rates_or_budget, portfolio, status FROM market_posts WHERE id = ?", (post_id,)).fetchone()
            if not row or row[7] != 'PENDING':
                return await interaction.response.send_message("❌ Postingan ini sudah ditinjau sebelumnya.", ephemeral=True)
            author_id, listing_type, skills, title, desc, rates, portfolio, _ = row

            target_id = get_setting("LFD_CHANNEL_ID") if listing_type == "Sell Services" else get_setting("FHD_CHANNEL_ID")
            
            ch = interaction.guild.get_channel(target_id)
            if not ch and target_id:
                try:
                    ch = await interaction.guild.fetch_channel(target_id)
                except Exception as e:
                    print(f"Gagal mengambil channel target {target_id}: {e}")

            published_msg_id = None
            if ch:
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
                p_embed.set_footer(text="RAC Marketplace • Safe & Secure Transactions")
                
                sent_msg = await ch.send(embed=p_embed)
                published_msg_id = sent_msg.id

            cursor.execute("UPDATE market_posts SET status = 'ACCEPTED', message_id = ? WHERE id = ?", (published_msg_id, post_id))
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
            return await interaction.response.send_message("❌ ID postingan tidak ditemukan.", ephemeral=True)

        with sqlite3.connect(DB_NAME) as conn:
            row = conn.cursor().execute("SELECT status FROM market_posts WHERE id = ?", (post_id,)).fetchone()
        if not row or row[0] != 'PENDING':
            return await interaction.response.send_message("❌ Postingan ini sudah ditinjau sebelumnya.", ephemeral=True)
        await interaction.response.send_modal(MarketDenyModal(post_id, interaction.message))

class OpenMarketRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Request", style=discord.ButtonStyle.primary, emoji="📝", custom_id="btn_open_market_request")
    async def open_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="📝 NEW LISTING", description="Hello! I'm here to help you with your journey into the RAC Marketplace.\n\nFirst, what would you like to post?", color=discord.Color.blue())
        embed.set_footer(text="Step 1 of 3 • Send 'abort' or click Cancel to exit")
        try:
            await interaction.user.send(embed=embed, view=MarketTypeView())
            await interaction.response.send_message("📩 Sent you a Direct Message to get started with your marketplace listing!", ephemeral=True)
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
                return await interaction.response.send_message(f"❌ Post `#{self.post_id}` tidak ditemukan di database.", ephemeral=True)
            
            author_id, listing_type, title, status, msg_id = row
            
            if status == "REMOVED":
                return await interaction.response.send_message(f"ℹ️ Post `#{self.post_id}` sudah ditandai terhapus sebelumnya.", ephemeral=True)

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
                delete_error = "Pesan sudah dihapus manual atau tidak ditemukan di channel."
            except discord.Forbidden:
                delete_error = "Bot tidak punya izin 'Manage Messages' di channel tersebut."
            except Exception as e:
                delete_error = f"Error saat menghapus: {e}"
        else:
            delete_error = "ID pesan kosong di database (postingan lama atau gagal terbit)."

        try:
            author = await interaction.client.fetch_user(author_id)
            if author:
                embed = discord.Embed(
                    title="🗑️ Marketplace Post Removed",
                    description=f"Your listing **'{title}'** (ID: `#{self.post_id}`) has been removed by moderators.",
                    color=discord.Color.orange(),
                    timestamp=discord.utils.utcnow()
                )
                embed.add_field(name="Reason for Removal", value=self.reason.value, inline=False)
                await author.send(embed=embed)
        except Exception as e:
            print(f"Gagal mengirim DM ke user {author_id}: {e}")

        if msg_deleted:
            await interaction.response.send_message(
                f"✅ Post `#{self.post_id}` (**{title}**) berhasil **DIHAPUS** dari <#{target_id}> & DM dikirim ke user.\n**Alasan:** {self.reason.value}", 
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"⚠️ Status DB diperbarui & DM terkirim, **tapi pesan fisik di channel TIDAK terhapus**.\n**Penyebab:** {delete_error}\n**Alasan:** {self.reason.value}", 
                ephemeral=True
                
			)

