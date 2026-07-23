async def reopen_report(interaction: discord.Interaction):
    channel = interaction.channel
    message = interaction.message
    reopener = interaction.user
    embed = message.embeds[0]

    await interaction.response.defer()

    creator_id = extract_creator_id(embed)
    creator_member = await resolve_member(interaction.guild, creator_id) if creator_id else None
    closer_id = extract_footer_id(embed)
    closer_member = await resolve_member(interaction.guild, closer_id) if closer_id else None

    # Restore reporter role + permissions
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

    # Move back to open category
    try:
        if REPORTS_CATEGORY_ID:
            open_category = interaction.guild.get_channel(REPORTS_CATEGORY_ID)
            if open_category:
                await channel.edit(category=open_category, reason="Report reopened")
    except Exception as e:
        print(f"⚠️ Failed to move channel back: {e}")

    # Rename channel
    try:
        new_name = f"🟡-{channel.name.lstrip('🟡🔵🟢🔴-')}"
        await channel.edit(name=new_name[:100])
    except Exception as e:
        print(f"⚠️ Failed to rename channel: {e}")

    # Update embed + restore normal status buttons
    embed.title = "🟡 Reopened - Awaiting Action"
    embed.set_footer(text=f"Reopened by: {reopener} ({reopener.id})")
    append_history(embed, f"🟡 **Reopened** by {reopener.mention} (<t:{int(discord.utils.utcnow().timestamp())}:R>)")

    # This is the key fix: force the normal status view back
    view = ThreadStatusView()
    await message.edit(embed=embed, view=view)

    # === Notifications ===
    staff_role = interaction.guild.get_role(ROLE_ID_TO_PING)
    ping = staff_role.mention if staff_role else ""

    await channel.send(
        f"🔓 {ping} {creator_member.mention if creator_member else ''}".strip(),
        embed=discord.Embed(
            title="Report Reopened",
            description=f"**{channel.name}** has been reopened by {reopener.mention}.",
            color=discord.Color.gold()
        )
    )

    # DM Creator
    if creator_member:
        try:
            await creator_member.send(
                f"🔓 Your report — **{channel.name}** ({channel.mention}) — has been reopened by management."
            )
        except Exception as e:
            print(f"⚠️ Failed to DM creator: {e}")

    # DM Closer (if different from reopener)
    if closer_member and closer_member.id != reopener.id:
        try:
            await closer_member.send(
                f"🔓 A report you closed — **{channel.name}** ({channel.mention}) — has been reopened by {reopener.mention}."
            )
        except Exception as e:
            print(f"⚠️ Failed to DM closer: {e}")

    # Log it
    logs_channel = interaction.guild.get_channel(LOGS_CHANNEL_ID)
    if logs_channel:
        try:
            await logs_channel.send(f"🔓 {channel.mention} was reopened by {reopener.mention}.")
        except Exception as e:
            print(f"⚠️ Failed to send reopen log: {e}")

    await interaction.followup.send("✅ Report reopened successfully.", ephemeral=True)
