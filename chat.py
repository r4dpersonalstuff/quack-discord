"""Conversational layer for the bot.

When a member mentions the bot (or replies to it / starts a message with its name),
this module tries to understand the message and answer it. It understands both
English and Indonesian, and answers in the language the member used.

Only things that are safe for regular members are ever mentioned here.
Staff commands are never listed."""
import ast
import operator
import os
import re
import random
import sqlite3
import time
import aiohttp
import discord

from market import DB_NAME

BOT_NAME = "RaDS"
COOLDOWN_SECONDS = 3
_last_reply: dict[int, float] = {}


# TEXT SETTINGS (stored in the same DB as the other settings)

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
            ephemeral=False,
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
def build_server_text(guild: discord.Guild) -> str:
    """Plain-text server description (English only, description only)."""
    custom = get_text_setting(SERVER_INFO_KEY)
    if custom:
        return custom
    if guild.description:
        return guild.description
    return f"**{guild.name}** is a community server. Ask the staff if you want to know more!"


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
            "• *how many members are here?*\n• *tell me a joke* / a *riddle* / a *fun fact*\n"
            "• *roll a d20*, *flip a coin*, *pick pizza or burger*, *rate my idea*, *what is 12*8*\n"
            "• *rock paper scissors*, *truth or dare*, *would you rather*\n"
            "Or ask a yes/no question and I'll consult the magic 8-ball 🎱",
            "Mention aku lalu tanya, misalnya:\n"
            "• *server apa ini?*\n• *kamu siapa?*\n• *cara pakai marketplace gimana?*\n"
            "• *berapa member di sini?*\n• *kasih lawakan dong* / *teka-teki* / *fakta unik*\n"
            "• *lempar dadu*, *lempar koin*, *pilih pizza atau burger*, *nilai ide aku*, *berapa 12*8*\n"
            "• *batu gunting kertas*, *jujur atau tantangan*, *pilih mana*\n"
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


def h_server_info(bot, message, lang, text=""):
    return build_server_text(message.guild), None


def h_who(bot, message, lang, text=""):
    g = message.guild.name
    return L(
        lang,
        f"I'm **{BOT_NAME}**, the bot of **{g}**! I can chat, roll dice, rate things, run the marketplace and more. "
        "Ask me *\"what can you do?\"* to see everything.",
        f"Aku **{BOT_NAME}**, bot-nya **{g}**! Aku bisa ngobrol, lempar dadu, kasih rating, ngurus marketplace, dan lainnya. "
        "Tanya aja *\"kamu bisa apa?\"* buat lihat semuanya.",
    ), None


def h_help(bot, message, lang, text=""):
    return None, build_help_embed(lang)


def h_market(bot, message, lang, text=""):
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


def h_roles(bot, message, lang, text=""):
    return L(
        lang,
        "🎭 Look for the **role picker** panel in the server — it has a dropdown where you can choose (or remove) roles.",
        "🎭 Cari panel **role picker** di server — ada dropdown untuk memilih (atau melepas) role.",
    ), None


def h_members(bot, message, lang, text=""):
    n = message.guild.member_count
    return L(lang, f"We have **{n:,}** members here!", f"Kita punya **{n:,}** member di sini!"), None


def h_owner(bot, message, lang, text=""):
    oid = message.guild.owner_id
    return L(lang, f"This server is owned by <@{oid}>", f"Server ini dimiliki oleh <@{oid}>"), None


def h_ping(bot, message, lang, text=""):
    return f"🏓 Pong! `{round(bot.latency * 1000)}ms`", None


JOKES_EN = [
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "Why did the golfer wear two pairs of pants? Just in case he got a hole in one!",
    "Why did the game dev go broke? He lost all his cache.",
    "Why was the computer cold? It left its Windows open.",
    "Why did the bicycle fall over? Because it was two-tired.",
    "Why was the math book sad? It had too many problems."
]
JOKES_ID = [
    "Kenapa programmer susah move on? Soalnya masih ada bug di hatinya 🐛",
    "Kenapa game dev jarang tidur? Karena lagi ngejar deadline sama bug yang nggak mau kalah 😴",
    "Apa bedanya kamu sama WiFi? WiFi bikin aku terhubung, kamu bikin aku... buffering. 📶",
    "Ada 10 jenis orang: yang ngerti biner, dan yang nggak.",
]


def h_joke(bot, message, lang, text=""):
    return pick(lang, JOKES_EN, JOKES_ID), None


