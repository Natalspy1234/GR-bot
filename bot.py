import discord
import os
import asyncio
from aiohttp import web

# ========================= CONFIG =========================
THREAD_CHANNEL_ID = 1529046450556895282   # ← CHANGE THIS
ROLE_ID_TO_PING = 1529040758286450819     # ← CHANGE THIS
# =======================================================

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True

bot = discord.Client(intents=intents)

class ThreadStatusView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Being handled", style=discord.ButtonStyle.primary, custom_id="thread:being_handled")
    async def being_handled(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_status(interaction, "🔄 Being handled")

    @discord.ui.button(label="Handled", style=discord.ButtonStyle.success, custom_id="thread:handled")
    async def handled(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_status(interaction, "✅ Handled")

    @discord.ui.button(label="No action", style=discord.ButtonStyle.gray, custom_id="thread:no_action")
    async def no_action(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_status(interaction, "⚪ No action")

    async def update_status(self, interaction: discord.Interaction, new_title: str):
        embed = interaction.message.embeds[0]
        embed.title = new_title
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    bot.add_view(ThreadStatusView())
    asyncio.create_task(run_web())  # Keep Railway alive


@bot.event
async def on_thread_create(thread: discord.Thread):
    if thread.parent_id != THREAD_CHANNEL_ID:
        return

    role = thread.guild.get_role(ROLE_ID_TO_PING)
    ping = role.mention if role else "@here"

    embed = discord.Embed(
        title="New Thread - Awaiting Action",
        description=f"**Thread:** {thread.mention}\n**Created by:** {thread.owner.mention if thread.owner else 'Unknown'}",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )

    view = ThreadStatusView()
    await thread.send(f"{ping} New thread needs attention!", embed=embed, view=view)


# Web server to keep it alive
async def health(request):
    return web.Response(text="Bot is running!")

app = web.Application()
app.router.add_get('/', health)

async def run_web():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', os.getenv("PORT", 8080))
    await site.start()


bot.run(os.getenv("DISCORD_TOKEN"))
