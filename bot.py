import discord
from discord.ext import commands
from discord import app_commands
import json
import os
from datetime import datetime, timezone
import asyncio

# --------------------
# Configuration
# --------------------
TOKEN = None  # pulled from environment later (set DISCORD_TOKEN in your .env)
CONFIG_FILE = "config.json"
TICKETS_FILE = "tickets.json"
MAX_TICKET_MESSAGE_LEN = 500
TICKET_MESSAGE_FIELD_LABEL = "Describe the problem (be concise)"

# --------------------
# JSON helpers
# --------------------

def safe_load(path, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f)
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return default


def safe_save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# Ensure files exist
safe_load(CONFIG_FILE, {})
safe_load(TICKETS_FILE, {})

# --------------------
# Config and tickets
# --------------------

def get_config(guild_id: int) -> dict:
    data = safe_load(CONFIG_FILE, {})
    return data.get(str(guild_id), {})


def set_config(guild_id: int, cfg: dict):
    data = safe_load(CONFIG_FILE, {})
    data[str(guild_id)] = cfg
    safe_save(CONFIG_FILE, data)


def get_tickets(guild_id: int) -> dict:
    """
    Return the tickets dict for the guild.
    Ensure the top-level JSON has a dict at str(guild_id) so the rest of the code can
    assume a dict is present.
    """
    data = safe_load(TICKETS_FILE, {})

    # If the file uses an unexpected top-level format like {"tickets": [...]}, keep it but
    # ensure we created the guild-entry so calls still work.
    if not isinstance(data, dict):
        data = {}

    if str(guild_id) not in data or not isinstance(data.get(str(guild_id)), dict):
        data[str(guild_id)] = {}
        safe_save(TICKETS_FILE, data)
    return data[str(guild_id)]


def save_tickets(guild_id: int, tickets: dict):
    data = safe_load(TICKETS_FILE, {})
    if not isinstance(data, dict):
        data = {}
    data[str(guild_id)] = tickets
    safe_save(TICKETS_FILE, data)

# --------------------
# Bot setup
# --------------------

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --------------------
# Utilities
# --------------------

def parse_mention(item: str):
    if not item:
        return None
    item = item.strip()
    try:
        if item.startswith("<@&") and item.endswith(">"):
            return ("role", int(item[3:-1]))
        if item.startswith("<@!") and item.endswith(">"):
            return ("user", int(item[3:-1]))
        if item.startswith("<@") and item.endswith(">"):
            return ("user", int(item[2:-1]))
        if item.isdigit():
            return ("id", int(item))
    except Exception:
        return None
    return None


def is_manager(member: discord.Member, cfg: dict) -> bool:
    # member is expected to be a Member in guild context (interaction.user will be a Member there)
    allowed_roles = cfg.get("allowed_roles", [])
    allowed_users = cfg.get("allowed_users", [])
    try:
        if member.id in allowed_users:
            return True
        member_role_ids = [r.id for r in member.roles]
        if any(rid in member_role_ids for rid in allowed_roles):
            return True
        if member.guild_permissions.manage_guild or member.guild_permissions.administrator:
            return True
    except Exception:
        # fallback: if member is not a Member (rare), deny
        return False
    return False

# --------------------
# Ticket modal and view
# --------------------

class TicketModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Create a Ticket")
        self.issue = discord.ui.TextInput(
            label=TICKET_MESSAGE_FIELD_LABEL,
            style=discord.TextStyle.paragraph,
            max_length=MAX_TICKET_MESSAGE_LEN,
            required=True,
            placeholder="Explain the issue in a few sentences...",
        )
        self.add_item(self.issue)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("This must be used in a server.", ephemeral=True)
            return

        cfg = get_config(guild.id)
        tickets = get_tickets(guild.id)
        now = datetime.now(timezone.utc)

        # count tickets this month for user (defensive)
        user_count = 0
        for t in tickets.values():
            try:
                created = t.get("created_at")
                if not created:
                    continue
                dt = datetime.fromisoformat(created)
                if dt.year == now.year and dt.month == now.month and t.get("owner") == interaction.user.id:
                    user_count += 1
            except Exception:
                continue

        limit = cfg.get("ticket_limit", 5)
        if user_count >= limit:
            await interaction.response.send_message(f"You have reached your monthly limit ({limit}).", ephemeral=True)
            return

        content = self.issue.value.strip()
        if len(content) > MAX_TICKET_MESSAGE_LEN:
            await interaction.response.send_message(f"Description too long (max {MAX_TICKET_MESSAGE_LEN}).", ephemeral=True)
            return

        today = now.strftime("%Y%m%d")
        count_today = sum(1 for k in tickets.keys() if k.startswith(today))
        ticket_id = f"{today}-{count_today+1:03d}"

        # Give attach_files permission to owner and staff so uploads work
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
        }
        for r in cfg.get("allowed_roles", []):
            role = guild.get_role(r)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True)
        for u in cfg.get("allowed_users", []):
            member = guild.get_member(u)
            if member:
                overwrites[member] = discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True)

        try:
            ticket_channel = await guild.create_text_channel(f"ticket-{ticket_id}", overwrites=overwrites)
        except Exception as e:
            await interaction.response.send_message("Failed to create ticket channel. Check bot permissions.", ephemeral=True)
            print("Create channel failed:", e)
            return

        tickets[ticket_id] = {
            "owner": interaction.user.id,
            "channel": ticket_channel.id,
            "message": content,
            "created_at": now.isoformat(),
        }
        save_tickets(guild.id, tickets)

        embed = discord.Embed(title=f"Ticket: {ticket_id}", color=discord.Color.green(), timestamp=now)
        embed.add_field(name="Owner", value=interaction.user.mention, inline=False)
        embed.add_field(name="Issue", value=content, inline=False)
        embed.set_footer(text="Support will respond here. Use /close to close the ticket.")

        ping_parts = []
        for rid in cfg.get("ping_roles", []):
            role = guild.get_role(rid)
            if role:
                ping_parts.append(role.mention)
        for uid in cfg.get("ping_users", []):
            m = guild.get_member(uid)
            if m:
                ping_parts.append(m.mention)

        ping_text = " ".join(ping_parts) if ping_parts else None
        try:
            if ping_text:
                await ticket_channel.send(content=ping_text, embed=embed)
            else:
                await ticket_channel.send(embed=embed)
        except Exception as e:
            print("Failed to send ticket embed:", e)

        await interaction.response.send_message(f"Ticket created: {ticket_channel.mention}", ephemeral=True)


class TicketOpenView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        # persistent button (custom_id includes guild id for uniqueness)
        btn = discord.ui.Button(label="Open Ticket", style=discord.ButtonStyle.primary, emoji="🎟️", custom_id=f"open_ticket_{guild_id}")
        btn.callback = self.on_button_click
        self.add_item(btn)

    async def on_button_click(self, interaction: discord.Interaction):
        cfg = get_config(interaction.guild.id)
        if cfg.get("ticket_channel") != interaction.channel.id:
            await interaction.response.send_message("This button is not active here.", ephemeral=True)
            return
        await interaction.response.send_modal(TicketModal())

# --------------------
# Events
# --------------------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    # For each guild, ensure the ticket message exists and register the persistent view,
    # then force a guild-specific command sync so commands show instantly in that guild.
    for guild in bot.guilds:
        try:
            cfg = get_config(guild.id)
            ticket_chan_id = cfg.get("ticket_channel")
            if ticket_chan_id:
                channel = guild.get_channel(ticket_chan_id)
                if channel:
                    embed = discord.Embed(
                        title="Open a Ticket",
                        description="Click the button below to open a ticket.",
                        color=discord.Color.blue()
                    )
                    msg_id = cfg.get("ticket_message_id")
                    try:
                        if msg_id:
                            msg = await channel.fetch_message(msg_id)
                            await msg.edit(embed=embed)
                            # register view handler (persistent view re-registration on startup)
                            bot.add_view(TicketOpenView(guild.id))
                        else:
                            sent = await channel.send(embed=embed, view=TicketOpenView(guild.id))
                            cfg["ticket_message_id"] = sent.id
                            set_config(guild.id, cfg)
                    except Exception as e:
                        print(f"Error ensuring ticket message in {guild.name}: {e}")
            # force guild-specific sync so commands appear instantly in this guild
            try:
                await bot.tree.sync(guild=discord.Object(id=guild.id))
                print(f"Synced commands for guild: {guild.name} ({guild.id})")
            except Exception as e:
                print(f"Failed to sync commands for guild {guild.name}: {e}")
        except Exception as e:
            print(f"Error in on_ready loop for guild {guild}: {e}")

    # also attempt a global sync (non-blocking)
    try:
        await bot.tree.sync()
        print("Global command sync attempted.")
    except Exception as e:
        print("Global sync error (non-fatal):", e)

