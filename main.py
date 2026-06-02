#!/usr/bin/env python3
"""
X.com Liked Posts Scraper & Knowledge Extractor

Extracts your liked posts from X.com, processes media (OCR for images,
transcription for videos), and exports clean knowledge notes.

Usage:
    python main.py                  # Run with config.py settings
    python main.py --count 50       # Scrape 50 posts
    python main.py --count 300 --username yourhandle  # Full run
    python main.py --headless       # Run without visible browser

Setup:
    1. Fill in your AUTH_TOKEN in config.py
    2. Optionally fill in X_USERNAME
    (Or pass --username on the command line)
"""

import argparse
import asyncio
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scraper import XScraper
from output_formatter import export_notes
from media_processor import process_post_media
import config


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Extract your liked posts from X.com into clean knowledge notes."
    )
    parser.add_argument(
        "--username", "-u",
        default=None,
        help="Your X.com username (overrides config.py)",
    )
    parser.add_argument(
        "--count", "-c",
        type=int,
        default=None,
        help="Number of posts to scrape (overrides config.py MAX_LIKES)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output format: json or markdown (overrides config.py)",
        choices=["json", "markdown"],
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode (no visible window)",
    )
    parser.add_argument(
        "--no-media",
        action="store_true",
        help="Skip media downloads and processing",
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="Enable OCR for images (overrides config.py)",
    )
    parser.add_argument(
        "--transcribe",
        action="store_true",
        help="Enable video transcription (overrides config.py)",
    )
    parser.add_argument(
        "--resume", "-r",
        action="store_true",
        help="Resume from a previous partial run (loads checkpoint)",
    )
    parser.add_argument(
        "--archive",
        default=None,
        help="Path to X data archive like.js file to scrape specific liked posts directly",
    )
    return parser.parse_args()


async def main():
    """Main execution flow."""
    args = parse_args()

    # Override config with command-line args
    username = args.username or config.X_USERNAME
    max_likes = args.count or config.MAX_LIKES
    do_media = not args.no_media and config.DOWNLOAD_MEDIA

    if args.ocr:
        config.OCR_IMAGES = True
    if args.transcribe:
        config.TRANSCRIBE_VIDEOS = True
    if args.output:
        config.OUTPUT_FORMAT = args.output

    # Validate config
    if not config.AUTH_TOKEN:
        print("[!] Error: AUTH_TOKEN is not set in config.py")
        print("[!] Please set your X.com auth_token cookie value.")
        sys.exit(1)

    if not username:
        print("[!] Error: X_USERNAME is not set.")
        print("[!] Provide it via --username <handle> or set it in config.py")
        sys.exit(1)

    print("=" * 60)
    print("  X.com Likes Scraper -- Knowledge Extractor")
    print("=" * 60)
    print(f"  Target: @{username}")
    print(f"  Posts to scrape: {max_likes}")
    print(f"  Download media: {do_media}")
    print(f"  OCR images: {config.OCR_IMAGES}")
    print(f"  Transcribe videos: {config.TRANSCRIBE_VIDEOS}")
    print(f"  Output format: {config.OUTPUT_FORMAT}")
    if args.resume:
        print(f"  Resume: YES (will skip already-processed posts)")
    print("=" * 60)
    print()

    # --- Step 1: Scrape the likes ---
    print("[1/3] Scraping liked posts from X.com...")
    print("      A browser window will open. Do not close it.")
    if args.resume:
        print("      Resume mode: checkpoint will be loaded automatically")
    print()

    if args.resume:
        print("[*] Resume mode enabled — will skip already-processed posts")
    if args.archive:
        print(f"[*] Archive mode enabled — reading from {args.archive}")

    scraper = None
    try:
        async with XScraper(resume=args.resume) as s:
            scraper = s
            await scraper.launch(headless=args.headless)

            if args.headless:
                print("[*] Running in headless mode")

            if args.archive:
                results = await scraper.scrape_from_archive(
                    archive_path=args.archive, username=username, max_likes=max_likes
                )
            else:
                results = await scraper.scrape_likes(
                    username=username, max_likes=max_likes
                )
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user.")
        results = getattr(scraper, 'results', []) if scraper else []
    except Exception as e:
        print(f"[!] Scraping failed: {e}")
        import traceback
        traceback.print_exc()
        return

    if not results:
        print("[!] No posts were scraped. Check your auth_token and username.")
        return

    print(f"\n[v] Scraped {len(results)} posts\n")

    # --- Step 2: Process media (OCR / transcription) ---
    media_results = []
    if do_media and (config.OCR_IMAGES or config.TRANSCRIBE_VIDEOS):
        print("[2/3] Processing media (OCR / transcription)...")
        print("      This may take a while for images and videos.")
        print()

        for i, post in enumerate(results, 1):
            print(f"  [{i}/{len(results)}] Processing media for post...")
            media_result = process_post_media(post)
            media_results.append(media_result)

        print(f"\n[v] Media processing complete\n")
    else:
        if config.DOWNLOAD_MEDIA:
            print("[2/3] Downloading media files...")
            from media_processor import download_media
            for i, post in enumerate(results, 1):
                if post["media"]["images"]:
                    download_media(
                        post["media"]["images"], f"post_{i}_images"
                    )
                if post["media"]["videos"]:
                    download_media(
                        post["media"]["videos"], f"post_{i}_videos"
                    )
            print(f"[v] Media downloads complete\n")
        else:
            print("[2/3] Skipping media processing (disabled in config)\n")

    # --- Step 3: Export as structured notes ---
    print("[3/3] Exporting knowledge notes...")
    notes = export_notes(results, media_results if media_results else None)

    print()
    print("=" * 60)
    print(f"  [DONE] Complete! Exported {len(results)} posts")
    print(f"  [FOLDER] Output: {config.OUTPUT_DIR}/")
    print("=" * 60)

    # Show a quick preview
    print()
    print("[NOTES] Preview of first note:")
    if notes:
        first = notes[0]
        print(f"  Author: {first.get('author', '?')} ({first.get('author_handle', '?')})")
        print(f"  Date: {first.get('date', '?')}")
        preview = first.get('content', '')[:150]
        if preview:
            print(f"  Content: {preview}...")
        if first.get('has_images'):
            print(f"  [IMG] {first.get('image_count')} image(s)")
        if first.get('has_videos'):
            print(f"  [VID] {first.get('video_count')} video(s)")
        print()


if __name__ == "__main__":
    asyncio.run(main())