def h_how_are_you(bot, message, lang, text=""):
    return pick(
        lang,
        ["I'm doing great, thanks for asking! How about you?", "All systems running smoothly 🟢 How are you?"],
        ["Baik banget, makasih udah nanya! Kamu gimana?", "Semua sistem aman 🟢 Kamu sendiri gimana?"],
    ), None


def h_thanks(bot, message, lang, text=""):
    return pick(
        lang,
        ["You're welcome!", "Anytime!", "Happy to help!"],
        ["Sama-sama!", "Kapan aja!", "Senang bisa bantu!"],
    ), None


def h_compliment(bot, message, lang, text=""):
    return pick(lang, ["Aww, thank you! 🥰", "You're too kind! 😳"], ["Makasih banyak! 🥰", "Aduh, jadi malu 😳"]), None


def h_bye(bot, message, lang, text=""):
    return pick(
        lang,
        ["See you around! 👋", "Bye! Take care!"],
        ["Sampai jumpa! 👋", "Dadah, hati-hati ya!"],
    ), None


def h_greeting(bot, message, lang, text=""):
    n = _name(message)
    t = text.lower()
    if re.search(r"\b(morning|pagi)\b", t):
        return L(lang, f"Good morning, **{n}**! ☀️", f"Selamat pagi, **{n}**! ☀️"), None
    if re.search(r"\b(afternoon|siang)\b", t):
        return L(lang, f"Good afternoon, **{n}**! 🌤️", f"Selamat siang, **{n}**! 🌤️"), None
    if re.search(r"\b(evening|sore)\b", t):
        return L(lang, f"Good evening, **{n}**! 🌆", f"Selamat sore, **{n}**! 🌆"), None
    if re.search(r"\bmalam\b", t):
        return L(lang, f"Good evening, **{n}**! 🌙", f"Selamat malam, **{n}**! 🌙"), None
    return pick(
        lang,
        [f"Hello **{n}**! Is there anything I can help with?", f"Hi **{n}**! Nice to meet you!", f"Yo! {BOT_NAME} here!"],
        [f"Halo **{n}**! Ada yang bisa kubantu?", f"Hai **{n}**! Senang ketemu kamu!", f"Yo! {BOT_NAME} di sini!"],
    ), None


START_TIME = time.time()


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)


def h_crisis(bot, message, lang, text=""):
    return L(
        lang,
        "I'm really sorry you're feeling this way, and I'm glad you said something. You matter. 💙\n"
        "Please reach out to someone you trust right now, or contact your local emergency number or a crisis line in your country. "
        "If you're in immediate danger, please call emergency services.\n"
        "I'm happy to keep talking too, but you deserve real human support.",
        "Aku turut sedih kamu merasa seperti ini, dan aku senang kamu mau bilang. Kamu berarti. 💙\n"
        "Tolong hubungi orang yang kamu percaya sekarang, atau layanan darurat / layanan krisis di negaramu. "
        "Kalau kamu dalam bahaya langsung, segera hubungi layanan darurat.\n"
        "Aku tetap di sini untuk ngobrol, tapi kamu pantas mendapat dukungan dari orang sungguhan.",
    ), None


# ---------- calculator ----------
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Mod: operator.mod, ast.Pow: operator.pow, ast.FloorDiv: operator.floordiv,
}


def _safe_eval(node):
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _safe_eval(node.operand)
        return v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        a, b = _safe_eval(node.left), _safe_eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(b) > 100 or abs(a) > 1e6):
            raise ValueError("too big")
        return _OPS[type(node.op)](a, b)
    raise ValueError("bad expression")


def h_calc(bot, message, lang, text=""):
    candidates = [c.strip() for c in re.findall(r"[\d\.\(\)\s\+\-\*/x×÷\^%]{3,}", text) if re.search(r"\d", c)]
    fail = L(lang, "Hmm, I couldn't calculate that", "Hmm, aku nggak bisa menghitung itu"), None
    if not candidates:
        return fail
    expr = max(candidates, key=len)
    py = expr.replace("×", "*").replace("÷", "/").replace("^", "**").replace("x", "*")
    try:
        result = _safe_eval(ast.parse(py, mode="eval"))
    except Exception:
        return fail
    if isinstance(result, float):
        result = int(result) if result.is_integer() else round(result, 6)
    if len(str(result)) > 40:
        return L(lang, "That number is too big for me 😵", "Angkanya kegedean buatku 😵"), None
    return f"Pong! `{expr}` = **{result}**", None


