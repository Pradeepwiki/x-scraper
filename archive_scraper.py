#!/usr/bin/env python3
"""
Archive-based X.com Liked Posts Scraper

Reads the X data archive (like.js) and scrapes each liked tweet individually
using Playwright. Avoids rate limits of continuous scrolling by visiting
one tweet at a time with configurable delays.

Strategy:
  1. Parse like.js for all liked tweet IDs
  2. Load existing checkpoint to find what's already scraped (good quality)
  3. For each remaining tweet: visit the URL, extract full data, save to checkpoint
  4. Each tweet is saved immediately — crash-safe resume

Usage:
    python archive_scraper.py                   # Continue from where you left off
    python archive_scraper.py --count 1         # Test with 1 tweet
    python archive_scraper.py --count 50        # Process 50 tweets
    python archive_scraper.py --delay 8         # 8 seconds between tweets
    python archive_scraper.py --headless        # No visible browser
    python archive_scraper.py --list            # List remaining tweets
    python archive_scraper.py --download-media  # Also download images/videos
"""

import argparse
import asyncio
import json
import os
import random
import re
import sys
import tempfile
import shutil
from datetime import datetime
from urllib.parse import urljoin

# Reconfigure stdout/stderr to support UTF-8 on Windows consoles to prevent UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from playwright.async_api import async_playwright

import config

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
BASE_URL = "https://x.com"
ARCHIVE_FILE = "like.js"
USERNAME = config.X_USERNAME
CHECKPOINT_FILE = os.path.join(config.CHECKPOINT_DIR, f"checkpoint_{USERNAME}.json")

DEFAULT_DELAY = 5.0        # Base delay between tweet visits
ERROR_BACKOFF = 30.0       # Extra delay after an error
MAX_CONSECUTIVE_ERRORS = 10  # Stop after this many consecutive errors


# ─────────────────────────────────────────────────────────────────────────────
# FILE I/O
# ─────────────────────────────────────────────────────────────────────────────

