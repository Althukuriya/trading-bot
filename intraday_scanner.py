import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime

# ==========================================
# 1. NIFTY 50 TICKER UNIVERSE (NSE: .NS)
# ==========================================
NIFTY50_SYMBOLS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "BPCL", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
    "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK", "INFY",
    "ITC", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "NESTLEIND", "NTPC", "ONGC", "POWERGRID",
    "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN", "SUNPHARMA",
    "TATACONSUM", "TMPV", "TATASTEEL", "TCS", "TECHM",
    "TITAN", "TRENT", "ULTRACEMCO", "WIPRO", "LTIM"
]

# ==========================================
# 2. TECHNICAL INDICATOR CALCULATIONS
# ==========================================
def calculate_indicators(df):
    """
    Computes EMA (5, 13, 26), Stochastic (14, 3, 3), MACD (12, 26, 9),
    and Heikin Ashi candles on the provided OHLC data.
    """
    close = df['Close']
    high = df['High']
    low = df['Low']
    open_p = df['Open']

    # --- A. EMAs (5, 13, 26) ---
    df['EMA5'] = close.ewm(span=5, adjust=False).mean()
    df['EMA13'] = close.ewm(span=13, adjust=False).mean()
    df['EMA26'] = close.ewm(span=26, adjust=False).mean()

    # --- B. Stochastic Oscillator (%K, %D: 14, 3, 3) ---
    low14 = low.rolling(window=14).min()
    high14 = high.rolling(window=14).max()
    fast_k = 100 * ((close - low14) / (high14 - low14).replace(0, np.nan))
    df['StochK'] = fast_k.rolling(window=3).mean()
    df['StochD'] = df['StochK'].rolling(window=3).mean()

    # --- C. MACD (12, 26, 9) ---
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26_macd = close.ewm(span=26, adjust=False).mean()
    df['MACDLine'] = ema12 - ema26_macd
    df['MACDSignal'] = df['MACDLine'].ewm(span=9, adjust=False).mean()

    # --- D. Heikin Ashi Candles ---
    ha_close = (open_p + high + low + close) / 4.0
    ha_open = np.zeros(len(df))
    ha_open[0] = open_p.iloc[0]

    for i in range(1, len(df)):
        ha_open[i] = (ha_open[i - 1] + ha_close.iloc[i - 1]) / 2.0

    df['HA_Open'] = ha_open
    df['HA_Close'] = ha_close

    return df

# ==========================================
# 3. SCANNING & RULE EVALUATION ENGINE
# ==========================================
def scan_nifty50():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting Intraday 15-Min Scan across Nifty 50 stocks...\n")
    results = []

    for sym in NIFTY50_SYMBOLS:
        ticker = f"{sym}.NS"
        try:
            # Fetch last 5 days of 15-minute intraday candle data
            data = yf.download(ticker, period="5d", interval="15m", progress=False)

            if data.empty or len(data) < 30:
                results.append({
                    "Symbol": sym, "Final": "NO DATA",
                    "EMA": "NO DATA", "EMA5": np.nan, "EMA13": np.nan, "EMA26": np.nan,
                    "Stoch": "NO DATA", "StochK": np.nan, "StochD": np.nan,
                    "MACD": "NO DATA", "MACDLine": np.nan, "MACDSignal": np.nan,
                    "HeikinAshi": "NO DATA", "HA_Open": np.nan, "HA_Close": np.nan,
                    "Confirmation": "No data available"
                })
                continue

            # Flatten multi-level columns if returned by yfinance
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = [col[0] for col in data.columns]

            df = calculate_indicators(data)
            latest = df.iloc[-1]
            prev_day_data = df.iloc[-25:]  # Approximate 1-day lookback for S/R

            # Key price action levels for Confirmation column
            pdh = round(prev_day_data['High'].max(), 2)
            pdl = round(prev_day_data['Low'].min(), 2)

            # --- Rule 1: EMA Condition (5 & 13 relative to 26) ---
            e5, e13, e26 = latest['EMA5'], latest['EMA13'], latest['EMA26']
            if e5 > e26 and e13 > e26:
                ema_signal = "Buy"
            elif e5 < e26 and e13 < e26:
                ema_signal = "Sell"
            else:
                ema_signal = "Neutral"

            # --- Rule 2: Stochastic Condition (%K vs %D) ---
            stoch_k, stoch_d = latest['StochK'], latest['StochD']
            if stoch_k > stoch_d:
                stoch_signal = "Buy"
            elif stoch_k < stoch_d:
                stoch_signal = "Sell"
            else:
                stoch_signal = "Neutral"

            # --- Rule 3: MACD Condition (Line vs Signal) ---
            macd_l, macd_s = latest['MACDLine'], latest['MACDSignal']
            if macd_l > macd_s:
                macd_signal = "Buy"
            elif macd_l < macd_s:
                macd_signal = "Sell"
            else:
                macd_signal = "Neutral"

            # --- Rule 4: Heikin Ashi Condition (Green vs Red) ---
            ha_open_val, ha_close_val = latest['HA_Open'], latest['HA_Close']
            if ha_close_val > ha_open_val:
                ha_signal = "Buy"
            elif ha_close_val < ha_open_val:
                ha_signal = "Sell"
            else:
                ha_signal = "Neutral"

            # --- Confluence Decision Rule ---
            signals = [ema_signal, stoch_signal, macd_signal, ha_signal]
            if all(s == "Buy" for s in signals):
                final_decision = "BUY"
                confirmation_note = f"Buy above Resistance/PDH: {pdh}; Support: {pdl}"
            elif all(s == "Sell" for s in signals):
                final_decision = "SELL"
                confirmation_note = f"Sell below Support/PDL: {pdl}; Resistance: {pdh}"
            else:
                final_decision = "SKIP"
                confirmation_note = "Indecision / Mixed Signals"

            results.append({
                "Symbol": sym,
                "Final": final_decision,
                "EMA": ema_signal,
                "EMA5": round(e5, 2),
                "EMA13": round(e13, 2),
                "EMA26": round(e26, 2),
                "Stoch": stoch_signal,
                "StochK": round(stoch_k, 2),
                "StochD": round(stoch_d, 2),
                "MACD": macd_signal,
                "MACDLine": round(macd_l, 2),
                "MACDSignal": round(macd_s, 2),
                "HeikinAshi": ha_signal,
                "HA_Open": round(ha_open_val, 2),
                "HA_Close": round(ha_close_val, 2),
                "Confirmation": confirmation_note
            })

        except Exception as e:
            print(f"Error fetching {sym}: {e}")

    # ==========================================
    # 4. EXPORT TO EXCEL
    # ==========================================
    output_df = pd.DataFrame(results)
    output_file = "nifty50_intraday_scan.xlsx"
    output_df.to_excel(output_file, sheet_name="Scan", index=False)

    print("\n" + "="*50)
    print(" SCAN COMPLETED")
    print("="*50)
    print(f"Total Stocks Scanned: {len(output_df)}")
    print(f"BUY Setups Found    : {(output_df['Final'] == 'BUY').sum()}")
    print(f"SELL Setups Found   : {(output_df['Final'] == 'SELL').sum()}")
    print(f"SKIP (Chop/Mixed)   : {(output_df['Final'] == 'SKIP').sum()}")
    print(f"\nSaved full results to '{output_file}'\n")

    # Display shortlisted trade candidates
    watchlist = output_df[output_df['Final'].isin(['BUY', 'SELL'])]
    if not watchlist.empty:
        print("--- WATCHLIST FOR NEXT SESSION ---")
        print(watchlist[['Symbol', 'Final', 'Confirmation']].to_string(index=False))
    else:
        print("No unanimous 4-indicator setups found today.")

if __name__ == "__main__":
    scan_nifty50()