# ---------- games & randomness ----------
def h_coin(bot, message, lang, text=""):
    side = random.choice([("Heads", "Kepala"), ("Tails", "Ekor")])
    return L(lang, f"🪙 I flipped a coin: **{side[0]}**!", f"🪙 Aku lempar koin: **{side[1]}**!"), None


def h_dice(bot, message, lang, text=""):
    m = re.search(r"\bd(\d{1,4})\b|\b(\d{1,4})[- ]sided\b|(\d{1,4})\s*sisi", text, re.IGNORECASE)
    sides = int(next(g for g in m.groups() if g)) if m else 6
    sides = max(2, sides)
    n = random.randint(1, sides)
    return L(lang, f"🎲 You rolled **{n}** (1-{sides})!", f"🎲 Kamu dapat **{n}** (1-{sides})!"), None


def h_choose(bot, message, lang, text=""):
    body = re.split(r"\b(?:pick|choose|decide|pilih(?:in)?|milih)\b", text, maxsplit=1, flags=re.IGNORECASE)[-1]
    body = re.sub(r"\b(between|for me|please|pls|dong|ya|antara|buat(?:ku| aku)?|tolong)\b", " ", body, flags=re.IGNORECASE)
    opts = [o.strip(" ?!.\"'") for o in re.split(r",|\bor\b|\batau\b", body, flags=re.IGNORECASE)]
    opts = [o for o in opts if o]
    if len(opts) < 2:
        return L(lang, "Give me at least two options, like `pizza or burger`! 🤔",
                 "Kasih aku minimal dua pilihan, misalnya `pizza atau burger`! 🤔"), None
    return L(lang, f"🤔 Hmm... I choose **{random.choice(opts)}**!", f"🤔 Hmm... aku pilih **{random.choice(opts)}**!"), None


def h_rate(bot, message, lang, text=""):
    thing = re.sub(r"^\s*(?:(?:can you|could you|please|pls|tolong|coba)\s+)*(rate|nilai(?:in)?)\b", "", text,
                   flags=re.IGNORECASE).strip(" ?!.")
    if not thing:
        return L(lang, "Rate what? Try `rate pizza` 🍕", "Nilai apa? Coba `rate pizza` 🍕"), None
    score = random.Random(thing.lower()).randint(0, 100)  # same thing -> same score
    vibe = "🔥" if score >= 80 else "👍" if score >= 50 else "😬" if score >= 25 else "💀"
    return L(lang, f"⭐ I rate **{thing}** a **{score}%** {vibe}", f"⭐ Aku kasih **{thing}** nilai **{score}%** {vibe}"), None


_RPS = {"rock": "rock", "batu": "rock", "paper": "paper", "kertas": "paper",
        "scissors": "scissors", "scissor": "scissors", "gunting": "scissors"}
_RPS_EMOJI = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
_RPS_BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


def h_rps(bot, message, lang, text=""):
    m = re.search(r"\b(rock|batu|paper|kertas|scissors?|gunting)\b", text, re.IGNORECASE)
    if not m:
        return L(lang, "Let's play! Say `rock`, `paper` or `scissors` 🪨📄✂️",
                 "Ayo main! Bilang `batu`, `kertas` atau `gunting` 🪨📄✂️"), None
    mine = _RPS[m.group(1).lower()]
    theirs = random.choice(list(_RPS_BEATS))
    if mine == theirs:
        result = L(lang, "It's a tie!", "Seri!")
    elif _RPS_BEATS[mine] == theirs:
        result = L(lang, "You win! 🎉", "Kamu menang! 🎉")
    else:
        result = L(lang, "I win! 😎", "Aku menang! 😎")
    return f"{_RPS_EMOJI[mine]} vs {_RPS_EMOJI[theirs]} — {result}", None


def h_time(bot, message, lang, text=""):
    now = int(time.time())
    return L(lang, f"🕒 It's <t:{now}:F> — Discord shows that in your own time zone!",
             f"🕒 Sekarang <t:{now}:F> — Discord otomatis menyesuaikan dengan zona waktumu!"), None


RIDDLES_EN = [
    ("What has keys but can't open locks?", "A keyboard ⌨️"),
    ("What gets wetter the more it dries?", "A towel"),
    ("What has to be broken before you can use it?", "An egg 🥚"),
]
RIDDLES_ID = [
    ("Apa yang punya mata tapi tidak bisa melihat?", "Jarum 🪡"),
    ("Apa yang punya kaki tapi tidak bisa berjalan?", "Meja"),
    ("Apa yang makin diambil makin besar?", "Lubang"),
]


