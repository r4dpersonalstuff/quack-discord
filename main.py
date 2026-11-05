import os
import discord
from discord.ext import commands

# Pengaturan izin membaca isi pesan
intents = discord.Intents.default()
intents.message_content = True

# Inisialisasi bot dengan prefix perintah '!'
bot = commands.Bot(command_prefix="!", intents=intents)

# Event saat bot berhasil terhubung ke server Discord
@bot.event
async def on_ready():
    print(f"Status: Bot 24/7 berhasil online sebagai {bot.user}")

# Perintah tes sederhana
@bot.command()
async def ping(ctx):
    await ctx.send("Pong! Bot aktif 24 jam nonstop dari server cloud!")

@bot.command()
async def hi(ctx):
    await ctx.send(f"Halo {ctx.author.name}! Bot ini berjalan dari server cloud tanpa HP menyala.")

# Membaca token bot dari Environment Variable Koyeb
TOKEN = os.getenv("MTU2Nzc4MTE4OTUyMjAzODg3NA.GsPFpj.BDoDbT07LbSkrGdA-9sqsHzlrnWqclamLJVXvU")
bot.run(TOKEN)
