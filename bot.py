async def handle_status_update(interaction: discord.Interaction, emoji: str, status: str, delete: bool, reason: str = None, message: discord.Message = None):
    channel = interaction.channel
    closer = interaction.user
    message = message or interaction.message
    is_modal = interaction.type == discord.InteractionType.modal_submit

    try:
        if is_modal:
            await interaction.response.defer(ephemeral=True, thinking=True)
        else:
            await interaction.response.defer()

        # Update channel name
        try:
            new_name = f"{emoji}-{channel.name.lstrip('🟡🔵🟢🔴-')}"
            await channel.edit(name=new_name[:100])
        except Exception as e:
            print(f"Status update channel rename error: {e}")

        embed = message.embeds[0].copy()  # Better to work on a copy

        embed.title = f"{emoji} {status}"
        embed.set_footer(text=f"Last action by: {closer} ({closer.id})")

        history_line = f"{emoji} **{status}** by {closer.mention}"
        if reason:
            history_line += f" — {reason}"
        history_line += f" (<t:{int(discord.utils.utcnow().timestamp())}:R>)"

        append_history(embed, history_line)

        # Protected edit
        await message.edit(embed=embed)

        if is_modal:
            await interaction.followup.send(f"✅ Report marked as **{status}**.", ephemeral=True)

        # === Rest of the logic (DM, close channel, logs) ===
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

            await asyncio.sleep(2)

            try:
                if CLOSED_CATEGORY_ID:
                    await channel.edit(category=interaction.guild.get_channel(CLOSED_CATEGORY_ID))
                await message.edit(view=ReopenView())
            except Exception as e:
                print(f"Close error: {e}")

            # Logs
            logs_channel = interaction.guild.get_channel(LOGS_CHANNEL_ID)
            if logs_channel:
                try:
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
                    log_embed.add_field(name="History", value=get_history(embed), inline=False)
                    await logs_channel.send(embed=log_embed)
                except Exception as e:
                    print(f"Logs embed failed: {e}")

    except Exception as e:
        print(f"CRITICAL ERROR in handle_status_update: {e}", exc_info=True)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ An error occurred while updating the report.", ephemeral=True)
            else:
                await interaction.followup.send("❌ An error occurred while updating the report.", ephemeral=True)
        except:
            pass