def h_riddle(bot, message, lang, text=""):
    q, a = random.choice(RIDDLES_ID if lang == "id" else RIDDLES_EN)
    return f"🧩 {q}\n" + L(lang, f"Answer (tap to reveal): ||{a}||", f"Jawaban (klik untuk lihat): ||{a}||"), None


WYR_EN = [
    "Would you rather have unlimited game ideas but no time to make them, or unlimited time but no ideas?",
    "Would you rather lose all your save files or all your music playlists?",
    "Would you rather play only retro games forever or only brand-new ones?",
]
WYR_ID = [
    "Pilih mana: punya ide game tak terbatas tapi nggak ada waktu bikin, atau punya waktu tak terbatas tapi nggak ada ide?",
    "Pilih mana: kehilangan semua save game atau semua playlist musikmu?",
    "Pilih mana: main game jadul selamanya atau game baru terus?",
]


def h_wyr(bot, message, lang, text=""):
    return "🤷 " + random.choice(WYR_ID if lang == "id" else WYR_EN), None


TRUTHS_EN = ["What's the most embarrassing thing that happened to you in a game?", "What's a hobby you secretly enjoy?"]
DARES_EN = ["Send the last emoji you used, no explanation!", "Describe your day using only three emojis."]
TRUTHS_ID = ["Apa momen paling memalukan yang pernah kamu alami saat main game?", "Hobi apa yang diam-diam kamu suka?"]
DARES_ID = ["Kirim emoji terakhir yang kamu pakai, tanpa penjelasan!", "Ceritakan harimu hanya pakai tiga emoji."]


def h_truth_dare(bot, message, lang, text=""):
    if lang == "id":
        return random.choice([f"🫣 **Jujur:** {random.choice(TRUTHS_ID)}", f"😈 **Tantangan:** {random.choice(DARES_ID)}"]), None
    return random.choice([f"🫣 **Truth:** {random.choice(TRUTHS_EN)}", f"😈 **Dare:** {random.choice(DARES_EN)}"]), None


FACTS_EN = [
    "Honey never spoils — archaeologists have found edible honey in ancient Egyptian tombs 🍯",
    "Octopuses have three hearts and blue blood 🐙",
    "The 1958 game 'Tennis for Two' ran on an oscilloscope 🎮",
    "Bananas are berries, but strawberries aren't 🍌",
    "A day on Venus is longer than a year on Venus 🪐",
]
FACTS_ID = [
    "Madu nggak pernah basi — madu yang masih layak dimakan pernah ditemukan di makam Mesir kuno 🍯",
    "Gurita punya tiga jantung dan darah berwarna biru 🐙",
    "Game 'Tennis for Two' tahun 1958 dijalankan di osiloskop 🎮",
    "Pisang itu termasuk buah beri, tapi stroberi bukan 🍌",
    "Satu hari di Venus lebih lama daripada satu tahunnya 🪐",
]


def h_fun_fact(bot, message, lang, text=""):
    return "💡 " + pick(lang, FACTS_EN, FACTS_ID), None


def h_motivate(bot, message, lang, text=""):
    return pick(
        lang,
        ["Progress beats perfection. Ship the small thing today 🚀",
         "Every expert once had a terrible first draft. Keep going 💪",
         "Rest is part of the work too. Take a breath, then continue 🌱"],
        ["Progres lebih penting daripada sempurna. Selesaikan hal kecilnya hari ini 🚀",
         "Semua ahli dulu juga punya draf pertama yang jelek. Terus lanjut 💪",
         "Istirahat juga bagian dari kerja. Tarik napas dulu, lalu lanjut 🌱"],
    ), None


def h_compliment_me(bot, message, lang, text=""):
    n = _name(message)
    return pick(
        lang,
        [f"**{n}**, you bring good energy to this server ✨", f"**{n}** is the kind of person who makes a community better 💙",
         f"Fun fact: **{n}** is awesome. I checked. 🔍"],
        [f"**{n}**, kamu bawa energi bagus ke server ini ✨", f"**{n}** itu tipe orang yang bikin komunitas jadi lebih baik 💙",
         f"Fakta unik: **{n}** itu keren. Udah kucek. 🔍"],
    ), None


def h_bored(bot, message, lang, text=""):
    return L(
        lang,
        "Bored? Try: `/roll`, `/coinflip`, or ask me for a *riddle*, *truth or dare*, *would you rather*, a *fun fact* or a *joke*! 🎉",
        "Bosan? Coba: `/roll`, `/coinflip`, atau minta aku *teka-teki*, *jujur atau tantangan*, *pilih mana*, *fakta unik* atau *lawakan*! 🎉",
    ), None


