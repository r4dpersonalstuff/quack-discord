"""
Conversational layer for the bot.

When a member mentions the bot (or replies to it / starts a message with its name),
this module tries to understand the message and answer it. It understands both
English and Indonesian, and answers in the language the member used.

Only things that are safe for regular members are ever mentioned here.
Staff commands are never listed.
"""
import re
import random
import sqlite3
import time
import discord

from market import DB_NAME

BOT_NAME = "RaDS"
COOLDOWN_SECONDS = 3
_last_reply: dict[int, float] = {}


# ==========================================
# TEXT SETTINGS (stored in the same DB as the other settings)
# ==========================================
def get_text_setting(key: str, default: str = "") -> str:
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS config_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        row = conn.execute("SELECT value FROM config_settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_text_setting(key: str, value: str):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS config_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO config_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


SERVER_INFO_KEY = "SERVER_INFO_TEXT"


class ServerInfoModal(discord.ui.Modal, title="Set Server Info"):
    info = discord.ui.TextInput(
        label="Server description",
        style=discord.TextStyle.paragraph,
        placeholder="What is this server about? Shown when members ask the bot or use /serverinfo.",
        required=True,
        max_length=2000,
    )

    def __init__(self, current: str = ""):
        super().__init__()
        if current:
            self.info.default = current

    async def on_submit(self, interaction: discord.Interaction):
        set_text_setting(SERVER_INFO_KEY, self.info.value.strip())
        await interaction.response.send_message(
            "✅ Server info saved! Members will see it when they ask the bot or use `/serverinfo`.",
            ephemeral=True,
        )


# ==========================================
# LANGUAGE HELPERS
# ==========================================
ID_WORDS = {
    "apa", "apaan", "ini", "itu", "siapa", "kamu", "kau", "anda", "lu", "lo", "aku", "saya", "gue", "gw",
    "gimana", "bagaimana", "kenapa", "mengapa", "dong", "deh", "nih", "ya", "yang", "di", "ke", "dari",
    "untuk", "bisa", "bisakah", "tolong", "makasih", "terima", "kasih", "kabar", "halo", "hallo", "hai",
    "berapa", "kapan", "dimana", "mana", "cara", "bantuan", "perintah", "tentang", "ada", "tidak", "nggak",
    "gak", "apakah", "akan", "boleh", "lagi", "sih", "kok", "banget", "dan", "atau", "mau", "pengen",
    "sini", "jelaskan", "jelasin", "cerita", "ceritakan", "lucu", "selamat", "pagi", "siang", "sore",
    "malam", "dadah", "sampai", "jumpa", "keren", "mantap", "hebat", "jual", "jualan", "orang", "anggota",
}


def detect_lang(text: str) -> str:
    words = re.findall(r"[a-z']+", text.lower())
    return "id" if any(w in ID_WORDS for w in words) else "en"


def L(lang: str, en: str, id_: str) -> str:
    return id_ if lang == "id" else en


def pick(lang: str, en: list[str], id_: list[str]) -> str:
    return random.choice(id_ if lang == "id" else en)


# ==========================================
# RESPONSE BUILDERS
# ==========================================
def build_server_embed(guild: discord.Guild, lang: str = "en") -> discord.Embed:
    custom = get_text_setting(SERVER_INFO_KEY)
    if custom:
        desc = custom
    elif guild.description:
        desc = guild.description
    else:
        desc = L(
            lang,
            f"**{guild.name}** is a community server. For more details, check the info channels or ask the staff!",
            f"**{guild.name}** adalah server komunitas. Untuk info lebih lengkap, cek channel info atau tanya staff ya!",
        )

    embed = discord.Embed(title=f"📌 {guild.name}", description=desc, color=discord.Color.blue())
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name=L(lang, "👥 Members", "👥 Member"), value=f"{guild.member_count:,}")
    embed.add_field(name=L(lang, "📅 Created", "📅 Dibuat"), value=discord.utils.format_dt(guild.created_at, "D"))
    if guild.owner_id:
        embed.add_field(name="👑 Owner", value=f"<@{guild.owner_id}>")
    embed.set_footer(text=L(lang, f"Ask me anything — mention @{BOT_NAME}!", f"Tanya apa aja — mention @{BOT_NAME}!"))
    return embed


