"""
Nifty50 Multi-Indicator Intraday Scanner
==========================================
Automates your 4-indicator confluence strategy across all Nifty50 stocks.

WHAT IT DOES:
  1. Pulls 15-min, 1-hour, and 4-hour candles for all 50 Nifty stocks
  2. Calculates EMA(5,13,26), Stochastic(4), MACD(12,26,9), Heikin Ashi
  3. Applies your "Full Signal" rule -> BUY / SELL / SKIP
  4. Writes everything into an Excel file automatically (color coded)

SETUP (run once in a terminal / command prompt):
    pip install yfinance pandas numpy openpyxl

HOW TO RUN:
    python nifty50_scanner.py

OUTPUT:
    nifty50_scan_result.xlsx  (created in the same folder, overwritten each run)

NOTE ON THE STOCK LIST:
    Nifty50 constituents change occasionally (index rebalancing, ~2x/year).
    The list below is current as of writing. If a symbol errors out, it may
    have been removed/renamed - just delete or update it in NIFTY50 below.
"""

import time
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import yfinance as yf
from openpyxl.styles import PatternFill, Font
from openpyxl.utils import get_column_letter

# ----------------------------------------------------------------------
# 1. NIFTY 50 STOCK LIST (Yahoo Finance tickers use .NS suffix for NSE)
# ----------------------------------------------------------------------
NIFTY50 = [
    "ADANIENT.NS", "ADANIPORTS.NS", "APOLLOHOSP.NS", "ASIANPAINT.NS", "AXISBANK.NS",
    "BAJAJ-AUTO.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS", "BEL.NS", "BHARTIARTL.NS",
    "CIPLA.NS", "COALINDIA.NS", "DRREDDY.NS", "EICHERMOT.NS", "GRASIM.NS",
    "HCLTECH.NS", "HDFCBANK.NS", "HDFCLIFE.NS", "HEROMOTOCO.NS", "HINDALCO.NS",
    "HINDUNILVR.NS", "ICICIBANK.NS", "ITC.NS", "INDUSINDBK.NS", "INFY.NS",
    "JSWSTEEL.NS", "KOTAKBANK.NS", "LT.NS", "M&M.NS", "MARUTI.NS",
    "NESTLEIND.NS", "NTPC.NS", "ONGC.NS", "POWERGRID.NS", "RELIANCE.NS",
    "SBILIFE.NS", "SHRIRAMFIN.NS", "SBIN.NS", "SUNPHARMA.NS", "TCS.NS",
    "TATACONSUM.NS", "TMPV.NS", "TATASTEEL.NS", "TECHM.NS", "TITAN.NS",
    "TRENT.NS", "ULTRACEMCO.NS", "WIPRO.NS", "LTIM.NS", "BPCL.NS"
]

# ----------------------------------------------------------------------
# 2. INDICATOR FUNCTIONS
# ----------------------------------------------------------------------

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def stochastic(df, period=4, smooth_k=3, smooth_d=3):
    low_min = df["Low"].rolling(period).min()
    high_max = df["High"].rolling(period).max()
    raw_k = 100 * (df["Close"] - low_min) / (high_max - low_min)
    k = raw_k.rolling(smooth_k).mean()
    d = k.rolling(smooth_d).mean()
    return k, d

