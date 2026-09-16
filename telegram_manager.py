import os
import io
import sys
import asyncio
import warnings
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import pandas as pd
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

# Import your untouched 4 scripts
import ema_primary_screener
import intraday_scanner
import nifty50_scanner
import market_verifier

warnings.filterwarnings("ignore")

# =====================================================================
# TIMEZONE & BOT CONFIGURATION
# =====================================================================
IST = ZoneInfo("Asia/Kolkata")
BOT_TOKEN = "8897532222:AAGWH2mMg3IUgY5U-xf5i0SF2QK3p37M5ak"
ADMIN_CHAT_ID = "8384111089"
SUBSCRIBERS_FILE = "subscribers.txt"

WAITING_CUSTOM_CAPITAL = 1

# =====================================================================
# DUMMY WEB SERVER (KEEPS RENDER FREE SERVICE HEALTHY & ONLINE)
# =====================================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Nifty 50 Bot is Online and Healthy!")

    def log_message(self, format, *args):
        # Silence HTTP access logs to keep bot terminal clean
        return

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

# =====================================================================
# SUBSCRIBER DATABASE ENGINE
# =====================================================================
def load_subscribers():
    subs = set()
    subs.add(str(ADMIN_CHAT_ID))
    if os.path.exists(SUBSCRIBERS_FILE):
        with open(SUBSCRIBERS_FILE, "r") as f:
            for line in f:
                cid = line.strip()
                if cid:
                    subs.add(cid)
    return list(subs)

def save_subscriber(chat_id):
    subs = load_subscribers()
    if str(chat_id) not in subs:
        with open(SUBSCRIBERS_FILE, "a") as f:
            f.write(f"{chat_id}\n")
        return True
    return False

# =====================================================================
# TELEGRAM SAFE DISPATCH
# =====================================================================
async def send_chunked_message(bot, chat_id, text, reply_markup=None):
    max_len = 3800
    if len(text) <= max_len:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=reply_markup)
        return

    lines = text.split("\n")
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > max_len:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="Markdown")
            chunk = line + "\n"
        else:
            chunk += line + "\n"

    if chunk.strip():
        await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="Markdown", reply_markup=reply_markup)

# =====================================================================
# PARSER ENGINE: READ ALL 3 GENERATED EXCELS
# =====================================================================
def get_actionable_signals():
    setups = {}

    if os.path.exists("nifty50_ema_primary_scan.xlsx"):
        try:
            df1 = pd.read_excel("nifty50_ema_primary_scan.xlsx")
            for _, r in df1[df1['Final'].isin(['BUY', 'SELL'])].iterrows():
                sym = str(r['Symbol']).strip().upper()
                rule = str(r.get('Confirmation_Rule', ''))
                pdh, pdl = 0.0, 0.0
                if "PDH" in rule:
                    try:
                        pdh = float(rule.split("PDH")[1].split(".")[0].strip().split()[0])
                    except Exception:
                        pass
                if "PDL" in rule:
                    try:
                        pdl = float(rule.split("PDL")[1].split(".")[0].strip().split()[0])
                    except Exception:
                        pass
                setups[sym] = {
                    'Symbol': sym, 'Signal': str(r['Final']).strip().upper(),
                    'Source': 'EMA Primary', 'PDH': pdh, 'PDL': pdl,
                    'Close': float(r.get('HA_Close', 0.0)) if pd.notnull(r.get('HA_Close')) else 0.0
                }
        except Exception as e:
            print(f"Error parsing File 1: {e}")

    if os.path.exists("nifty50_intraday_scan.xlsx"):
        try:
            df2 = pd.read_excel("nifty50_intraday_scan.xlsx")
            for _, r in df2[df2['Final'].isin(['BUY', 'SELL'])].iterrows():
                sym = str(r['Symbol']).strip().upper()
                if sym not in setups:
                    setups[sym] = {
                        'Symbol': sym, 'Signal': str(r['Final']).strip().upper(),
                        'Source': 'Intraday 15m', 'PDH': 0.0, 'PDL': 0.0,
                        'Close': float(r.get('HA_Close', 0.0)) if pd.notnull(r.get('HA_Close')) else 0.0
                    }
        except Exception as e:
            print(f"Error parsing File 2: {e}")

    if os.path.exists("nifty50_scan_result.xlsx"):
        try:
            df3 = pd.read_excel("nifty50_scan_result.xlsx")
            for _, r in df3[df3['Final'].isin(['BUY', 'SELL'])].iterrows():
                sym = str(r['Symbol']).strip().upper()
                if sym not in setups:
                    setups[sym] = {
                        'Symbol': sym, 'Signal': str(r['Final']).strip().upper(),
                        'Source': 'Multi-TF 15m/1h/4h', 'PDH': 0.0, 'PDL': 0.0,
                        'Close': float(r.get('HA_Close', 0.0)) if pd.notnull(r.get('HA_Close')) else 0.0
                    }
        except Exception as e:
            print(f"Error parsing File 3: {e}")

    return setups

