import discord
import os
import asyncio
from aiohttp import web

# ========================= CONFIG =========================
THREAD_CHANNEL_ID = 1529046450556895282 # ← CHANGE THIS
ROLE_ID_TO_PING = 1529040758286450819 # ← CHANGE THIS
STAFF_ROLE_ID = 1477457940553138349     # ← NEW: Role allowed to press buttons
# =======================================================

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True

bot = discord.Client(intents=intents)

class ThreadStatusView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        staff_role = interaction.guild.get_role(STAFF_ROLE_ID)
        if staff_role and staff_role in interaction.user.roles:
            return True
        else:
            await interaction.response.send_message("❌ You don't have permission to use these buttons.", ephemeral=True)
            return False

    @discord.ui.button(label="Being handled", style=discord.ButtonStyle.primary, custom_id="thread:being_handled")
    async def being_handled(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_thread(interaction, "🔵")

    @discord.ui.button(label="Handled", style=discord.ButtonStyle.success, custom_id="thread:handled")
    async def handled(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_thread(interaction, "🟢")

    @discord.ui.button(label="No action", style=discord.ButtonStyle.danger, custom_id="thread:no_action")
    async def no_action(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_thread(interaction, "🔴")

    async def update_thread(self, interaction: discord.Interaction, emoji: str):
        thread = interaction.channel
        if isinstance(thread, discord.Thread):
            new_name = f"{emoji} {thread.name.lstrip('🔵🟢🔴 ')}"
            try:
                await thread.edit(name=new_name[:100])
            except:
                pass
        
        embed = interaction.message.embeds[0]
        embed.title = f"{emoji} Status Updated"
        await interaction.response.edit_message(embed=embed)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    bot.add_view(ThreadStatusView())
    asyncio.create_task(run_web())


@bot.event
async def on_thread_create(thread: discord.Thread):
    if thread.parent_id != THREAD_CHANNEL_ID:
        return

    role = thread.guild.get_role(ROLE_ID_TO_PING)
    ping = role.mention if role else "@here"

    creator = "Unknown"
    if thread.owner:
        creator = thread.owner.mention
    elif thread.owner_id:
        creator = f"<@{thread.owner_id}>"

    embed = discord.Embed(
        title="New Game Report - Awaiting Action",
        description=f"{ping}, Please check this game report",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Created by", value=creator, inline=True)

    view = ThreadStatusView()
    await thread.send(embed=embed, view=view)


# Web server
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
