"""One-time cleanup: Remove DeepSeek-generated items from the checkpoint.

DeepSeek items have empty url, empty author, and empty stats.
These were generated at indices 180-242 without proper browser extraction.
"""
import json
import shutil
from datetime import datetime

CHECKPOINT = "checkpoints/checkpoint_NephliumDark.json"

def main():
    # Backup first
    backup = f"{CHECKPOINT}.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(CHECKPOINT, backup)
    print(f"[v] Backup saved: {backup}")

    with open(CHECKPOINT, "r", encoding="utf-8") as f:
        data = json.load(f)

    results = data["results"]
    print(f"[*] Original results count: {len(results)}")

    # Separate good items from DeepSeek items
    good_results = []
    removed = 0
    for r in results:
        is_bad = (r.get("url", "") == "" or r.get("author", "") == "") and r.get("stats", {}) == {}
        if is_bad:
            removed += 1
        else:
            good_results.append(r)

    print(f"[*] Removed {removed} DeepSeek items")
    print(f"[*] Keeping {len(good_results)} good items")

    # Rebuild processed_urls from good results only
    good_urls = []
    good_ids = set()
    for r in good_results:
        if r["url"]:
            good_urls.append(r["url"])
        good_ids.add(r["id"])

    # Also keep URLs from processed_urls that match good IDs
    # (some may have different URL formats)
    for url in data.get("processed_urls", []):
        for gid in good_ids:
            if gid in url and url not in good_urls:
                good_urls.append(url)

    # Write cleaned checkpoint
    cleaned = {
        "processed_urls": good_urls,
        "results": good_results,
        "results_count": len(good_results),
        "url_count": len(good_urls),
    }

    with open(CHECKPOINT, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2, ensure_ascii=False)

    print(f"[v] Checkpoint cleaned: {len(good_results)} results, {len(good_urls)} URLs")

if __name__ == "__main__":
    main()