def load_archive(path: str) -> list:
    """Parse like.js — extract tweet entries from the JS variable assignment."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    start_idx = content.index("[")
    end_idx = content.rindex("]") + 1
    data = json.loads(content[start_idx:end_idx])

    tweets = []
    for entry in data:
        like = entry.get("like", {})
        tweets.append({
            "tweetId": like.get("tweetId", ""),
            "fullText": like.get("fullText", ""),
            "expandedUrl": like.get("expandedUrl", ""),
        })
    return tweets


def load_checkpoint() -> dict:
    """Load existing checkpoint. Returns empty state if none exists."""
    if not os.path.exists(CHECKPOINT_FILE):
        return {"processed_urls": [], "results": [], "results_count": 0, "url_count": 0}
    with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_checkpoint(checkpoint: dict):
    """Save checkpoint to disk atomically."""
    os.makedirs(os.path.dirname(CHECKPOINT_FILE), exist_ok=True)
    checkpoint["results_count"] = len(checkpoint["results"])
    checkpoint["url_count"] = len(checkpoint["processed_urls"])

    # Write to temp file first, then rename (atomic on most OS)
    tmp_path = CHECKPOINT_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, indent=2, ensure_ascii=False)
    shutil.move(tmp_path, CHECKPOINT_FILE)


def get_good_tweet_ids(checkpoint: dict) -> set:
    """Get tweet IDs that have good-quality data (non-empty author + url)."""
    ids = set()
    for r in checkpoint.get("results", []):
        if r.get("author", "") and r.get("url", ""):
            ids.add(r["id"])
    return ids


def clean_html_entities(text: str) -> str:
    """Clean HTML entities from archive text."""
    return (text
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#x27;", "'")
        .replace("&quot;", '"'))


# ─────────────────────────────────────────────────────────────────────────────
# INTERSTITIAL DETECTION
# ─────────────────────────────────────────────────────────────────────────────

async def check_interstitial(page) -> dict:
    """Check for login walls, rate limits, error pages, etc."""
    result = {"detected": False, "type": "none", "detail": ""}

    try:
        url = page.url.lower()
        title = await page.title()

        # Login / signup redirect
        if "login" in url or "/i/flow" in url:
            result.update(detected=True, type="login", detail="Redirected to login")
            return result

        if any(w in title.lower() for w in ["signup", "log in", "login"]):
            result.update(detected=True, type="login", detail=f"Title: {title}")
            return result

        # Rate limit
        if "429" in url or "rate_limit" in url:
            result.update(detected=True, type="rate_limit", detail=f"URL: {url}")
            return result

        # Error page detection
        body_text = await page.evaluate(
            "() => document.body?.innerText?.substring(0, 500) || ''"
        )
        error_signals = [
            "something went wrong", "try again", "couldn't load",
            "this page doesn", "doesn't exist",
            "you are unable to view", "hmm...",
        ]
        body_lower = body_text.lower()
        for signal in error_signals:
            if signal in body_lower:
                result.update(detected=True, type="error_page", detail=f"'{signal}' in body")
                return result

        # Suspended account
        if "suspended" in body_lower:
            result.update(detected=True, type="suspended", detail="Account suspended")
            return result

    except Exception:
        pass

    return result


# ─────────────────────────────────────────────────────────────────────────────
# TWEET EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

async def wait_for_tweet_content(page, timeout_s: float = 20.0) -> bool:
    """Wait for tweet-specific DOM elements. Returns True if found."""
    selectors = [
        '[data-testid="tweetText"]',
        '[data-testid="User-Name"]',
        'article[data-testid="tweet"]',
        'time',
    ]
    for sel in selectors:
        try:
            await page.wait_for_selector(sel, timeout=timeout_s * 1000)
            return True
        except Exception:
            continue
    return False


async def find_tweet_element(page, tweet_id: str):
    """Find the specific article element representing the target tweet on the page."""
    try:
        articles = await page.query_selector_all('article[data-testid="tweet"]')
        if not articles:
            return None

        # Scenario 1: Look for the article containing a link with the specific status ID
        for article in articles:
            time_links = await article.query_selector_all('a[href*="/status/"]')
            for link in time_links:
                href = await link.get_attribute("href")
                if href and f"/status/{tweet_id}" in href:
                    return article

        # Scenario 2: If there's only one article on the page, use it
        if len(articles) == 1:
            return articles[0]

        # Scenario 3: Fallback - look for the main article by page layout or position
        return articles[0]
    except Exception:
        return None


async def extract_tweet(
    page,
    tweet_id: str,
    archive_text: str,
    video_poster_map: dict = None,
    captured_video_urls: list = None
) -> dict:
    """Extract full tweet data from a loaded tweet page.

    The data format matches exactly what the original scraper produces:
    {url, id, author, author_handle, timestamp, text, media, stats, thread_context, is_reply, reply_to}
    """
    data = {
        "url": "",
        "id": tweet_id,
        "author": "",
        "author_handle": "",
        "timestamp": "",
        "text": clean_html_entities(archive_text),
        "media": {"images": [], "videos": [], "urls": []},
        "stats": {},
        "thread_context": {},
        "is_reply": False,
        "reply_to": "",
    }

    try:
        # Stage 1: Wait for page load
        await page.wait_for_load_state("load", timeout=30000)
        await asyncio.sleep(1.5)

        # Check for interstitials
        interstitial = await check_interstitial(page)
        if interstitial["detected"]:
            print(f"\n    [!] {interstitial['type']}: {interstitial['detail']}")
            if interstitial["type"] in ("login", "rate_limit"):
                return data  # Can't extract anything useful

        # Stage 2: Wait for tweet content
        found = await wait_for_tweet_content(page, timeout_s=20.0)
        if not found:
            # Fallback: try networkidle
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            await asyncio.sleep(2.0)
            found = await wait_for_tweet_content(page, timeout_s=10.0)

        if not found:
            title = await page.title()
            print(f"\n    [!] No tweet content found. Title: {title}")

        # ── URL (may have redirected from /i/web/status/) ──
        current_url = page.url
        data["url"] = current_url.split("?")[0]
        status_match = re.search(r"/status/(\d+)", current_url)
        if status_match:
            data["id"] = status_match.group(1)

        # Locate the specific tweet container element to avoid grabbing info from replies/sidebar
        tweet_elem = await find_tweet_element(page, data["id"])
        elem = tweet_elem if tweet_elem else page

        # ── Author ──
        author_elem = await elem.query_selector('[data-testid="User-Name"]')
        if author_elem:
            author_text = await author_elem.inner_text()
            lines = author_text.strip().split("\n")
            if lines:
                data["author"] = lines[0]
            for line in lines:
                if line.startswith("@"):
                    data["author_handle"] = line
                    break

        # Build canonical URL from handle + ID if we have both
        if data["author_handle"] and data["id"]:
            handle = data["author_handle"].lstrip("@")
            data["url"] = f"{BASE_URL}/{handle}/status/{data['id']}"

        # ── Timestamp ──
        time_elem = await elem.query_selector("time")
        if time_elem:
            dt = await time_elem.get_attribute("datetime")
            if dt:
                data["timestamp"] = dt

        # ── Text ──
        text_elem = await elem.query_selector('[data-testid="tweetText"]')
        if text_elem:
            page_text = await text_elem.inner_text()
            if page_text and len(page_text.strip()) > 0:
                data["text"] = page_text

        # Try to trigger video play to get network responses
        try:
            video_el = await elem.query_selector('video')
            if video_el:
                player_container = await elem.query_selector('[data-testid="videoPlayer"]')
                if player_container:
                    await player_container.hover()
                    await asyncio.sleep(0.5)
                
                play_btn = await elem.query_selector('[data-testid="playButton"]')
                if play_btn:
                    await play_btn.click()
                else:
                    target_click = player_container if player_container else video_el
                    await target_click.click()
                
                # Wait for video requests to fire and be intercepted
                await asyncio.sleep(4.0)
        except Exception as e:
            print(f"\n    [!] Playback trigger issue: {e}")

        # ── Media ──
        data["media"] = await extract_media(elem, video_poster_map)

        # ── Stats ──
        data["stats"] = await extract_stats(elem)

        # ── Reply Context / Thread Context ──
        if tweet_elem:
            try:
                # Check for quoted tweets (nested articles)
                quoted_tweet = await tweet_elem.query_selector(
                    'article[data-testid="tweet"] article[data-testid="tweet"]'
                )
                if not quoted_tweet:
                    quoted_tweet = await tweet_elem.query_selector(
                        '[data-testid="card.wrapper"]'
                    )
                if quoted_tweet:
                    quoted_text_elem = await quoted_tweet.query_selector(
                        '[data-testid="tweetText"]'
                    )
                    if quoted_text_elem:
                        data["thread_context"]["quoted_text"] = await quoted_text_elem.inner_text()

                    quoted_author = await quoted_tweet.query_selector('[data-testid="User-Name"]')
                    if quoted_author:
                        q_text = await quoted_author.inner_text()
                        q_lines = q_text.strip().split("\n")
                        for line in q_lines:
                            if line.startswith("@"):
                                data["thread_context"]["quoted_author_handle"] = line
                                break
            except Exception:
                pass

        # ── Reply To (Parent in thread) ──
        try:
            parent_articles = await page.query_selector_all('article[data-testid="tweet"]')
            if len(parent_articles) > 1 and tweet_elem:
                # Find index of target tweet in articles
                target_idx = -1
                for idx, art in enumerate(parent_articles):
                    if art == tweet_elem:
                        target_idx = idx
                        break

                # If target tweet is not the first, it has a parent above it
                if target_idx > 0:
                    parent_art = parent_articles[target_idx - 1]
                    parent_time = await parent_art.query_selector("time")
                    if parent_time:
                        parent_anchor = await parent_time.evaluate_handle("el => el.closest('a')")
                        if parent_anchor:
                            pa_elem = parent_anchor.as_element()
                            if pa_elem:
                                parent_href = await pa_elem.get_attribute("href")
                                if parent_href:
                                    parent_id_match = re.search(r"/status/(\d+)", parent_href)
                                    if parent_id_match and parent_id_match.group(1) != data["id"]:
                                        data["is_reply"] = True
                                        data["reply_to"] = urljoin(BASE_URL, parent_href.split("?")[0])
                                        data["thread_context"]["reply_to_url"] = data["reply_to"]
                                        data["thread_context"]["is_reply"] = True
        except Exception:
            pass

        # ── Resolve Blob/Pending Video URLs ──
        if video_poster_map and captured_video_urls:
            resolved_videos = []
            for v_url in data["media"]["videos"]:
                if v_url.startswith("blob:"):
                    poster = video_poster_map.get(v_url, "")
                    if not poster:
                        # Try finding any value in poster map as fallback
                        for k, val in video_poster_map.items():
                            if val:
                                poster = val
                                break
                    if poster:
                        # Match video ID from poster URL
                        id_match = re.search(
                            r"/(ext_tw_video|amplify_video|tweet_video)_thumb/(\d+)/", poster
                        )
                        if id_match:
                            video_type = id_match.group(1)
                            vid_id = id_match.group(2)
                            matched_url = None
                            for captured in captured_video_urls:
                                if f"/{video_type}/{vid_id}/" in captured:
                                    matched_url = captured
                                    break
                            if not matched_url:
                                for captured in captured_video_urls:
                                    if f"/{video_type}/{vid_id}." in captured:
                                        matched_url = captured
                                        break
                            if matched_url:
                                resolved_videos.append(matched_url)
                else:
                    resolved_videos.append(v_url)
            # Filter remaining blob URLs
            data["media"]["videos"] = [v for v in resolved_videos if not v.startswith("blob:")]

    except Exception as e:
        print(f"\n    [!] Extraction error: {e}")

    return data


async def extract_media(elem, video_poster_map: dict = None) -> dict:
    """Extract images, videos, and external links from a tweet element."""
    media = {"images": [], "videos": [], "urls": []}

    try:
        # ── Images ──
        img_selectors = [
            'div[data-testid="tweetPhoto"] img',
            'img[src*="twimg.com/media"]',
        ]
        for selector in img_selectors:
            imgs = await elem.query_selector_all(selector)
            for img in imgs:
                src = await img.get_attribute("src")
                if src and "media" in src:
                    large_src = re.sub(r'&name=\w+', '&name=large', src)
                    if large_src not in media["images"]:
                        media["images"].append(large_src)

        # ── Videos ──
        video = await elem.query_selector('video')
        if video:
            src = await video.get_attribute("src")
            poster = await video.get_attribute("poster")

            if src and not src.startswith("blob:"):
                media["videos"].append(src)

            # Check <source> elements
            sources = await elem.query_selector_all("video source")
            for source in sources:
                s_src = await source.get_attribute("src")
                if s_src and not s_src.startswith("blob:") and s_src not in media["videos"]:
                    media["videos"].append(s_src)

            # Track poster for blob matching
            if src and src.startswith("blob:") and poster and video_poster_map is not None:
                video_poster_map[src] = poster

            # If still no video URL, try poster-based construction
            if not media["videos"] and poster:
                id_match = re.search(
                    r"/(ext_tw_video|amplify_video|tweet_video)_thumb/(\d+)/", poster
                )
                if id_match:
                    video_type = id_match.group(1)
                    vid_id = id_match.group(2)
                    constructed = f"https://video.twimg.com/{video_type}/{vid_id}/vid/avc1/720x720.mp4"
                    media["videos"].append(constructed)

            # If we still only have blob URL, store it as indicator
            if not media["videos"] and src and src.startswith("blob:"):
                pending_key = f"blob:pending-{len(media['videos'])}"
                if video_poster_map is not None and poster:
                    video_poster_map[pending_key] = poster
                media["videos"].append(pending_key)
            elif not media["videos"] and not src:
                # Video element exists but no src (lazy-loaded)
                pending_key = f"blob:pending-{len(media['videos'])}"
                if video_poster_map is not None and poster:
                    video_poster_map[pending_key] = poster
                media["videos"].append(pending_key)

        # ── External Links ──
        links = await elem.query_selector_all(
            'a[href*="http"]:not([href*="x.com"]):not([href*="twitter.com"]):not([href*="t.co"])'
        )
        seen = set()
        for link in links:
            href = await link.get_attribute("href")
            if href and href not in seen:
                seen.add(href)
                media["urls"].append(href)

    except Exception as e:
        print(f"\n    [!] Media extraction error: {e}")

    return media


async def extract_stats(elem) -> dict:
    """Extract engagement stats from a tweet element."""
    stats = {}
    selectors = {
        "replies": '[data-testid="reply"]',
        "retweets": '[data-testid="retweet"]',
        "likes": '[data-testid="like"]',
        "bookmarks": '[data-testid="bookmark"]',
    }
    for name, selector in selectors.items():
        try:
            elem_stat = await elem.query_selector(selector)
            if elem_stat:
                label = await elem_stat.get_attribute("aria-label")
                if label:
                    stats[name] = label
                else:
                    text = await elem_stat.inner_text()
                    if text and text.strip():
                        stats[name] = text.strip()
        except Exception:
            pass
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# MEDIA DOWNLOAD (optional)
# ─────────────────────────────────────────────────────────────────────────────

def download_tweet_media(tweet_data: dict):
    """Download images and videos for a tweet. Uses existing media_processor module."""
    try:
        from media_processor import download_media, download_video_with_ytdlp
        media = tweet_data.get("media", {})

        if media.get("images"):
            img_paths = download_media(media["images"], "images")
            if img_paths:
                media["downloaded_images"] = img_paths

        if media.get("videos"):
            tweet_url = tweet_data.get("url") or f"https://x.com/i/web/status/{tweet_data['id']}"
            vid_paths = download_video_with_ytdlp(tweet_url)
            
            # Fallback to direct download
            if not vid_paths:
                valid_videos = [v for v in media.get("videos", []) if not v.startswith("blob:")]
                if valid_videos:
                    vid_paths = download_media(valid_videos, "videos")
                    
            if vid_paths:
                media["downloaded_videos"] = vid_paths

    except Exception as e:
        print(f"\n    [!] Media download error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PROCESSING LOOP
# ─────────────────────────────────────────────────────────────────────────────

async def process_tweets(
    archive_tweets: list,
    max_count: int = None,
    delay: float = DEFAULT_DELAY,
    headless: bool = False,
    do_download_media: bool = False,
    target_tweet_id: str = None,
):
    """Process unscraped tweets from the archive one by one."""

    # Load checkpoint and determine what's already done
    checkpoint = load_checkpoint()
    good_ids = get_good_tweet_ids(checkpoint)
    processed_urls = list(checkpoint.get("processed_urls", []))
    results = list(checkpoint.get("results", []))

    print(f"  [*] Checkpoint: {len(results)} posts, {len(good_ids)} good tweet IDs")

    # Build work queue: archive tweets not yet in good_ids
    to_process = []
    for tweet in archive_tweets:
        tid = tweet["tweetId"]
        if target_tweet_id:
            if tid == target_tweet_id:
                to_process.append(tweet)
                break
        else:
            if tid not in good_ids:
                to_process.append(tweet)
            if max_count and len(to_process) >= max_count:
                break

    print(f"  [*] Tweets to process: {len(to_process)}")
    if not to_process:
        print("  [v] Nothing to process — all tweets already scraped!")
        return

    # Launch browser
    print(f"  [*] Starting browser (headless={headless})...")
    print(f"  [*] Delay between tweets: {delay}s\n")

    session_dir = tempfile.mkdtemp(prefix="x_archive_scraper_")
    consecutive_errors = 0

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=session_dir,
            headless=headless,
            channel="chrome",
            no_viewport=False,
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )

        # Set auth cookies
        cookies = [
            {"name": "auth_token", "value": config.AUTH_TOKEN, "domain": ".x.com",
             "path": "/", "httpOnly": True, "secure": True, "sameSite": "Lax"},
            {"name": "auth_token", "value": config.AUTH_TOKEN, "domain": ".twitter.com",
             "path": "/", "httpOnly": True, "secure": True, "sameSite": "Lax"},
        ]
        if config.CT0_TOKEN:
            for domain in [".x.com", ".twitter.com"]:
                cookies.append({
                    "name": "ct0", "value": config.CT0_TOKEN, "domain": domain,
                    "path": "/", "httpOnly": False, "secure": True, "sameSite": "Lax",
                })
        await context.add_cookies(cookies)

        page = await context.new_page()

        # Anti-detection
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """)

        # Set up video URL interception
        captured_video_urls = []
        video_poster_map = {}

        async def _capture_video_response(response):
            url = response.url
            if 'video.twimg.com' in url and '.mp4' in url:
                base_url = url.split('?')[0]
                if base_url not in [v.split('?')[0] for v in captured_video_urls]:
                    captured_video_urls.append(url)

        page.on('response', _capture_video_response)

        # Warm-up: visit x.com to establish session
        print("  [*] Warming up session...")
        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3.0)
            interstitial = await check_interstitial(page)
            if interstitial["detected"]:
                print(f"  [!] Warm-up issue: {interstitial['type']} — {interstitial['detail']}")
                print("  [!] Your auth_token may need refreshing.")
            else:
                print(f"  [v] Session OK: {page.url[:60]}")
        except Exception as e:
            print(f"  [*] Warm-up had an issue: {e}")
            print("  [*] Continuing anyway...")

        # ── Process each tweet ──
        total_archive = len(archive_tweets)
        new_count = 0

        for idx, tweet in enumerate(to_process):
            tweet_id = tweet["tweetId"]
            archive_text = tweet["fullText"]
            tweet_url = f"{BASE_URL}/i/web/status/{tweet_id}"

            # Progress display
            progress = f"[{idx + 1}/{len(to_process)}]"
            preview = clean_html_entities(archive_text)[:60].replace("\n", " ").strip()
            print(f"{progress} {tweet_id} | {preview}...", end="", flush=True)

            try:
                # Navigate to tweet
                await page.goto(tweet_url, wait_until="domcontentloaded", timeout=60000)

                # Extract data
                tweet_data = await extract_tweet(
                    page,
                    tweet_id,
                    archive_text,
                    video_poster_map=video_poster_map,
                    captured_video_urls=captured_video_urls
                )

                # Determine quality
                has_author = bool(tweet_data.get("author"))
                has_url = bool(tweet_data.get("url"))
                has_stats = bool(tweet_data.get("stats"))

                quality = "v" if (has_author and has_url) else "~"

                # Store result
                store_url = tweet_data["url"] or tweet_url
                if store_url not in processed_urls:
                    processed_urls.append(store_url)
                results.append(tweet_data)
                good_ids.add(tweet_data["id"])

                img_count = len(tweet_data["media"].get("images", []))
                vid_count = len(tweet_data["media"].get("videos", []))
                author_handle = tweet_data.get("author_handle", "?")

                print(f" [{quality}] {author_handle} | {img_count}img {vid_count}vid", flush=True)

                # Download media if requested
                if do_download_media:
                    download_tweet_media(tweet_data)

                new_count += 1
                consecutive_errors = 0

                # Save checkpoint after each tweet
                save_checkpoint({
                    "processed_urls": processed_urls,
                    "results": results,
                })

            except Exception as e:
                consecutive_errors += 1
                print(f" [!] Error: {e}", flush=True)

                # Save progress
                save_checkpoint({
                    "processed_urls": processed_urls,
                    "results": results,
                })

                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    print(f"\n  [!] {MAX_CONSECUTIVE_ERRORS} consecutive errors. Stopping.")
                    print(f"  [*] You may need to refresh your auth_token.")
                    break

                print(f"  [*] Backing off {ERROR_BACKOFF}s...")
                await asyncio.sleep(ERROR_BACKOFF)
                continue

            # Delay between tweets (not after the last one)
            if idx < len(to_process) - 1:
                jitter = random.uniform(0.5, 2.0)
                wait = delay + jitter
                await asyncio.sleep(wait)

        # Close browser
        await context.close()

    # Cleanup temp dir
    try:
        shutil.rmtree(session_dir, ignore_errors=True)
    except Exception:
        pass

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  DONE: {new_count} new tweets scraped")
    print(f"  TOTAL: {len(results)} posts in checkpoint")
    print(f"  REMAINING: ~{len(archive_tweets) - len(good_ids)} tweets left")
    print(f"{'=' * 60}")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def list_remaining(archive_tweets: list):
    """Show remaining tweets without scraping."""
    checkpoint = load_checkpoint()
    good_ids = get_good_tweet_ids(checkpoint)

    remaining = []
    for i, tweet in enumerate(archive_tweets):
        if tweet["tweetId"] not in good_ids:
            remaining.append((i, tweet))

    print(f"\n  Remaining tweets to scrape: {len(remaining)}")
    print(f"  {'─' * 70}")
    for idx, tweet in remaining[:30]:
        preview = clean_html_entities(tweet["fullText"])[:80].replace("\n", " ").strip()
        print(f"  [{idx:>4}] {tweet['tweetId']} | {preview}")
    if len(remaining) > 30:
        print(f"  ... and {len(remaining) - 30} more")
    print(f"  {'─' * 70}")


