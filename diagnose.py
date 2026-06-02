"""Diagnostic: test if X.com auth_token and likes page load correctly."""
import asyncio
import os
import sys
from playwright.async_api import async_playwright

AUTH_TOKEN = "6608f4f4b3ae84c45c10313d43a3da45fb8978fa"
USERNAME = "NephliumDark"

async def main():
    p = await async_playwright().start()
    
    # Create a totally fresh temp directory
    import tempfile
    session_dir = tempfile.mkdtemp(prefix="x_diag_")
    
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
    
    # Add same anti-detection as the scraper
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    """)
    
    print("=" * 60)
    print("STEP 1: Navigate to x.com homepage")
    print("=" * 60)
    try:
        await page.goto("https://x.com", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)
        print(f"URL: {page.url[:100]}")
        print(f"Title: {(await page.title())[:100]}")
        body = (await page.inner_text("body"))[:300].replace("\n", " ").strip()
        print(f"Body: {body[:300]}")
    except Exception as e:
        print(f"ERROR: {e}")
    
    print()
    print("=" * 60)
    print(f"STEP 2: Navigate to {USERNAME}/likes")
    print("=" * 60)
    try:
        await page.goto(f"https://x.com/{USERNAME}/likes", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)
        print(f"URL: {page.url[:100]}")
        print(f"Title: {(await page.title())[:100]}")
        body = (await page.inner_text("body"))[:400].replace("\n", " ").strip()
        print(f"Body: {body[:400]}")
        
        # Check for rate limit signals
        body_lower = body.lower()
        if "rate limit" in body_lower or "too many requests" in body_lower or "429" in body_lower:
            print("\n>>> RATE LIMITED <<<")
        elif "log in" in body_lower or "sign up" in body_lower:
            print("\n>>> LOGIN WALL <<<")
        else:
            print("\n>>> PAGE LOADED OK <<<")
    except Exception as e:
        print(f"ERROR: {e}")
    
    await p.stop()
    
    # Clean up temp dir
    import shutil
    shutil.rmtree(session_dir, ignore_errors=True)

if __name__ == "__main__":
    asyncio.run(main())