# =====================================================================
# CALCULATION ENGINES (PLANS, SIMULATOR, CONFLUENCE)
# =====================================================================
def generate_trade_plan_text():
    setups = get_actionable_signals()
    if not setups:
        return "⚠️ *No active trade candidates found.* Run the scans first or wait for the 8:45 AM pre-market scan."

    tickers = [f"{s}.NS" for s in setups.keys()]
    data = yf.download(tickers=tickers, period="1d", interval="1m", progress=False)

    text = "🎯 *ACTIONABLE TRADE PLANS (ENTRY, SL, TP1, TP2)*\n"
    text += f"🕒 `{datetime.now(IST).strftime('%d-%b-%Y %I:%M:%S %p IST')}`\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

    for sym, plan in setups.items():
        ns_sym = f"{sym}.NS"
        try:
            if len(tickers) == 1:
                cmp = float(data['Close'].dropna().iloc[-1])
            else:
                cmp = float(data['Close'][ns_sym].dropna().iloc[-1])
        except Exception:
            cmp = float(plan['Close'])

        if cmp <= 0:
            continue

        sig = plan['Signal']
        if sig == "BUY":
            entry = round(plan['PDH'] if plan['PDH'] > 0 else cmp, 2)
            sl = round(plan['PDL'] if plan['PDL'] > 0 else (entry * 0.992), 2)
            risk = round(abs(entry - sl), 2)
            if risk <= 0:
                risk = round(entry * 0.008, 2)
                sl = round(entry - risk, 2)
            tp1 = round(entry + (risk * 1.5), 2)
            tp2 = round(entry + (risk * 2.0), 2)

            text += (
                f"\n🟢 *LONG / BUY: {sym}*\n"
                f"   • *CMP:* ₹{round(cmp, 2)}\n"
                f"   • *Entry Trigger:* ₹{entry} (Breakout above PDH)\n"
                f"   • *Stop Loss (SL):* ₹{sl} (-{round((risk/entry)*100, 2)}%)\n"
                f"   • *Target 1 (1:1.5 RR):* ₹{tp1} (+{round(((tp1-entry)/entry)*100, 2)}%)\n"
                f"   • *Target 2 (1:2.0 RR):* ₹{tp2} (+{round(((tp2-entry)/entry)*100, 2)}%)\n"
                f"   • *Risk/Share:* ₹{risk}\n"
                f"   ─────────────────────────\n"
            )
        else:
            entry = round(plan['PDL'] if plan['PDL'] > 0 else cmp, 2)
            sl = round(plan['PDH'] if plan['PDH'] > 0 else (entry * 1.008), 2)
            risk = round(abs(sl - entry), 2)
            if risk <= 0:
                risk = round(entry * 0.008, 2)
                sl = round(entry + risk, 2)
            tp1 = round(entry - (risk * 1.5), 2)
            tp2 = round(entry - (risk * 2.0), 2)

            text += (
                f"\n🔴 *SHORT / SELL: {sym}*\n"
                f"   • *CMP:* ₹{round(cmp, 2)}\n"
                f"   • *Entry Trigger:* ₹{entry} (Breakdown below PDL)\n"
                f"   • *Stop Loss (SL):* ₹{sl} (+{round((risk/entry)*100, 2)}%)\n"
                f"   • *Target 1 (1:1.5 RR):* ₹{tp1} (+{round(((entry-tp1)/entry)*100, 2)}%)\n"
                f"   • *Target 2 (1:2.0 RR):* ₹{tp2} (+{round(((entry-tp2)/entry)*100, 2)}%)\n"
                f"   • *Risk/Share:* ₹{risk}\n"
                f"   ─────────────────────────\n"
            )

    return text

