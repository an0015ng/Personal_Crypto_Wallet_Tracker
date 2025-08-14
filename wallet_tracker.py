# Shared: imports at top of file
import os, time, json, hashlib, re
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Shared: load secrets/env
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")
SMTP_SERVER    = os.getenv("SMTP_SERVER")
SMTP_PORT      = int(os.getenv("SMTP_PORT", 587))
EMAIL_USER     = os.getenv("EMAIL_USER")
EMAIL_PASS     = os.getenv("EMAIL_PASS")
NOTIFY_EMAIL   = os.getenv("NOTIFY_EMAIL")

SEEN_FILE = "seen_transactions.json"

def load_seen_transactions():
    try:
        return set(json.load(open(SEEN_FILE)))
    except:
        return set()

def save_seen_transactions(seen):
    json.dump(list(seen), open(SEEN_FILE, "w"))

def scrape_debank_wallet_real(wallet):
    """Unified scraper for portfolio + history."""
    opts = Options()
    for arg in ["--headless=new","--no-sandbox","--disable-dev-shm-usage","--disable-gpu"]:
        opts.add_argument(arg)
    opts.add_argument("--window-size=1920,1080")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.implicitly_wait(10)

    # PORTFOLIO
    driver.get(f"https://debank.com/profile/{wallet}")
    time.sleep(5)
    holdings = []; seen = set()
    sel_hold = "div.db-table.TokenWallet_table__bmN1O div.db-table-body > div"
    for row in driver.find_elements(By.CSS_SELECTOR, sel_hold):
        try:
            t = row.find_element(By.CSS_SELECTOR, "div:nth-child(1) a").text.strip()
            price = float(row.find_element(By.CSS_SELECTOR, "div:nth-child(2)").text.strip().replace("$","").replace(",","") or 0)
            amt   = float(row.find_element(By.CSS_SELECTOR, "div:nth-child(3)").text.strip().replace(",","") or 0)
            val   = float(row.find_element(By.CSS_SELECTOR, "div:nth-child(4)").text.strip().replace("$","").replace(",","") or amt*price)
            if t and val>0 and (key:=f"{t}-{val}") not in seen:
                seen.add(key)
                holdings.append({"token":t,"price":price,"amount":amt,"value_usd":val,"chains":["ethereum"]})
        except:
            pass

    # TRANSACTIONS
    driver.get(f"https://debank.com/profile/{wallet}/history")
    time.sleep(5)
    txs = []; seen_tx = set()
    sel_tx = "div.db-table-body > div"
    for i, row in enumerate(driver.find_elements(By.CSS_SELECTOR, sel_tx)[:25]):
        try:
            txt = row.text.strip()
            hsh = "0x"+hashlib.md5(f"{txt}{i}".encode()).hexdigest()
            cells = row.find_elements(By.CSS_SELECTOR,"div")
            val = float(cells[2].text.strip().replace("$","").replace(",","") or 0)
            sym = re.search(r"\b[A-Z0-9]{2,10}\b", cells[1].text)
            if txt and val>0 and hsh not in seen_tx:
                seen_tx.add(hsh)
                txs.append({
                    "hash":hsh,
                    "type":cells.text.strip(),
                    "amount":cells[1].text.strip(),
                    "token":sym.group(0) if sym else "",
                    "value_usd":val,
                    "timestamp":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "from":wallet,"to":"unknown"
                })
        except:
            pass

    driver.quit()
    return txs, holdings

def send_email_notification(new_txs, holdings):
    """Unified, rich HTML email body."""
    sig = [tx for tx in new_txs if tx["value_usd"]>10000]
    top = sorted(holdings, key=lambda h:h["value_usd"], reverse=True)[:10]
    total = sum(h["value_usd"] for h in holdings) or 0

    msg = MIMEMultipart()
    msg["From"]=EMAIL_USER; msg["To"]=NOTIFY_EMAIL
    msg["Subject"]=f"DeBank Update: {len(sig)} New TXs + Top Holdings"

    body = f"""
    <html><body style="font-family:Arial">
      <h2>📬 DeBank Wallet Update</h2>
      <p><strong>Wallet:</strong> {WALLET_ADDRESS}</p>
      <p><strong>Time:</strong> {datetime.now():%Y-%m-%d %H:%M:%S}</p>
      <h3>🚨 Significant New Transactions (>$10k)</h3>
      {"".join(f"<li><strong>{tx['type']}</strong>: {tx['amount']} {tx['token']} (${tx['value_usd']:,.2f})</li>" for tx in sig) or "<p>No new tx over $10k.</p>"}
      <h2>Total Portfolio Value: ${total:,.2f}</h2>
      <h3>💰 Top 10 Holdings</h3>
      <table border="1" cellpadding="5" style="border-collapse:collapse;width:100%">
        <tr><th>Rank</th><th>Token</th><th>Amount</th><th>Value</th><th>%</th></tr>
        {"".join(f"<tr><td>{i+1}</td><td>{h['token']}</td><td align='right'>{h['amount']:,}</td><td align='right'>${h['value_usd']:,.2f}</td><td align='right'>{h['value_usd']/total*100 if total else 0:.2f}%</td></tr>" for i,h in enumerate(top))}
      </table>
    </body></html>"""

    msg.attach(MIMEText(body,"html"))
    with smtplib.SMTP(SMTP_SERVER,SMTP_PORT) as s:
        s.starttls()
        s.login(EMAIL_USER,EMAIL_PASS)
        s.send_message(msg)

