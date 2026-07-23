import discord
import os
import re
import asyncio
from datetime import datetime, timezone
from aiohttp import web
# ========================= CONFIG =========================
PANEL_CHANNEL_ID = 1529103880506310918         # Channel where the "Create Report" button/panel lives
REPORTS_CATEGORY_ID = 1477260121339072532      # Category new report channels are created under
CLOSED_CATEGORY_ID = 1529247109537202337       # Category closed report channels get moved into
ROLE_ID_TO_PING = 1529040758286450819          # Role that gets pinged on new report channel
STAFF_ROLE_ID = 1477457940553138349            # Role allowed to view report channels / use the status buttons
REOPEN_ROLE_ID = 1477262472942587967           # Role allowed to reopen a closed report
REPORTER_ROLE_ID = 1529103473696575498         # Role given to a report creator while their report channel is open
LOGS_CHANNEL_ID = 1529091178945839164          # Logs channel ID
CLOSED_REPORTS_CHANNEL_ID = 1529096698201116782  # Fallback channel used only if DMing the report creator fails (e.g. their DMs are closed)
SETUP_COMMAND = "!setup_report_button"         # Text command (staff only) to (re)post the "Create Report" panel
REPORT_THUMBNAIL_URL = "https://cdn.discordapp.com/attachments/1477262109480980621/1529247992521953380/game_reposts_banner.png"  # Thumbnail shown on report embeds
# =======================================================
intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # needed to add/remove roles reliably
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


def extract_creator_id(embed: discord.Embed):
    """Pull the report creator's user ID back out of the 'Created by' field."""
    for field in embed.fields:
        if field.name == "Created by":
            match = re.search(r"`(\d+)`", field.value)
            if match:
                return int(match.group(1))
    return None


def extract_footer_id(embed: discord.Embed):
    """Pull a user ID back out of the embed footer, e.g. 'Last action by: Name (123456)'."""
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


