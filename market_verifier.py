import os
import pandas as pd
import yfinance as yf

# -------------------------------------------------------------
# 1. SET YOUR EXACT FILE PATHS HERE
# Use raw string format r"..." so backslashes don't cause errors
# -------------------------------------------------------------
FILE_PATHS = {
    "scan_result": "nifty50_scan_result.xlsx",
    "intraday_scan": "nifty50_intraday_scan.xlsx",
    "ema_primary": "nifty50_ema_primary_scan.xlsx",
}

def verify_different_folders():
    signals = {}

    # Check and read each scanner file individually from its own folder
    for scanner_name, file_path in FILE_PATHS.items():
        if not os.path.exists(file_path):
            print(f"⚠️ File not found for [{scanner_name}]: {file_path}")
            continue

        try:
            xls = pd.ExcelFile(file_path)
            sheet_name = xls.sheet_names[0]  # Reads the first sheet
            df = pd.read_excel(file_path, sheet_name=sheet_name)
            df.columns = [str(col).strip() for col in df.columns]

            # Filter out non-actionable signals
            active_df = df[~df['Final'].isin(['SKIP', 'NO DATA', None])].dropna(subset=['Final'])

            for _, row in active_df.iterrows():
                symbol = str(row['Symbol']).strip().upper()
                action = str(row['Final']).strip().upper()
                
                # Get reference price (HA_Close or Close)
                ref_price = row.get('HA_Close', row.get('Close', 0))

                if symbol not in signals:
                    signals[symbol] = {
                        'Symbol': symbol,
                        'Signal': action,
                        'Morning_Price': float(ref_price) if pd.notnull(ref_price) else 0.0,
                        'Scanners': [scanner_name]
                    }
                else:
                    signals[symbol]['Scanners'].append(scanner_name)

        except Exception as e:
            print(f"Error reading {file_path}: {e}")

    if not signals:
        print("No active BUY/SELL signals found across the 3 folders.")
        return

    # Fetch live NSE quotes
    tickers = [f"{sym}.NS" for sym in signals.keys()]
    print(f"\nPulling current live prices for: {list(signals.keys())} ...\n")
    data = yf.download(tickers=tickers, period="1d", interval="1m", progress=False)

    report = []
    for sym, item in signals.items():
        ns_sym = f"{sym}.NS"
        try:
            if len(tickers) == 1:
                cmp = float(data['Close'].dropna().iloc[-1])
            else:
                cmp = float(data['Close'][ns_sym].dropna().iloc[-1])
        except Exception:
            cmp = 0.0

        ref = item['Morning_Price']
        
        if ref > 0 and cmp > 0:
            pct_move = ((cmp - ref) / ref) * 100
            # For SELL, price drop = positive PnL
            pnl_pct = -pct_move if item['Signal'] == 'SELL' else pct_move
            status = "PROFIT / IN FAVOR" if pnl_pct > 0 else "AGAINST / LOSS"
        else:
            pct_move = 0.0
            pnl_pct = 0.0
            status = "DATA PENDING"

        report.append({
            "Stock": sym,
            "Signal": item['Signal'],
            "9:15 AM Price": round(ref, 2),
            "Current Live Price": round(cmp, 2),
            "Actual Move %": f"{pct_move:+.2f}%",
            "Trade Result %": f"{pnl_pct:+.2f}%",
            "Status": status,
            "Matched Scanners": f"{len(item['Scanners'])}/3 ({', '.join(item['Scanners'])})"
        })

    summary_df = pd.DataFrame(report)
    print(summary_df.to_string(index=False))

if __name__ == "__main__":
    verify_different_folders()