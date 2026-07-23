import discord
import os
import re
import asyncio
from datetime import datetime, timezone
from aiohttp import web

# ========================= CONFIG =========================
PANEL_CHANNEL_ID = 1529103880506310918
REPORTS_CATEGORY_ID = 1477260121339072532
CLOSED_CATEGORY_ID = 1529247109537202337
ROLE_ID_TO_PING = 1529040758286450819
STAFF_ROLE_ID = 1477457940553138349
REOPEN_ROLE_ID = 1477262472942587967
REPORTER_ROLE_ID = 1529103473696575498
LOGS_CHANNEL_ID = 1529091178945839164
CLOSED_REPORTS_CHANNEL_ID = 1529096698201116782
SETUP_COMMAND = "!setup_report_button"
REPORT_THUMBNAIL_URL = "https://cdn.discordapp.com/attachments/1477262109480980621/1529247992521953380/game_reposts_banner.png"
# =======================================================

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True

bot = discord.Client(intents=intents)

MAX_HISTORY_FIELD_LEN = 1024

def append_history(embed: discord.Embed, line: str):
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
            lines = new_value.split("\n")
            while len("\n".join(lines)) > MAX_HISTORY_FIELD_LEN and len(lines) > 1:
                lines.pop(0)
            new_value = "\n".join(lines)
        embed.set_field_at(history_index, name="History", value=new_value, inline=False)

def remove_history_field(embed: discord.Embed):
    for i, field in enumerate(embed.fields):
        if field.name == "History":
            embed.remove_field(i)
            break

def get_history(embed: discord.Embed) -> str:
    for field in embed.fields:
        if field.name == "History":
            return field.value
    return "No actions recorded."

def extract_creator_id(embed: discord.Embed):
    for field in embed.fields:
        if field.name == "Created by":
            match = re.search(r"`(\d+)`", field.value)
            if match:
                return int(match.group(1))
    return None

def extract_footer_id(embed: discord.Embed):
    if embed.footer and embed.footer.text:
        match = re.search(r"\((\d+)\)", embed.footer.text)
        if match:
            return int(match.group(1))
    return None

async def resolve_member(guild: discord.Guild, user_id: int):
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except Exception:
            member = None
    return member

def make_channel_name(reason: str, user: discord.abc.User) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", reason.lower()).strip("-")
    if not slug:
        slug = "report"
    return f"{slug}-{str(user.id)[-4:]}"[:100]


# ====================== MODALS & VIEWS ======================
class ReopenReasonModal(discord.ui.Modal, title="Reopen Report"):
    def __init__(self, message: discord.Message):
        super().__init__()
        # Captured from the button click, not relied on from the modal-submit
        # interaction, since Interaction.message is not always populated
        # reliably on modal submissions once a message has already been
        # edited/had its view swapped once before (this was the cause of
        # "can't re-close after reopen").
        self.message = message
        self.reason_input = discord.ui.TextInput(label="Reason for reopening", style=discord.TextStyle.paragraph, required=True, max_length=500)
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        await handle_reopen(interaction, self.reason_input.value, self.message)