# --------------------
# Slash utilities
# --------------------

@bot.tree.command(name="synccommands", description="(Admin) Register slash commands to this server immediately")
async def synccommands(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admin only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        await bot.tree.sync(guild=discord.Object(id=interaction.guild.id))
        await interaction.followup.send("Slash commands synced to this guild.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Failed to sync: {e}", ephemeral=True)

# --------------------
# Config modification commands
# --------------------

@bot.tree.command(name="addallowedrole", description="Add allowed role or user (mention or id)")
@app_commands.describe(item="Role mention (@Role) or user mention (@User) or raw id")
async def addallowedrole(interaction: discord.Interaction, item: str):
    if not is_manager(interaction.user, get_config(interaction.guild.id)):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    parsed = parse_mention(item)
    if not parsed:
        await interaction.response.send_message("Could not parse mention/id.", ephemeral=True)
        return
    kind, val = parsed
    cfg = get_config(interaction.guild.id)
    if kind == "role":
        cfg.setdefault("allowed_roles", [])
        if val not in cfg["allowed_roles"]:
            cfg["allowed_roles"].append(val)
    else:
        cfg.setdefault("allowed_users", [])
        if val not in cfg["allowed_users"]:
            cfg["allowed_users"].append(val)
    set_config(interaction.guild.id, cfg)
    await interaction.response.send_message("Added to allowed list.", ephemeral=True)

@bot.tree.command(name="removeallowedrole", description="Remove allowed role or user (mention or id)")
@app_commands.describe(item="Role mention (@Role) or user mention (@User) or raw id")
async def removeallowedrole(interaction: discord.Interaction, item: str):
    if not is_manager(interaction.user, get_config(interaction.guild.id)):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    parsed = parse_mention(item)
    if not parsed:
        await interaction.response.send_message("Could not parse mention/id.", ephemeral=True)
        return
    kind, val = parsed
    cfg = get_config(interaction.guild.id)
    if kind == "role" and "allowed_roles" in cfg and val in cfg["allowed_roles"]:
        cfg["allowed_roles"].remove(val)
    elif kind in ("user", "id") and "allowed_users" in cfg and val in cfg["allowed_users"]:
        cfg["allowed_users"].remove(val)
    set_config(interaction.guild.id, cfg)
    await interaction.response.send_message("Removed from allowed list.", ephemeral=True)

@bot.tree.command(name="addpingedrole", description="Add role or user to be pinged when tickets open")
@app_commands.describe(item="Role mention (@Role) or user mention (@User) or raw id")
async def addpingedrole(interaction: discord.Interaction, item: str):
    if not is_manager(interaction.user, get_config(interaction.guild.id)):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    parsed = parse_mention(item)
    if not parsed:
        await interaction.response.send_message("Could not parse mention/id.", ephemeral=True)
        return
    kind, val = parsed
    cfg = get_config(interaction.guild.id)
    if kind == "role":
        cfg.setdefault("ping_roles", [])
        if val not in cfg["ping_roles"]:
            cfg["ping_roles"].append(val)
    else:
        cfg.setdefault("ping_users", [])
        if val not in cfg["ping_users"]:
            cfg["ping_users"].append(val)
    set_config(interaction.guild.id, cfg)
    await interaction.response.send_message("Added to ping list.", ephemeral=True)

@bot.tree.command(name="removepingedrole", description="Remove role or user from ping list")
@app_commands.describe(item="Role mention (@Role) or user mention (@User) or raw id")
async def removepingedrole(interaction: discord.Interaction, item: str):
    if not is_manager(interaction.user, get_config(interaction.guild.id)):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    parsed = parse_mention(item)
    if not parsed:
        await interaction.response.send_message("Could not parse mention/id.", ephemeral=True)
        return
    kind, val = parsed
    cfg = get_config(interaction.guild.id)
    if kind == "role" and "ping_roles" in cfg and val in cfg["ping_roles"]:
        cfg["ping_roles"].remove(val)
    elif kind in ("user", "id") and "ping_users" in cfg and val in cfg["ping_users"]:
        cfg["ping_users"].remove(val)
    set_config(interaction.guild.id, cfg)
    await interaction.response.send_message("Removed from ping list.", ephemeral=True)

# --------------------
# Wipe commands
# --------------------

@bot.tree.command(name="wipeticketstatus", description="Wipe ticket records for a user (keeps config)")
@app_commands.describe(user="User to wipe tickets for")
async def wipeticketstatus(interaction: discord.Interaction, user: discord.Member):
    if not is_manager(interaction.user, get_config(interaction.guild.id)):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    tickets = get_tickets(interaction.guild.id)
    removed = 0
    for tid in list(tickets.keys()):
        if tickets[tid].get("owner") == user.id:
            tickets.pop(tid, None)
            removed += 1
    save_tickets(interaction.guild.id, tickets)
    await interaction.response.send_message(f"Removed {removed} ticket records for {user}.", ephemeral=True)

@bot.tree.command(name="wipeconfig", description="Wipe ticket configuration for this server (keeps tickets)")
async def wipeconfig(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admin only.", ephemeral=True)
        return
    set_config(interaction.guild.id, {})
    await interaction.response.send_message("Configuration wiped (tickets retained).", ephemeral=True)

# --------------------
# History command
# --------------------

@bot.tree.command(name="history", description="Show ticket history for a user")
@app_commands.describe(user="User to view tickets")
async def history(interaction: discord.Interaction, user: discord.Member):
    tickets = get_tickets(interaction.guild.id)
    # Use items() and cast owner to int for robust comparison
    user_tickets = [(tid, rec) for tid, rec in tickets.items() if int(rec.get("owner", 0)) == user.id]
    if not user_tickets:
        await interaction.response.send_message("No tickets found.", ephemeral=True)
        return
    if len(user_tickets) <= 5:
        embed = discord.Embed(title=f"Ticket history: {user}")
        for tid, rec in user_tickets:
            embed.add_field(name=f"Ticket {tid} ({rec.get('created_at')})", value=rec.get("message", "No message"), inline=False)
        await interaction.response.send_message(embed=embed)
        return
    lines = []
    for tid, rec in user_tickets:
        lines.append(f"ID: {tid} | Channel: {rec.get('channel')} | Created: {rec.get('created_at')}\n"
                     f"Message: {rec.get('message')}\n---\n")
    filename = f"{user.id}_tickets.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.writelines(lines)
    await interaction.response.send_message(file=discord.File(filename))
    os.remove(filename)

# --------------------
# Close commands
# --------------------

@bot.tree.command(name="close", description="Close this ticket (owner or allowed roles)")
@app_commands.describe(resolution="Optional resolution message to DM the ticket owner")
async def slash_close(interaction: discord.Interaction, resolution: str = None):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    if not guild:
        await interaction.followup.send("This command must be used in a server.", ephemeral=True)
        return
    cfg = get_config(guild.id)
    tickets = get_tickets(guild.id)
    ticket_id = None
    for tid, rec in tickets.items():
        if rec.get("channel") == interaction.channel.id:
            ticket_id = tid
            break
    if not ticket_id:
        await interaction.followup.send("This channel is not a tracked ticket.", ephemeral=True)
        return
    rec = tickets[ticket_id]
    owner_id = rec.get("owner")
    if interaction.user.id != owner_id and not is_manager(interaction.user, cfg):
        await interaction.followup.send("You don't have permission to close this ticket.", ephemeral=True)
        return
    try:
        owner = guild.get_member(owner_id)
        if resolution and owner:
            await owner.send(f"Your ticket `{ticket_id}` was closed. Resolution: {resolution}")
    except Exception:
        pass
    try:
        await interaction.channel.delete()
    except Exception:
        pass
    tickets.pop(ticket_id, None)
    save_tickets(guild.id, tickets)
    await interaction.followup.send("Ticket closed and channel deleted.", ephemeral=True)

# --------------------
# Close all & reset
# --------------------

class ResetConfirmView(discord.ui.View):
    def __init__(self, author: discord.User):
        super().__init__(timeout=30)
        self.author = author
        self.value = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # only allow the command invoker to interact with this view
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("You cannot confirm this reset.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.stop()
        await interaction.response.defer()


@bot.tree.command(name="reseteverything", description="Delete all tickets and config for this server (confirmation required)")
async def slash_reset(interaction: discord.Interaction):
    # keep permission model: admin only
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admin only.", ephemeral=True)
        return

    embed = discord.Embed(
        title="⚠ Reset Confirmation",
        description="This will **delete ALL tickets and config** for this server.\n\nClick Confirm to proceed or Cancel to abort.",
        color=discord.Color.red()
    )

    view = ResetConfirmView(interaction.user)
    await interaction.response.send_message(embed=embed, view=view)

    # wait for user to click a button or timeout
    await view.wait()

    # if no interaction within timeout
    if view.value is None:
        try:
            await interaction.edit_original_response(content="❌ Reset cancelled (timed out).", embed=None, view=None)
        except Exception:
            pass
        return

    if view.value:
        # perform reset
        tickets = get_tickets(interaction.guild.id)
        for rec in list(tickets.values()):
            ch = interaction.guild.get_channel(rec.get("channel"))
            try:
                if ch:
                    await ch.delete()
            except Exception:
                pass
        save_tickets(interaction.guild.id, {})
        set_config(interaction.guild.id, {})
        try:
            await interaction.edit_original_response(content="✅ Reset complete.", embed=None, view=None)
        except Exception:
            pass
    else:
        try:
            await interaction.edit_original_response(content="❌ Reset cancelled.", embed=None, view=None)
        except Exception:
            pass

# --------------------
# Setup command
# --------------------

@bot.tree.command(name="setup", description="Configure ticket system for this server")
@app_commands.describe(ticket_channel="Channel to post the ticket button in",
                       allowed_roles="Comma-separated mentions/ids for roles/users allowed to manage tickets (optional)",
                       ping_roles="Comma-separated mentions/ids for roles/users to ping when a ticket opens (optional)",
                       ticket_limit="Monthly ticket limit per user (default 5)")
async def slash_setup(interaction: discord.Interaction, ticket_channel: discord.TextChannel, allowed_roles: str = "", ping_roles: str = "", ticket_limit: int = 5):
    if not interaction.user.guild_permissions.manage_guild and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You must be a server manager/admin to setup the ticket system.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    cfg = get_config(interaction.guild.id)
    cfg["ticket_channel"] = ticket_channel.id
    cfg["ticket_limit"] = max(1, ticket_limit)
    # parse lists
    allowed_roles_list = []
    allowed_users_list = []
    for raw in [x.strip() for x in allowed_roles.split(",") if x.strip()]:
        p = parse_mention(raw)
        if not p:
            continue
        kind, val = p
        if kind == "role":
            allowed_roles_list.append(val)
        elif kind in ("user", "id"):
            allowed_users_list.append(val)
    ping_roles_list = []
    ping_users_list = []
    for raw in [x.strip() for x in ping_roles.split(",") if x.strip()]:
        p = parse_mention(raw)
        if not p:
            continue
        kind, val = p
        if kind == "role":
            ping_roles_list.append(val)
        elif kind in ("user", "id"):
            ping_users_list.append(val)
    cfg["allowed_roles"] = allowed_roles_list
    cfg["allowed_users"] = allowed_users_list
    cfg["ping_roles"] = ping_roles_list
    cfg["ping_users"] = ping_users_list
    # post message
    try:
        embed = discord.Embed(title="Open a Ticket", description="Click the button below to open a ticket.", color=discord.Color.blue())
        sent = await ticket_channel.send(embed=embed, view=TicketOpenView(interaction.guild.id))
        cfg["ticket_message_id"] = sent.id
    except Exception as e:
        print("Failed to post ticket embed during setup:", e)
    set_config(interaction.guild.id, cfg)
    # sync commands for the guild immediately so everything appears
    try:
        await bot.tree.sync(guild=discord.Object(id=interaction.guild.id))
    except Exception:
        pass
    await interaction.followup.send("Ticket system configured.", ephemeral=True)

# --------------------
# Run
# --------------------
from dotenv import load_dotenv
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("DISCORD_TOKEN is not set. Please configure it in a .env file.")
    bot.run(TOKEN)
