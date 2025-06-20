import discord
from discord.ext import commands
from discord.ui import View, Select, Button
import json
import asyncio
import traceback

# --- Load Configs ---
with open("config/config.json", encoding="utf-8") as f:
    config = json.load(f)

with open("config/channels.json", encoding="utf-8") as f:
    channels = json.load(f)

with open("config/embeds.json", encoding="utf-8") as f:
    embeds_data = json.load(f)

with open("config/options.json", encoding="utf-8") as f:
    dropdown_options = json.load(f)

# --- Constants ---
TOKEN = config["TOKEN"]
CATEGORY_ID = channels["CATEGORY_ID"]
LOG_CHANNEL_ID = channels["LOG_CHANNEL_ID"]
PANEL_CHANNEL_ID = channels["PANEL_CHANNEL_ID"]
TICKET_ROLE_ID = channels["TICKET_ROLE_ID"]

# --- Helpers ---
def parse_color(color_str):
    return {
        "blurple": discord.Color.blurple(),
        "green": discord.Color.green(),
        "red": discord.Color.red()
    }.get(color_str.lower(), discord.Color.default())

def build_embed(embed_json, user=None, ticket_type=None, admin=None, closer=None):
    description = embed_json["description"]
    if user:
        description = description.replace("{user}", user.mention).replace("{username}", user.name)
    if ticket_type:
        description = description.replace("{ticket_type}", ticket_type)
    if admin:
        description = description.replace("{admin}", admin.mention)
    if closer:
        description = description.replace("{closer}", closer.mention)
    return discord.Embed(
        title=embed_json["title"],
        description=description,
        color=parse_color(embed_json["color"])
    )

# --- Bot Setup ---
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.members = True  # important to get members for roles and DMs

bot = commands.Bot(command_prefix="!", intents=intents)

# --- UI Elements ---
class CloseButton(Button):
    def __init__(self):
        super().__init__(label="Close Ticket", style=discord.ButtonStyle.red, emoji="🔒")

    async def callback(self, interaction: discord.Interaction):
        channel = interaction.channel
        guild = interaction.guild
        ticket_owner = None

        # Find ticket owner via channel topic or overwrites
        if channel.topic:
            try:
                ticket_owner = guild.get_member(int(channel.topic))
                print(f"ℹ️ Ticket owner found from topic: {ticket_owner}")
            except Exception as e:
                print(f"⚠️ Failed to get ticket owner from channel topic: {e}")
                ticket_owner = None

        if ticket_owner is None:
            for target, overwrite in channel.overwrites.items():
                if isinstance(target, discord.Member) and overwrite.view_channel:
                    ticket_owner = target
                    print(f"ℹ️ Ticket owner found from overwrite: {ticket_owner}")
                    break

        if ticket_owner is None:
            ticket_owner = interaction.user  # fallback
            print(f"ℹ️ Ticket owner fallback to interaction user: {ticket_owner}")

        # Respond immediately to interaction to avoid timeout
        await interaction.response.send_message("🔒 Closing ticket in 5 seconds...", ephemeral=True)

        # DM ticket owner
        try:
            await ticket_owner.send(
                embed=build_embed(embeds_data["close_embed"], ticket_owner, closer=interaction.user),
                allowed_mentions=discord.AllowedMentions(users=True)
            )
            print(f"✅ Sent close DM to ticket owner {ticket_owner}")
        except discord.Forbidden:
            print(f"⚠️ Cannot DM ticket owner {ticket_owner}")
        except Exception as e:
            print(f"⚠️ Unexpected error when DMing ticket owner {ticket_owner}: {e}")
            traceback.print_exc()

        # DM admins with the ticket role
        ticket_role = guild.get_role(TICKET_ROLE_ID)
        if ticket_role is None:
            print("⚠️ Ticket role not found in guild.")
        else:
            try:
                await guild.chunk()  # update members cache
            except Exception as e:
                print(f"⚠️ Failed to chunk guild members: {e}")
                traceback.print_exc()

            role_members = ticket_role.members
            print(f"ℹ️ Ticket role members count for close DM: {len(role_members)}")

            for i, admin in enumerate(role_members, start=1):
                print(f"ℹ️ Sending close DM to admin {i}/{len(role_members)}: {admin} ({admin.id})")
                try:
                    embed_to_send = build_embed(embeds_data["admin_ticket_close"], ticket_owner, admin=admin, closer=interaction.user)
                    await admin.send(
                        embed=embed_to_send,
                        allowed_mentions=discord.AllowedMentions(users=True, roles=True)
                    )
                    print(f"✅ Sent close DM to admin {admin}")
                except discord.Forbidden:
                    print(f"⚠️ Cannot DM admin {admin} - Forbidden")
                except Exception as e:
                    print(f"⚠️ Unexpected error DMing admin {admin}: {e}")
                    traceback.print_exc()

        # Log channel message
        log_channel = guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            try:
                await log_channel.send(
                    embed=build_embed(embeds_data["log_ticket_close"], ticket_owner, closer=interaction.user)
                )
            except Exception as e:
                print(f"⚠️ Failed to send log message: {e}")
                traceback.print_exc()

        # Wait then delete channel
        await asyncio.sleep(5)
        try:
            await channel.delete()
        except Exception as e:
            print(f"⚠️ Failed to delete ticket channel: {e}")
            traceback.print_exc()