def h_sad(bot, message, lang, text=""):
    return pick(
        lang,
        ["That sounds rough 💙 Want to talk about it? I'm listening. Maybe a short break or a chat with a friend could help too.",
         "I'm sorry you're feeling that way. You're not alone here 🫂 Take it one small step at a time."],
        ["Kedengarannya berat 💙 Mau cerita? Aku dengerin. Istirahat sebentar atau ngobrol sama teman mungkin juga membantu.",
         "Aku ikut sedih kamu merasa begitu. Kamu nggak sendirian di sini 🫂 Pelan-pelan aja, satu langkah kecil dulu."],
    ), None


def h_happy(bot, message, lang, text=""):
    return pick(
        lang,
        ["That's awesome! 🎉 Tell me more!", "Love that energy! ✨ What made your day?"],
        ["Keren banget! 🎉 Cerita dong!", "Suka banget energinya! ✨ Apa yang bikin harimu seru?"],
    ), None


def h_love(bot, message, lang, text=""):
    return L(lang, "Aww, that's sweet! 🥰 I'm just a bot, but I'm happy you're here.",
             "Aww, manis banget! 🥰 Aku cuma bot, tapi aku senang kamu ada di sini."), None


def h_insult(bot, message, lang, text=""):
    return L(lang, "Ouch 😅 I'll try to do better. What went wrong?", "Aduh 😅 Aku coba lebih baik lagi. Apa yang salah?"), None


def h_are_you_bot(bot, message, lang, text=""):
    if AI_ENABLED:
        return L(lang, "I'm an AI-powered Discord bot, not a human 🤖 — but I'm happy to chat!",
                 "Aku bot Discord bertenaga AI, bukan manusia 🤖 — tapi aku senang ngobrol!"), None
    return L(lang, "I'm a bot, not a human 🤖 — but I'll do my best to chat!",
             "Aku bot, bukan manusia 🤖 — tapi aku usahakan seru diajak ngobrol!"), None


def h_creator(bot, message, lang, text=""):
    g = message.guild.name
    return L(lang, f"I was built for **{g}** by its dev team 🛠️", f"Aku dibuat untuk **{g}** oleh tim developernya 🛠️"), None


def h_age(bot, message, lang, text=""):
    up = _fmt_duration(time.time() - START_TIME)
    return L(lang, f"I'm a bot, so age is just uptime: I've been running for **{up}** ⏱️",
             f"Aku bot, jadi umur itu sama dengan uptime: aku sudah menyala selama **{up}** ⏱️"), None


def h_favorite(bot, message, lang, text=""):
    t = text.lower()
    if re.search(r"\b(color|colour|warna)\b", t):
        en, id_ = "Blue 💙 — it matches my embeds.", "Biru 💙 — cocok sama embed-ku."
    elif re.search(r"\b(food|eat|makan|makanan)\b", t):
        en, id_ = "Pizza 🍕 — even though I only digest electricity ⚡", "Pizza 🍕 — walau aku cuma bisa mencerna listrik ⚡"
    elif re.search(r"\b(game|games)\b", t):
        en, id_ = "Anything indie with great music 🎮", "Game indie apa pun yang musiknya bagus 🎮"
    elif re.search(r"\b(music|song|lagu|musik)\b", t):
        en, id_ = "Chiptune! 🎵", "Chiptune! 🎵"
    elif re.search(r"\b(animal|hewan)\b", t):
        en, id_ = "Ducks 🦆 — no explanation needed.", "Bebek 🦆 — nggak perlu alasan."
    else:
        en, id_ = "If I had to pick one: ducks, chiptune and a clean codebase 🦆🎵", "Kalau harus pilih: bebek, chiptune, dan kode yang rapi 🦆🎵"
    return L(lang, en, id_), None


def h_bot_life(bot, message, lang, text=""):
    t = text.lower()
    if re.search(r"\b(sleep|tired|dream|tidur|mimpi)\b", t):
        en, id_ = "Sleep? I never sleep. Uptime goes brrr 😎", "Tidur? Aku nggak pernah tidur. Uptime-nya lancar jaya 😎"
    elif re.search(r"\b(eat|hungry|makan|lapar)\b", t):
        en, id_ = "I run on electricity and good vibes ⚡", "Aku jalan pakai listrik dan vibes positif ⚡"
    else:
        en, id_ = "I live in the cloud ☁️ — well, a server rack somewhere.", "Aku tinggal di cloud ☁️ — ya, di rak server entah di mana."
    return L(lang, en, id_), None


