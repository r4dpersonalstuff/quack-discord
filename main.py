import os
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"=================================")
    print(f"Bot berhasil online sebagai: {bot.user}")
    print(f"=================================")
    
    # Sync slash command ke Discord
    try:
        synced = await bot.tree.sync()
        print(f"Berhasil mensinkronkan {len(synced)} slash commands.")
    except Exception as e:
        print(f"Gagal mensinkronkan: {e}")

# ==========================================
# COMMAND /SAY CUSTOM (PESAN + CHANNEL)
# ==========================================
@bot.tree.command(name="say", description="Send custom messages from bots to specific channels")
@discord.app_commands.describe(
    pesan="What message will the bot send?",
    channel="Select the target channel where the bot will send the message"
)
async def say_custom(
    interaction: discord.Interaction, 
    pesan: str, 
    channel: discord.TextChannel
):
    # 1. Bot mengirimkan pesan ke channel yang dipilih user
    await channel.send(pesan)
    
    # 2. Berikan konfirmasi rahasia ke pengirim command
    # (ephemeral=True membuat balasan konfirmasi ini HANYA bisa dilihat oleh kamu)
    await interaction.response.send_message(
        f"Message sent successfully {channel.mention}!", 
        ephemeral=True
    )

# JALANKAN BOT
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    print("ERROR: DISCORD_TOKEN tidak ditemukan!")
else:
    bot.run(TOKEN)
    