def calculate_capital_returns(capital_amount):
    setups = get_actionable_signals()
    if not setups:
        return "⚠️ No active trade candidates right now to simulate capital returns."

    tickers = [f"{s}.NS" for s in setups.keys()]
    data = yf.download(tickers=tickers, period="1d", interval="1m", progress=False)

    text = f"💰 *PROFIT SIMULATOR (CAPITAL: ₹{capital_amount:,.2f})*\n"
    text += f"🕒 `{datetime.now(IST).strftime('%d-%b-%Y %I:%M:%S %p IST')}`\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

    for sym, plan in setups.items():
        ns_sym = f"{sym}.NS"
        try:
            if len(tickers) == 1:
                cmp = float(data['Close'].dropna().iloc[-1])
            else:
                cmp = float(data['Close'][ns_sym].dropna().iloc[-1])
        except Exception:
            cmp = float(plan['Close'])

        if cmp <= 0:
            continue

        sig = plan['Signal']
        entry = cmp
        risk_per_share = round(entry * 0.008, 2)
        sl = round(entry - risk_per_share, 2) if sig == "BUY" else round(entry + risk_per_share, 2)
        tp1 = round(entry + (risk_per_share * 1.5), 2) if sig == "BUY" else round(entry - (risk_per_share * 1.5), 2)
        tp2 = round(entry + (risk_per_share * 2.0), 2) if sig == "BUY" else round(entry - (risk_per_share * 2.0), 2)

        qty = int(capital_amount // entry)
        if qty == 0:
            continue

        invested = round(qty * entry, 2)
        p_tp1 = round(qty * abs(tp1 - entry), 2)
        p_tp2 = round(qty * abs(tp2 - entry), 2)
        l_sl = round(qty * abs(entry - sl), 2)

        text += (
            f"\n📊 *{sym}* [{sig}]\n"
            f"   • *Invested:* ₹{invested:,.2f} ({qty} Qty @ CMP ₹{entry})\n"
            f"   • 🟢 *Profit @ TP1 (1:1.5):* `+₹{p_tp1:,.2f}` (+{round((p_tp1/invested)*100, 2)}%)\n"
            f"   • 🚀 *Profit @ TP2 (1:2.0):* `+₹{p_tp2:,.2f}` (+{round((p_tp2/invested)*100, 2)}%)\n"
            f"   • 🔴 *Max Loss @ SL:* `-₹{l_sl:,.2f}` (-{round((l_sl/invested)*100, 2)}%)\n"
            f"   ─────────────────────────\n"
        )

    return text

def get_multi_scanner_confluence():
    f1 = "nifty50_ema_primary_scan.xlsx"
    f2 = "nifty50_intraday_scan.xlsx"
    f3 = "nifty50_scan_result.xlsx"

    scores = {}
    for path, name in [(f1, "Script 1"), (f2, "Script 2"), (f3, "Script 3")]:
        if os.path.exists(path):
            try:
                df = pd.read_excel(path)
                for _, r in df[df['Final'].isin(['BUY', 'SELL'])].iterrows():
                    s = str(r['Symbol']).strip().upper()
                    act = str(r['Final']).strip().upper()
                    if s not in scores:
                        scores[s] = {'BUY': 0, 'SELL': 0, 'Sources': []}
                    scores[s][act] += 1
                    scores[s]['Sources'].append(f"{name} ({act})")
            except Exception:
                pass

    text = "🔥 *HIGH CONFLUENCE SIGNALS (MULTIPLE SCANNERS AGREE)*\n"
    text += f"🕒 `{datetime.now(IST).strftime('%d-%b-%Y %I:%M:%S %p IST')}`\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

    found = False
    for sym, item in scores.items():
        total = max(item['BUY'], item['SELL'])
        if total >= 2:
            found = True
            direction = "BUY" if item['BUY'] >= item['SELL'] else "SELL"
            text += (
                f"\n⭐ *{sym}* ➔ *{direction}* ({total}/3 Scanners Agree!)\n"
                f"   • Confirmed By: {', '.join(item['Sources'])}\n"
            )

    if not found:
        text += "No multi-scanner confluence found yet. Check individual setups under Trade Plans."

    return text

# =====================================================================
# SCRIPT RUNNERS
# =====================================================================
async def run_script_1_detailed(bot, chat_id):
    await bot.send_message(chat_id=chat_id, text="⏳ Running *ema_primary_screener.py*...", parse_mode="Markdown")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, ema_primary_screener.screen_market)

    df = pd.read_excel("nifty50_ema_primary_scan.xlsx")
    buys = df[df['Final'] == 'BUY']
    sells = df[df['Final'] == 'SELL']

    msg = f"⚡ *DETAILED REPORT: SCRIPT 1 (EMA PRIMARY)*\n🕒 `{datetime.now(IST).strftime('%I:%M:%S %p IST')}`\n━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"🟢 *BUY SIGNALS ({len(buys)})*\n"
    for _, r in buys.iterrows():
        msg += f"• *{r['Symbol']}*: EMA=`{r['EMA5']}/{r['EMA13']}/{r['EMA26']}` | Stoch=`{r['Stoch']}` | MACD=`{r['MACD']}`\n  🎯 {r['Confirmation_Rule']}\n\n"
    msg += f"🔴 *SELL SIGNALS ({len(sells)})*\n"
    for _, r in sells.iterrows():
        msg += f"• *{r['Symbol']}*: EMA=`{r['EMA5']}/{r['EMA13']}/{r['EMA26']}` | Stoch=`{r['Stoch']}` | MACD=`{r['MACD']}`\n  🎯 {r['Confirmation_Rule']}\n\n"

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📥 Download Excel 1", callback_data="dl_s1")]])
    await send_chunked_message(bot, chat_id, msg, reply_markup=kb)

