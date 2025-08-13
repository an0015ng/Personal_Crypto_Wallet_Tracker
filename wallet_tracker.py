#!/usr/bin/env python
# coding: utf-8

# In[1]:


# install required libraries
get_ipython().system('pip install requests beautifulsoup4 lxml smtplib email')


# In[89]:


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

# Add these two:
import random
from selenium import webdriver


# File to store seen transactions
SEEN_TRANSACTIONS_FILE = "seen_transactions.json"


# In[90]:


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

# Initialize seen transactions
seen_transactions = load_seen_transactions()
print(f"Loaded {len(seen_transactions)} previously seen transactions")


# In[91]:


def scrape_debank_wallet(wallet_address):
    """
    Scrape wallet data from DeBank profile page
    Returns: (transactions_list, holdings_list) or (None, None) if failed
    """
    url = f"https://debank.com/profile/{wallet_address}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
    }
    
    try:
        print(f"Fetching data from: {url}")
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        # Parse HTML
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract transaction data (this is simplified - DeBank uses JS loading)
        # For now, we'll look for any transaction-like patterns in the HTML
        transactions = []
        holdings = []
        
        # Try to find script tags that might contain JSON data
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string and 'transaction' in script.string.lower():
                # This would need more sophisticated parsing in reality
                print("Found potential transaction data in script tag")
                
        print(f"Successfully fetched page (length: {len(response.content)} bytes)")
        return transactions, holdings
        
    except requests.RequestException as e:
        print(f"Error fetching DeBank data: {e}")
        return None, None


# In[99]:


def scrape_debank_wallet_real(wallet_address):
    """
    Selenium scraper for DeBank portfolio & history tables.
    Returns (transactions, holdings) or (None, None) on failure.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    import time, hashlib
    from datetime import datetime

    # Headless Chrome setup
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30)

    try:
        # --- SCRAPE PORTFOLIO HOLDINGS ---
        driver.get(f"https://debank.com/profile/{wallet_address}")
        time.sleep(5)  # Wait for table to load

        rows = driver.find_elements(
            By.CSS_SELECTOR,
            "div.db-table.TokenWallet_table__bmN1O div.db-table-body.is-noEndBorder > div"
        )
        holdings = []
        seen = set()

        for row in rows:
            try:
                token = row.find_element(By.CSS_SELECTOR, "div > div:nth-child(1) a").text.strip()
                if not token:
                    continue

                price_text = row.find_element(By.CSS_SELECTOR, "div > div:nth-child(2)").text.strip()
                price = float(price_text.lstrip("$").replace(",", ""))

                amt_text = row.find_element(By.CSS_SELECTOR, "div > div:nth-child(3)").text.strip()
                amount = float(amt_text.replace(",", ""))

                val_text = row.find_element(By.CSS_SELECTOR, "div > div:nth-child(4)").text.strip()
                value_usd = float(val_text.lstrip("$").replace(",", ""))

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
            except:
                continue

        # Consolidate across chains
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
        time.sleep(5)  # Wait for history table

        tx_rows = driver.find_elements(
            By.CSS_SELECTOR,
            "div.db-table.Body_history__bmN1O div.db-table-body > div"
        )
        transactions = []
        for i, r in enumerate(tx_rows[:25]):
            try:
                cols = r.find_elements(By.CSS_SELECTOR, "div")
                text = r.text.strip()
                if len(cols) < 3 or not text:
                    continue

                hsh = hashlib.md5(f"{text}{i}".encode()).hexdigest()
                value = float(cols[2].text.strip().lstrip("$").replace(",", ""))
                if value <= 0:
                    continue

                # Extract token symbol from second column
                token_match = re.search(r"\b[A-Za-z0-9]{2,10}\b", cols[1].text)
                symbol = token_match.group(0) if token_match else ""

                transactions.append({
                    "hash": f"0x{hsh}",
                    "type": cols[0].text.strip(),
                    "amount": cols[1].text.strip(),
                    "token": symbol,
                    "value_usd": value,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "from": wallet_address,
                    "to": "unknown"
                })
            except:
                continue

        return transactions, final_holdings

    finally:
        driver.quit()


# In[93]:


def calculate_holdings_percentages(holdings):
    """Calculate percentage of each holding"""
    if not holdings:
        return []
    
    total_value = sum(holding['value_usd'] for holding in holdings)
    
    holdings_with_percentages = []
    for holding in holdings:
        percentage = (holding['value_usd'] / total_value) * 100 if total_value > 0 else 0
        holdings_with_percentages.append({
            **holding,
            'percentage': round(percentage, 2)
        })
    
    return holdings_with_percentages


# In[94]:


def filter_spam_transactions(transactions):
    """
    Filter out spam transactions based on simple heuristics
    """
    filtered = []
    
    for tx in transactions:
        # Skip if transaction value is too low (likely spam)
        if tx.get('value_usd', 0) < 10:
            print(f"Filtering low-value transaction: ${tx.get('value_usd', 0)}")
            continue
            
        # Skip if transaction type indicates internal/spam
        tx_type = tx.get('type', '').lower()
        if any(spam_word in tx_type for spam_word in ['internal', 'airdrop', 'faucet', 'spam']):
            print(f"Filtering spam transaction type: {tx_type}")
            continue
            
        # Add more filters as needed
        filtered.append(tx)
    
    return filtered


# In[101]:


def send_email_notification(new_transactions, current_holdings):
    """
    Send email notification every run:
      - Lists significant new transactions (value > $10,000)
      - Shows top 10 current holdings by USD value
    """
    try:
        # Filter transactions > $10,000
        significant_txs = [tx for tx in new_transactions if tx.get('value_usd', 0) > 10000]
        
        # Sort holdings descending by USD value and take top 10
        sorted_holdings = sorted(current_holdings, key=lambda h: h['value_usd'], reverse=True)
        top_holdings = sorted_holdings[:10]
        
        # Build email message
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = NOTIFY_EMAIL
        msg['Subject'] = f"DeBank Notification: {len(significant_txs)} Transactions + Top Holdings"
        
        # HTML body start
        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.4;">
          <h2>📬 DeBank Wallet Update</h2>
          <p><strong>Wallet:</strong> {WALLET_ADDRESS}</p>
          <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        """
        
        # Section: Significant New Transactions
        html += "<h3>🚨 Significant New Transactions (>$10,000)</h3>"
        if significant_txs:
            html += "<ul>"
            for tx in significant_txs:
                html += f"""
                  <li>
                    <strong>{tx['type']}</strong> —
                    {tx['amount']} {tx['token']} 
                    (<span style="color: green;">${tx['value_usd']:,.2f}</span>)<br>
                    <small>Hash: {tx['hash']}</small>
                  </li>
                """
            html += "</ul>"
        else:
            html += "<p>No new transactions above $10,000.</p>"

        # Calculate total portfolio value
        total_value = sum(h['value_usd'] for h in current_holdings)

        # Footer before holdings
        html += f"<h2><strong>Total Portfolio Value:</strong> ${total_value:,.2f}</h2>"
        
        # Section: Top 10 Current Holdings
        html += "<h3>💰 Top 10 Current Holdings</h3>"
        html += """
          <table border="1" cellpadding="5" cellspacing="0" 
                 style="border-collapse: collapse; width:100%; max-width:600px;">
            <tr style="background:#f0f0f0;">
              <th align="left">Rank</th>
              <th align="left">Token</th>
              <th align="right">Amount</th>
              <th align="right">Value (USD)</th>
              <th align="right">%</th>
            </tr>
        """
        total_value = sum(h['value_usd'] for h in current_holdings) or 1
        for idx, h in enumerate(top_holdings, start=1):
            pct = h['value_usd'] / total_value * 100
            html += f"""
            <tr>
              <td>{idx}</td>
              <td>{h['token']}</td>
              <td align="right">{h['amount']:,}</td>
              <td align="right">${h['value_usd']:,.2f}</td>
              <td align="right">{pct:.2f}%</td>
            </tr>
            """
        html += "</table>"
        
        # Footer
        html += """
          <p style="font-size:0.9em; color:#555;">
            (Automated notification from your DeBank tracker.)
          </p>
        </body>
        </html>
        """
        
        # Attach and send
        msg.attach(MIMEText(html, 'html'))
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
        
        print("✅ Email sent successfully.")
        
    except Exception as e:
        print(f"❌ Error sending email: {e}")