def track_wallet():
    seen = load_seen_transactions()
    txs, holds = scrape_debank_wallet_real(WALLET_ADDRESS)
    if txs is None:
        return
    new = [tx for tx in txs if tx["hash"] not in seen]
    for tx in new: seen.add(tx["hash"])
    send_email_notification(new, holds)
    save_seen_transactions(seen)

if __name__=="__main__":
    track_wallet()






"""
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
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    import time
    import hashlib
    import re
    from datetime import datetime
    
    # Chrome options setup
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-web-security")
    options.add_argument("--allow-running-insecure-content")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--user-agent=Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36")

    driver = None
    try:
        # Initialize Chrome driver with Service
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(60)
        driver.implicitly_wait(10)

        # --- SCRAPE PORTFOLIO HOLDINGS ---
        print(f"Loading portfolio page for {wallet_address}")
        driver.get(f"https://debank.com/profile/{wallet_address}")
        time.sleep(8)  # Increased wait time

        # Try multiple selectors for holdings table
        holdings_selectors = [
            "div.db-table.TokenWallet_table__bmN1O div.db-table-body.is-noEndBorder > div",
            "[class*='TokenWallet_table'] [class*='db-table-body'] > div",
            "[class*='table'] [class*='body'] > div[class*='row']",
            "div[class*='portfolio'] div[class*='row']"
        ]
        
        rows = []
        for selector in holdings_selectors:
            try:
                rows = driver.find_elements(By.CSS_SELECTOR, selector)
                if rows:
                    print(f"Found {len(rows)} holding rows with selector: {selector}")
                    break
            except Exception as e:
                print(f"Selector {selector} failed: {e}")
                continue

        holdings = []
        seen = set()

        for i, row in enumerate(rows):
            try:
                # Try multiple approaches to extract token data
                token_selectors = [
                    "div > div:nth-child(1) a",
                    "[class*='token'] a",
                    "a[href*='/token/']",
                    "div:first-child a"
                ]
                
                token = None
                for sel in token_selectors:
                    try:
                        token_elem = row.find_element(By.CSS_SELECTOR, sel)
                        token = token_elem.text.strip()
                        if token:
                            break
                    except:
                        continue
                
                if not token:
                    continue

                # Extract price, amount, value with fallbacks
                cells = row.find_elements(By.CSS_SELECTOR, "div")
                if len(cells) < 4:
                    continue

                try:
                    # Try to parse price from second cell
                    price_text = cells[1].text.strip()
                    price = 0.0
                    if price_text and '$' in price_text:
                        price = float(price_text.replace('$', '').replace(',', ''))
                except:
                    price = 0.0

                try:
                    # Try to parse amount from third cell
                    amount_text = cells[2].text.strip()
                    amount = float(amount_text.replace(',', '')) if amount_text else 0.0
                except:
                    amount = 0.0

                try:
                    # Try to parse USD value from fourth cell
                    value_text = cells[3].text.strip()
                    value_usd = 0.0
                    if value_text and '$' in value_text:
                        value_usd = float(value_text.replace('$', '').replace(',', ''))
                except:
                    value_usd = price * amount if price > 0 and amount > 0 else 0.0

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

        # Consolidate holdings across chains
        consolidated = {}
        for h in holdings:
            t = h["token"]
            if t in consolidated:
                consolidated[t]["amount"] += h["amount"]
                consolidated[t]["value_usd"] += h["value_usd"]
            else:
                consolidated[t] = h.copy()
        final_holdings = list(consolidated.values())

        # --- SCRAPE TRANSACTION HISTORY ---
        print(f"Loading transaction history for {wallet_address}")
        driver.get(f"https://debank.com/profile/{wallet_address}/history")
        time.sleep(8)

        # Try multiple selectors for transaction table
        tx_selectors = [
            "div.db-table-body > div",
            "[class*='history'] [class*='table'] [class*='body'] > div",
            "[class*='transaction'] div[class*='row']",
            "div[class*='list'] > div[class*='item']"
        ]
        
        tx_rows = []
        for selector in tx_selectors:
            try:
                tx_rows = driver.find_elements(By.CSS_SELECTOR, selector)
                if tx_rows:
                    print(f"Found {len(tx_rows)} transaction rows with selector: {selector}")
                    break
            except Exception as e:
                print(f"Transaction selector {selector} failed: {e}")
                continue

        transactions = []
        for i, row in enumerate(tx_rows[:25]):
            try:
                text = row.text.strip()
                if not text:
                    continue

                # Generate hash for transaction
                hsh = hashlib.md5(f"{text}{i}".encode()).hexdigest()

                # Try to extract transaction details
                cells = row.find_elements(By.CSS_SELECTOR, "div")
                if len(cells) < 2:
                    continue

                # Parse transaction type
                tx_type = cells[0].text.strip() if cells else "Unknown"

                # Parse amount and token
                amount_text = cells[1].text.strip() if len(cells) > 1 else ""
                token_match = re.search(r'\b([A-Z]{2,10})\b', amount_text)
                symbol = token_match.group(1) if token_match else ""

                # Parse USD value
                value_usd = 0.0
                for cell in cells:
                    cell_text = cell.text.strip()
                    if '$' in cell_text:
                        try:
                            value_usd = float(cell_text.replace('$', '').replace(',', ''))
                            break
                        except:
                            continue

                if value_usd <= 0:
                    continue

                transactions.append({
                    "hash": f"0x{hsh}",
                    "type": tx_type,
                    "amount": amount_text,
                    "token": symbol,
                    "value_usd": value_usd,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "from": wallet_address,
                    "to": "unknown"
                })
                print(f"Added transaction: {tx_type} = ${value_usd:,.2f}")

            except Exception as e:now
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
"""

if __name__ == "__main__":
    track_wallet()
