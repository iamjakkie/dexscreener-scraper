import asyncio
from telebot.async_telebot import AsyncTeleBot
import os
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
import time
from dotenv import load_dotenv
import boto3
import json
from threading import local
from selenium.webdriver.chrome.options import Options
import threading


load_dotenv()

S3_BUCKET = "bijaj"
S3_KEY = "tokens.json"


CHAT_ID = -1002346609516
TOKEN = os.getenv("TOKEN")

session = boto3.Session(
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY"),
    aws_secret_access_key=os.getenv("AWS_SECRET_KEY")
)
s3_client = session.client("s3")

# Thread-local storage for the driver
thread_local = local()

def get_driver():
    # return Driver(uc=True, headless=True)
    return Driver(
        uc=True,  # Undetected Chrome is not used for remote WebDriver
        browser="chrome",  # Use Chrome browser
    )

def fetch_data():
    """
    Fetch data from Dexscreener using a persistent ChromeDriver instance.
    """
    # Check if the driver is already initialized in the current thread
    print(f"Thread: {threading.current_thread().name}")
    if not hasattr(thread_local, "driver"):
        print("Initializing ChromeDriver...")
        thread_local.driver = get_driver()
    driver = thread_local.driver
    # Open the target URL
    url = "https://dexscreener.com/?rankBy=trendingScoreH6&order=desc&chainIds=solana&dexIds=raydium&minLiq=45000&minMarketCap=500000&maxMarketCap=10000000&maxAge=168&min24HTxns=500"
    thread_local.driver.uc_open_with_reconnect(url, reconnect_time=6)

    tokens = []

    try:
        print("Refreshing page...")
        driver.refresh()
        print("Waiting for elements to load...")
        try:
            WebDriverWait(driver, 40).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "a.ds-dex-table-row.ds-dex-table-row-top"))
            )
        except Exception as wait_error:
            print(f"Wait error: {wait_error}")
            raise
        print("Elements loaded.")
        print("Page refreshed. Fetching elements...")
        elements = driver.find_elements(By.CSS_SELECTOR, "a.ds-dex-table-row.ds-dex-table-row-top")
        print(f"Found {len(elements)} elements.")


        for element in elements:
            data = element.text.split("\n")
            data_len = len(data)
            link = element.get_attribute("href")
            a = data[1]
            b = data[2]
            if a == 'CLMM' or a == '?':
                symbol = b
                name = data[5]
            elif data_len == 18:
                symbol = data[3]
                name = data[6]
            else:
                symbol = a
                name = data[4]

            coin = {
                "link": link,
                "symbol": symbol,
                "name": name,
                "price": data[-11],
                "age": data[-10],
                "txns": data[-9],
                "volume": data[-8],
                "5m": data[-6],
                "1h": data[-5],
                "6h": data[-4],
                "24h": data[-3],
                "liquidity": data[-2],
                "market_cap": data[-1],
            }
            tokens.append(coin)
    except Exception as e:
        print(f"Error fetching data: {e}")
        # Handle driver disconnection by reinitializing
        if hasattr(thread_local, "driver"):
            print("Reinitializing ChromeDriver due to error...")
            thread_local.driver.quit()
            del thread_local.driver

        return []  # Return an empty list to avoid breaking the loop
    
    return tokens 

async def get_dexscreener_data(queue: asyncio.Queue):
    """
    Continuously fetch data using `fetch_data` and push results to a queue.
    """
    try:
        while True:
            print("Fetching data...")
            # Run the synchronous fetch_data function in a separate thread
            tokens = await asyncio.to_thread(fetch_data)
            if tokens:
                print(f"Pushing {len(tokens)} tokens to the queue.")
                await queue.put(tokens)
            await asyncio.sleep(60)  # Non-blocking wait for the next fetch
    except asyncio.CancelledError:
        print("Task cancelled, cleaning up resources.")
        # Cleanup thread-local driver
        if hasattr(thread_local, "driver"):
            thread_local.driver.quit()
            print("ChromeDriver closed.")

async def run_telegram_bot(bot: AsyncTeleBot, queue: asyncio.Queue):
    """
    Telegram bot task: sends notifications when new tokens are available.
    """
    seen_tokens = load_seen_tokens_S3()
    print(f"Loaded {len(seen_tokens)} seen tokens from S3.")
    while True:
        tokens = await queue.get()
        print(f"Received {len(tokens)} tokens from the queue.")
        new_tokens = [token for token in tokens if token['link'] not in seen_tokens]

        if new_tokens:
            msg = ""
            for token in new_tokens:
                link = token['link']
                msg += (
                    f"🚨 New Coin Alert 🚨\n"
                    f"Name: {token['name']} Symbol: {token['symbol']} Price: {token['price']}\n"
                    f"Market Cap: {token['market_cap']}\n"
                    f"Link: {link}\n\n")
                seen_tokens.add(link)
            await send_large_msg(bot, msg)

            save_seen_tokens_S3(seen_tokens)
                
        queue.task_done()

async def send_large_msg(bot, msg):
    message_chunks = [msg[i:i+4000]
                      for i in range(0, len(msg), 4000)]
    
    for chunk in message_chunks:
        try:
            await bot.send_message(CHAT_ID, chunk)
        except Exception as e:
            print(f"Error sending message: {e}")
            pass

def load_seen_tokens_S3():
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=S3_KEY)
        return set(json.loads(response["Body"].read()))
    except s3_client.exceptions.NoSuchKey:
        return set()

def save_seen_tokens_S3(seen_tokens):
    s3_client.put_object(
        Bucket=S3_BUCKET,
        Key=S3_KEY,
        Body=json.dumps(list(seen_tokens))
    )

async def main():
    # Shared queue for communication
    data_queue = asyncio.Queue()

    # Initialize Telegram bot
    bot = AsyncTeleBot(TOKEN)

    await bot.send_message(CHAT_ID, "BOT STARTED")
    # Run tasks concurrently
    fetcher_task = asyncio.create_task(get_dexscreener_data(data_queue))
    telegram_task = asyncio.create_task(run_telegram_bot(bot, data_queue))
    
    try:
        await asyncio.gather(fetcher_task, telegram_task)
    except KeyboardInterrupt:
        print("Shutting down...")
        fetcher_task.cancel()
        telegram_task.cancel()
        await asyncio.gather(fetcher_task, telegram_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())