# Polymarket Wallet Tracker

Watches a list of Polymarket wallets and sends a **Discord alert** whenever one
of them places a new buy. It runs itself **every ~5 minutes on GitHub Actions** —
free, no server, and no need to keep a computer turned on.

Each alert shows:

- the **price** the wallet entered the market at
- the **dollar size** of the bet
- **how that bet compares** to that wallet's average buy size
- a **link to the market**

It only alerts on **buys** (entries), and it never alerts the same trade twice.

---

## What's in this folder

| File | What it is |
|------|------------|
| `tracker.py` | The tracker program. You don't need to touch this. |
| `wallets.json` | The list of wallets being watched. **You edit this to add/remove wallets.** |
| `config.json` | Settings: minimum bet size, the Discord role to ping. |
| `state.json` | The tracker's memory. It updates this itself — don't edit it. |
| `.github/workflows/tracker.yml` | The 5-minute schedule that runs everything. |

---

## One-time setup (about 10 minutes)

### 1. Create a GitHub account
If you don't already have one, go to [github.com](https://github.com) and sign up — it's free.

### 2. Create a new repository
- Click the **+** in the top-right of github.com, then **New repository**.
- Give it any name, for example `polymarket-tracker`.
- Choose **Public** (recommended — public repos get unlimited free run-time).
  Your Discord webhook stays private either way; see step 4.
- Click **Create repository**.

### 3. Upload these files
- On your new repo's page, click the **uploading an existing file** link.
- Drag in everything from this folder: `tracker.py`, `wallets.json`,
  `config.json`, `state.json`, `README.md`, and the `.github` folder.
- Click **Commit changes**.

### 4. Add your Discord webhook as a secret
This keeps the webhook private, even in a public repo.

- In the repo, go to **Settings** → **Secrets and variables** → **Actions**.
- Click **New repository secret**.
- **Name:** `DISCORD_WEBHOOK_URL`
- **Secret:** paste your full Discord webhook URL.
- Click **Add secret**.

### 5. Turn it on
- Go to the **Actions** tab. If GitHub asks, click the button to enable workflows.
- On the left, click **Polymarket Wallet Tracker**, then **Run workflow** to do
  a first run immediately.
- Within a minute you should see a **"Now tracking"** message appear in Discord.

Done. From now on it checks every ~5 minutes on its own.

---

## Adding or removing wallets

Edit `wallets.json` right on github.com:

1. Open `wallets.json` in your repo and click the **pencil** icon to edit.
2. Add or remove entries. The format is:

   ```json
   [
     { "address": "0xfirstwalletaddress", "label": "A nickname" },
     { "address": "0xsecondwalletaddress", "label": "" }
   ]
   ```

3. `label` is optional — leave it as `""` and the tracker fills in the wallet's
   Polymarket display name automatically.
4. Click **Commit changes**.

A newly added wallet is **baselined silently** on its first check — you won't get
a flood of its past trades, only new buys from that point on.

---

## Changing settings

Edit `config.json` the same way:

- `min_bet_usd` — only buys worth at least this many dollars trigger an alert
  (currently **100**).
- `mention` — the Discord role pinged on each alert.

---

## Good to know

- GitHub runs scheduled jobs about every 5 minutes (that's GitHub's minimum).
  Occasionally a run can be a few minutes late when GitHub is busy — that's normal.
- The tracker alerts on **buys only**, never sells.
- It will **never alert the same trade twice**.
- To check it's healthy, open the **Actions** tab — a column of green checkmarks
  means it's running fine.
- The Discord webhook URL is **never stored in these files**. It lives only in the
  GitHub secret from step 4.