def build_help_embed(lang: str = "en") -> discord.Embed:
    """Member-facing help. Staff commands are intentionally NOT listed."""
    embed = discord.Embed(
        title=L(lang, "📖 What I can do", "📖 Yang bisa kulakukan"),
        color=discord.Color.blue(),
    )
    embed.add_field(
        name=L(lang, "💬 Talk to me", "💬 Ngobrol denganku"),
        value=L(
            lang,
            "Mention me and ask things like:\n"
            "• *what is this server?*\n• *who are you?*\n• *how do I use the marketplace?*\n"
            "• *how many members are here?*\n• *tell me a joke*\n"
            "Or ask a yes/no question and I'll consult the magic 8-ball 🎱",
            "Mention aku lalu tanya, misalnya:\n"
            "• *server apa ini?*\n• *kamu siapa?*\n• *cara pakai marketplace gimana?*\n"
            "• *berapa member di sini?*\n• *kasih lawakan dong*\n"
            "Atau tanya pertanyaan ya/tidak dan aku jawab pakai 8-ball ajaib 🎱",
        ),
        inline=False,
    )
    embed.add_field(
        name=L(lang, "🎮 Fun & Utility", "🎮 Fun & Utility"),
        value="`/roll` • `/coinflip` • `/rate` • `/serverinfo`",
        inline=False,
    )
    embed.add_field(
        name="🛒 Marketplace",
        value=L(
            lang,
            "Use the marketplace panel to post a listing.\n`/mypost` • `/market_search`",
            "Pakai panel marketplace untuk posting listing.\n`/mypost` • `/market_search`",
        ),
        inline=False,
    )
    return embed


# ==========================================
# INTENT HANDLERS  ->  (content, embed)
# ==========================================
def _name(message: discord.Message) -> str:
    return discord.utils.escape_markdown(message.author.display_name)


def h_server_info(bot, message, lang):
    return L(lang, "Here's a quick intro to this server 👇", "Ini perkenalan singkat server ini 👇"), \
        build_server_embed(message.guild, lang)


def h_who(bot, message, lang):
    g = message.guild.name
    return L(
        lang,
        f"I'm **{BOT_NAME}**, the bot of **{g}**! 🤖 I can chat, roll dice, rate things, run the marketplace and more. "
        "Ask me *\"what can you do?\"* to see everything.",
        f"Aku **{BOT_NAME}**, bot-nya **{g}**! 🤖 Aku bisa ngobrol, lempar dadu, kasih rating, ngurus marketplace, dan lainnya. "
        "Tanya aja *\"kamu bisa apa?\"* buat lihat semuanya.",
    ), None


def h_help(bot, message, lang):
    return None, build_help_embed(lang)


def h_market(bot, message, lang):
    return L(
        lang,
        "🛒 **Marketplace guide**\n"
        "• Find the marketplace panel in the server and click its button — the whole process happens in your DMs.\n"
        "• Pick your listing type and skills, then fill in the details. Staff reviews it before it goes live.\n"
        "• Use `/mypost` to edit, renew or close your listings, and `/market_search` to browse listings.\n"
        "• Changed your mind while posting? Type `abort` in the DM.",
        "🛒 **Panduan marketplace**\n"
        "• Cari panel marketplace di server lalu klik tombolnya — seluruh prosesnya berlangsung lewat DM.\n"
        "• Pilih tipe listing dan skill, lalu isi detailnya. Staff akan me-review sebelum listing tayang.\n"
        "• Pakai `/mypost` untuk edit, perpanjang, atau tutup listing-mu, dan `/market_search` untuk cari listing.\n"
        "• Berubah pikiran saat posting? Ketik `abort` di DM.",
    ), None


def h_roles(bot, message, lang):
    return L(
        lang,
        "🎭 Look for the **role picker** panel in the server — it has a dropdown where you can choose (or remove) roles.",
        "🎭 Cari panel **role picker** di server — ada dropdown untuk memilih (atau melepas) role.",
    ), None