async def run_script_2_detailed(bot, chat_id):
    await bot.send_message(chat_id=chat_id, text="⏳ Running *intraday_scanner.py*...", parse_mode="Markdown")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, intraday_scanner.scan_nifty50)

    df = pd.read_excel("nifty50_intraday_scan.xlsx")
    buys = df[df['Final'] == 'BUY']
    sells = df[df['Final'] == 'SELL']

    msg = f"📊 *DETAILED REPORT: SCRIPT 2 (INTRADAY 15M)*\n🕒 `{datetime.now(IST).strftime('%I:%M:%S %p IST')}`\n━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"🟢 *BUY SIGNALS ({len(buys)})*\n"
    for _, r in buys.iterrows():
        msg += f"• *{r['Symbol']}*: {r['Confirmation']}\n"
    msg += f"\n🔴 *SELL SIGNALS ({len(sells)})*\n"
    for _, r in sells.iterrows():
        msg += f"• *{r['Symbol']}*: {r['Confirmation']}\n"

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📥 Download Excel 2", callback_data="dl_s2")]])
    await send_chunked_message(bot, chat_id, msg, reply_markup=kb)

async def run_script_3_detailed(bot, chat_id):
    await bot.send_message(chat_id=chat_id, text="⏳ Running *nifty50_scanner.py*...", parse_mode="Markdown")
    loop = asyncio.get_running_loop()
    def r3():
        res = nifty50_scanner.run_scan()
        nifty50_scanner.write_excel(res)
    await loop.run_in_executor(None, r3)

    df = pd.read_excel("nifty50_scan_result.xlsx")
    buys = df[df['Final'] == 'BUY']
    sells = df[df['Final'] == 'SELL']

    msg = f"🌐 *DETAILED REPORT: SCRIPT 3 (MULTI-TF)*\n🕒 `{datetime.now(IST).strftime('%I:%M:%S %p IST')}`\n━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"🟢 *BUY SIGNALS ({len(buys)})*\n"
    for _, r in buys.iterrows():
        msg += f"• *{r['Symbol']}*: 15m EMA=`{r['EMA']}` | Stoch=`{r['Stoch']}` | 1h MACD=`{r['MACD']}` | 4h HA=`{r['HeikinAshi']}`\n"
    msg += f"\n🔴 *SELL SIGNALS ({len(sells)})*\n"
    for _, r in sells.iterrows():
        msg += f"• *{r['Symbol']}*: 15m EMA=`{r['EMA']}` | Stoch=`{r['Stoch']}` | 1h MACD=`{r['MACD']}` | 4h HA=`{r['HeikinAshi']}`\n"

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📥 Download Excel 3", callback_data="dl_s3")]])
    await send_chunked_message(bot, chat_id, msg, reply_markup=kb)

