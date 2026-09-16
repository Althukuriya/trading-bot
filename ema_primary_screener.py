import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime

# =====================================================================
# 1. NIFTY 50 TICKER UNIVERSE (NSE: .NS for yfinance)
# =====================================================================
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

# =====================================================================
# 2. INDICATOR ENGINE (15-Minute Candle Calculations)
# =====================================================================
def compute_indicators(df):
    close = df['Close']
    high = df['High']
    low = df['Low']
    open_p = df['Open']

    # --- Step A: Primary Signal EMAs (5, 13, 26) ---
    df['EMA5'] = close.ewm(span=5, adjust=False).mean()
    df['EMA13'] = close.ewm(span=13, adjust=False).mean()
    df['EMA26'] = close.ewm(span=26, adjust=False).mean()

    # --- Step B: Stochastic Oscillator (14, 3, 3) ---
    low14 = low.rolling(window=14).min()
    high14 = high.rolling(window=14).max()
    fast_k = 100 * ((close - low14) / (high14 - low14).replace(0, np.nan))
    df['StochK'] = fast_k.rolling(window=3).mean()
    df['StochD'] = df['StochK'].rolling(window=3).mean()

    # --- Step C: MACD (12, 26, 9) ---
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26_macd = close.ewm(span=26, adjust=False).mean()
    df['MACDLine'] = ema12 - ema26_macd
    df['MACDSignal'] = df['MACDLine'].ewm(span=9, adjust=False).mean()

    # --- Step D: Heikin Ashi Calculations ---
    ha_close = (open_p + high + low + close) / 4.0
    ha_open = np.zeros(len(df))
    ha_open[0] = open_p.iloc[0]

    for i in range(1, len(df)):
        ha_open[i] = (ha_open[i - 1] + ha_close.iloc[i - 1]) / 2.0

    df['HA_Open'] = ha_open
    df['HA_Close'] = ha_close

    return df