# In[96]:


def track_wallet():
    """Main function to track wallet and send notifications"""
    global seen_transactions
    
    print(f"\n🔍 Checking wallet: {WALLET_ADDRESS}")
    print(f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Get real wallet data (no more mock data)
    transactions, holdings = get_wallet_data()
    
    if transactions is None or holdings is None:
        print("❌ Failed to fetch wallet data")
        return
    
    # Filter spam transactions
    transactions = filter_spam_transactions(transactions)
    
    # Find new transactions
    new_transactions = []
    for tx in transactions:
        tx_hash = tx.get('hash')
        if tx_hash and tx_hash not in seen_transactions:
            new_transactions.append(tx)
            seen_transactions.add(tx_hash)
    
    # Calculate holdings percentages
    holdings_with_percentages = calculate_holdings_percentages(holdings)
    
    print(f"📊 Found {len(transactions)} total transactions ({len(new_transactions)} new)")
    print(f"💼 Portfolio has {len(holdings_with_percentages)} holdings")
    
    # Display current holdings with multi-chain info
    if holdings_with_percentages:
        print("\n💰 Current Holdings:")
        for holding in holdings_with_percentages:
            chains_info = " + ".join(holding.get('chains', ['unknown']))
            print(f"  {holding['token']}: {holding['amount']} (${holding['value_usd']:,.2f}) - {holding['percentage']}% - Chains: {chains_info}")

    # Send notification if there are new transactions
    send_email_notification(new_transactions, holdings_with_percentages)
    
    # # Send notification if there are new transactions
    # if new_transactions:
    #     print(f"\n🚨 {len(new_transactions)} new transaction(s) detected!")
    #     for tx in new_transactions:
    #         print(f"  - {tx.get('type', 'Unknown')}: {tx.get('amount', 'N/A')} {tx.get('token', 'TOKEN')} (${tx.get('value_usd', 0):.2f})")
        
    #     send_email_notification(new_transactions, holdings_with_percentages)
    # else:
    #     print("✅ No new transactions")
    
    # Save seen transactions
    save_seen_transactions(seen_transactions)
    print("💾 Saved transaction history")


# In[102]:


# Test the complete system with real scraping
print("🧪 Testing complete wallet tracking system...")
track_wallet()


# In[2]:


def run_continuous_monitoring(check_interval_minutes=1):
    """
    Run continuous monitoring (for testing only)
    In production, use cloud scheduling instead
    """
    print(f"🔄 Starting continuous monitoring every {check_interval_minutes} minutes")
    print("Press Ctrl+C to stop")
    
    try:
        while True:
            track_wallet()
            print(f"⏳ Waiting {check_interval_minutes} minutes until next check...")
            time.sleep(check_interval_minutes)
    except KeyboardInterrupt:
        print("\n🛑 Monitoring stopped by user")

# Uncomment to test continuous monitoring (WARNING: This will run forever)
# run_continuous_monitoring(check_interval_minutes=1)  # Check every 1 minute for testing


# In[ ]:





# In[ ]:





# In[ ]:





# In[ ]:




