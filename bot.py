import discord
from discord.ext import commands
import os

# ========================= CONFIG =========================
THREAD_CHANNEL_ID = 1529046450556895282  # ← Change to your channel ID
ROLE_ID_TO_PING = 1529040758286450819      # ← Change to your role ID
BOT_TOKEN = "MTUyOTA0NzU0MjIwODI2NjM3MQ.GnIYq9.bWp9iWVya3Vqgw7u42mQSwiYAlmXGzrE7si-qs"   # Or hardcode temporarily (not recommended)
# =======================================================

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

class ThreadStatusView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # Persistent

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
        # Disable all buttons after action
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    # Make buttons persistent
    bot.add_view(ThreadStatusView())


@bot.event
async def on_thread_create(thread: discord.Thread):
    if thread.parent_id != THREAD_CHANNEL_ID:
        return  # Only respond in the configured channel

    role = thread.guild.get_role(ROLE_ID_TO_PING)
    ping = role.mention if role else "@here"

    embed = discord.Embed(
        title="New Thread - Awaiting Action",
        description=f"**Thread:** {thread.mention}\n**Created by:** {thread.owner.mention if thread.owner else 'Unknown'}",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )

    view = ThreadStatusView()
    msg = await thread.send(f"{ping} New thread needs attention!", embed=embed, view=view)


# Optional: Slash command to test
@bot.tree.command(name="setup_thread_monitor", description="Confirm the bot is monitoring the channel")
async def setup(interaction: discord.Interaction):
    await interaction.response.send_message(f"Monitoring threads in <#{THREAD_CHANNEL_ID}> and pinging <@&{ROLE_ID_TO_PING}>", ephemeral=True)

@bot.tree.command(name="test", description="Test if bot is working")
async def test(interaction: discord.Interaction):
    await interaction.response.send_message("Bot is working! Channel ID being used: " + str(THREAD_CHANNEL_ID))

bot.run(BOT_TOKEN)