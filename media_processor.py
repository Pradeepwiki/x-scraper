"""
Media Processor - Downloads images/videos, performs OCR and transcription.
Models are cached (loaded once) for efficiency across hundreds of posts.
"""

import os
from pathlib import Path
from urllib.parse import urlparse

import requests
from tqdm import tqdm

import config


# ── Model Cache (lazy-loaded, shared across all function calls) ──────────────
_ocr_reader = None
_whisper_model = None


def _get_ocr_reader():
    """Get or create the EasyOCR reader (cached after first load)."""
    global _ocr_reader
    if _ocr_reader is not None:
        return _ocr_reader

    if not config.OCR_IMAGES:
        return None

    try:
        import easyocr
        import logging
        # Suppress EasyOCR's verbose logging
        logging.getLogger("easyocr").setLevel(logging.WARNING)
        # Set environment variable to avoid Unicode issues in the terminal
        os.environ["PYTHONIOENCODING"] = "utf-8"
        _ocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        print("  [i] EasyOCR reader loaded (CPU mode)")
        return _ocr_reader
    except Exception as e:
        print(f"  [!] Failed to initialize EasyOCR: {e}")
        print("      OCR will be skipped for images.")
        return None


def _get_whisper_model():
    """Get or create the Whisper model (cached after first load)."""
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model

    if not config.TRANSCRIBE_VIDEOS:
        return None

    try:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        print("  [i] Whisper model loaded (CPU, int8)")
        return _whisper_model
    except Exception as e:
        print(f"  [!] Failed to initialize faster-whisper: {e}")
        print("      Video transcription will be skipped.")
        return None


# ── Utility Functions ───────────────────────────────────────────────────────

def sanitize_filename(url: str, max_len: int = 60) -> str:
    """Create a safe filename from a URL."""
    parsed = urlparse(url)
    name = os.path.basename(parsed.path)
    if not name or len(name) < 5:
        name = parsed.path.replace("/", "_") or "media"
    name = name.split("?")[0]
    safe = "".join(c for c in name if c.isalnum() or c in "._-")
    if len(safe) > max_len:
        ext = os.path.splitext(safe)[1]
        base = safe[: max_len - len(ext)]
        safe = base + ext
    return safe or "unnamed"


def download_media(media_urls: list, subdir: str) -> list:
    """Download media files. Returns list of local file paths."""
    if not media_urls:
        return []

    media_dir = Path(config.MEDIA_DIR) / subdir
    media_dir.mkdir(parents=True, exist_ok=True)

    downloaded = []
    for url in tqdm(media_urls, desc=f"Downloading {subdir}", leave=False):
        filename = sanitize_filename(url)
        filepath = media_dir / filename

        if filepath.exists():
            downloaded.append(str(filepath))
            continue

        try:
            resp = requests.get(url, timeout=30, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                )
            })
            if resp.status_code == 200:
                filepath.write_bytes(resp.content)
                downloaded.append(str(filepath))
        except Exception as e:
            print(f"  [!] Failed to download {url}: {e}")

    return downloaded


# ── OCR ─────────────────────────────────────────────────────────────────────

def ocr_image(image_path: str) -> str:
    """Extract text from an image using EasyOCR (model is cached)."""
    reader = _get_ocr_reader()
    if reader is None:
        return ""

    try:
        result = reader.readtext(image_path, detail=0, paragraph=True)
        text = "\n".join(result).strip()
        return text
    except Exception as e:
        return f"[OCR error: {e}]"


# ── Transcription ───────────────────────────────────────────────────────────

def transcribe_video(video_path: str) -> str:
    """Transcribe audio from a video using faster-whisper (model is cached)."""
    model = _get_whisper_model()
    if model is None:
        return ""

    try:
        segments, info = model.transcribe(video_path, beam_size=5)
        text = "\n".join(seg.text.strip() for seg in segments).strip()
        return text
    except Exception as e:
        return f"[Transcription error: {e}]"


# ── Post Media Processing ───────────────────────────────────────────────────

def download_video_with_ytdlp(tweet_url: str) -> list:
    """Download video from tweet URL using yt-dlp. Returns list of local paths."""
    try:
        import yt_dlp
    except ImportError:
        print("  [!] yt-dlp is not installed. Video download fallback will be used.")
        return []

    video_dir = Path(config.MEDIA_DIR) / "videos"
    video_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        'outtmpl': str(video_dir / '%(id)s.%(ext)s'),
        'format': 'best[ext=mp4]/best',
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(tweet_url, download=True)
            filename = ydl.prepare_filename(info)
            if os.path.exists(filename):
                return [str(filename)]
    except Exception as e:
        print(f"  [!] yt-dlp download failed for {tweet_url}: {e}")
    return []


def process_post_media(post: dict) -> dict:
    """Download and process all media for a single post (OCR images, transcribe videos)."""
    media_results = {"images_text": [], "videos_text": []}

    # Download and OCR images
    if post["media"]["images"]:
        img_paths = download_media(post["media"]["images"], "images")
        for img_path in img_paths:
            text = ocr_image(img_path)
            if text and not text.startswith("[OCR error"):
                media_results["images_text"].append({
                    "file": img_path,
                    "ocr_text": text,
                })
            elif text and text.startswith("[OCR error"):
                print(f"  [!] OCR failed for {img_path}: {text}")

    # Download and transcribe videos
    if post["media"]["videos"]:
        # Primary: try downloading via yt-dlp using status URL
        tweet_url = post.get("url") or f"https://x.com/i/web/status/{post['id']}"
        vid_paths = download_video_with_ytdlp(tweet_url)
        
        # Fallback: try direct media download
        if not vid_paths:
            valid_videos = [v for v in post["media"]["videos"] if not v.startswith("blob:")]
            if valid_videos:
                vid_paths = download_media(valid_videos, "videos")

        for vid_path in vid_paths:
            text = transcribe_video(vid_path)
            if text and not text.startswith("[Transcription error"):
                media_results["videos_text"].append({
                    "file": vid_path,
                    "transcription": text,
                })
            elif text and text.startswith("[Transcription error"):
                print(f"  [!] Transcription failed for {vid_path}: {text}")

    return media_results
