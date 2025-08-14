import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
import time
from datetime import datetime
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

    # Chrome headless options
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Linux; Android 10; K) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Mobile Safari/537.36"
    )

    driver = None
    try:
        # Initialize ChromeDriver via webdriver_manager
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(60)
        driver.implicitly_wait(10)

        # --- SCRAPE PORTFOLIO HOLDINGS ---
        print(f"Loading portfolio page for {wallet_address}")
        driver.get(f"https://debank.com/profile/{wallet_address}")
        time.sleep(8)

        # Try multiple selectors for holdings rows
        holdings_selectors = [
            "div.db-table.TokenWallet_table__bmN1O div.db-table-body.is-noEndBorder > div",
            "[class*='TokenWallet_table'] [class*='db-table-body'] > div",
            "[class*='table'] [class*='body'] > div[class*='row']",
            "div[class*='portfolio'] div[class*='row']"
        ]

        rows = []
        for sel in holdings_selectors:
            try:
                rows = driver.find_elements(By.CSS_SELECTOR, sel)
                if rows:
                    print(f"Found {len(rows)} holding rows with selector: {sel}")
                    break
            except Exception as e:
                print(f"Selector {sel} failed: {e}")
                continue

        holdings = []
        seen = set()

        for i, row in enumerate(rows):
            try:
                # Extract token symbol
                token = None
                for tok_sel in ["div > div:nth-child(1) a", "[class*='token'] a", "a[href*='/token/']"]:
                    try:
                        elem = row.find_element(By.CSS_SELECTOR, tok_sel)
                        token = elem.text.strip()
                        if token:
                            break
                    except:
                        continue
                if not token:
                    continue

                # Parse cells with correct indices
                cells = row.find_elements(By.CSS_SELECTOR, "div")
                print(f"DEBUG row {i} cells:", [c.text for c in cells])  # debug

                if len(cells) < 4:
                    continue

                price_text  = cells[1].text.strip()
                amount_text = cells[2].text.strip()
                value_text  = cells[3].text.strip()

                try:
                    price = float(price_text.replace("$", "").replace(",", ""))
                except:
                    price = 0.0

                try:
                    amount = float(amount_text.replace(",", ""))
                except:
                    amount = 0.0

                try:
                    value_usd = float(value_text.replace("$", "").replace(",", ""))
                except:
                    value_usd = price * amount if price and amount else 0.0

                if value_usd <= 0:
                    continue

                key = f"{token}-{value_usd}"
                if key in seen:
                    continue
                seen.add(key)

                holdings.append({
                    "token": token,
                    "price": price,
                    "amount": amount,
                    "value_usd": value_usd,
                    "chains": ["ethereum"]
                })
                print(f"Added holding: {token} = ${value_usd:,.2f}")

            except Exception as e:
                print(f"Error parsing holding row {i}: {e}")
                continue

        # Consolidate multi-chain holdings
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
        print(f"Loading transaction history for {wallet_address}")
        driver.get(f"https://debank.com/profile/{wallet_address}/history")
        time.sleep(8)

        tx_selectors = [
            "div.db-table-body > div",
            "[class*='history'] [class*='body'] > div",
            "[class*='transaction'] div[class*='row']",
            "div[class*='list'] > div[class*='item']"
        ]

        tx_rows = []
        for sel in tx_selectors:
            try:
                tx_rows = driver.find_elements(By.CSS_SELECTOR, sel)
                if tx_rows:
                    print(f"Found {len(tx_rows)} transaction rows with selector: {sel}")
                    break
            except Exception as e:
                print(f"Transaction selector {sel} failed: {e}")
                continue

        transactions = []
        for i, row in enumerate(tx_rows[:25]):
            try:
                text = row.text.strip()
                if not text:
                    continue

                hsh = hashlib.md5(f"{text}{i}".encode()).hexdigest()
                cells = row.find_elements(By.CSS_SELECTOR, "div")
                if len(cells) < 3:
                    continue

                tx_type     = cells[0].text.strip()
                amount_text = cells[1].text.strip()
                token_match = re.search(r"\b[A-Z0-9]{2,10}\b", amount_text)
                symbol      = token_match.group(0) if token_match else ""

                value_usd = 0.0
                for cell in cells:
                    ct = cell.text.strip()
                    if "$" in ct:
                        try:
                            value_usd = float(ct.replace("$", "").replace(",", ""))
                            break
                        except:
                            continue

                if value_usd <= 0:
                    continue

                transactions.append({
                    "hash":      f"0x{hsh}",
                    "type":      tx_type,
                    "amount":    amount_text,
                    "token":     symbol,
                    "value_usd": value_usd,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "from":      wallet_address,
                    "to":        "unknown"
                })
                print(f"Added transaction: {tx_type} = ${value_usd:,.2f}")

            except Exception as e:
                print(f"Error parsing transaction row {i}: {e}")
                continue

        print(f"Scraped {len(final_holdings)} holdings and {len(transactions)} transactions")
        return transactions, final_holdings

    except Exception as e:
        print(f"Critical error in scraping: {e}")
        return None, None

    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

def send_email_notification(new_transactions, current_holdings):
    """Send email notification with new transactions and top holdings."""
    try:
        significant_txs = [tx for tx in new_transactions if tx.get('value_usd', 0) > 10000]
        sorted_holdings = sorted(current_holdings, key=lambda h: h['value_usd'], reverse=True)
        top_holdings = sorted_holdings[:10]

        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = NOTIFY_EMAIL
        msg['Subject'] = f"DeBank Update: {len(significant_txs)} New TXs + Top Holdings"

        total_value = sum(h['value_usd'] for h in current_holdings)
        html = f"""
        <html><body><h2>DeBank Wallet Update for {WALLET_ADDRESS}</h2>
        <h3>Total Portfolio Value: ${total_value:,.2f}</h3>
        <h3>Significant New Transactions (>{'$10,000'})</h3>
        """
        if significant_txs:
            html += "<ul>"
            for tx in significant_txs:
                html += f"<li><strong>{tx['type']}</strong>: {tx['amount']} {tx['token']} (${tx['value_usd']:,.2f})</li>"
            html += "</ul>"
        else:
            html += "<p>No new transactions over $10,000.</p>"

        html += "<h3>Top 10 Holdings</h3><table border='1' cellpadding='5' style='border-collapse:collapse;width:100%;'>"
        html += "<tr style='background:#f0f0f0;'><th>Rank</th><th>Token</th><th>Amount</th><th>Value (USD)</th><th>%</th></tr>"
        total_value_for_pct = total_value or 1
        for idx, h in enumerate(top_holdings, 1):
            pct = (h['value_usd'] / total_value_for_pct) * 100
            html += f"<tr><td>{idx}</td><td>{h['token']}</td><td align='right'>{h['amount']:,}</td><td align='right'>${h['value_usd']:,.2f}</td><td align='right'>{pct:.2f}%</td></tr>"
        html += "</table></body></html>"

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