async def run_verifier_detailed(bot, chat_id):
    await bot.send_message(chat_id=chat_id, text="🔍 Running *market_verifier.py* (Live CMP & PnL)...", parse_mode="Markdown")
    loop = asyncio.get_running_loop()
    buf = io.StringIO()
    old_out = sys.stdout
    try:
        sys.stdout = buf
        await loop.run_in_executor(None, market_verifier.verify_different_folders)
    finally:
        sys.stdout = old_out
    out = buf.getvalue().strip() or "No active trade signals found to verify."
    await send_chunked_message(bot, chat_id, f"📈 *LIVE MARKET VERIFIER REPORT*\n```\n{out}\n```")

async def run_all_three_for_user(bot, chat_id):
    await bot.send_message(chat_id=chat_id, text="🚀 *Starting All 3 Scanners in Sequence...*", parse_mode="Markdown")
    await run_script_1_detailed(bot, chat_id)
    await run_script_2_detailed(bot, chat_id)
    await run_script_3_detailed(bot, chat_id)
    plan_msg = generate_trade_plan_text()
    await send_chunked_message(bot, chat_id, plan_msg)

# =====================================================================
# UI MENU
# =====================================================================
def get_main_menu():
    keyboard = [
        [
            InlineKeyboardButton("🔔 ADD ME IN DAILY 8:45 AM ALERT", callback_data="btn_subscribe")
        ],
        [
            InlineKeyboardButton("⚡ Run 1: EMA Screener", callback_data="run_s1"),
            InlineKeyboardButton("📊 Run 2: Intraday 15M", callback_data="run_s2")
        ],
        [
            InlineKeyboardButton("🌐 Run 3: Multi-TF Scan", callback_data="run_s3"),
            InlineKeyboardButton("🚀 Run ALL 3 Scanners", callback_data="run_all")
        ],
        [
            InlineKeyboardButton("🎯 Trade Plans (Entry, SL, TP)", callback_data="btn_plans"),
            InlineKeyboardButton("🔥 High Confluence Setups", callback_data="btn_confluence")
        ],
        [
            InlineKeyboardButton("💰 Investment & Profit Calculator", callback_data="btn_calc_prompt"),
            InlineKeyboardButton("🔍 Live Market Verifier (PnL)", callback_data="run_verifier")
        ],
        [
            InlineKeyboardButton("📥 Download Excels", callback_data="menu_downloads"),
            InlineKeyboardButton("🖥 System Status", callback_data="btn_status")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def send_file(bot, chat_id, filepath):
    if os.path.exists(filepath):
        with open(filepath, 'rb') as f:
            await bot.send_document(chat_id=chat_id, document=f, filename=os.path.basename(filepath))
    else:
        await bot.send_message(chat_id=chat_id, text=f"⚠️ File `{filepath}` not found. Run the scan first.")

# =====================================================================
# HANDLERS & CALLBACK ROUTING
# =====================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = (
        f"👋 Hello *{user.first_name}*!\n\n"
        "🤖 *NIFTY 50 TRADING COMMAND CENTER*\n"
        "• Automatically scans Nifty 50 before market opens.\n"
        "• Gives precise Entry, Stop Loss, and Take Profit levels.\n\n"
        "👉 *Tap the top button below to get automatic morning alerts delivered to your chat!*"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_menu())

async def main_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user = query.from_user
    data = query.data

    if data == "btn_subscribe":
        is_new = save_subscriber(chat_id)
        if is_new:
            await query.message.reply_text(
                "✅ *Subscription Successful!*\n\n"
                f"Your Chat ID (`{chat_id}`) has been added to our automated distribution list.\n"
                "You will now automatically receive the full market scan, actionable watchlists, and Entry/SL/TP levels every trading day at *08:45 AM IST*.",
                parse_mode="Markdown"
            )
            try:
                username_str = f"@{user.username}" if user.username else "No Username"
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"📢 *New Subscriber Added!*\n• Name: {user.full_name}\n• User: {username_str}\n• Chat ID: `{chat_id}`",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
        else:
            await query.message.reply_text(
                "ℹ️ *You are already subscribed!* You will receive the daily 08:45 AM pre-market alert automatically.",
                parse_mode="Markdown"
            )

    elif data == "run_s1":
        await run_script_1_detailed(context.bot, chat_id)
    elif data == "run_s2":
        await run_script_2_detailed(context.bot, chat_id)
    elif data == "run_s3":
        await run_script_3_detailed(context.bot, chat_id)
    elif data == "run_all":
        await run_all_three_for_user(context.bot, chat_id)
    elif data == "run_verifier":
        await run_verifier_detailed(context.bot, chat_id)
    elif data == "btn_plans":
        plan_text = generate_trade_plan_text()
        await send_chunked_message(context.bot, chat_id, plan_text)
    elif data == "btn_confluence":
        conf_text = get_multi_scanner_confluence()
        await send_chunked_message(context.bot, chat_id, conf_text)
    elif data == "btn_calc_prompt":
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("₹25,000", callback_data="cap_25000"),
                InlineKeyboardButton("₹50,000", callback_data="cap_50000"),
                InlineKeyboardButton("₹1,00,000", callback_data="cap_100000")
            ],
            [InlineKeyboardButton("✍️ Type Custom Amount", callback_data="cap_custom")]
        ])
        await query.message.reply_text("💵 *Select an Investment Capital Amount:*", parse_mode="Markdown", reply_markup=kb)
    elif data in ["cap_25000", "cap_50000", "cap_100000"]:
        amount = float(data.replace("cap_", ""))
        report = calculate_capital_returns(amount)
        await send_chunked_message(context.bot, chat_id, report)
    elif data == "menu_downloads":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Excel 1 (EMA Primary)", callback_data="dl_s1")],
            [InlineKeyboardButton("📥 Excel 2 (Intraday 15M)", callback_data="dl_s2")],
            [InlineKeyboardButton("📥 Excel 3 (Multi-TF Scan)", callback_data="dl_s3")],
            [InlineKeyboardButton("📦 Download ALL 3 Excels", callback_data="dl_all")]
        ])
        await query.message.reply_text("📁 *Choose which Excel to download:*", parse_mode="Markdown", reply_markup=kb)
    elif data == "dl_s1":
        await send_file(context.bot, chat_id, "nifty50_ema_primary_scan.xlsx")
    elif data == "dl_s2":
        await send_file(context.bot, chat_id, "nifty50_intraday_scan.xlsx")
    elif data == "dl_s3":
        await send_file(context.bot, chat_id, "nifty50_scan_result.xlsx")
    elif data == "dl_all":
        await send_file(context.bot, chat_id, "nifty50_ema_primary_scan.xlsx")
        await send_file(context.bot, chat_id, "nifty50_intraday_scan.xlsx")
        await send_file(context.bot, chat_id, "nifty50_scan_result.xlsx")
    elif data == "btn_status":
        now_ist = datetime.now(IST).strftime('%Y-%m-%d %I:%M:%S %p IST')
        subs = load_subscribers()
        await query.message.reply_text(
            f"✅ *System Operational*\n"
            f"• Host: Render Cloud Web Service\n"
            f"• Timezone: Indian Standard Time (IST)\n"
            f"• Auto Schedule: Mon-Fri @ 08:45 AM IST\n"
            f"• Current Time: `{now_ist}`\n"
            f"• Total Active Subscribers: `{len(subs)}`",
            parse_mode="Markdown"
        )

