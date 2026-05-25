#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymarket Wallet Tracker  (GitHub Actions edition)
===================================================
Watches a list of Polymarket wallets and posts a Discord alert whenever one of
them makes a new BUY worth at least the configured amount. Designed to run every
~5 minutes as a GitHub Actions scheduled workflow - no server, no always-on
computer required.

Each alert shows: the entry price, the dollar size of the bet, how that bet
compares to the wallet's average buy, and a link to the market.

Files (all in the repository root)
----------------------------------
  wallets.json   - the wallets being tracked  (edit this to add/remove wallets)
  config.json    - settings: minimum bet size, the Discord role to ping, etc.
  state.json     - dedup data + running averages (auto-managed; committed each run)
  tracker.py     - this script
  .github/workflows/tracker.yml - the schedule that runs it

The Discord webhook URL is NOT stored in these files. It is kept as a GitHub
repository secret named DISCORD_WEBHOOK_URL and read from the environment, so it
stays private even in a public repository.

Run locally
-----------
  DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." python tracker.py
  python tracker.py --dry-run      # detect new buys, but post nothing
  python tracker.py --test-alert   # post one sample alert to confirm it works
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
WALLETS_PATH = os.path.join(BASE, "wallets.json")
STATE_PATH = os.path.join(BASE, "state.json")

DATA_API = "https://data-api.polymarket.com/trades"
GREEN = 0x2ECC71
BLUE = 0x3498DB
ORANGE = 0xE67E22
MAX_ALERTS_PER_WALLET = 12


def log(msg):
    print("[%s] %s" % (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"), msg))


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        log("WARN could not read %s (%s) - using default" % (os.path.basename(path), e))
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def bet_usd(t):
    """USDC value of a trade = shares * price."""
    try:
        return float(t.get("size", 0)) * float(t.get("price", 0))
    except (TypeError, ValueError):
        return 0.0


def trade_key(t):
    """A stable unique key for a single trade row."""
    return "|".join(str(t.get(k, "")) for k in
                     ("transactionHash", "asset", "side", "price", "size", "timestamp"))


def market_url(t):
    slug = t.get("eventSlug") or t.get("slug") or ""
    return ("https://polymarket.com/event/%s" % slug) if slug else "https://polymarket.com"


def price_str(p):
    """Format a 0-1 Polymarket price as cents, e.g. 0.14 -> '14c'."""
    c = p * 100.0
    if abs(c - round(c)) < 0.05:
        return u"%d¢" % round(c)
    return u"%.1f¢" % c


def short_addr(a):
    return (a[:6] + u"…" + a[-4:]) if len(a) > 12 else a


def _opener():
    proxy = (os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
             or os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY"))
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"https": proxy, "http": proxy}))
    return urllib.request.build_opener()


def fetch_trades(addr, limit):
    """Fetch a wallet's most recent trades from the Polymarket data API."""
    url = "%s?user=%s&limit=%d&takerOnly=false" % (DATA_API, addr.lower(), int(limit))
    req = urllib.request.Request(
        url, headers={"User-Agent": "PolymarketWalletTracker/1.0",
                      "Accept": "application/json"})
    last_err = None
    for attempt in range(3):
        try:
            with _opener().open(req, timeout=30) as resp:
                obj = json.loads(resp.read().decode("utf-8"))
            if not isinstance(obj, list):
                raise ValueError("API did not return a list")
            return obj
        except Exception as e:
            last_err = e
            time.sleep(2)
    raise last_err


def post_discord(webhook, content, embed, allowed_mentions, dry_run=False):
    payload = {
        "content": content or "",
        "allowed_mentions": allowed_mentions,
        "embeds": [embed] if embed else [],
    }
    if dry_run:
        log("DRY-RUN would post -> " + json.dumps(payload)[:400])
        return True
    data = json.dumps(payload).encode("utf-8")
    opener = _opener()
    for attempt in range(4):
        req = urllib.request.Request(
            webhook, data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "User-Agent": "PolymarketWalletTracker/1.0"})
        try:
            with opener.open(req, timeout=30) as resp:
                if resp.status in (200, 204):
                    return True
                log("WARN Discord returned HTTP %s" % resp.status)
                return False
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry = 2.0
                try:
                    retry = float(json.loads(e.read().decode()).get("retry_after", 2.0))
                except Exception:
                    pass
                log("Discord rate-limited, waiting %.1fs" % retry)
                time.sleep(retry + 0.5)
                continue
            body = ""
            try:
                body = e.read().decode()[:200]
            except Exception:
                pass
            log("ERROR Discord HTTP %s: %s" % (e.code, body))
            return False
        except Exception as e:
            log("ERROR posting to Discord (attempt %d): %s" % (attempt + 1, e))
            time.sleep(2)
    return False