def h_members(bot, message, lang):
    n = message.guild.member_count
    return L(lang, f"We have **{n:,}** members here! 🎉", f"Kita punya **{n:,}** member di sini! 🎉"), None


def h_owner(bot, message, lang):
    oid = message.guild.owner_id
    return L(lang, f"This server is owned by <@{oid}> 👑", f"Server ini dimiliki oleh <@{oid}> 👑"), None


def h_ping(bot, message, lang):
    return f"🏓 Pong! `{round(bot.latency * 1000)}ms`", None


JOKES_EN = [
    "Why do programmers prefer dark mode? Because light attracts bugs 🐛",
    "A QA engineer walks into a bar. Orders 1 beer. Orders 0 beers. Orders 99999 beers. Orders -1 beers. Orders a lizard. 🦎",
    "Why did the game dev go broke? He lost all his cache. 💸",
    "There are 10 types of people: those who understand binary and those who don't.",
]
JOKES_ID = [
    "Kenapa programmer susah move on? Soalnya masih ada bug di hatinya 🐛",
    "Kenapa game dev jarang tidur? Karena lagi ngejar deadline sama bug yang nggak mau kalah 😴",
    "Apa bedanya kamu sama WiFi? WiFi bikin aku terhubung, kamu bikin aku... buffering. 📶",
    "Ada 10 jenis orang: yang ngerti biner, dan yang nggak.",
]


def h_joke(bot, message, lang):
    return pick(lang, JOKES_EN, JOKES_ID), None


def h_how_are_you(bot, message, lang):
    return pick(
        lang,
        ["I'm doing great, thanks for asking! How about you?", "All systems running smoothly 🟢 How are you?"],
        ["Baik banget, makasih udah nanya! Kamu gimana?", "Semua sistem aman 🟢 Kamu sendiri gimana?"],
    ), None


def h_thanks(bot, message, lang):
    return pick(
        lang,
        ["You're welcome! 😄", "Anytime!", "Happy to help!"],
        ["Sama-sama! 😄", "Kapan aja!", "Senang bisa bantu!"],
    ), None


def h_compliment(bot, message, lang):
    return pick(lang, ["Aww, thank you! 🥰", "You're too kind! 😳"], ["Makasih banyak! 🥰", "Aduh, jadi malu 😳"]), None


def h_bye(bot, message, lang):
    return pick(
        lang,
        ["See you around! 👋", "Bye! Take care!"],
        ["Sampai jumpa! 👋", "Dadah, hati-hati ya!"],
    ), None


def h_greeting(bot, message, lang):
    n = _name(message)
    return pick(
        lang,
        [f"Hello **{n}**! Is there anything I can help with?", f"Hi **{n}**! Nice to meet you!", f"Yo! {BOT_NAME} here!"],
        [f"Halo **{n}**! Ada yang bisa kubantu?", f"Hai **{n}**! Senang ketemu kamu!", f"Yo! {BOT_NAME} di sini!"],
    ), None