class TicketDropdown(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=opt["label"], description=opt["description"], emoji=opt["emoji"])
            for opt in dropdown_options
        ]
        super().__init__(placeholder="Select the type of ticket...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        ticket_type = self.values[0]
        user = interaction.user
        guild = interaction.guild

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.get_role(TICKET_ROLE_ID): discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }

        category = guild.get_channel(CATEGORY_ID)
        if category is None:
            await interaction.response.send_message("❌ Ticket category not found. Contact admin.", ephemeral=True)
            return

        channel_name = f"ticket-{user.name}".replace(" ", "-").lower()
        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=str(user.id)
        )

        view = View()
        view.add_item(CloseButton())

        # Send the ticket opened embed
        await ticket_channel.send(embed=build_embed(embeds_data["open_embed"], user, ticket_type), view=view)

        # Log ticket creation with embed in the log channel
        log_channel = guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            try:
                await log_channel.send(
                    embed=build_embed(embeds_data["log_ticket_open"], user, ticket_type)
                )
            except Exception as e:
                print(f"⚠️ Failed to send log message for ticket creation: {e}")
                traceback.print_exc()

        await interaction.response.send_message(
            f"✅ Your ticket has been created: {ticket_channel.mention}", ephemeral=True
        )

        # DM the ticket owner
        try:
            await user.send(
                embed=build_embed(embeds_data["open_embed"], user, ticket_type),
                allowed_mentions=discord.AllowedMentions(users=True)
            )
        except discord.Forbidden:
            pass

        # DM all admins with the ticket role
        ticket_role = guild.get_role(TICKET_ROLE_ID)
        if ticket_role:
            for member in ticket_role.members:
                try:
                    # Send DM to admin with a mention and the ticket details
                    await member.send(
                        f"|| {member.mention} ||",
                        embed=build_embed(embeds_data["staff_ticket_alert"], user, ticket_type, admin=member),
                        allowed_mentions=discord.AllowedMentions(users=True, roles=True)
                    )
                    print(f"✅ Successfully DM'd admin {member}")
                except discord.Forbidden:
                    print(f"⚠️ Could not DM admin {member} about new ticket.")
                except Exception as e:
                    print(f"⚠️ Error when DMing admin {member}: {str(e)}")

class TicketPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketDropdown())

@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")
    panel_channel = bot.get_channel(PANEL_CHANNEL_ID)
    if panel_channel is None:
        print("⚠️ PANEL_CHANNEL_ID is invalid or inaccessible.")
        return

    existing_panel_msg = None
    async for msg in panel_channel.history(limit=50):
        if msg.author == bot.user and msg.embeds:
            if msg.embeds[0].title == embeds_data["panel_embed"]["title"]:
                existing_panel_msg = msg
                break

    view = TicketPanelView()
    if existing_panel_msg:
        try:
            await existing_panel_msg.edit(view=view)
            print(f"ℹ️ Existing ticket panel updated (ID: {existing_panel_msg.id})")
        except Exception as e:
            print(f"⚠️ Failed to re-attach View: {e}")
            traceback.print_exc()
    else:
        await panel_channel.send(embed=build_embed(embeds_data["panel_embed"]), view=view)
        print("✅ Ticket panel sent automatically.")

@bot.command()
@commands.has_permissions(administrator=True)
async def sendpanel(ctx):
    embed = build_embed(embeds_data["panel_embed"])
    await ctx.send(embed=embed, view=TicketPanelView())

bot.run(TOKEN)