# =====================================================================
# CUSTOM CAPITAL CONVERSATION
# =====================================================================
async def prompt_custom_capital(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("✏️ Enter your investment capital in Rupees (e.g. `75000` or `200000`):", parse_mode="Markdown")
    return WAITING_CUSTOM_CAPITAL

async def handle_custom_capital_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", "").replace("₹", "")
    try:
        amt = float(text)
        report = calculate_capital_returns(amt)
        await send_chunked_message(context.bot, update.message.chat_id, report)
    except ValueError:
        await update.message.reply_text("⚠️ Invalid number. Please enter digits only.")
    return ConversationHandler.END

# =====================================================================
# MORNING 8:45 AM AUTO-BROADCAST TO ALL SUBSCRIBERS
# =====================================================================
async def morning_auto_scan(app):
    while True:
        now = datetime.now(IST)
        # Mon-Fri at 08:45 AM IST
        if now.weekday() < 5 and now.hour == 8 and now.minute == 45:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, ema_primary_screener.screen_market)[cite: 1]
            await loop.run_in_executor(None, intraday_scanner.scan_nifty50)[cite: 2]
            
            def r3():
                res = nifty50_scanner.run_scan()[cite: 3]
                nifty50_scanner.write_excel(res)[cite: 3]
            await loop.run_in_executor(None, r3)

            plan_msg = generate_trade_plan_text()
            confluence_msg = get_multi_scanner_confluence()

            morning_package = (
                "⏰ *08:45 AM PRE-MARKET NIFTY 50 REPORT*\n"
                "All 3 strategy algorithms have finished scanning.\n\n"
                f"{confluence_msg}\n\n"
                f"{plan_msg}"
            )

            all_subscribers = load_subscribers()
            for sub_id in all_subscribers:
                try:
                    await send_chunked_message(app.bot, sub_id, morning_package)
                    await asyncio.sleep(0.1)
                except Exception as e:
                    print(f"Could not send to subscriber {sub_id}: {e}")

            await asyncio.sleep(65)
        await asyncio.sleep(20)

# =====================================================================
# APPLICATION ENTRY POINT & POST-INIT HOOK
# =====================================================================
async def post_init(application):
    asyncio.create_task(morning_auto_scan(application))

def main():
    # Start the dummy web server in a background daemon thread for Render
    web_thread = threading.Thread(target=run_health_server, daemon=True)
    web_thread.start()

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    custom_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(prompt_custom_capital, pattern="^cap_custom$")],
        states={
            WAITING_CUSTOM_CAPITAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_capital_text)]
        },
        fallbacks=[CommandHandler("start", start_command)],
        per_message=False
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(custom_conv)
    app.add_handler(CallbackQueryHandler(main_callback_router))

    print("Command Center is online. Dummy health web server active on Render.")
    app.run_polling()

if __name__ == "__main__":
    main()