# =====================================================================
# 3. RULE PIPELINE: PRIMARY EMA FIRST -> CONFIRMATIONS SECOND
# =====================================================================
def screen_market():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Running Screener: Primary EMA + Confirmations...")
    records = []

    for sym in NIFTY50_SYMBOLS:
        ticker = f"{sym}.NS"
        try:
            # Download intraday 15-minute data (last 5 trading days)
            data = yf.download(ticker, period="5d", interval="15m", progress=False)

            if data.empty or len(data) < 30:
                records.append({
                    "Symbol": sym, "Final": "NO DATA", "Primary_EMA": "NO DATA",
                    "EMA5": np.nan, "EMA13": np.nan, "EMA26": np.nan,
                    "Stoch": "NO DATA", "StochK": np.nan, "StochD": np.nan,
                    "MACD": "NO DATA", "MACDLine": np.nan, "MACDSignal": np.nan,
                    "HeikinAshi": "NO DATA", "HA_Open": np.nan, "HA_Close": np.nan,
                    "Confirmation_Rule": "Missing Data Feed"
                })
                continue

            if isinstance(data.columns, pd.MultiIndex):
                data.columns = [col[0] for col in data.columns]

            df = compute_indicators(data)
            bar = df.iloc[-1]
            prev_bar = df.iloc[-2]

            # Price Action Boundaries for Confirmation (approx 1 day / 25 bars)
            daily_bars = df.iloc[-25:]
            pdh = round(float(daily_bars['High'].max()), 2)
            pdl = round(float(daily_bars['Low'].min()), 2)

            # -------------------------------------------------------------
            # RULE 1: PRIMARY SIGNAL — EMA (5, 13, 26)
            # -------------------------------------------------------------
            e5, e13, e26 = bar['EMA5'], bar['EMA13'], bar['EMA26']
            if (e5 > e26) and (e13 > e26):
                primary_ema = "Buy"
            elif (e5 < e26) and (e13 < e26):
                primary_ema = "Sell"
            else:
                primary_ema = "Neutral"

            # -------------------------------------------------------------
            # RULE 2: SECONDARY CONFIRMATIONS
            # -------------------------------------------------------------
            # Stochastic (14, 3, 3)
            k, d = bar['StochK'], bar['StochD']
            stoch_sig = "Buy" if k > d else ("Sell" if k < d else "Neutral")

            # MACD (12, 26, 9)
            macd_l, macd_s = bar['MACDLine'], bar['MACDSignal']
            macd_sig = "Buy" if macd_l > macd_s else ("Sell" if macd_l < macd_s else "Neutral")

            # Heikin Ashi: Requires 2 consecutive trend bars
            ha_open_curr, ha_close_curr = bar['HA_Open'], bar['HA_Close']
            ha_open_prev, ha_close_prev = prev_bar['HA_Open'], prev_bar['HA_Close']

            ha_bullish = (ha_close_curr > ha_open_curr) and (ha_close_prev > ha_open_prev)
            ha_bearish = (ha_close_curr < ha_open_curr) and (ha_close_prev < ha_open_prev)
            ha_sig = "Buy" if ha_bullish else ("Sell" if ha_bearish else "Neutral")

            # -------------------------------------------------------------
            # RULE 3: CONFLUENCE VERDICT & EXECUTION LEVELS
            # -------------------------------------------------------------
            if primary_ema == "Buy":
                # Check if all confirmations agree
                if (stoch_sig == "Buy") and (macd_sig == "Buy") and (ha_sig == "Buy"):
                    final_verdict = "BUY"
                    confirmation_rule = (
                        f"Enter LONG on 5m candle close > Resistance/PDH {pdh}. "
                        f"SL below support {pdl}."
                    )
                else:
                    final_verdict = "SKIP"
                    confirmation_rule = "EMA is Buy, but Secondary Confirmations Failed"

            elif primary_ema == "Sell":
                # Check if all confirmations agree
                if (stoch_sig == "Sell") and (macd_sig == "Sell") and (ha_sig == "Sell"):
                    final_verdict = "SELL"
                    confirmation_rule = (
                        f"Enter SHORT on 5m candle close < Support/PDL {pdl}. "
                        f"SL above resistance {pdh}."
                    )
                else:
                    final_verdict = "SKIP"
                    confirmation_rule = "EMA is Sell, but Secondary Confirmations Failed"

            else:
                # Primary EMA is Neutral / Tangled -> Discard immediately
                final_verdict = "SKIP"
                confirmation_rule = "Primary EMA Neutral / Choppy - No Trend"

            records.append({
                "Symbol": sym,
                "Final": final_verdict,
                "Primary_EMA": primary_ema,
                "EMA5": round(e5, 2),
                "EMA13": round(e13, 2),
                "EMA26": round(e26, 2),
                "Stoch": stoch_sig,
                "StochK": round(k, 2),
                "StochD": round(d, 2),
                "MACD": macd_sig,
                "MACDLine": round(macd_l, 2),
                "MACDSignal": round(macd_s, 2),
                "HeikinAshi": ha_sig,
                "HA_Open": round(ha_open_curr, 2),
                "HA_Close": round(ha_close_curr, 2),
                "Confirmation_Rule": confirmation_rule
            })

        except Exception as err:
            print(f"Error scanning {sym}: {err}")

    # =====================================================================
    # 4. EXPORT AND SUMMARY
    # =====================================================================
    res_df = pd.DataFrame(records)
    filename = "nifty50_ema_primary_scan.xlsx"
    res_df.to_excel(filename, sheet_name="Intraday_Scan", index=False)

    print("\n" + "=" * 60)
    print(" SCANNING COMPLETE ")
    print("=" * 60)
    print(f"Total Stocks Scanned : {len(res_df)}")
    print(f"BUY Signals Confirmed: {(res_df['Final'] == 'BUY').sum()}")
    print(f"SELL Signals Confirmed: {(res_df['Final'] == 'SELL').sum()}")
    print(f"Filtered Out (SKIP)  : {(res_df['Final'] == 'SKIP').sum()}")
    print(f"\nSaved output file: {filename}\n")

    # Display clean table of ready setups
    actionable = res_df[res_df['Final'].isin(['BUY', 'SELL'])]
    if not actionable.empty:
        print("--- ACTIONABLE INTRADAY WATCHLIST ---")
        for _, r in actionable.iterrows():
            print(f"[{r['Final']}] {r['Symbol']}: {r['Confirmation_Rule']}")
    else:
        print("No setups where Primary EMA + All Confirmations fully aligned.")

if __name__ == "__main__":
    screen_market()