import discord
import os
import re
import asyncio
from aiohttp import web
# ========================= CONFIG =========================
PANEL_CHANNEL_ID = 1529103880506310918         # Channel where the "Create Report" button/panel lives
REPORTS_CATEGORY_ID = 1477260121339072532      # Category new report channels are created under
CLOSED_CATEGORY_ID = 1529247109537202337       # Category closed report channels get moved into
ROLE_ID_TO_PING = 1529040758286450819          # Role that gets pinged on new report channel
STAFF_ROLE_ID = 1477457940553138349            # Role allowed to view report channels / use the status buttons
REPORTER_ROLE_ID = 1529103473696575498         # Role given to a report creator while their report channel is open
LOGS_CHANNEL_ID = 1529091178945839164          # Logs channel ID
CLOSED_REPORTS_CHANNEL_ID = 1529096698201116782  # Channel where an embed is posted to notify the creator their report is closed
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


async def resolve_member(guild: discord.Guild, user_id: int):
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except Exception:
            member = None
    return member


def make_channel_name(user: discord.abc.User) -> str:
    base = re.sub(r"[^a-z0-9-]", "", user.display_name.lower().replace(" ", "-"))
    if not base:
        base = "user"
    return f"report-{base}-{str(user.id)[-4:]}"[:100]


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
            name=make_channel_name(creator),
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
        title=f"New Report: {reason}",
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
    new_name = f"{emoji}-{channel.name.lstrip('🔵🟢🔴-')}"
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

        # Notify in the closed-reports channel
        closed_channel = interaction.guild.get_channel(CLOSED_REPORTS_CHANNEL_ID)
        if closed_channel:
            notify_embed = discord.Embed(
                title=f"{emoji} Report Closed: {status}",
                description=f"{creator_mention}, your report **{channel.name}** has been closed.",
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


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    bot.add_view(ThreadStatusView())
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