def build_alert(t, wallet_label, avg, sample_count):
    """Build the Discord embed for a single new BUY."""
    bu = bet_usd(t)
    trader = t.get("name") or t.get("pseudonym") or wallet_label
    outcome = t.get("outcome", "?")
    title = t.get("title", "Unknown market")
    url = market_url(t)
    ps = price_str(float(t.get("price", 0) or 0))

    if sample_count > 0 and avg > 0:
        mult = bu / avg
        arrow = u"\U0001F53A" if mult >= 1 else u"\U0001F53B"
        pct = (mult - 1.0) * 100.0
        cmp_val = (u"%s **%.2fx** their average buy of **$%s**\n"
                   u"(%+.0f%% vs. their typical bet, based on %d prior buys)"
                   % (arrow, mult, format(avg, ",.0f"), pct, sample_count))
    else:
        cmp_val = u"First recorded buy for this wallet - no average yet."

    embed = {
        "title": u"\U0001F7E2 %s - new BUY" % trader,
        "url": url,
        "color": GREEN,
        "description": u"**%s**\n[View market on Polymarket](%s)" % (title, url),
        "fields": [
            {"name": "Bet size", "value": u"**$%s**" % format(bu, ",.2f"), "inline": True},
            {"name": "Entry price", "value": ps, "inline": True},
            {"name": "Outcome", "value": str(outcome), "inline": True},
            {"name": "vs. their average bet", "value": cmp_val, "inline": False},
        ],
        "footer": {"text": u"Polymarket Wallet Tracker · %s" % wallet_label},
    }
    icon = t.get("icon")
    if icon:
        embed["thumbnail"] = {"url": icon}
    ts = t.get("timestamp")
    if ts:
        try:
            embed["timestamp"] = datetime.fromtimestamp(int(ts), timezone.utc).isoformat()
        except Exception:
            pass
    return embed


