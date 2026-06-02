"""Diagnostic 2: check what X.com actually renders."""
import asyncio, os, sys, tempfile, shutil
from playwright.async_api import async_playwright

AUTH_TOKEN = "6608f4f4b3ae84c45c10313d43a3da45fb8978fa"
USERNAME = "NephliumDark"

async def main():
    p = await async_playwright().start()
    session_dir = tempfile.mkdtemp(prefix="x_diag2_")
    
    ctx = await p.chromium.launch_persistent_context(
        user_data_dir=session_dir,
        headless=True,
        channel="chrome",
        no_viewport=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        args=["--disable-blink-features=AutomationControlled"],
    )
    
    await ctx.add_cookies([{
        "name": "auth_token",
        "value": AUTH_TOKEN,
        "domain": ".x.com",
        "path": "/",
    }])
    
    page = await ctx.new_page()
    
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    """)
    
    # Step 1: Go to homepage
    print("=== STEP 1: x.com homepage ===")
    await page.goto("https://x.com", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(10)  # Wait longer for JS to render
    
    print(f"URL: {page.url}")
    print(f"Title: '{await page.title()}'")
    
    # Check actual HTML
    html = await page.content()
    print(f"HTML length: {len(html)}")
    print(f"Has 'data-testid': {'data-testid' in html}")
    print(f"Has 'tweet': {'tweet' in html.lower()}")
    print(f"Has 'article': {'<article' in html}")
    
    # Check for specific elements
    tweets = await page.query_selector_all('article[data-testid="tweet"]')
    print(f"Tweet article count: {len(tweets)}")
    
    body_text = (await page.inner_text("body"))[:500].replace("\n", " ").strip()
    print(f"Body text: '{body_text[:200]}'")
    
    # Check for any text content
    all_text = (await page.inner_text("body")).strip()
    print(f"Total body text length: {len(all_text)}")
    
    print()
    print("=== STEP 2: Likes page ===")
    await page.goto(f"https://x.com/{USERNAME}/likes", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(10)  # Wait longer
    
    print(f"URL: {page.url}")
    print(f"Title: '{await page.title()}'")
    
    html = await page.content()
    print(f"HTML length: {len(html)}")
    print(f"Has 'tweet': {'tweet' in html.lower()}")
    
    tweets = await page.query_selector_all('article[data-testid="tweet"]')
    print(f"Tweet article count: {len(tweets)}")
    
    body_text = (await page.inner_text("body"))[:500].replace("\n", " ").strip()
    print(f"Body text: '{body_text[:200]}'")
    
    # Check for specific error signals
    body_lower = body_text.lower()
    if "rate limit" in body_lower:
        print(">>> RATE LIMITED <<<")
    elif "429" in body_lower:
        print(">>> 429 ERROR <<<")
    elif "something went wrong" in body_lower:
        print(">>> SOMETHING WENT WRONG <<<")
    elif "log in" in body_lower or "sign up" in body_lower:
        print(">>> LOGIN WALL <<<")
    else:
        print(">>> No obvious errors <<<")
    
    await p.stop()
    shutil.rmtree(session_dir, ignore_errors=True)

if __name__ == "__main__":
    asyncio.run(main())