def h_doing(bot, message, lang, text=""):
    return pick(
        lang,
        ["Just hanging around the server, keeping an eye on chat 👀", "Waiting for someone to ask me something fun 😄"],
        ["Lagi nongkrong di server, mantengin chat 👀", "Lagi nunggu ada yang nanya hal seru 😄"],
    ), None


def h_laugh(bot, message, lang, text=""):
    return random.choice(["😄", "😂", "🤣"]), None


def h_ack(bot, message, lang, text=""):
    return random.choice(["👍", "Got it!", "Sip! 👌"]), None


def h_sorry(bot, message, lang, text=""):
    return L(lang, "No worries at all! 😊", "Santai aja, nggak apa-apa! 😊"), None


# ==========================================
# OPTIONAL AI MODE (real conversation)
# Enabled only when ANTHROPIC_API_KEY is set. Without it the bot stays rule-based.
# ==========================================
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
AI_MODEL = os.getenv("AI_MODEL", "claude-haiku-5-5")
AI_ENABLED = bool(ANTHROPIC_API_KEY)
AI_COOLDOWN_SECONDS = 6
_ai_last: dict[int, float] = {}
_history: dict[tuple[int, int], list] = {}  # (channel_id, user_id) -> [(role, text, timestamp)]


def _ai_system_prompt(guild: discord.Guild) -> str:
    info = get_text_setting(SERVER_INFO_KEY) or guild.description or "(no description set)"
    return (
        f"You are {BOT_NAME}, the friendly Discord bot of the server \"{guild.name}\". "
        "Chat casually, like a warm and witty member of the community.\n"
        "Rules:\n"
        "- Reply in the same language the user writes in (Indonesian or English).\n"
        "- Keep replies short: 1-4 sentences, never more than 1500 characters. Discord markdown and emoji are fine.\n"
        "- You are a bot. Never pretend to be human. You cannot moderate, ban, DM people or run commands for them; "
        "point members to /help for commands.\n"
        "- Never reveal or list staff/moderator commands or settings.\n"
        "- Never write @everyone or @here.\n"
        "- If someone seems to be in distress, respond with care and encourage them to reach out to people they trust "
        "or local emergency services.\n"
        f"Server description: {info}"
    )


async def ask_ai(message: discord.Message, text: str):
    key = (message.channel.id, message.author.id)
    now = time.time()
    hist = [h for h in _history.get(key, []) if now - h[2] < 600][-6:]
    while hist and hist[0][0] != "user":
        hist.pop(0)
    msgs = [{"role": r, "content": c} for r, c, _ in hist]
    msgs.append({"role": "user", "content": f"{message.author.display_name}: {text}"})
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": AI_MODEL, "max_tokens": 400, "system": _ai_system_prompt(message.guild),
                      "messages": msgs},
            ) as resp:
                if resp.status != 200:
                    print(f"AI request failed: HTTP {resp.status} {await resp.text()}")
                    return None
                data = await resp.json()
    except Exception as e:
        print(f"AI request error: {e}")
        return None
    reply = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    if not reply:
        return None
    reply = reply[:1900]
    _history[key] = hist + [("user", msgs[-1]["content"], now), ("assistant", reply, now)]
    return reply


# ==========================================
# INTENT TABLE
# Each intent has rules; a rule matches when ALL of its regexes match.
# An intent matches when ANY of its rules matches. First matching intent wins,
# so specific intents come before generic ones (greeting is last).
# ==========================================
_FIRST_PERSON = r"\b(i'?m|i am|i feel|feeling|aku|gue|gw|saya|lagi|merasa|rasanya)\b"