def format_duration(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def extract_first_being_handled_ts(history_text: str):
    """Find the timestamp of the first time this report was marked 'Being handled'."""
    for line in history_text.split("\n"):
        if "Being handled" in line:
            match = re.search(r"<t:(\d+):R>", line)
            if match:
                return int(match.group(1))
    return None


def make_channel_name(reason: str, user: discord.abc.User) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", reason.lower()).strip("-")
    if not slug:
        slug = "report"
    return f"{slug}-{str(user.id)[-4:]}"[:100]


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


class ReopenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        reopen_role = interaction.guild.get_role(REOPEN_ROLE_ID)
        if reopen_role and reopen_role in interaction.user.roles:
            return True
        await interaction.response.send_message("❌ You don't have permission to reopen this report.", ephemeral=True)
        return False

    @discord.ui.button(label="Reopen", emoji="🔓", style=discord.ButtonStyle.secondary, custom_id="thread:reopen")
    async def reopen(self, interaction: discord.Interaction, button: discord.ui.Button):
        await reopen_report(interaction)


async def reopen_report(interaction: discord.Interaction):
    channel = interaction.channel
    message = interaction.message
    reopener = interaction.user
    embed = message.embeds[0]

    await interaction.response.defer()

    creator_id = extract_creator_id(embed)
    creator_member = await resolve_member(interaction.guild, creator_id) if creator_id else None
    closer_id = extract_footer_id(embed)  # who closed it — grab before we overwrite the footer below
    closer_member = await resolve_member(interaction.guild, closer_id) if closer_id else None

    # Restore the reporter role and their send access
    if creator_member:
        reporter_role = interaction.guild.get_role(REPORTER_ROLE_ID)
        if reporter_role and reporter_role not in creator_member.roles:
            try:
                await creator_member.add_roles(reporter_role, reason="Report reopened")
            except Exception as e:
                print(f"⚠️ Failed to re-add reporter role: {e}")
        try:
            await channel.set_permissions(creator_member, view_channel=True, send_messages=True, reason="Report reopened")
        except Exception as e:
            print(f"⚠️ Failed to restore send permissions: {e}")

    # Move it back to the open reports category
    try:
        if REPORTS_CATEGORY_ID:
            open_category = interaction.guild.get_channel(REPORTS_CATEGORY_ID)
            if open_category:
                await channel.edit(category=open_category, reason="Report reopened")
    except Exception as e:
        print(f"⚠️ Failed to move channel back to open category: {e}")

    # Rename back to pending
    try:
        new_name = f"🟡-{channel.name.lstrip('🟡🔵🟢🔴-')}"
        await channel.edit(name=new_name[:100])
    except Exception as e:
        print(f"⚠️ Failed to rename channel on reopen: {e}")

    # Update embed + swap the view back to the normal status buttons
    embed.title = "🟡 Reopened - Awaiting Action"
    embed.set_footer(text=f"Reopened by: {reopener} ({reopener.id})")
    append_history(embed, f"🟡 **Reopened** by {reopener.mention} (<t:{int(discord.utils.utcnow().timestamp())}:R>)")
    await message.edit(embed=embed, view=ThreadStatusView())

    logs_channel = interaction.guild.get_channel(LOGS_CHANNEL_ID)
    if logs_channel:
        try:
            await logs_channel.send(f"🔓 {channel.mention} was reopened by {reopener.mention}.")
        except Exception as e:
            print(f"⚠️ Failed to send reopen log: {e}")

    if closer_member:
        try:
            await closer_member.send(
                f"🔓 A report you closed — **{channel.name}** ({channel.mention}) — has been reopened by management."
            )
        except Exception as e:
            print(f"⚠️ Failed to DM closer about reopen: {e}")

    if creator_member:
        try:
            await creator_member.send(
                f"🔓 Your report — **{channel.name}** ({channel.mention}) — has been reopened by management."
            )
        except Exception as e:
            print(f"⚠️ Failed to DM creator about reopen: {e}")

    await interaction.followup.send("✅ Report reopened.", ephemeral=True)


REASON_OPTIONS = [
    ("RDM", "rdm"),
    ("VDM", "vdm"),
    ("GTA Driving", "gta_driving"),
    ("Other", "other"),
]
REASON_LABELS = {value: label for label, value in REASON_OPTIONS}


class OtherReasonModal(discord.ui.Modal, title="Report Details"):
    def __init__(self, selected_values):
        super().__init__()
        self.selected_values = selected_values
        self.other_input = discord.ui.TextInput(
            label="Describe the reason",
            style=discord.TextStyle.short,
            placeholder="What happened?",
            required=True,
            max_length=100,
        )
        self.video_input = discord.ui.TextInput(
            label="Video clip link",
            style=discord.TextStyle.short,
            placeholder="Paste a link — or leave blank if you'll upload a clip instead",
            required=False,
            max_length=300,
        )
        self.add_item(self.other_input)
        self.add_item(self.video_input)

    async def on_submit(self, interaction: discord.Interaction):
        labels = [self.other_input.value if v == "other" else REASON_LABELS.get(v, v) for v in self.selected_values]
        await create_report_channel(interaction, ", ".join(labels), self.video_input.value)


class VideoLinkModal(discord.ui.Modal, title="Report Details"):
    def __init__(self, selected_values):
        super().__init__()
        self.selected_values = selected_values
        self.video_input = discord.ui.TextInput(
            label="Video clip link",
            style=discord.TextStyle.short,
            placeholder="Paste a link — or leave blank if you'll upload a clip instead",
            required=False,
            max_length=300,
        )
        self.add_item(self.video_input)

    async def on_submit(self, interaction: discord.Interaction):
        labels = [REASON_LABELS.get(v, v) for v in self.selected_values]
        await create_report_channel(interaction, ", ".join(labels), self.video_input.value)


class ReasonSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=label, value=value) for label, value in REASON_OPTIONS]
        super().__init__(
            placeholder="Select one or more reasons...",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values
        if "other" in selected:
            await interaction.response.send_modal(OtherReasonModal(selected))
        else:
            await interaction.response.send_modal(VideoLinkModal(selected))


class ReasonSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(ReasonSelect())


class CreateReportView(discord.ui.View):
    """Posted once in PANEL_CHANNEL_ID. Anyone can click it to open a report,
    pick a reason (or type one in for 'Other'), give a video link/clip, and
    get a private channel with staff."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Report", emoji="📝", style=discord.ButtonStyle.primary, custom_id="report:create")
    async def create_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Select the reason(s) for this report:", view=ReasonSelectView(), ephemeral=True
        )


async def create_report_channel(interaction: discord.Interaction, reason: str, video_link: str):
    await interaction.response.defer(ephemeral=True, thinking=True)

    guild = interaction.guild
    creator = interaction.user

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        creator: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_permissions=True, read_message_history=True),
    }
    staff_role = guild.get_role(STAFF_ROLE_ID)
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    category = guild.get_channel(REPORTS_CATEGORY_ID) if REPORTS_CATEGORY_ID else None

    try:
        channel = await guild.create_text_channel(
            name=f"🟡-{make_channel_name(reason, creator)}",
            category=category,
            overwrites=overwrites,
            reason=f"Report opened by {creator} ({creator.id})",
        )
    except Exception as e:
        await interaction.followup.send(f"⚠️ Couldn't create a report channel: {e}", ephemeral=True)
        return

    role = guild.get_role(ROLE_ID_TO_PING)
    ping = role.mention if role else None

    embed = discord.Embed(
        title="New Game Report - Awaiting Action",
        description="Please check this game report",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_image(url=REPORT_THUMBNAIL_URL)
    embed.add_field(name="Created by", value=f"{creator.mention} (`{creator.id}`)", inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    if video_link:
        embed.add_field(name="Video", value=video_link, inline=False)
    else:
        embed.add_field(name="Video", value="⚠️ No link provided — waiting on an uploaded clip below.", inline=False)

    view = ThreadStatusView()
    await channel.send(content=f"{ping} {creator.mention}".strip() if ping else creator.mention, embed=embed, view=view)

    if not video_link:
        await channel.send("📎 Please upload your video clip here as an attachment to complete your report.")

    reporter_role = guild.get_role(REPORTER_ROLE_ID)
    if reporter_role:
        try:
            await creator.add_roles(reporter_role, reason="Opened a report")
        except Exception as e:
            print(f"⚠️ Failed to add reporter role: {e}")

    await interaction.followup.send(f"✅ Your report channel has been created: {channel.mention}", ephemeral=True)


async def handle_status_update(interaction: discord.Interaction, emoji: str, status: str, delete: bool, reason: str = None):
    channel = interaction.channel
    closer = interaction.user
    message = interaction.message

    is_modal_submit = interaction.type == discord.InteractionType.modal_submit
    if is_modal_submit:
        await interaction.response.defer(ephemeral=True, thinking=True)
    else:
        await interaction.response.defer()

    # Update channel name
    new_name = f"{emoji}-{channel.name.lstrip('🟡🔵🟢🔴-')}"
    try:
        await channel.edit(name=new_name[:100])
    except:
        pass

    # Update embed
    embed = message.embeds[0]
    embed.title = f"{emoji} {status}"
    embed.set_footer(text=f"Last action by: {closer} ({closer.id})")

    history_line = f"{emoji} **{status}** by {closer.mention}"
    if reason:
        history_line += f" — {reason}"
    history_line += f" (<t:{int(discord.utils.utcnow().timestamp())}:R>)"
    append_history(embed, history_line)

    await message.edit(embed=embed)

    if is_modal_submit:
        await interaction.followup.send(f"✅ Report marked as **{status}**.", ephemeral=True)

    if delete:
        creator_id = extract_creator_id(embed)
        creator_member = await resolve_member(interaction.guild, creator_id) if creator_id else None
        creator_mention = creator_member.mention if creator_member else (f"<@{creator_id}>" if creator_id else "Unknown user")

        # Remove the reporter role now that the report is closed
        if creator_member:
            reporter_role = interaction.guild.get_role(REPORTER_ROLE_ID)
            if reporter_role and reporter_role in creator_member.roles:
                try:
                    await creator_member.remove_roles(reporter_role, reason="Report closed")
                except Exception as e:
                    print(f"⚠️ Failed to remove reporter role: {e}")

        # DM the creator that their report is closed (fall back to the channel only if the DM fails, e.g. DMs disabled)
        notify_embed = discord.Embed(
            title=f"{emoji} Report Closed: {status}",
            description=f"Your report **{channel.name}** has been closed.",
            color=discord.Color.green() if status == "Handled" else discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        notify_embed.add_field(name="Closed by", value=closer.mention, inline=True)
        if reason:
            notify_embed.add_field(name="Reason", value=reason, inline=False)

        dm_sent = False
        if creator_member:
            try:
                await creator_member.send(embed=notify_embed)
                dm_sent = True
            except Exception as e:
                print(f"⚠️ Failed to DM report creator: {e}")

        if not dm_sent:
            closed_channel = interaction.guild.get_channel(CLOSED_REPORTS_CHANNEL_ID)
            if closed_channel:
                try:
                    await closed_channel.send(content=creator_mention, embed=notify_embed)
                except Exception as e:
                    print(f"⚠️ Failed to send closed-report fallback notification: {e}")

        # Send to logs channel, including the full action history and a link to the channel
        logs_channel = interaction.guild.get_channel(LOGS_CHANNEL_ID)
        if logs_channel:
            log_embed = discord.Embed(
                title="Report Closed",
                description=f"**Channel:** {channel.mention}\n**Final status:** {status}\n**Closed by:** {closer.mention}",
                color=discord.Color.green() if status == "Handled" else discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            )
            log_embed.add_field(name="Original Creator", value=creator_mention, inline=False)
            if reason:
                log_embed.add_field(name="Closing Reason", value=reason, inline=False)

            # How long the report sat pending before it was first marked "Being handled"
            # (or, if it was never marked that, before it was closed)
            pending_start = embed.timestamp
            if pending_start:
                being_handled_ts = extract_first_being_handled_ts(get_history(embed))
                pending_end = (
                    datetime.fromtimestamp(being_handled_ts, tz=timezone.utc)
                    if being_handled_ts
                    else discord.utils.utcnow()
                )
                pending_seconds = (pending_end - pending_start).total_seconds()
                label = "Time to first response" if being_handled_ts else "Time pending (never marked 'Being handled')"
                log_embed.add_field(name=label, value=format_duration(pending_seconds), inline=False)

            log_embed.add_field(name="History", value=get_history(embed), inline=False)
            await logs_channel.send(embed=log_embed)

        # Lock the channel down instead of deleting it — reporter keeps read access, loses send access
        await asyncio.sleep(3)
        try:
            if creator_member:
                await channel.set_permissions(creator_member, view_channel=True, send_messages=False, reason="Report closed")
            if CLOSED_CATEGORY_ID:
                closed_category = interaction.guild.get_channel(CLOSED_CATEGORY_ID)
                if closed_category:
                    await channel.edit(category=closed_category, reason="Report closed")
        except Exception as e:
            print(f"⚠️ Failed to lock down closed report channel: {e}")

        # Swap the status buttons out for a single "Reopen" button, gated to REOPEN_ROLE_ID
        try:
            await message.edit(view=ReopenView())
        except Exception as e:
            print(f"⚠️ Failed to swap in the reopen button: {e}")


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    bot.add_view(ThreadStatusView())
    bot.add_view(ReopenView())
    bot.add_view(CreateReportView())
    asyncio.create_task(run_web())


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.content.strip() == SETUP_COMMAND:
        staff_role = message.guild.get_role(STAFF_ROLE_ID) if message.guild else None
        if not staff_role or staff_role not in message.author.roles:
            return
        panel_embed = discord.Embed(
            title="📝 Submit a Game Report",
            description="Click the button below to open a private report channel. "
                        "You'll be asked for a reason and a video link/clip.",
            color=discord.Color.blurple(),
        )
        panel_embed.set_image(url=REPORT_THUMBNAIL_URL)
        await message.channel.send(embed=panel_embed, view=CreateReportView())
        try:
            await message.delete()
        except Exception:
            pass


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