def run(test_alert=False, dry_run=False):
    config = load_json(CONFIG_PATH, {})
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip() \
        or config.get("discord_webhook_url", "").strip()
    mention = config.get("mention", "")
    mention_parse = config.get("mention_parse", "roles")
    min_bet = float(config.get("min_bet_usd", 100))
    max_seen = int(config.get("max_seen_per_wallet", 2000))
    limit = int(config.get("history_fetch_limit", 100))
    if not webhook:
        log("ERROR no Discord webhook. Set the DISCORD_WEBHOOK_URL secret/env var.")
        return 1

    wallets = load_json(WALLETS_PATH, [])
    if not wallets:
        log("No wallets configured in wallets.json - nothing to do.")
        return 0

    state = load_json(STATE_PATH, {"wallets": {}})
    state.setdefault("wallets", {})

    alert_mentions = {"parse": [mention_parse]} if mention else {"parse": []}
    silent_mentions = {"parse": []}
    total_alerts = 0

    for w in wallets:
        addr = (w.get("address") or "").lower().strip()
        if not addr:
            continue
        label = w.get("label") or short_addr(addr)

        try:
            raw = fetch_trades(addr, limit)
        except Exception as e:
            log("%s: fetch failed (%s) - skipping this run" % (label, e))
            continue

        trades = [t for t in raw if (t.get("proxyWallet") or "").lower() == addr]
        trades.sort(key=lambda t: int(t.get("timestamp", 0) or 0))
        if not w.get("label"):
            for t in trades:
                if t.get("name"):
                    label = t["name"]
                    break
        if not trades:
            log("%s: no trades returned" % label)
            continue

        # ---- test mode: post the latest qualifying buy, never touch state ----
        if test_alert:
            qualifying = [t for t in trades
                          if t.get("side") == "BUY" and bet_usd(t) >= min_bet]
            if not qualifying:
                log("%s: no buy >= $%.0f available to test with" % (label, min_bet))
                continue
            t = qualifying[-1]
            others = [b for b in trades
                      if b.get("side") == "BUY" and trade_key(b) != trade_key(t)]
            avg = (sum(bet_usd(b) for b in others) / len(others)) if others else 0.0
            embed = build_alert(t, label, avg, len(others))
            content = (u"\U0001F9EA **Test alert** - your Polymarket wallet "
                       u"tracker is live. Real alerts will look like this and "
                       u"ping %s." % (mention or "the configured role"))
            ok = post_discord(webhook, content, embed, silent_mentions, dry_run)
            log("%s: test alert %s" % (label, "posted" if ok else "FAILED"))
            total_alerts += 1 if ok else 0
            continue

        ws = state["wallets"].get(addr)

        # ---- first time we see this wallet: seed a baseline, no historical spam
        if ws is None or not ws.get("initialized"):
            buys = [t for t in trades if t.get("side") == "BUY"]
            buy_sum = sum(bet_usd(t) for t in buys)
            buy_count = len(buys)
            state["wallets"][addr] = {
                "initialized": True,
                "label": label,
                "seen": [trade_key(t) for t in trades][-max_seen:],
                "buy_count": buy_count,
                "buy_sum": buy_sum,
                "last_run_utc": datetime.now(timezone.utc).isoformat(),
                "last_trade_ts": max((int(t.get("timestamp", 0) or 0)
                                      for t in trades), default=0),
            }
            avg = (buy_sum / buy_count) if buy_count else 0.0
            embed = {
                "title": u"\U0001F4E1 Now tracking %s" % label,
                "color": BLUE,
                "description": (u"Wallet `%s` is being monitored.\n"
                                u"Baseline from the last %d trades: **%d buys**, "
                                u"average buy size **$%s**.\n"
                                u"You'll be alerted on every new buy of **$%s+**."
                                % (addr, len(trades), buy_count,
                                   format(avg, ",.0f"), format(min_bet, ",.0f"))),
                "footer": {"text": "Polymarket Wallet Tracker"},
            }
            post_discord(webhook, "", embed, silent_mentions, dry_run)
            log("%s: initialized (%d trades, %d buys, avg $%s)"
                % (label, len(trades), buy_count, format(avg, ",.0f")))
            continue

        # ---- normal run: find genuinely new trades ----
        seen = set(ws.get("seen", []))
        new = [t for t in trades if trade_key(t) not in seen]
        new.sort(key=lambda t: int(t.get("timestamp", 0) or 0))

        alerts = []
        for t in new:
            if t.get("side") == "BUY":
                bu = bet_usd(t)
                ws["buy_count"] = ws.get("buy_count", 0) + 1
                ws["buy_sum"] = ws.get("buy_sum", 0.0) + bu
                if bu >= min_bet:
                    alerts.append(t)

        if len(alerts) > MAX_ALERTS_PER_WALLET:
            big = sorted(alerts, key=bet_usd, reverse=True)[:10]
            lines = u"\n".join(
                u"- $%s on %s (%s)"
                % (format(bet_usd(t), ",.0f"), t.get("title", "?"), t.get("outcome", "?"))
                for t in big)
            embed = {
                "title": u"\U0001F7E0 %s - %d new buys detected" % (label, len(alerts)),
                "color": ORANGE,
                "description": u"Largest new buys:\n%s" % lines,
                "footer": {"text": "Polymarket Wallet Tracker"},
            }
            ok = post_discord(webhook, mention, embed, alert_mentions, dry_run)
            total_alerts += 1 if ok else 0
            log("%s: %d new buys (capped) - posted summary" % (label, len(alerts)))
        else:
            for t in alerts:
                prior_count = ws.get("buy_count", 0) - 1
                prior_sum = ws.get("buy_sum", 0.0) - bet_usd(t)
                avg = (prior_sum / prior_count) if prior_count > 0 else 0.0
                embed = build_alert(t, label, avg, prior_count)
                if post_discord(webhook, mention, embed, alert_mentions, dry_run):
                    total_alerts += 1
                time.sleep(0.6)
            if new:
                log("%s: %d new trade(s), %d alert(s) posted"
                    % (label, len(new), len(alerts)))
            else:
                log("%s: no new trades" % label)

        ws["seen"] = (ws.get("seen", []) + [trade_key(t) for t in new])[-max_seen:]
        ws["label"] = label
        ws["last_run_utc"] = datetime.now(timezone.utc).isoformat()
        ws["last_trade_ts"] = max(int(t.get("timestamp", 0) or 0) for t in trades)

    if not test_alert and not dry_run:
        save_json(STATE_PATH, state)
    log("Run complete - %d alert(s) posted." % total_alerts)
    return 0


if __name__ == "__main__":
    flags = set(a.lower() for a in sys.argv[1:])
    sys.exit(run("--test-alert" in flags, "--dry-run" in flags))