def main():
    parser = argparse.ArgumentParser(
        description="Scrape X.com liked posts from archive (like.js)"
    )
    parser.add_argument(
        "--count", type=int, default=None,
        help="Max tweets to process (default: all remaining)"
    )
    parser.add_argument(
        "--delay", type=float, default=DEFAULT_DELAY,
        help=f"Delay between tweets in seconds (default: {DEFAULT_DELAY})"
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run browser without visible window"
    )
    parser.add_argument(
        "--list", action="store_true",
        help="Just list remaining tweets, don't scrape"
    )
    parser.add_argument(
        "--download-media", action="store_true", dest="download_media",
        help="Download images and videos after scraping each tweet"
    )
    parser.add_argument(
        "--no-download-media", action="store_false", dest="download_media",
        help="Do not download images and videos after scraping each tweet"
    )
    parser.set_defaults(download_media=config.DOWNLOAD_MEDIA)

    parser.add_argument(
        "--tweet-id", type=str, default=None,
        help="Scrape a specific tweet ID directly from the archive"
    )

    args = parser.parse_args()

    # Load archive
    print(f"[*] Loading archive: {ARCHIVE_FILE}")
    archive_tweets = load_archive(ARCHIVE_FILE)
    print(f"[*] Found {len(archive_tweets)} tweets in archive")

    if args.list:
        list_remaining(archive_tweets)
        return

    if args.count:
        print(f"[*] Max tweets to process: {args.count}")
    if args.tweet_id:
        print(f"[*] Targeting specific tweet ID: {args.tweet_id}")
    print()

    asyncio.run(process_tweets(
        archive_tweets=archive_tweets,
        max_count=args.count,
        delay=args.delay,
        headless=args.headless,
        do_download_media=args.download_media,
        target_tweet_id=args.tweet_id,
    ))


if __name__ == "__main__":
    main()