class ThreadStatusView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild.get_role(STAFF_ROLE_ID) in interaction.user.roles:
            return True
        await interaction.response.send_message("❌ No permission.", ephemeral=True)
        return False

    @discord.ui.button(label="Being handled", style=discord.ButtonStyle.primary, custom_id="thread:being_handled")
    async def being_handled(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_status_update(interaction, "🔵", "Being handled", delete=False, message=interaction.message)

    @discord.ui.button(label="Handled", style=discord.ButtonStyle.success, custom_id="thread:handled")
    async def handled(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReasonModal("🟢", "Handled", interaction.message))

    @discord.ui.button(label="No action", style=discord.ButtonStyle.danger, custom_id="thread:no_action")
    async def no_action(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReasonModal("🔴", "No action", interaction.message))


class ReopenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild.get_role(REOPEN_ROLE_ID) in interaction.user.roles:
            return True
        await interaction.response.send_message("❌ No permission to reopen.", ephemeral=True)
        return False

    @discord.ui.button(label="Reopen", emoji="🔓", style=discord.ButtonStyle.secondary, custom_id="thread:reopen")
    async def reopen(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReopenReasonModal(interaction.message))


class ReasonModal(discord.ui.Modal):
    def __init__(self, emoji: str, status: str, message: discord.Message):
        super().__init__(title=f"Reason: {status}")
        self.emoji = emoji
        self.status = status
        # Same fix as ReopenReasonModal above - capture the message at
        # button-click time instead of trusting interaction.message later.
        self.message = message
        self.reason_input = discord.ui.TextInput(label="Reason", style=discord.TextStyle.paragraph, required=True, max_length=500)
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        await handle_status_update(interaction, self.emoji, self.status, delete=True, reason=self.reason_input.value, message=self.message)


# ====================== REOPEN ======================
async def handle_reopen(interaction: discord.Interaction, reason: str, message: discord.Message = None):
    channel = interaction.channel
    # Prefer the message captured at button-click time; fall back to
    # interaction.message just in case (e.g. if called from a non-modal path).
    message = message or interaction.message
    reopener = interaction.user
    embed = message.embeds[0]

    await interaction.response.defer()

    creator_id = extract_creator_id(embed)
    creator_member = await resolve_member(interaction.guild, creator_id) if creator_id else None
    closer_id = extract_footer_id(embed)
    closer_member = await resolve_member(interaction.guild, closer_id) if closer_id else None

    closer_name = closer_member.mention if closer_member else "Unknown Staff"

    if creator_member:
        try:
            if r := interaction.guild.get_role(REPORTER_ROLE_ID):
                await creator_member.add_roles(r)
            await channel.set_permissions(creator_member, view_channel=True, send_messages=True)
        except Exception as e:
            print(f"Reopen permission restore error: {e}")

    try:
        if REPORTS_CATEGORY_ID:
            await channel.edit(category=interaction.guild.get_channel(REPORTS_CATEGORY_ID))
        new_name = f"🟡-{channel.name.lstrip('🟡🔵🟢🔴-')}"
        await channel.edit(name=new_name[:100])
    except Exception as e:
        print(f"Reopen channel edit error: {e}")

    embed.title = "🟡 Reopened - Awaiting Action"
    embed.set_footer(text=f"Reopened by: {reopener} ({reopener.id})")
    remove_history_field(embed)
    reopened_ts = int(discord.utils.utcnow().timestamp())
    append_history(embed, f"🟡 **Reopened** by {reopener.mention} — {reason} (<t:{reopened_ts}:R>)")

    await message.edit(embed=embed, view=ThreadStatusView())

    creator_mention = creator_member.mention if creator_member else f"<@{creator_id}>"
    await channel.send(f"Hey {creator_mention}, your report that was closed by {closer_name} has been re opened by a high ranking staff member. the reason behind this is {reason}")

    # === LOGS EMBED FOR REOPEN ===
    logs_channel = interaction.guild.get_channel(LOGS_CHANNEL_ID)
    if logs_channel:
        log_embed = discord.Embed(
            title="Report Reopened",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )
        log_embed.add_field(name="Channel", value=channel.mention, inline=False)
        log_embed.add_field(name="Reopened by", value=reopener.mention, inline=False)
        log_embed.add_field(name="Original Creator", value=creator_mention, inline=False)
        log_embed.add_field(name="Previously closed by", value=closer_name, inline=False)
        log_embed.add_field(name="Reopen Reason", value=reason, inline=False)
        log_embed.add_field(name="History", value=get_history(embed), inline=False)

        try:
            await logs_channel.send(embed=log_embed)
        except Exception as e:
            print(f"Reopen logs embed failed: {e}")

    await interaction.followup.send("✅ Report reopened.", ephemeral=True)


# ====================== CLOSE / STATUS UPDATE ======================
async def handle_status_update(interaction: discord.Interaction, emoji: str, status: str, delete: bool, reason: str = None, message: discord.Message = None):
    channel = interaction.channel
    closer = interaction.user
    # Prefer the message captured at button-click time; fall back to
    # interaction.message for the non-modal ("Being handled") path.
    message = message or interaction.message
    is_modal = interaction.type == discord.InteractionType.modal_submit

    if is_modal:
        await interaction.response.defer(ephemeral=True, thinking=True)
    else:
        await interaction.response.defer()

    # Update name
    try:
        new_name = f"{emoji}-{channel.name.lstrip('🟡🔵🟢🔴-')}"
        await channel.edit(name=new_name[:100])
    except Exception as e:
        print(f"Status update channel rename error: {e}")

    embed = message.embeds[0]
    embed.title = f"{emoji} {status}"
    embed.set_footer(text=f"Last action by: {closer} ({closer.id})")

    history_line = f"{emoji} **{status}** by {closer.mention}"
    if reason:
        history_line += f" — {reason}"
    history_line += f" (<t:{int(discord.utils.utcnow().timestamp())}:R>)"
    append_history(embed, history_line)

    await message.edit(embed=embed)

    if is_modal:
        await interaction.followup.send(f"✅ Report marked as **{status}**.", ephemeral=True)

    if delete:
        creator_id = extract_creator_id(embed)
        creator_member = await resolve_member(interaction.guild, creator_id) if creator_id else None
        creator_mention = creator_member.mention if creator_member else f"<@{creator_id}>"

        if creator_member and (r := interaction.guild.get_role(REPORTER_ROLE_ID)):
            try:
                await creator_member.remove_roles(r)
            except Exception as e:
                print(f"Remove reporter role error: {e}")

        # DM Creator
        if creator_member:
            try:
                dm_embed = discord.Embed(
                    title=f"{emoji} Your Report Has Been Closed",
                    description=f"Your report **{channel.name}** has been closed as **{status}**.",
                    color=discord.Color.green() if status == "Handled" else discord.Color.red(),
                )
                dm_embed.add_field(name="Closed by", value=closer.mention)
                if reason:
                    dm_embed.add_field(name="Reason", value=reason)
                await creator_member.send(embed=dm_embed)
            except Exception as e:
                print(f"DM creator error: {e}")

        # Close channel + change view
        await asyncio.sleep(2)
        try:
            if CLOSED_CATEGORY_ID:
                await channel.edit(category=interaction.guild.get_channel(CLOSED_CATEGORY_ID))
            await message.edit(view=ReopenView())
        except Exception as e:
            print(f"Close error: {e}")

        # === RICH LOGS EMBED (like in your screenshot) ===
        logs_channel = interaction.guild.get_channel(LOGS_CHANNEL_ID)
        if logs_channel:
            log_embed = discord.Embed(
                title="Report Closed",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            log_embed.add_field(name="Channel", value=channel.mention, inline=False)
            log_embed.add_field(name="Final status", value=status, inline=False)
            log_embed.add_field(name="Closed by", value=closer.mention, inline=False)
            log_embed.add_field(name="Original Creator", value=creator_mention, inline=False)
            if reason:
                log_embed.add_field(name="Closing Reason", value=reason, inline=False)

            # Time to first response
            pending_start = embed.timestamp
            if pending_start:
                # Simple version
                log_embed.add_field(name="Time to first response", value="N/A", inline=False)

            log_embed.add_field(name="History", value=get_history(embed), inline=False)

            try:
                await logs_channel.send(embed=log_embed)
            except Exception as e:
                print(f"Logs embed failed: {e}")


# ====================== CREATE REPORT ======================
REASON_OPTIONS = [("RDM", "rdm"), ("VDM", "vdm"), ("GTA Driving", "gta_driving"), ("Other", "other")]
REASON_LABELS = {v: l for l, v in REASON_OPTIONS}

class OtherReasonModal(discord.ui.Modal, title="Report Details"):
    def __init__(self, selected_values):
        super().__init__()
        self.selected_values = selected_values
        self.other_input = discord.ui.TextInput(label="Describe the reason", style=discord.TextStyle.short, required=True, max_length=100)
        self.video_input = discord.ui.TextInput(label="Video clip link", style=discord.TextStyle.short, required=False, max_length=300)
        self.add_item(self.other_input)
        self.add_item(self.video_input)

    async def on_submit(self, interaction: discord.Interaction):
        labels = [self.other_input.value if v == "other" else REASON_LABELS.get(v, v) for v in self.selected_values]
        await create_report_channel(interaction, ", ".join(labels), self.video_input.value)


class VideoLinkModal(discord.ui.Modal, title="Report Details"):
    def __init__(self, selected_values):
        super().__init__()
        self.selected_values = selected_values
        self.video_input = discord.ui.TextInput(label="Video clip link", style=discord.TextStyle.short, required=False, max_length=300)
        self.add_item(self.video_input)

    async def on_submit(self, interaction: discord.Interaction):
        labels = [REASON_LABELS.get(v, v) for v in self.selected_values]
        await create_report_channel(interaction, ", ".join(labels), self.video_input.value)


class ReasonSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=label, value=value) for label, value in REASON_OPTIONS]
        super().__init__(placeholder="Select reason(s)...", min_values=1, max_values=len(options), options=options)

    async def callback(self, interaction: discord.Interaction):
        if "other" in self.values:
            await interaction.response.send_modal(OtherReasonModal(self.values))
        else:
            await interaction.response.send_modal(VideoLinkModal(self.values))


class ReasonSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(ReasonSelect())


class CreateReportView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Report", emoji="📝", style=discord.ButtonStyle.primary, custom_id="report:create")
    async def create_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Select the reason(s) for this report:", view=ReasonSelectView(), ephemeral=True)


async def create_report_channel(interaction: discord.Interaction, reason: str, video_link: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = interaction.guild
    creator = interaction.user

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        creator: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_permissions=True, read_message_history=True),
    }
    if staff_role := guild.get_role(STAFF_ROLE_ID):
        overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    try:
        channel = await guild.create_text_channel(
            name=f"🟡-{make_channel_name(reason, creator)}",
            category=guild.get_channel(REPORTS_CATEGORY_ID),
            overwrites=overwrites,
        )
    except Exception as e:
        await interaction.followup.send(f"⚠️ Failed: {e}", ephemeral=True)
        return

    ping = guild.get_role(ROLE_ID_TO_PING).mention if guild.get_role(ROLE_ID_TO_PING) else ""
    embed = discord.Embed(title="New Game Report - Awaiting Action", color=discord.Color.blurple(), timestamp=discord.utils.utcnow())
    embed.set_image(url=REPORT_THUMBNAIL_URL)
    embed.add_field(name="Created by", value=f"{creator.mention} (`{creator.id}`)", inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Video", value=video_link or "⚠️ No link provided.", inline=False)

    await channel.send(content=f"{ping} {creator.mention}".strip(), embed=embed, view=ThreadStatusView())

    if reporter_role := guild.get_role(REPORTER_ROLE_ID):
        try:
            await creator.add_roles(reporter_role)
        except:
            pass

    await interaction.followup.send(f"✅ Report created: {channel.mention}", ephemeral=True)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    bot.add_view(ThreadStatusView())
    bot.add_view(ReopenView())
    bot.add_view(CreateReportView())
    asyncio.create_task(run_web())


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.content.strip() != SETUP_COMMAND:
        return
    if message.guild.get_role(STAFF_ROLE_ID) not in message.author.roles:
        return

    embed = discord.Embed(title="📝 Submit a Game Report", description="Click below to open a report.", color=discord.Color.blurple())
    embed.set_image(url=REPORT_THUMBNAIL_URL)
    await message.channel.send(embed=embed, view=CreateReportView())
    try:
        await message.delete()
    except:
        pass


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