def macd(series, fast=12, slow=26, signal=9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line

def heikin_ashi(df):
    ha = pd.DataFrame(index=df.index)
    ha["HA_Close"] = (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4
    ha_open = [(df["Open"].iloc[0] + df["Close"].iloc[0]) / 2]
    for i in range(1, len(df)):
        ha_open.append((ha_open[i - 1] + ha["HA_Close"].iloc[i - 1]) / 2)
    ha["HA_Open"] = ha_open
    ha["HA_High"] = pd.concat([df["High"], ha["HA_Open"], ha["HA_Close"]], axis=1).max(axis=1)
    ha["HA_Low"] = pd.concat([df["Low"], ha["HA_Open"], ha["HA_Close"]], axis=1).min(axis=1)
    return ha

# ----------------------------------------------------------------------
# 3. SIGNAL LOGIC FOR ONE STOCK
# ----------------------------------------------------------------------

def download_with_retry(symbol, period, interval, tries=4, delay=3):
    """Retries a download up to `tries` times with increasing delay - handles
    Yahoo Finance rate-limiting after many back-to-back requests, so a stock
    isn't wrongly marked as unavailable when it's just being throttled."""
    for attempt in range(tries):
        df = yf.download(symbol, period=period, interval=interval, progress=False)
        if not df.empty:
            # Newer yfinance versions return MultiIndex columns
            # (e.g. ('Close','RELIANCE.NS')) even for a single symbol.
            # Flatten to plain 'Close', 'Open', etc. so comparisons work.
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df
        time.sleep(delay * (attempt + 1))  # 3s, 6s, 9s, 12s - backs off more each retry
    return pd.DataFrame()


def analyze_stock(symbol):
    result = {"Symbol": symbol.replace(".NS", ""),
              "EMA": "N/A", "EMA5": None, "EMA13": None, "EMA26": None,
              "Stoch": "N/A", "StochK": None, "StochD": None,
              "MACD": "N/A", "MACDLine": None, "MACDSignal": None,
              "HeikinAshi": "N/A", "HA_Open": None, "HA_Close": None,
              "Final": "SKIP"}
    try:
        # --- 15-min data (for EMA + Stochastic) ---
        df15 = download_with_retry(symbol, period="5d", interval="15m")
        if df15.empty or len(df15) < 30:
            result["Final"] = "NO DATA"
            return result

        e5 = ema(df15["Close"], 5)
        e13 = ema(df15["Close"], 13)
        e26 = ema(df15["Close"], 26)
        result["EMA5"] = round(float(e5.iloc[-1]), 2)
        result["EMA13"] = round(float(e13.iloc[-1]), 2)
        result["EMA26"] = round(float(e26.iloc[-1]), 2)
        if e5.iloc[-1] > e26.iloc[-1] and e13.iloc[-1] > e26.iloc[-1]:
            result["EMA"] = "Buy"
        elif e5.iloc[-1] < e26.iloc[-1] and e13.iloc[-1] < e26.iloc[-1]:
            result["EMA"] = "Sell"
        else:
            result["EMA"] = "Neutral"

        k, d = stochastic(df15, period=4)
        result["StochK"] = round(float(k.iloc[-1]), 2) if not pd.isna(k.iloc[-1]) else None
        result["StochD"] = round(float(d.iloc[-1]), 2) if not pd.isna(d.iloc[-1]) else None
        if k.iloc[-1] > d.iloc[-1] and k.iloc[-2] <= d.iloc[-2]:
            result["Stoch"] = "Buy"
        elif k.iloc[-1] < d.iloc[-1] and k.iloc[-2] >= d.iloc[-2]:
            result["Stoch"] = "Sell"
        elif k.iloc[-1] > d.iloc[-1]:
            result["Stoch"] = "Buy"
        elif k.iloc[-1] < d.iloc[-1]:
            result["Stoch"] = "Sell"

        # --- 1-hour data (for MACD) ---
        df1h = download_with_retry(symbol, period="1mo", interval="60m")
        if df1h.empty or len(df1h) < 35:
            result["Final"] = "NO DATA"
            return result
        macd_line, signal_line = macd(df1h["Close"])
        result["MACDLine"] = round(float(macd_line.iloc[-1]), 2)
        result["MACDSignal"] = round(float(signal_line.iloc[-1]), 2)
        result["MACD"] = "Buy" if macd_line.iloc[-1] > signal_line.iloc[-1] else "Sell"

        # --- 4-hour data (resampled from 1h, for Heikin Ashi) ---
        df4h = df1h.resample("4h").agg({
            "Open": "first", "High": "max", "Low": "min", "Close": "last"
        }).dropna()
        if len(df4h) < 3:
            return result
        ha = heikin_ashi(df4h)
        result["HA_Open"] = round(float(ha["HA_Open"].iloc[-1]), 2)
        result["HA_Close"] = round(float(ha["HA_Close"].iloc[-1]), 2)
        last_candle_bullish = ha["HA_Close"].iloc[-1] > ha["HA_Open"].iloc[-1]
        no_lower_wick = abs(ha["HA_Low"].iloc[-1] - min(ha["HA_Open"].iloc[-1], ha["HA_Close"].iloc[-1])) < 0.05 * ha["HA_Close"].iloc[-1]
        no_upper_wick = abs(ha["HA_High"].iloc[-1] - max(ha["HA_Open"].iloc[-1], ha["HA_Close"].iloc[-1])) < 0.05 * ha["HA_Close"].iloc[-1]
        if last_candle_bullish and no_lower_wick:
            result["HeikinAshi"] = "Buy"
        elif not last_candle_bullish and no_upper_wick:
            result["HeikinAshi"] = "Sell"
        else:
            result["HeikinAshi"] = "Neutral"

        # --- Full Signal Rule ---
        signals = [result["EMA"], result["Stoch"], result["MACD"], result["HeikinAshi"]]
        if all(s == "Buy" for s in signals):
            result["Final"] = "BUY"
        elif all(s == "Sell" for s in signals):
            result["Final"] = "SELL"
        else:
            result["Final"] = "SKIP"

    except Exception as e:
        result["Final"] = f"ERROR: {str(e)[:30]}"

    return result

# ----------------------------------------------------------------------
# 4. RUN SCAN ACROSS ALL 50 STOCKS
# ----------------------------------------------------------------------

def run_scan():
    print(f"Scanning {len(NIFTY50)} Nifty50 stocks... this takes a couple of minutes.")
    rows = []
    for i, symbol in enumerate(NIFTY50, 1):
        print(f"  [{i}/{len(NIFTY50)}] {symbol}")
        rows.append(analyze_stock(symbol))
        time.sleep(0.6)  # be gentle on the data source
    return pd.DataFrame(rows)

# ----------------------------------------------------------------------
# 5. WRITE TO EXCEL WITH COLOR CODING
# ----------------------------------------------------------------------

def write_excel(df, filename="nifty50_scan_result.xlsx"):
    # Reorder columns so raw values sit next to their signal - easy to eyeball and verify
    column_order = ["Symbol", "Final",
                     "EMA", "EMA5", "EMA13", "EMA26",
                     "Stoch", "StochK", "StochD",
                     "MACD", "MACDLine", "MACDSignal",
                     "HeikinAshi", "HA_Open", "HA_Close"]
    df = df[[c for c in column_order if c in df.columns]]
    df = df.sort_values(by="Final", ascending=False)
    df.to_excel(filename, index=False, sheet_name="Scan")

    from openpyxl import load_workbook
    wb = load_workbook(filename)
    ws = wb["Scan"]

    green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    red = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    bold = Font(bold=True)

    final_col = df.columns.get_loc("Final") + 1
    for row in range(2, ws.max_row + 1):
        cell = ws.cell(row=row, column=final_col)
        cell.font = bold
        if cell.value == "BUY":
            cell.fill = green
        elif cell.value == "SELL":
            cell.fill = red

    for col_idx, col_name in enumerate(df.columns, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = max(14, len(col_name) + 4)

    wb.save(filename)
    print(f"\nDone. Results saved to: {filename}")

# ----------------------------------------------------------------------
if __name__ == "__main__":
    result_df = run_scan()
    write_excel(result_df)
    buys = result_df[result_df["Final"] == "BUY"]["Symbol"].tolist()
    sells = result_df[result_df["Final"] == "SELL"]["Symbol"].tolist()
    print(f"\nBUY signals ({len(buys)}): {buys}")
    print(f"SELL signals ({len(sells)}): {sells}")