import discord
import os
import asyncio
from aiohttp import web
# ========================= CONFIG =========================
THREAD_CHANNEL_ID = 1529046450556895282 # ← CHANGE THIS
ROLE_ID_TO_PING = 1529040758286450819 # ← CHANGE THIS
STAFF_ROLE_ID = 1477457940553138349     # ← NEW: Role allowed to press buttons
LOGS_CHANNEL_ID = 1529091178945839164     # ← NEW: Logs channel ID
CLOSED_THREADS_CHANNEL_ID = 1529093723416428695 # ← NEW: Threads-enabled channel where a closed-report thread is created to ping the creator
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
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return False
    @discord.ui.button(label="Being handled", style=discord.ButtonStyle.primary, custom_id="thread:being_handled")
    async def being_handled(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_status(interaction, "🔵", "Being handled", delete=False)
    @discord.ui.button(label="Handled", style=discord.ButtonStyle.success, custom_id="thread:handled")
    async def handled(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_status(interaction, "🟢", "Handled", delete=True)
    @discord.ui.button(label="No action", style=discord.ButtonStyle.danger, custom_id="thread:no_action")
    async def no_action(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_status(interaction, "🔴", "No action", delete=True)
    async def update_status(self, interaction: discord.Interaction, emoji: str, status: str, delete: bool):
        await interaction.response.defer()
        thread = interaction.channel
        closer = interaction.user
        # Update thread name
        if isinstance(thread, discord.Thread):
            new_name = f"{emoji} {thread.name.lstrip('🔵🟢🔴 ')}"
            try:
                await thread.edit(name=new_name[:100])
            except:
                pass
        # Update embed
        embed = interaction.message.embeds[0]
        embed.title = f"{emoji} {status}"
        embed.set_footer(text=f"Action by: {closer} ({closer.id})")
        await interaction.edit_original_response(embed=embed)
        # If closing the thread
        if delete:
            creator_mention = thread.owner.mention if thread.owner else f"<@{thread.owner_id}>"
            # Notify inside the thread (no ping, since the creator is now pinged in a separate channel)
            try:
                await thread.send(f"This report has been closed.\n**Status:** {status}\n**Closed by:** {closer.mention}")
            except:
                pass
            # Create a new thread in the closed-threads channel and ping the creator there
            closed_threads_channel = interaction.guild.get_channel(CLOSED_THREADS_CHANNEL_ID)
            if closed_threads_channel:
                try:
                    closed_thread = await closed_threads_channel.create_thread(
                        name=f"{emoji} {thread.name.lstrip('🔵🟢🔴 ')}"[:100],
                        type=discord.ChannelType.public_thread
                    )
                    await closed_thread.send(f"{creator_mention} Your report **{thread.name}** has been closed.\n**Status:** {status}\n**Closed by:** {closer.mention}")
                except:
                    pass
            # Send to logs channel
            logs_channel = interaction.guild.get_channel(LOGS_CHANNEL_ID)
            if logs_channel:
                log_embed = discord.Embed(
                    title="Report Closed",
                    description=f"**Thread:** {thread.name}\n**Status:** {status}\n**Closed by:** {closer.mention}",
                    color=discord.Color.green() if status == "Handled" else discord.Color.red(),
                    timestamp=discord.utils.utcnow()
                )
                log_embed.add_field(name="Original Creator", value=creator_mention)
                await logs_channel.send(embed=log_embed)
            # Delete / Archive thread
            await asyncio.sleep(3)
            try:
                await thread.delete()
            except:
                await thread.edit(archived=True)
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
    creator = thread.owner.mention if thread.owner else f"<@{thread.owner_id}>" if thread.owner_id else "Unknown"
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


# ========================= CONFIG =========================
THREAD_CHANNEL_ID = 1529046450556895282 # ← CHANGE THIS
ROLE_ID_TO_PING = 1529040758286450819 # ← CHANGE THIS
STAFF_ROLE_ID = 1477457940553138349     # ← NEW: Role allowed to press buttons
LOGS_CHANNEL_ID = 1529091178945839164     # ← NEW: Logs channel ID
# =======================================================