# ==========================================
# INTENT TABLE
# Each intent has rules; a rule matches when ALL of its regexes match.
# An intent matches when ANY of its rules matches. First matching intent wins,
# so specific intents come before generic ones (greeting is last).
# ==========================================
_INTENT_DEFS = [
    ("server_info", h_server_info, [
        (r"\b(server|community|komunitas|guild)\b",
         r"\b(what|which|about|info|information|describe|explain|introduce|apa|apaan|tentang|jelaskan|jelasin|kenalkan|kenalin)\b",
         r"\b(this|here|ini|sini|nih)\b"),
        (r"\bwhere am i\b",),
        (r"\b(dimana|di mana)\b.*\b(aku|saya|gue|gw)\b|\b(aku|saya|gue|gw)\b.*\b(dimana|di mana)\b",),
    ]),
    ("who", h_who, [
        (r"\b(who|what)\s+(are|r)\s+(you|u)\b(?!\s+(doing|up to))",),
        (r"\bsiapa\b", r"\b(kamu|kau|anda|lu|lo|namamu|nama kamu)\b"),
        (r"\b(kamu|kau)\s+(itu\s+)?apa\b",),
        (r"\bintroduce yourself\b|\bperkenalkan diri\b|\bkenalan\b",),
        (r"\b(your name|namamu|nama kamu|nama lu)\b",),
    ]),
    ("owner", h_owner, [
        (r"\b(who|siapa)\b", r"\b(owner|owns|pemilik|founder|pendiri)\b"),
        (r"\b(who|siapa)\b", r"\b(made|created|bikin|membuat|dibuat)\b", r"\b(server|community|komunitas)\b"),
    ]),
    ("members", h_members, [
        (r"\b(how many|berapa)\b", r"\b(members?|people|users?|orang|anggota)\b"),
    ]),
    ("market", h_market, [
        (r"\b(marketplace|market|listing|listings)\b",),
        (r"\b(sell|hire|hiring|jual|jualan|freelance|commission|komisi|jasa)\b",
         r"\b(how|where|can|cara|gimana|bagaimana|dimana|di mana|bisa|mau|want|wanna|pengen)\b"),
    ]),
    ("roles", h_roles, [
        (r"\b(role|roles|peran)\b",
         r"\b(how|get|where|pick|choose|change|cara|gimana|bagaimana|dapat|dapetin|ambil|pilih|ganti|dimana|di mana)\b"),
    ]),
    ("ping", h_ping, [
        (r"\blatency\b",),
        (r"^\s*ping\s*[!?.]*\s*$",),
        (r"\bping\b", r"\b(your|you|kamu|mu|bot|what|berapa|cek|check)\b"),
    ]),
    ("joke", h_joke, [
        (r"\b(joke|jokes|lawak|lawakan)\b",),
        (r"\b(tell|say|ceritakan|kasih|bikin|buat|cerita)\b", r"\b(funny|lucu|bercanda)\b"),
    ]),
    ("help", h_help, [
        (r"\bwhat\b", r"\b(can|could)\b", r"\b(you|u)\b", r"\b(do|help)\b"),
        (r"\b(help|commands?|features?|menu|bantuan|perintah|fitur|bantu)\b",),
        (r"\b(bisa|dapat)\b", r"\b(apa|ngapain)\b"),
    ]),
    ("how_are_you", h_how_are_you, [
        (r"\bhow\b", r"\b(are|r)\s+(you|u)\b"),
        (r"\b(apa kabar|gimana kabar|bagaimana kabar|kabarmu|kabar kamu|lagi apa|sedang apa)\b",),
    ]),
    ("thanks", h_thanks, [
        (r"\b(thanks|thank you|thx|tysm|makasih|terima kasih|terimakasih|trims|tengkyu)\b|\bty\b",),
    ]),
    ("compliment", h_compliment, [
        (r"\b(good|nice|great|best|awesome)\b", r"\b(bot|job)\b"),
        (r"\b(keren|mantap|mantul|hebat|pinter|pintar)\b",),
    ]),
    ("bye", h_bye, [
        (r"\b(bye|goodbye|see you|see ya|cya|gtg|sampai jumpa|dadah|good night|goodnight|selamat tidur)\b",),
    ]),
    ("greeting", h_greeting, [
        (r"\b(hello|hi|hey|hai|halo|hallo|yo|sup|oi|woi)\b",),
        (r"\bselamat\s+(pagi|siang|sore|malam)\b|\bgood\s+(morning|evening|afternoon)\b|assalamu'?alaikum",),
    ]),
]

INTENTS = [
    (name, handler, [tuple(re.compile(rx, re.IGNORECASE) for rx in rule) for rule in rules])
    for name, handler, rules in _INTENT_DEFS
]


def match_intent(text: str):
    """Return (name, handler) for the first matching intent, or None."""
    t = text.replace("’", "'")
    for name, handler, rules in INTENTS:
        if any(all(rx.search(t) for rx in rule) for rule in rules):
            return name, handler
    return None


