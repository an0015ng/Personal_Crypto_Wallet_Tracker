import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
import time
from datetime import datetime, timedelta
import re
import os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
# Import webdriver_manager
from webdriver_manager.chrome import ChromeDriverManager
import hashlib

# --- Configuration - Load from Environment Variables ---
# These are set as secrets in GitHub Actions
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL")
DEBANK_API_KEY = os.getenv("DEBANK_API_KEY") # This is unused but kept for consistency

# File to store seen transactions
SEEN_TRANSACTIONS_FILE = "seen_transactions.json"

def load_seen_transactions():
    """Load previously seen transaction hashes from file"""
    try:
        with open(SEEN_TRANSACTIONS_FILE, 'r') as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()

def save_seen_transactions(seen_txs):
    """Save seen transaction hashes to file"""
    with open(SEEN_TRANSACTIONS_FILE, 'w') as f:
        json.dump(list(seen_txs), f)

def scrape_debank_wallet_real(wallet_address):
    """
    Selenium scraper for DeBank portfolio & history tables.
    Returns (transactions, holdings) or (None, None) on failure.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from webdriver_manager.chrome import ChromeDriverManager
    import time, hashlib, re
    from datetime import datetime

    # Configure headless Chrome
    options = Options()
    for arg in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"):
        options.add_argument(arg)
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Linux; Android 10; K) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Mobile Safari/537.36"
    )

    driver = None
    try:
        # Launch ChromeDriver
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(60)
        driver.implicitly_wait(10)

        # --- SCRAPE PORTFOLIO HOLDINGS ---
        driver.get(f"https://debank.com/profile/{wallet_address}")
        time.sleep(8)

        row_elems = driver.find_elements(
            By.CSS_SELECTOR,
            "div.db-table.TokenWallet_table__bmN1O div.db-table-body.is-noEndBorder > div"
        )

        holdings = []
        seen = set()

        for row in row_elems:
            # Each row.text is multiline: [TOKEN, PRICE, AMOUNT, USD_VALUE]
            lines = [line.strip() for line in row.text.splitlines() if line.strip()]
            if len(lines) != 4:
                continue

            token, price_txt, amt_txt, val_txt = lines

            try:
                price = float(price_txt.replace("$", "").replace(",", ""))
                amount = float(amt_txt.replace(",", ""))
                value_usd = float(val_txt.replace("$", "").replace(",", ""))
            except:
                continue

            if value_usd <= 0:
                continue

            key = f"{token}-{value_usd}"
            if key in seen:
                continue
            seen.add(key)

            holdings.append({
                "token":     token,
                "price":     price,
                "amount":    amount,
                "value_usd": value_usd,
                "chains":   ["ethereum"]
            })

        # Consolidate by token
        consolidated = {}
        for h in holdings:
            t = h["token"]
            if t in consolidated:
                consolidated[t]["amount"]    += h["amount"]
                consolidated[t]["value_usd"] += h["value_usd"]
            else:
                consolidated[t] = h.copy()
        final_holdings = list(consolidated.values())

        # --- SCRAPE TRANSACTION HISTORY ---
        driver.get(f"https://debank.com/profile/{wallet_address}/history")
        time.sleep(8)

        tx_elems = driver.find_elements(By.CSS_SELECTOR, "div.db-table-body > div")
        transactions = []
        seen_tx = set()

        for i, row in enumerate(tx_elems[:25]):
            text = row.text.strip()
            if not text:
                continue

            hsh = hashlib.md5(f"{text}{i}".encode()).hexdigest()
            if hsh in seen_tx:
                continue
            seen_tx.add(hsh)

            cells = [c.text.strip() for c in row.find_elements(By.CSS_SELECTOR, "div") if c.text.strip()]
            if len(cells) < 3:
                continue

            tx_type = cells[0]
            print(f"[SCRAPE] tx_type: {tx_type}")
            amount_txt = cells[1]
            print(f"[SCRAPE] amount_txt: {amount_txt}")
            token_match = re.search(r"\b[A-Z0-9]{2,10}\b", amount_txt)
            symbol = token_match.group(0) if token_match else ""
            print(f"[SCRAPE] token: {symbol}")

            # find first $ value in cells
            value_usd = 0.0
            for c in cells:
                if c.startswith("$"):
                    try:
                        value_usd = float(c.replace("$", "").replace(",", ""))
                        break
                    except:
                        continue

            if value_usd <= 0:
                continue
            print(f"[SCRAPE] value_usd: {value_usd}")
            
            transactions.append({
                "hash":      f"0x{hsh}",
                "type":      tx_type,
                "amount":    amount_txt,
                "token":     symbol,
                "value_usd": value_usd,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "from":      wallet_address,
                "to":        "unknown"
            })

        return transactions, final_holdings

    except Exception:
        return None, None

    finally:
        if driver:
            driver.quit()

def send_email_notification(new_transactions, current_holdings):
    """
    Send email notification with:
      - Significant new transactions in last 24 hours (value > $10,000)
      - Top 10 current holdings
    """
    try:
        # Parse timestamps and filter only last 24 hours
        cutoff = datetime.now() - timedelta(days=1)
        recent_significant = []
        for tx in new_transactions:
            # tx['timestamp'] is in "%Y-%m-%d %H:%M:%S" format
            tx_time = datetime.strptime(tx['timestamp'], "%Y-%m-%d %H:%M:%S")
            if tx['value_usd'] > 10000 and tx_time >= cutoff:
                recent_significant.append(tx)
        
        # Sort holdings descending by USD value and take top 10
        sorted_holdings = sorted(current_holdings, key=lambda h: h['value_usd'], reverse=True)
        top_holdings = sorted_holdings[:10]
        
        # Build email
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To']   = NOTIFY_EMAIL
        msg['Subject'] = f"DeBank Update: {len(recent_significant)} New TXs (24h) + Top Holdings"
        
        # Total portfolio value
        total_value = sum(h['value_usd'] for h in current_holdings)
        
        html = f"""
        <html><body style="font-family: Arial, sans-serif; line-height:1.4;">
          <h2>📬 DeBank Wallet Update</h2>
          <p><strong>Wallet:</strong> {WALLET_ADDRESS}</p>
          <p><strong>Time:</strong> {datetime.now():%Y-%m-%d %H:%M:%S}</p>"""
          
        html += f"<h2>Total Portfolio Value: ${total_value:,.2f}</h2>"
        
        # Top 10 holdings table
        html += """
        <h3>Top 10 Current Holdings</h3>
          <table border="1" cellpadding="5" cellspacing="0"
                 style="border-collapse:collapse; width:100%; max-width:600px;">
            <tr style="background:#f0f0f0;">
              <th>Rank</th><th>Token</th><th align="right">Amount</th>
              <th align="right">Value (USD)</th><th align="right">%</th>
            </tr>
        """
        
        total_for_pct = total_value or 1
        for idx, h in enumerate(top_holdings, 1):
            pct = h['value_usd'] / total_for_pct * 100
            html += f"""
            <tr>
              <td>{idx}</td>
              <td>{h['token']}</td>
              <td align="right">{h['amount']:,}</td>
              <td align="right">${h['value_usd']:,.2f}</td>
              <td align="right">{pct:.2f}%</td>
            </tr>
            """
        
        html += """
          </table>
        <h3>🚨 Significant New Transactions in Last 24 Hours (>$10,000)</h3>
        """
        
        if recent_significant:
            html += "<ul>"
            for tx in recent_significant:
                html += f"""
                  <li>
                    <strong>{tx['type']}</strong> —
                    {tx['amount']} {tx['token']} 
                    (<span style="color: green;">${tx['value_usd']:,.2f}</span>)<br>
                    <small>Time: {tx['timestamp']} | Hash: {tx['hash']}</small>
                  </li>
                """
            html += "</ul>"
        else:
            html += "<p>No new transactions over $10,000 in the last 24 hours.</p>"
        
        html += """
          <p style="font-size:0.9em; color:#555;">
            (Automated notification from your DeBank tracker.)
          </p>
        </body></html>
        """
        
        msg.attach(MIMEText(html, 'html'))
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
        
        print("✅ Email sent successfully.")
    except Exception as e:
        print(f"❌ Error sending email: {e}")


def track_wallet():
    """Main function to track wallet and send notifications."""
    print(f"🔍 Checking wallet: {WALLET_ADDRESS}")
    seen_transactions = load_seen_transactions()

    # Correct function call
    transactions, holdings = scrape_debank_wallet_real(WALLET_ADDRESS)

    if transactions is None or holdings is None:
        print("❌ Failed to fetch wallet data, exiting.")
        return

    new_transactions = []
    for tx in transactions:
        tx_hash = tx.get('hash')
        if tx_hash and tx_hash not in seen_transactions:
            new_transactions.append(tx)
            seen_transactions.add(tx_hash)

    print(f"📊 Found {len(transactions)} total transactions ({len(new_transactions)} new).")
    print(f"💼 Portfolio has {len(holdings)} holdings.")

    send_email_notification(new_transactions, holdings)
    save_seen_transactions(seen_transactions)
    print("💾 Saved transaction history.")


if __name__ == "__main__":
    track_wallet()
