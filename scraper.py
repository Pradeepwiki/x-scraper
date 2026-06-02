"""
X.com Likes Scraper - Core module
Uses Playwright to extract liked posts with full context.
"""

import asyncio
import json
import os
import random
import re
import shutil
import tempfile
import time
from datetime import datetime
from typing import Dict, List, Optional, Set
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page, BrowserContext
# tqdm removed - using simple print statements instead

import config


class XScraper:
    """Scrapes liked posts from X.com using browser automation."""

    BASE_URL = "https://x.com"

    def __init__(self, resume: bool = False):
        self.results = []
        self.processed_urls: Set[str] = set()
        self.playwright = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._captured_video_urls: List[str] = []
        self._video_poster_map: Dict[str, str] = {}
        self._resume = resume
        self._retry_count = 0
        self._checkpoint_path = ""  # Set once username is known
        self._checkpoint_dirty = False
        self._likes_url = ""  # Stored for rate-limit retry reloads
        self._page_crashed = False  # Flag set by crash listener
        self._session_dirs = []  # Tracked for cleanup in close()

    def _checkpoint_file(self, username: str) -> str:
        """Get the checkpoint file path for a given username."""
        checkpoint_dir = config.CHECKPOINT_DIR
        os.makedirs(checkpoint_dir, exist_ok=True)
        return os.path.join(checkpoint_dir, f"checkpoint_{username}.json")

    def _save_checkpoint(self):
        """Save current progress to a checkpoint file."""
        if not self._checkpoint_path:
            return
        # Preserve scraped post data so resume produces a complete combined output
        max_saved = 500  # cap to keep files small
        checkpoint = {
            "processed_urls": list(self.processed_urls),
            "results": self.results[:max_saved],
            "results_count": len(self.results),
            "url_count": len(self.processed_urls),
        }
        try:
            with open(self._checkpoint_path, "w", encoding="utf-8") as f:
                json.dump(checkpoint, f, indent=2)
            self._checkpoint_dirty = False
        except Exception as e:
            print(f"  [!] Failed to save checkpoint: {e}")

    def _load_checkpoint(self, username: str) -> bool:
        """Load checkpoint for a username. Returns True if checkpoint was found and applied."""
        path = self._checkpoint_file(username)
        if not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                checkpoint = json.load(f)
            self.processed_urls = set(checkpoint.get("processed_urls", []))
            self.results = checkpoint.get("results", [])
            restored_count = len(self.results)
            print(f"[v] Resuming from checkpoint: {restored_count} posts restored")
            print(f"     ({len(self.processed_urls)} tweet URLs already seen)")
            return True
        except Exception as e:
            print(f"[!] Failed to load checkpoint: {e}")
            return False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        """Clean up browser resources and temp directories."""
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass

        # Clean up temp session directories
        for d in self._session_dirs:
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass

    async def _random_delay(self, min_s=1.0, max_s=3.0):
        """Human-like random delay."""
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def _setup_cookies(self):
        """Set authentication cookies on the browser context."""
        cookies = [
            {
                "name": "auth_token",
                "value": config.AUTH_TOKEN,
                "domain": ".x.com",
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            },
            {
                "name": "auth_token",
                "value": config.AUTH_TOKEN,
                "domain": ".twitter.com",
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            },
        ]

        # Add ct0 token if provided
        if config.CT0_TOKEN:
            for domain in [".x.com", ".twitter.com"]:
                cookies.append({
                    "name": "ct0",
                    "value": config.CT0_TOKEN,
                    "domain": domain,
                    "path": "/",
                    "httpOnly": False,
                    "secure": True,
                    "sameSite": "Lax",
                })

        await self.context.add_cookies(cookies)

    async def _create_context(self, headless: bool = False) -> BrowserContext:
        """Create a fresh browser context with a clean temp directory."""
        session_dir = tempfile.mkdtemp(prefix="x_scraper_session_")
        print(f"    [*] Creating fresh browser session in temp dir...")

        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        )

        ctx = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=session_dir,
            headless=headless,
            channel="chrome",
            no_viewport=False,
            viewport={"width": 1920, "height": 1080},
            user_agent=user_agent,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )

        # Assign to self.context so _setup_cookies can use it
        self.context = ctx
        self._session_dirs.append(session_dir)

        await self._setup_cookies()
        return ctx

    async def launch(self, headless: bool = False):
        """Launch the browser and set up authentication.

        Args:
            headless: If True, runs the browser without a visible window.
                     If False (default), shows the browser so you can monitor progress.
        """
        self.playwright = await async_playwright().start()

        # Create fresh context with clean temp directory
        self.context = await self._create_context(headless=headless)

        # Create a new page
        self.page = await self.context.new_page()

        # Override webdriver detection
        await self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            // Override the navigator.plugins length
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            // Override the languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
        """)

        # Listen for page crashes to auto-recover
        async def _on_crash():
            self._page_crashed = True
            print("    [!] Page crashed! Will create fresh browser context on retry.")
        self.page.on("crash", _on_crash)

    async def _ensure_page(self, fresh_context: bool = False):
        """Create a fresh page if the current one crashed or is in a bad state.

        Args:
            fresh_context: If True, also creates a fresh browser context (for page crashes).

        Returns True if the page/recovery was done, False if it was already alive.
        """
        try:
            # Quick health check: evaluate a simple expression
            await self.page.evaluate("1+1")
            if fresh_context:
                raise Exception("Forced fresh context creation")
            return False  # Page is alive
        except Exception:
            action = "Creating fresh browser context" if fresh_context else "Creating fresh page"
            print(f"    [*] {action}...")

            if fresh_context and self.context:
                # Close the old context entirely
                try:
                    await self.context.close()
                except Exception:
                    pass

                # Create a brand new context with a fresh temp directory
                # This completely eliminates any cached rate-limit markers
                self.context = await self._create_context(headless=False)
                print("    [*] Created fresh browser context with clean temp directory.")
                print("    [*] All previous rate-limit markers cleared.")

                # Let the new context settle
                await self._random_delay(2.0, 3.0)
            else:
                # Close the old page if it's still hanging around
                try:
                    if self.page and not self.page.is_closed():
                        await self.page.close()
                except Exception:
                    pass

            # Create a brand new page
            self.page = await self.context.new_page()
            self._page_crashed = False

            # Re-apply anti-detection scripts
            await self.page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });
            """)

            # Set up crash listener on the new page
            async def _on_crash():
                self._page_crashed = True
                print("    [!] Page crashed! Will create fresh browser context on retry.")
            self.page.on("crash", _on_crash)

            # Re-register video response interception
            if hasattr(self, '_capture_video_response'):
                self.page.on('response', self._capture_video_response)

            # Navigate to homepage first as warm-up, then to likes page.
            # Retry on failure so we don't continue with a broken page.
            if self._likes_url:
                max_nav_retries = 3
                nav_success = False

                for attempt in range(1, max_nav_retries + 1):
                    try:
                        # Navigate directly to likes page (no warm-up — sequential
                        # navigations trigger X.com's bot detection)
                        await self.page.goto(self._likes_url, wait_until="domcontentloaded", timeout=90000)
                        await self._random_delay(3.0, 5.0)
                        nav_success = True
                        break
                    except Exception as e:
                        print(f"    [!] Navigation attempt {attempt}/{max_nav_retries} failed: {e}")
                        if attempt < max_nav_retries:
                            wait = 15 * attempt
                            print(f"    [*] Waiting {wait}s before retrying navigation...")
                            await asyncio.sleep(wait)

                if not nav_success:
                    print(f"    [!] All {max_nav_retries} navigation attempts failed after fresh context creation.")
                    print(f"    [!] This likely means X.com is actively rate-limiting this IP/token.")
                    # Don't proceed with a broken page — the outer retry loop will handle it
                    return True

            print(f"    [v] {'Fresh context' if fresh_context else 'Fresh page'} created and navigated to likes page.")
            return True

    async def _check_rate_limit(self):
        """Quick lightweight check for rate limiting.
        Avoids reading full body text which hangs on large X.com pages."""
        # 1. Check URL first (fastest)
        try:
            current_url = self.page.url
            if "rate_limit" in current_url or "429" in current_url:
                print(f"    [debug] Rate limit detected via URL: {current_url}", flush=True)
                return True
        except Exception:
            return True

        # 2. Quick check: page title
        try:
            title = await asyncio.wait_for(self.page.title(), timeout=3.0)
            if "rate limit" in title.lower() or "429" in title.lower():
                print(f"    [debug] Rate limit detected via title: {title}", flush=True)
                return True
        except Exception:
            return True

        # 3. Quick check: look for rate limit text in a small visible element
        try:
            # Just check a small area near the top of the page
            small_text = await asyncio.wait_for(
                self.page.evaluate("""() => {
                    const el = document.querySelector('[data-testid="cellInnerDiv"]');
                    if (!el) return '';
                    return el.innerText.substring(0, 500);
                }"""),
                timeout=5.0
            )
            error_signals = ["rate limit", "too many requests", "something went wrong", "try again later", "429"]
            text_lower = small_text.lower()
            for signal in error_signals:
                if signal in text_lower:
                    print(f"    [debug] Detected '{signal}' in page content.", flush=True)
                    return True
        except Exception:
            pass

        return False

    async def _extract_thread_context(self, tweet_article):
        """Extract thread context if the post is part of a thread."""
        thread_context = {}

        try:
            # Try to find replying-to information
            reply_elem = await tweet_article.query_selector(
                '[data-testid="User-Names"] a[href*="/status/"]'
            )
            if reply_elem:
                href = await reply_elem.get_attribute("href")
                if href:
                    thread_context["reply_to_url"] = urljoin(self.BASE_URL, href)
                    thread_context["is_reply"] = True

            # Check for quoted tweets
            # In current X.com, quoted tweets appear as nested article elements
            quoted_tweet = await tweet_article.query_selector(
                'article[data-testid="tweet"] article[data-testid="tweet"]'
            )
            if not quoted_tweet:
                quoted_tweet = await tweet_article.query_selector(
                    '[data-testid="card.wrapper"]'
                )

            if quoted_tweet:
                quoted_text_elem = await quoted_tweet.query_selector(
                    '[data-testid="tweetText"]'
                )
                if quoted_text_elem:
                    thread_context["quoted_text"] = await quoted_text_elem.inner_text()

        except Exception as e:
            print(f"  [!] Error extracting thread context: {e}")

        return thread_context

    async def _extract_tweet_text(self, element) -> str:
        """Extract text content from a tweet element."""
        try:
            # Primary selector for tweet text
            text_elem = await element.query_selector(
                '[data-testid="tweetText"]'
            )
            if text_elem:
                return await text_elem.inner_text()

            # Fallback: try to get all text
            return await element.inner_text()
        except Exception:
            return ""

    async def _extract_media(self, tweet_article) -> dict:
        """Extract media URLs from a tweet (images, videos)."""
        media = {"images": [], "videos": [], "urls": []}

        try:
            # --- Extract images ---
            img_selectors = [
                'div[data-testid="tweetPhoto"] img',
                'img[src*="twimg.com/media"]',
                'article img[src*="media"]',
                '[data-testid="card.wrapper"] img',
            ]

            for selector in img_selectors:
                imgs = await tweet_article.query_selector_all(selector)
                for img in imgs:
                    src = await img.get_attribute("src")
                    if src and "media" in src and src not in media["images"]:
                        # Get the largest available version by modifying URL
                        large_src = re.sub(r'&name=\w+', '&name=large', src)
                        if large_src not in media["images"]:
                            media["images"].append(large_src)

            # --- Extract video ---
            # Try multiple selectors to find video elements.
            # On X.com's timeline, videos may use different testids and the actual
            # <video> element may not be fully rendered (lazy-loaded).
            video_elem = None
            for vs_selector in [
                'div[data-testid="videoPlayer"] video',
                'div[data-testid="videoComponent"] video',
                'video',
            ]:
                video_elem = await tweet_article.query_selector(vs_selector)
                if video_elem:
                    break

            if video_elem:
                src = await video_elem.get_attribute("src")
                if src:
                    if not src.startswith('blob:'):
                        # Direct video URL (rare on X.com but possible)
                        media["videos"].append(src)
                    else:
                        # Save the poster URL for ID-based matching later
                        poster = await video_elem.get_attribute("poster")
                        if poster:
                            self._video_poster_map[src] = poster

                        # Blob URL — try to get the actual video URL via browser JS
                        actual_url = await self.page.evaluate("""(videoElem) => {
                            try {
                                if (videoElem.currentSrc && !videoElem.currentSrc.startsWith('blob:'))
                                    return videoElem.currentSrc;
                                const sources = videoElem.querySelectorAll('source');
                                for (const s of sources) {
                                    if (s.src && !s.src.startsWith('blob:'))
                                        return s.src;
                                }
                                return null;
                            } catch(e) { return null; }
                        }""", video_elem)

                        if actual_url:
                            media["videos"].append(actual_url)
                        else:
                            # Store blob URL temporarily — post-processing will try
                            # to match it to a captured video.twimg.com URL by video ID
                            media["videos"].append(src)
                else:
                    # Video element exists but has no src yet (lazy-loaded).
                    # Check for poster as video indicator.
                    poster = await video_elem.get_attribute("poster")
                    if poster:
                        pending_key = f"blob:pending-{len(media['videos'])}"
                        self._video_poster_map[pending_key] = poster
                        media["videos"].append(pending_key)

            # Fallback: try <source> elements (sometimes the video src is in a child source)
            if not media["videos"]:
                source_els = await tweet_article.query_selector_all('video source')
                for src_el in source_els:
                    src = await src_el.get_attribute("src")
                    if src and not src.startswith('blob:') and src not in media["videos"]:
                        media["videos"].append(src)
                    elif src and src.startswith('blob:'):
                        # Get poster from parent video for ID matching
                        parent_vid = await src_el.evaluate_handle("el => el.closest('video')")
                        pv = parent_vid.as_element() if parent_vid else None
                        if pv:
                            poster = await pv.get_attribute("poster")
                            if poster:
                                self._video_poster_map[src] = poster
                        media["videos"].append(src)

            # --- Extract links/URLs in tweet ---
            links = await tweet_article.query_selector_all(
                'a[href*="http"]:not([href*="x.com"]):not([href*="twitter.com"]):not([href*="t.co"])'
            )
            seen_urls = set()
            for link in links:
                href = await link.get_attribute("href")
                if href and href not in seen_urls:
                    seen_urls.add(href)
                    # Filter out tracking/internal/promoted links
                    skip_domains = ["x.com/", "twitter.com/", "t.co/"]
                    if not any(d in href for d in skip_domains):
                        media["urls"].append(href)

        except Exception as e:
            print(f"  [!] Error extracting media: {e}")

        return media

    async def _extract_tweet_url(self, tweet_article) -> str:
        """Fast extraction of just the tweet URL."""
        try:
            time_elem = await tweet_article.query_selector("time")
            if time_elem:
                parent_anchor_handle = await time_elem.evaluate_handle(
                    "el => el.closest('a')"
                )
                if parent_anchor_handle:
                    parent_anchor = parent_anchor_handle.as_element()
                    if parent_anchor:
                        url = await parent_anchor.get_attribute("href")
                        if url:
                            return urljoin(self.BASE_URL, url.split("?")[0])
        except Exception:
            pass
        return ""

    async def _extract_tweet_data(self, tweet_article) -> dict:
        """Extract all data from a single tweet article element."""
        data = {
            "url": "",
            "id": "",
            "author": "",
            "author_handle": "",
            "timestamp": "",
            "text": "",
            "media": {"images": [], "videos": [], "urls": []},
            "stats": {},
            "thread_context": {},
            "is_reply": False,
            "reply_to": "",
        }

        try:
            # --- Extract tweet URL/id ---
            time_elem = await tweet_article.query_selector("time")
            if time_elem:
                parent_anchor_handle = await time_elem.evaluate_handle(
                    "el => el.closest('a')"
                )
                if parent_anchor_handle:
                    parent_anchor = parent_anchor_handle.as_element()
                    if parent_anchor:
                        url = await parent_anchor.get_attribute("href")
                        if url:
                            data["url"] = urljoin(self.BASE_URL, url.split("?")[0])
                            # Extract tweet ID from URL
                            status_match = re.search(r"/status/(\d+)", data["url"])
                            if status_match:
                                data["id"] = status_match.group(1)

            # --- Extract author info ---
            author_elem = await tweet_article.query_selector(
                '[data-testid="User-Name"]'
            )
            if author_elem:
                author_text = await author_elem.inner_text()
                lines = author_text.strip().split("\n")
                if lines:
                    data["author"] = lines[0]
                for line in lines:
                    if line.startswith("@"):
                        data["author_handle"] = line

            # --- Extract timestamp ---
            if time_elem:
                dt = await time_elem.get_attribute("datetime")
                if dt:
                    data["timestamp"] = dt

            # --- Extract tweet text ---
            data["text"] = await self._extract_tweet_text(tweet_article)

            # --- Extract media ---
            data["media"] = await self._extract_media(tweet_article)

            # --- Extract engagement stats ---
            stat_selectors = {
                "replies": '[data-testid="reply"]',
                "retweets": '[data-testid="retweet"]',
                "likes": '[data-testid="like"]',
                "bookmarks": '[data-testid="bookmark"]',
            }

            for stat_name, selector in stat_selectors.items():
                elem = await tweet_article.query_selector(selector)
                if elem:
                    label = await elem.get_attribute("aria-label")
                    if label:
                        data["stats"][stat_name] = label
                    else:
                        text = await elem.inner_text()
                        data["stats"][stat_name] = text

            # --- Extract thread context ---
            data["thread_context"] = await self._extract_thread_context(tweet_article)
            if data["thread_context"].get("is_reply"):
                data["is_reply"] = True
                data["reply_to"] = data["thread_context"].get("reply_to_url", "")

        except Exception as e:
            print(f"  [!] Error extracting tweet data: {e}")

        return data

    async def _scroll_and_load(self, target_count: int):
        """Scroll the page to load more tweets until we reach target_count."""
        print(f"[*] Scrolling to load up to {target_count} liked posts...", flush=True)
        print(f"[*] Already have {len(self.results)} posts, skipping {len(self.processed_urls)} URLs", flush=True)
        consecutive_empty = 0
        max_empty_scrolls = 500  # Allow more empty scrolls before giving up
        total_scrolls = 0
        max_total_scrolls = 5000  # Safety limit

        last_checkpoint_count = len(self.results)
        last_break_count = len(self.results)

        while len(self.results) < target_count and total_scrolls < max_total_scrolls:
            # Check for rate limiting or page crash
            if await self._check_rate_limit() or self._page_crashed:
                self._retry_count += 1
                if self._retry_count > config.MAX_RATE_LIMIT_RETRIES:
                    reason = "page crashes" if self._page_crashed else "rate limits"
                    print(f"\n[!] {reason} {self._retry_count} times. Giving up.", flush=True)
                    print("    Run again with --resume to continue where you left off.", flush=True)
                    print("    Tip: Get a fresh auth_token from your browser's cookies and update config.py", flush=True)
                    break

                delay = min(
                    config.RETRY_BASE_DELAY * (2 ** (self._retry_count - 1)),
                    config.RETRY_MAX_DELAY,
                )

                if self._page_crashed:
                    print(f"\n[*] Page crash (attempt {self._retry_count}/{config.MAX_RATE_LIMIT_RETRIES}).", flush=True)
                else:
                    print(f"\n[*] Rate limited (attempt {self._retry_count}/{config.MAX_RATE_LIMIT_RETRIES}).", flush=True)

                # Save checkpoint before retry
                if self._checkpoint_path:
                    self._save_checkpoint()
                    print(f"    Checkpoint saved — you can Ctrl+C and resume later.", flush=True)

                if not self._page_crashed:
                    print(f"    Waiting {delay}s before retrying...", flush=True)
                    for remaining in range(int(delay), 0, -10):
                        print(f"    Waiting... {remaining}s", flush=True)
                        await asyncio.sleep(10)
                print("    Retrying...", flush=True)

                # For both page crashes and rate limits: create a completely fresh session
                await self._ensure_page(fresh_context=True)
                self._page_crashed = False
                continue

            # Reset retry count on successful iteration
            self._retry_count = 0

            # Wait for tweets to be rendered before extraction
            try:
                await self.page.wait_for_selector(
                    'article[data-testid="tweet"]',
                    timeout=15000
                )
            except Exception:
                pass

            # Extract currently visible tweets
            tweets = await self.page.query_selector_all(
                'article[data-testid="tweet"]'
            )

            new_count = 0
            for tweet in tweets:
                # Fast path: check URL before full extraction to speed up resuming
                url = await self._extract_tweet_url(tweet)
                if url and url in self.processed_urls:
                    continue

                tweet_data = await self._extract_tweet_data(tweet)
                if tweet_data["url"] and tweet_data["url"] not in self.processed_urls:
                    self.processed_urls.add(tweet_data["url"])
                    self.results.append(tweet_data)
                    self._checkpoint_dirty = True
                    new_count += 1
                await self._random_delay(0.1, 0.3)

            if new_count > 0:
                consecutive_empty = 0
                print(f"[+] Found {new_count} new posts (total: {len(self.results)})", flush=True)
            else:
                consecutive_empty += 1
                if consecutive_empty % 10 == 0:
                    print(f"[*] No new posts - scroll {total_scrolls}, empty {consecutive_empty}", flush=True)

            # Periodic checkpoint save
            if (
                self._checkpoint_dirty
                and self._checkpoint_path
                and (len(self.results) - last_checkpoint_count) >= config.CHECKPOINT_INTERVAL
            ):
                self._save_checkpoint()
                last_checkpoint_count = len(self.results)

            # Take a longer break after every N posts
            if (
                len(self.results) - last_break_count >= config.BREAK_INTERVAL
                and new_count > 0
            ):
                print(f"[*] Taking {config.BREAK_DURATION}s break...", flush=True)
                for remaining in range(config.BREAK_DURATION, 0, -10):
                    print(f"    Break... {remaining}s", flush=True)
                    await asyncio.sleep(10)
                last_break_count = len(self.results)

            if len(self.results) >= target_count:
                break

            if consecutive_empty >= max_empty_scrolls:
                print(f"[*] No new posts after {max_empty_scrolls} consecutive scrolls.", flush=True)
                print(f"    Loaded {len(self.results)} posts so far.", flush=True)
                break

            # Scroll down
            scroll_distance = random.randint(500, 900)
            await self.page.evaluate(
                f"window.scrollBy(0, {scroll_distance})"
            )
            await self._random_delay(
                config.SCROLL_PAUSE, config.SCROLL_PAUSE + 2.0
            )
            total_scrolls += 1

        # Final checkpoint save
        if self._checkpoint_path and self._checkpoint_dirty:
            self._save_checkpoint()

        print(f"[v] Loaded {len(self.results)} posts")

    async def scrape_likes(self, username: str = None, max_likes: int = None):
        """Main entry point: navigate to likes page and scrape."""
        username = username or config.X_USERNAME
        max_likes = max_likes or config.MAX_LIKES

        # Set up checkpoint path for this username
        self._checkpoint_path = self._checkpoint_file(username)

        # Load checkpoint if resuming
        if self._resume:
            checkpoint_loaded = self._load_checkpoint(username)
            if checkpoint_loaded and len(self.processed_urls) > 0:
                print(f"[*] Will skip {len(self.processed_urls)} already-processed tweets.")
                print(f"[*] Target: {max_likes} new posts (in addition to existing checkpoint)")

        likes_url = f"{self.BASE_URL}/{username}/likes"
        self._likes_url = likes_url  # Store for rate-limit retry reloads

        # Navigate directly to the likes URL (most reliable approach)
        print(f"[*] Navigating to likes page: {likes_url}", flush=True)
        try:
            await self.page.goto(likes_url, wait_until="domcontentloaded", timeout=90000)
        except Exception as e:
            print(f"[!] Navigation timeout, continuing anyway: {e}", flush=True)

        # Wait for the likes page to render
        print("    [*] Waiting for likes page to render...", flush=True)
        await self._random_delay(8.0, 12.0)

        # Check if we hit a login wall using the page URL (more reliable than content parsing)
        current_url = self.page.url
        if "login" in current_url.lower() or "i/flow" in current_url:
            print("[!] Not logged in! Cookie may be expired or invalid.", flush=True)
            print("[!] Please update your auth_token in config.py", flush=True)
            print()
            print("    To get a fresh auth_token:", flush=True)
            print("    1. Open Chrome and go to x.com (make sure you're logged in)", flush=True)
            print("    2. Open DevTools (F12) -> Application tab -> Cookies -> x.com", flush=True)
            print("    3. Find 'auth_token' and copy its value", flush=True)
            print("    4. Update AUTH_TOKEN in config.py", flush=True)
            return []

        # Check for "doesn't exist" or protected tweets
        page_content = await self.page.content()
        if "doesn't exist" in page_content or "these Tweets are protected" in page_content.lower():
            print("[!] The likes page is not accessible.")
            print("    Make sure your username is correct (@{username}).")
            return []

        print(f"[v] Likes page loaded successfully. Starting extraction...")

        # Set up video URL interception
        async def _capture_video_response(response):
            url = response.url
            if 'video.twimg.com' in url and '.mp4' in url:
                base_url = url.split('?')[0]
                if base_url not in [v.split('?')[0] for v in self._captured_video_urls]:
                    self._captured_video_urls.append(url)

        self._capture_video_response = _capture_video_response
        self.page.on('response', self._capture_video_response)

        # Also intercept X's internal API responses for tweet data
        async def _capture_api_response(response):
            url = response.url
            # Capture GraphQL API responses that contain tweet data
            if '/i/api/graphql/' in url and 'Likes' in url:
                try:
                    json_data = await response.json()
                    # Store for processing later
                    if not hasattr(self, '_captured_api_responses'):
                        self._captured_api_responses = []
                    self._captured_api_responses.append(json_data)
                except Exception:
                    pass
            # Also capture UserTweets endpoint responses
            if '/i/api/graphql/' in url and ('UserTweets' in url or 'UserMedia' in url):
                try:
                    json_data = await response.json()
                    if not hasattr(self, '_captured_api_responses'):
                        self._captured_api_responses = []
                    self._captured_api_responses.append(json_data)
                except Exception:
                    pass

        self.page.on('response', _capture_api_response)

        # Scroll and extract
        await self._scroll_and_load(max_likes)

        # Process any captured API responses for additional tweet data
        if hasattr(self, '_captured_api_responses') and self._captured_api_responses:
            print(f"[v] Captured {len(self._captured_api_responses)} API responses")
            # Process API responses to extract any tweets we missed
            for api_resp in self._captured_api_responses:
                tweets_found = self._extract_tweets_from_api(api_resp)
                if tweets_found:
                    print(f"  [i] Found {len(tweets_found)} tweets via API response")

        # Post-processing: match captured video URLs to tweets with blob video URLs
        # using the poster thumbnail URL to extract the video ID for reliable matching.
        # The poster URL (e.g., .../ext_tw_video_thumb/123456789/poster.jpg) contains the
        # same video ID as the actual video URL (e.g., .../ext_tw_video/123456789/...mp4).
        matched_count = 0
        if self._captured_video_urls and self._video_poster_map:
            for post in self.results:
                new_videos = []
                for v_url in post["media"]["videos"]:
                    if v_url.startswith('blob:'):
                        # Try to match by video ID from poster URL
                        poster = self._video_poster_map.get(v_url, '')
                        # Extract video ID from poster thumbnail URL
                        id_match = re.search(r'/(ext_tw_video|amplify_video|tweet_video)_thumb/(\d+)/', poster)
                        if id_match:
                            video_type = id_match.group(1)
                            vid_id = id_match.group(2)
                            # Try matching with trailing slash (standard format)
                            matched_url = None
                            for captured in self._captured_video_urls:
                                if f'/{video_type}/{vid_id}/' in captured:
                                    matched_url = captured
                                    break
                            # Fallback: try without trailing slash (GIF format: /tweet_video/12345.mp4)
                            if not matched_url:
                                for captured in self._captured_video_urls:
                                    if f'/{video_type}/{vid_id}.' in captured:
                                        matched_url = captured
                                        break
                            if matched_url:
                                new_videos.append(matched_url)
                                matched_count += 1
                        # If no match found, skip the blob URL (video download won't be possible)
                    else:
                        new_videos.append(v_url)
                post["media"]["videos"] = new_videos

        # Safety filter: drop any remaining blob URLs that couldn't be resolved
        blob_dropped = 0
        for post in self.results:
            cleaned = [v for v in post["media"]["videos"] if not v.startswith('blob:')]
            dropped = len(post["media"]["videos"]) - len(cleaned)
            blob_dropped += dropped
            post["media"]["videos"] = cleaned

        print(f"[v] Matched {matched_count} blob video URLs to captured video.twimg.com URLs")
        if blob_dropped:
            print(f"[v] Dropped {blob_dropped} unresolvable blob: URLs from output")
        print(f"[v] Captured {len(self._captured_video_urls)} video URLs via network interception")
        if self._video_poster_map:
            print(f"[v] Tracked {len(self._video_poster_map)} poster thumbnail URLs for ID matching")

        return self.results

    def _extract_tweets_from_api(self, api_response: dict) -> List[dict]:
        """Extract tweet data from X's internal GraphQL API response JSON."""
        tweets = []
        try:
            # Navigate through the nested structure to find tweet results
            instructions = (
                api_response.get("data", {})
                .get("user", {})
                .get("result", {})
                .get("timeline_v2", {})
                .get("timeline", {})
                .get("instructions", [])
            )
            if not instructions:
                # Try alternate path (UserTweets endpoint)
                instructions = (
                    api_response.get("data", {})
                    .get("user", {})
                    .get("result", {})
                    .get("timeline", {})
                    .get("timeline", {})
                    .get("instructions", [])
                )

            for instruction in instructions:
                entries = instruction.get("entries", [])
                for entry in entries:
                    entry_id = entry.get("entryId", "")
                    if not entry_id.startswith("tweet-"):
                        continue

                    content = entry.get("content", {})
                    item_content = content.get("itemContent", {})
                    if item_content.get("itemType") != "TimelineTweet":
                        continue

                    tweet_result = (
                        item_content.get("tweet_results", {})
                        .get("result", {})
                    )
                    if not tweet_result:
                        continue

                    rest_id = tweet_result.get("rest_id", "")
                    legacy = tweet_result.get("legacy", {})
                    if not legacy or not rest_id:
                        continue

                    # Extract author info
                    core = tweet_result.get("core", {})
                    user_result = core.get("user_results", {}).get("result", {})
                    author_name = user_result.get("legacy", {}).get("name", "")
                    author_handle = user_result.get("legacy", {}).get("screen_name", "")

                    # Extract text
                    full_text = legacy.get("full_text", "")

                    # Extract timestamp
                    created_at = legacy.get("created_at", "")

                    # Extract media
                    entities = legacy.get("entities", {})
                    media_list = entities.get("media", [])
                    images = []
                    videos = []
                    for m in media_list:
                        media_url = m.get("media_url_https", "")
                        if not media_url:
                            media_url = m.get("media_url", "")
                        if m.get("type") == "photo":
                            images.append(media_url + "?format=jpg&name=large")
                        elif m.get("type") in ("video", "animated_gif"):
                            variants = m.get("video_info", {}).get("variants", [])
                            best_variant = None
                            best_bitrate = -1
                            for v in variants:
                                if v.get("content_type") == "video/mp4":
                                    bitrate = v.get("bitrate", 0)
                                    if bitrate > best_bitrate:
                                        best_bitrate = bitrate
                                        best_variant = v.get("url", "")
                            if best_variant:
                                videos.append(best_variant)

                    # Extract stats
                    reply_count = legacy.get("reply_count", 0)
                    retweet_count = legacy.get("retweet_count", 0)
                    like_count = legacy.get("favorite_count", 0)
                    bookmark_count = legacy.get("bookmark_count", 0)

                    tweet_url = f"{self.BASE_URL}/{author_handle}/status/{rest_id}"

                    # Skip if already processed
                    if tweet_url in self.processed_urls:
                        continue

                    tweet_data = {
                        "url": tweet_url,
                        "id": rest_id,
                        "author": author_name,
                        "author_handle": f"@{author_handle}",
                        "timestamp": created_at,
                        "text": full_text,
                        "media": {
                            "images": images,
                            "videos": videos,
                            "urls": []
                        },
                        "stats": {
                            "replies": f"{reply_count} Replies. Reply",
                            "retweets": f"{retweet_count} reposts. Repost",
                            "likes": f"{like_count} likes. Like",
                            "bookmarks": "Bookmark",
                        },
                        "thread_context": {},
                        "is_reply": False,
                        "reply_to": "",
                    }

                    # Check if it's a reply
                    in_reply_to_user = legacy.get("in_reply_to_screen_name", "")
                    in_reply_to_status = legacy.get("in_reply_to_status_id_str", "")
                    if in_reply_to_user and in_reply_to_status:
                        tweet_data["is_reply"] = True
                        tweet_data["reply_to"] = f"{self.BASE_URL}/{in_reply_to_user}/status/{in_reply_to_status}"

                    tweets.append(tweet_data)

        except Exception as e:
            print(f"  [!] Error extracting tweets from API response: {e}")

        return tweets

    async def get_thread_context(self, tweet_url: str) -> dict:
        """Navigate to a specific tweet and get its full thread context."""
        result = {"parent_tweets": [], "child_tweets": []}

        try:
            await self.page.goto(tweet_url, wait_until="domcontentloaded", timeout=30000)
            await self._random_delay(1.0, 2.0)

            # Look for "Show this thread" button and click it
            show_thread = await self.page.query_selector(
                'span:has-text("Show this thread")'
            )
            if show_thread:
                await show_thread.click()
                await self._random_delay(1.0, 2.0)

            # Collect all tweets in the thread
            thread_tweets = await self.page.query_selector_all(
                'article[data-testid="tweet"]'
            )
            for tweet in thread_tweets[:10]:  # Limit to 10 tweets
                tweet_data = await self._extract_tweet_data(tweet)
                if tweet_data["text"]:
                    result["child_tweets"].append(tweet_data)

        except Exception as e:
            print(f"  [!] Error getting thread context: {e}")

        return result


async def run_scraper():
    """Convenience function to run the scraper."""
    async with XScraper() as scraper:
        await scraper.launch()
        results = await scraper.scrape_likes()
        return results


if __name__ == "__main__":
    asyncio.run(run_scraper())