# ==========================================
# FALLBACK: magic 8-ball for yes/no questions
# ==========================================
YES_NO_START = re.compile(
    r"^(is|are|am|was|were|will|would|can|could|should|do|does|did|have|has|may|shall|"
    r"apakah|bisakah|bisa|boleh|akankah|haruskah|perlu|ada|jadi|emang|emangnya)\b", re.IGNORECASE)
WH_START = re.compile(
    r"^(what|who|whom|whose|where|when|why|how|which|apa|siapa|dimana|di mana|kapan|kenapa|mengapa|"
    r"bagaimana|gimana|berapa|mana)\b", re.IGNORECASE)

EIGHT_BALL_EN = [
    "It is certain.", "Without a doubt.", "Yes, definitely.", "Ask again later.", "Cannot predict now.",
    "Don't count on it.", "My reply is no.", "My reply is yes.", "Very doubtful.",
]
EIGHT_BALL_ID = [
    "Pasti!", "Tanpa diragukan lagi.", "Ya, tentu saja.", "Tanya lagi nanti ya.", "Belum bisa diprediksi.",
    "Jangan terlalu berharap.", "Jawabanku: tidak.", "Jawabanku: ya.", "Sangat diragukan.",
]


def looks_like_yes_no(text: str) -> bool:
    t = text.strip()
    if YES_NO_START.match(t):
        return True
    return t.endswith("?") and not WH_START.match(t)


# ==========================================
# ENTRY POINT (called from on_message)
# ==========================================
def _name_call_regex() -> re.Pattern:
    return re.compile(
        rf"^\s*(?:(?:hey|hi|hello|halo|hai|yo|oi|woi|ok|okay)[\s,]+)?{re.escape(BOT_NAME)}\b[\s,:!.\-]*",
        re.IGNORECASE,
    )


NAME_CALL = _name_call_regex()


async def handle_chat(bot, message: discord.Message) -> bool:
    """Try to answer a message addressed to the bot. Returns True if the message was handled."""
    if message.guild is None:
        return False

    uid = bot.user.id
    raw = message.content

    explicit_mention = f"<@{uid}>" in raw or f"<@!{uid}>" in raw
    text = re.sub(rf"<@!?{uid}>", " ", raw).strip()

    called_by_name = False
    m = NAME_CALL.match(text)
    if m:
        called_by_name = True
        text = text[m.end():].strip()

    ref = message.reference.resolved if message.reference else None
    reply_to_bot = isinstance(ref, discord.Message) and ref.author.id == uid

    if not (explicit_mention or called_by_name or reply_to_bot):
        return False

    # Simple per-user cooldown so nobody can make the bot spam
    now = time.monotonic()
    if now - _last_reply.get(message.author.id, 0) < COOLDOWN_SECONDS:
        return True
    _last_reply[message.author.id] = now

    lang = detect_lang(text)

    if not text:
        if explicit_mention or called_by_name:
            await _send(message, L(lang, "Ask me anything! (Try *\"what can you do?\"*)",
                                   "Tanya apa aja! (Coba *\"kamu bisa apa?\"*)"), None)
            return True
        return False

    found = match_intent(text)
    if found:
        content, embed = found[1](bot, message, lang)
        await _send(message, content, embed)
        return True

    # Nothing matched: only an explicit @mention gets the fallback answer
    if not explicit_mention:
        return False

    if looks_like_yes_no(text):
        ans = random.choice(EIGHT_BALL_ID if lang == "id" else EIGHT_BALL_EN)
        await _send(message, f"🎱 {ans}", None)
    else:
        await _send(message, L(
            lang,
            "Hmm, I'm not sure how to answer that 🤔 Try asking *\"what can you do?\"* — or ask a yes/no question for the magic 8-ball!",
            "Hmm, aku belum paham maksudnya 🤔 Coba tanya *\"kamu bisa apa?\"* — atau kasih pertanyaan ya/tidak buat 8-ball ajaib!",
        ), None)
    return True


async def _send(message: discord.Message, content, embed):
    try:
        await message.reply(content=content, embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as e:
        print(f"Failed to reply to message {message.id}: {e}")
