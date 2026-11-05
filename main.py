import os
import discord
from discord.ext import commands

# 1. Mengaktifkan izin intents membaca pesan
intents = discord.Intents.default()
intents.message_content = True

# 2. Inisialisasi bot dengan prefix '!'
bot = commands.Bot(command_prefix="!", intents=intents)

# 3. Event saat bot online
@bot.event
async def on_ready():
    print(f"=================================")
    print(f"Bot berhasil online di Railway sebagai: {bot.user}")
    print(f"=================================")

# 4. Perintah tes !ping
@bot.command()
async def ping(ctx):
    await ctx.send("Pong! I'm Here!")

# 5. Mengambil Token secara aman dari Environment Variable Railway
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    print("ERROR: DISCORD_TOKEN tidak ditemukan! Harap atur Variable di dashboard Railway.")
else:
    bot.run(TOKEN)
    
