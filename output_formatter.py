"""
Output Formatter - Converts scraped post data into clean, structured notes.
Supports JSON and Markdown output formats.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict

import config


def format_timestamp(ts: str) -> str:
    """Format ISO timestamp to readable date."""
    if not ts:
        return "Unknown date"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, AttributeError):
        return ts


def truncate_text(text: str, max_len: int = 80) -> str:
    """Truncate text for preview purposes."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def post_to_knowledge_note(post: dict, media_results: dict = None) -> dict:
    """
    Transform a raw post + media results into a clean knowledge note.
    This focuses on extracting the *informational value* from the post.
    """
    note = {
        "source_url": post.get("url", ""),
        "author": post.get("author", ""),
        "author_handle": post.get("author_handle", ""),
        "date": format_timestamp(post.get("timestamp", "")),
        "content": post.get("text", ""),
        "content_type": "post",
        "has_images": len(post.get("media", {}).get("images", [])) > 0,
        "has_videos": len(post.get("media", {}).get("videos", [])) > 0,
        "has_links": len(post.get("media", {}).get("urls", [])) > 0,
        "is_reply": post.get("is_reply", False),
        "reply_context": post.get("reply_to", ""),
        "links": post.get("media", {}).get("urls", []),
        "image_count": len(post.get("media", {}).get("images", [])),
        "video_count": len(post.get("media", {}).get("videos", [])),
        "stats": post.get("stats", {}),
    }

    # Add media processing results if available
    if media_results:
        if media_results.get("images_text"):
            note["image_texts"] = [
                {
                    "file": img["file"],
                    "ocr_text": img["ocr_text"],
                }
                for img in media_results["images_text"]
            ]
        if media_results.get("videos_text"):
            note["video_transcripts"] = [
                {
                    "file": vid["file"],
                    "transcription": vid["transcription"],
                }
                for vid in media_results["videos_text"]
            ]

    # Generate a content summary
    note["summary"] = _generate_summary(note)

    return note


def _generate_summary(note: dict) -> str:
    """Generate a brief summary of the post's knowledge value."""
    parts = []
    content = note.get("content", "").strip()

    if content:
        # Take first 200 chars as summary
        summary = content[:200]
        if len(content) > 200:
            summary += "..."
        parts.append(summary)

    extras = []
    if note.get("has_images"):
        extras.append(f"{note['image_count']} image(s)")
    if note.get("has_videos"):
        extras.append(f"{note['video_count']} video(s)")
    if note.get("has_links"):
        extras.append(f"{len(note['links'])} link(s)")

    if extras:
        parts.append(f"[Contains: {', '.join(extras)}]")

    return "\n".join(parts) if parts else ""


def export_to_json(posts: List[dict], filepath: str):
    """Export all processed notes to a JSON file."""
    output = {
        "export_date": datetime.utcnow().isoformat() + "Z",
        "total_posts": len(posts),
        "notes": posts,
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"[v] Exported {len(posts)} notes to {filepath}")


def export_to_markdown(posts: List[dict], filepath: str):
    """Export all processed notes to a Markdown file with clean formatting."""
    lines = [
        "# X.com Liked Posts - Knowledge Archive",
        "",
        f"*Exported: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}*",
        f"*Total posts: {len(posts)}*",
        "",
        "---",
        "",
    ]

    for i, note in enumerate(posts, 1):
        lines.append(f"## {i}. {note.get('author', 'Unknown')} ({note.get('author_handle', '')})")
        lines.append("")
        lines.append(f"**Date:** {note.get('date', '')}")
        lines.append(f"**Source:** [{note.get('source_url', '')}]({note.get('source_url', '')})")
        lines.append("")

        # Content
        content = note.get("content", "").strip()
        if content:
            lines.append("### Content")
            lines.append("")
            lines.append(content)
            lines.append("")

        # Image OCR text
        if note.get("image_texts"):
            lines.append("### [IMG] Image Text (OCR)")
            lines.append("")
            for img in note["image_texts"]:
                if img.get("ocr_text") and not img["ocr_text"].startswith("["):
                    lines.append(f"**From:** `{img['file']}`")
                    lines.append("```")
                    lines.append(img["ocr_text"])
                    lines.append("```")
                    lines.append("")

        # Video transcripts
        if note.get("video_transcripts"):
            lines.append("### [VID] Video Transcript")
            lines.append("")
            for vid in note["video_transcripts"]:
                if vid.get("transcription") and not vid["transcription"].startswith("["):
                    lines.append(f"**From:** `{vid['file']}`")
                    lines.append("```")
                    lines.append(vid["transcription"])
                    lines.append("```")
                    lines.append("")

        # Links
        if note.get("links"):
            lines.append("### [LINK] Links")
            lines.append("")
            for link in note["links"]:
                lines.append(f"- {link}")
            lines.append("")

        # Stats
        if note.get("stats"):
            lines.append("### Stats")
            lines.append("")
            for stat, val in note["stats"].items():
                lines.append(f"- **{stat}:** {val}")
            lines.append("")

        # Reply context
        if note.get("is_reply") and note.get("reply_context"):
            lines.append(f"> Reply to: {note['reply_context']}")
            lines.append("")

        lines.append("---")
        lines.append("")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[v] Exported {len(posts)} notes to {filepath}")


def export_notes(posts: List[dict], media_results: List[dict] = None):
    """Export all notes in the configured format."""
    # Ensure output directory exists
    output_dir = Path(config.OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Convert posts to knowledge notes
    notes = []
    for i, post in enumerate(posts):
        mr = media_results[i] if media_results and i < len(media_results) else None
        notes.append(post_to_knowledge_note(post, mr))

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    if config.OUTPUT_FORMAT == "markdown":
        filepath = output_dir / f"x_likes_{timestamp}.md"
        export_to_markdown(notes, str(filepath))
    else:
        filepath = output_dir / f"x_likes_{timestamp}.json"
        export_to_json(notes, str(filepath))

    # Also always save raw data for debugging
    raw_path = output_dir / f"x_likes_raw_{timestamp}.json"
    export_to_json(posts, str(raw_path))

    return notes
