import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot berhasil online sebagai: {bot.user}")

@bot.command()
async def ping(ctx):
    await ctx.send("Pong! Bot aktif 24/7!")

bot.run("MTU2Nzc4MTE4OTUyMjAzODg3NA.GdBDWO.1yjjG-qTLctBUFq8b8nDhz7k2C5c6scnlzuCQM")