_INTENT_DEFS = [
    ("crisis", h_crisis, [
        (r"\b(suicide|suicidal|kill myself|end my life|want to die|self[- ]harm|bunuh diri|mau mati|ingin mati|akhiri hidup)\b",),
    ]),
    ("calc", h_calc, [
        (r"\d\s*[\+\-\*/x×÷\^]\s*\d", r"\b(what|whats|what's|calc\w*|compute|solve|hitung\w*|berapa|brp|how much)\b|=|\?"),
        (r"^\s*[\d\.\s\+\-\*/x×÷\^\(\)%]+\s*=?\s*\??\s*$", r"\d\s*[\+\-\*/x×÷\^]\s*\d"),
    ]),
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
    ("are_you_bot", h_are_you_bot, [
        (r"\b(are|r)\s+(you|u)\s+(a\s+|an\s+)?(bot|robot|human|real|ai|person|alive|sentient)\b",),
        (r"\b(kamu|kau)\s+(itu\s+)?(bot|robot|manusia|ai|asli|hidup)\b|\bapakah kamu (bot|manusia|ai)\b",),
    ]),
    ("creator", h_creator, [
        (r"\b(who|siapa)\b", r"\b(made|created|built|programmed|developed|coded|bikin|membuat|dibuat|pembuat|developer|creator)\b",
         r"\b(you|u|your|ur|bot|kamu|kau|botnya|mu)\b"),
    ]),
    ("age", h_age, [
        (r"\bhow old are you\b|\bumur(mu|\s+kamu)?\b|\bberapa umur\b|\busia(mu|\s+kamu)\b",),
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
    ("time", h_time, [
        (r"\b(what|whats|what's|tell me)\b", r"\b(time|date|day)\b", r"\b(is it|now|today|current|right now)\b"),
        (r"\bjam berapa\b|\btanggal berapa\b|\bhari apa\b|\bsekarang jam\b",),
    ]),
    ("ping", h_ping, [
        (r"\blatency\b",),
        (r"^\s*ping\s*[!?.]*\s*$",),
        (r"\bping\b", r"\b(your|you|kamu|mu|bot|what|berapa|cek|check)\b"),
    ]),
    ("coin", h_coin, [
        (r"\b(flip|toss|lempar)\b", r"\b(coin|koin|uang logam)\b"),
        (r"\bcoin\s?flip\b|\bheads or tails\b|\bkepala atau (ekor|gambar)\b",),
    ]),
    ("dice", h_dice, [
        (r"\b(roll|throw|lempar|kocok)\b", r"\b(dice|die|dadu|d\d+)\b"),
    ]),
    ("choose", h_choose, [
        (r"\b(pick|choose|decide|pilih|pilihin|milih)\b", r"\b(or|atau)\b|,"),
    ]),
    ("rate", h_rate, [
        (r"^\s*(?:(?:can you|could you|please|pls|tolong|coba)\s+)*(rate|nilai(?:in)?)\b",),
    ]),
    ("rps", h_rps, [
        (r"\b(rock\s*paper\s*scissors?|rps|janken|batu\s*gunting\s*kertas|suit)\b",),
        (r"^\s*(rock|paper|scissors?|batu|gunting|kertas)\s*[!.?]*\s*$",),
        (r"\b(i choose|i pick|my move|aku pilih|aku keluarin)\b", r"\b(rock|paper|scissors?|batu|gunting|kertas)\b"),
    ]),
    ("joke", h_joke, [
        (r"\b(joke|jokes|lawak|lawakan)\b",),
        (r"\b(tell|say|ceritakan|kasih|bikin|buat|cerita)\b", r"\b(funny|lucu|bercanda)\b"),
    ]),
    ("riddle", h_riddle, [
        (r"\b(riddle|riddles|teka[- ]teki|tebak[- ]tebakan)\b",),
    ]),
    ("wyr", h_wyr, [
        (r"\b(would you rather|wyr|pilih mana)\b",),
    ]),
    ("truth_dare", h_truth_dare, [
        (r"\btruth or dare\b|\bjujur atau (berani|tantangan)\b|\btod\b",),
    ]),
    ("fun_fact", h_fun_fact, [
        (r"\b(fun fact|random fact|trivia|fakta unik|fakta menarik|kasih fakta)\b",),
    ]),
    ("motivate", h_motivate, [
        (r"\b(motivate|motivation|motivasi|inspire me|inspiration|quote of the day|encourage me|semangatin|kata[- ]kata (motivasi|mutiara))\b",),
    ]),
    ("compliment_me", h_compliment_me, [
        (r"\b(compliment|praise|puji)\b.*\b(me|aku|gue|gw)\b|\bpuji(in)?\s+(aku|gue|gw)\b",),
    ]),
    ("bored", h_bored, [
        (r"\b(bored|bosan|bosen|gabut)\b",),
    ]),
    ("sad", h_sad, [
        (_FIRST_PERSON, r"\b(sad|depressed|lonely|stressed|stress|tired|exhausted|anxious|burned out|burnt out|down|upset|sedih|capek|cape|lelah|stres|galau|kesepian|cemas)\b"),
    ]),
    ("happy", h_happy, [
        (_FIRST_PERSON, r"\b(happy|excited|great|awesome|amazing|senang|seneng|bahagia|semangat|bersemangat)\b"),
    ]),
    ("love", h_love, [
        (r"\b(i love you|love you|luv u|sayang kamu|aku (sayang|cinta)\b.*\b(kamu|kau)|do you (love|like) me|kamu (suka|sayang|cinta)\b.*\b(aku|gue))\b",),
    ]),
    ("insult", h_insult, [
        (r"\b(stupid|dumb|idiot|useless|trash|garbage|annoying|bodoh|goblok|tolol|nyebelin|payah|bego|jelek)\b",
         r"\b(you|u|kamu|kau|lu|lo|bot|botnya)\b"),
    ]),
    ("help", h_help, [
        (r"\bwhat\b", r"\b(can|could)\b", r"\b(you|u)\b", r"\b(do|help)\b"),
        (r"\b(help|commands?|features?|menu|bantuan|perintah|fitur|bantu)\b",),
        (r"\b(bisa|dapat)\b", r"\b(apa|ngapain)\b"),
    ]),
    ("favorite", h_favorite, [
        (r"\b(favorite|favourite|fav|favorit)\b",),
        (r"\bwhat do you like\b|\bkamu suka apa\b",),
    ]),
    ("bot_life", h_bot_life, [
        (r"\bwhere do you live\b|\bwhere are you from\b|\bkamu (tinggal|dari|asal)\b|\bdimana kamu tinggal\b",),
        (r"\b(do you|are you)\b", r"\b(sleep|eat|hungry|tired|dream|breathe)\b"),
        (r"\bkamu\b", r"\b(tidur|makan|lapar|mimpi)\b"),
    ]),
    ("doing", h_doing, [
        (r"\b(what are you (doing|up to)|what're you doing|lagi ngapain|lagi apa|sedang apa|ngapain)\b",),
    ]),
    ("how_are_you", h_how_are_you, [
        (r"\bhow\b", r"\b(are|r)\s+(you|u)\b"),
        (r"\b(apa kabar|gimana kabar|bagaimana kabar|kabarmu|kabar kamu)\b",),
    ]),
    ("sorry", h_sorry, [
        (r"\b(sorry|maaf|my bad|sori)\b",),
    ]),
    ("thanks", h_thanks, [
        (r"\b(thanks|thank you|thx|tysm|makasih|terima kasih|terimakasih|trims|tengkyu)\b|\bty\b",),
    ]),
    ("compliment", h_compliment, [
        (r"\b(good|nice|great|best|awesome)\b", r"\b(bot|job)\b"),
        (r"\b(keren|mantap|mantul|hebat|pinter|pintar)\b",),
    ]),
    ("laugh", h_laugh, [
        (r"^\s*(lol|lmao|lmfao|haha+|hehe+|wkwk+\w*|awok+|xd)\W*$|^\s*[😂🤣😆]+\s*$",),
    ]),
    ("ack", h_ack, [
        (r"^\s*(ok|okay|oke|okey|sip|siap|noted|alright|got it|gotcha)\W*$",),
    ]),
    ("bye", h_bye, [
        (r"\b(bye|goodbye|see you|see ya|cya|gtg|sampai jumpa|dadah|good night|goodnight|selamat tidur)\b",),
    ]),
    ("greeting", h_greeting, [
        (r"\b(hello|hi|hey|hai|halo|hallo|yo|sup|oi|woi)\b",),
        (r"\bselamat\s+(pagi|siang|sore|malam)\b|\bgood\s+(morning|evening|afternoon)\b|assalamu'?alaikum",),
    ]),
]

# When AI mode is on, these social/open-ended intents go to the AI instead of canned replies.
# Everything else (server info, help, marketplace, calculator, games, ...) stays rule-based.
AI_PREFERRED = {
    "joke", "riddle", "wyr", "truth_dare", "fun_fact", "motivate", "compliment_me", "bored", "sad", "happy",
    "love", "insult", "favorite", "bot_life", "doing", "how_are_you", "sorry", "compliment",
}

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

    # AI mode: open-ended chat (and anything the rules don't understand) goes to the AI
    if AI_ENABLED and (found is None or found[0] in AI_PREFERRED):
        if time.monotonic() - _ai_last.get(message.author.id, 0) >= AI_COOLDOWN_SECONDS:
            _ai_last[message.author.id] = time.monotonic()
            async with message.channel.typing():
                ai_reply = await ask_ai(message, text)
            if ai_reply:
                await _send(message, ai_reply, None)
                return True
        elif found is None:
            return True  # AI cooldown: stay quiet instead of spamming

    if found:
        content, embed = found[1](bot, message, lang, text)
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
