import discord
import os
import asyncio
from aiohttp import web
# ========================= CONFIG =========================
THREAD_CHANNEL_ID = 1529046450556895282 # Channel where threads are created
ROLE_ID_TO_PING = 1529040758286450819 # Role that gets pinged on new thread
STAFF_ROLE_ID = 1477457940553138349 # Role allowed to use buttons
LOGS_CHANNEL_ID = 1529091178945839164 # Logs channel ID
CLOSED_REPORTS_CHANNEL_ID = 1529096698201116782 # Channel where an embed is posted to notify the creator their report is closed
# =======================================================
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True
bot = discord.Client(intents=intents)

MAX_HISTORY_FIELD_LEN = 1024  # Discord embed field value limit


def append_history(embed: discord.Embed, line: str):
    """Add a line to the embed's 'History' field, creating it if it doesn't exist yet."""
    history_index = None
    for i, field in enumerate(embed.fields):
        if field.name == "History":
            history_index = i
            break
    if history_index is None:
        embed.add_field(name="History", value=line, inline=False)
    else:
        existing = embed.fields[history_index].value
        new_value = f"{existing}\n{line}"
        if len(new_value) > MAX_HISTORY_FIELD_LEN:
            # Trim oldest lines so we stay under Discord's field length limit
            lines = new_value.split("\n")
            while len("\n".join(lines)) > MAX_HISTORY_FIELD_LEN and len(lines) > 1:
                lines.pop(0)
            new_value = "\n".join(lines)
        embed.set_field_at(history_index, name="History", value=new_value, inline=False)


def get_history(embed: discord.Embed) -> str:
    for field in embed.fields:
        if field.name == "History":
            return field.value
    return "No actions recorded."


class ReasonModal(discord.ui.Modal):
    def __init__(self, emoji: str, status: str):
        super().__init__(title=f"Reason: {status}")
        self.emoji = emoji
        self.status = status
        self.reason_input = discord.ui.TextInput(
            label="Reason",
            style=discord.TextStyle.paragraph,
            placeholder="Explain why this report is being closed...",
            required=True,
            max_length=500,
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        await handle_status_update(interaction, self.emoji, self.status, delete=True, reason=self.reason_input.value)


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
        await handle_status_update(interaction, "🔵", "Being handled", delete=False)

    @discord.ui.button(label="Handled", style=discord.ButtonStyle.success, custom_id="thread:handled")
    async def handled(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReasonModal("🟢", "Handled"))

    @discord.ui.button(label="No action", style=discord.ButtonStyle.danger, custom_id="thread:no_action")
    async def no_action(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReasonModal("🔴", "No action"))


async def handle_status_update(interaction: discord.Interaction, emoji: str, status: str, delete: bool, reason: str = None):
    thread = interaction.channel
    closer = interaction.user
    message = interaction.message  # Works for both button clicks and modal submits from a component

    # Acknowledge the interaction. Modal submits need a "thinking" defer since they
    # aren't directly tied to editing the original message the way component clicks are.
    is_modal_submit = interaction.type == discord.InteractionType.modal_submit
    if is_modal_submit:
        await interaction.response.defer(ephemeral=True, thinking=True)
    else:
        await interaction.response.defer()

    # Update thread name
    if isinstance(thread, discord.Thread):
        new_name = f"{emoji} {thread.name.lstrip('🔵🟢🔴 ')}"
        try:
            await thread.edit(name=new_name[:100])
        except:
            pass

    # Update embed
    embed = message.embeds[0]
    embed.title = f"{emoji} {status}"
    embed.set_footer(text=f"Last action by: {closer} ({closer.id})")

    # Append this action to the History field
    history_line = f"{emoji} **{status}** by {closer.mention}"
    if reason:
        history_line += f" — {reason}"
    history_line += f" (<t:{int(discord.utils.utcnow().timestamp())}:R>)"
    append_history(embed, history_line)

    await message.edit(embed=embed)

    if is_modal_submit:
        await interaction.followup.send(f"✅ Report marked as **{status}**.", ephemeral=True)

    # If closing the report
    if delete:
        creator_mention = thread.owner.mention if thread.owner else f"<@{thread.owner_id}>"

        # Notify in the closed-reports channel
        closed_channel = interaction.guild.get_channel(CLOSED_REPORTS_CHANNEL_ID)
        if closed_channel:
            notify_embed = discord.Embed(
                title=f"{emoji} Report Closed: {status}",
                description=f"{creator_mention}, your report **{thread.name}** has been closed.",
                color=discord.Color.green() if status == "Handled" else discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            )
            notify_embed.add_field(name="Closed by", value=closer.mention, inline=True)
            if reason:
                notify_embed.add_field(name="Reason", value=reason, inline=False)
            try:
                await closed_channel.send(content=creator_mention, embed=notify_embed)
            except Exception as e:
                print(f"⚠️ Failed to send closed-report notification: {e}")
        else:
            print(f"⚠️ CLOSED_REPORTS_CHANNEL_ID ({CLOSED_REPORTS_CHANNEL_ID}) not found in guild.")

        # Send to logs channel, including the full action history
        logs_channel = interaction.guild.get_channel(LOGS_CHANNEL_ID)
        if logs_channel:
            log_embed = discord.Embed(
                title="Report Closed",
                description=f"**Thread:** {thread.name}\n**Final status:** {status}\n**Closed by:** {closer.mention}",
                color=discord.Color.green() if status == "Handled" else discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            )
            log_embed.add_field(name="Original Creator", value=creator_mention, inline=False)
            if reason:
                log_embed.add_field(name="Closing Reason", value=reason, inline=False)
            log_embed.add_field(name="History", value=get_history(embed), inline=False)
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
        timestamp=discord.utils.utcnow(),
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
