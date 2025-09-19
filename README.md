# Discord Ticket Bot 🎟️

A fully featured Discord ticket system bot with:
- Persistent ticket channels
- Slash commands for configuration
- Role and user-based permissions
- Ticket limits and history tracking
- Reset and wipe commands
- Easy setup and deployment

---

## 📦 Features
- Create tickets via **button + modal**
- Configure allowed staff roles and ping roles
- Automatic ticket channel creation with permissions
- Monthly ticket limits per user
- Commands to view history, close, or wipe tickets
- Full reset with confirmation
- Persistent button & commands synced on startup

---

## ⚙️ Installation

### 1. Clone the repository
```bash
git clone https://github.com/your-username/discord-ticket-bot.git
cd discord-ticket-bot
````

### 2. Create a virtual environment (optional but recommended)

```bash
python -m venv venv
source venv/bin/activate   # Linux/Mac
venv\Scripts\activate      # Windows
```

### 3. Install requirements

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root:

```
DISCORD_TOKEN=your_discord_bot_token_here
```

⚠️ Never commit your real `.env` file to GitHub!

---

## ▶️ Usage

Run the bot:

```bash
python main.py
```

---

## 🛠️ Commands

### Setup

```
/setup ticket_channel:<#channel> allowed_roles:@Role,@User ping_roles:@Role ticket_limit:5
```

### Admin Utilities

* `/synccommands` → force sync commands
* `/wipeconfig` → reset config only
* `/wipeticketstatus` → wipe tickets for a user
* `/reseteverything` → delete all tickets + config

### Ticket Commands

* `/close` → close a ticket channel
* `/history` → show a user’s ticket history
* `/addallowedrole` / `/removeallowedrole`
* `/addpingedrole` / `/removepingedrole`

---

## 📁 Project Structure

```
discord-ticket-bot/
│── main.py
│── requirements.txt
│── .env.example
│── .gitignore
│── README.md
│── config.json   # auto-created, do not edit manually
│── tickets.json  # auto-created, stores ticket data
