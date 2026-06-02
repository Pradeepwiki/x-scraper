"""
LLM-Powered X Likes Categorizer
================================
Uses OpenAI API to semantically understand each post and assign it to
the most appropriate category. Processes posts in batches of 40.

Usage: OPENAI_API_KEY=$(echo %OPENAI_API_KEY%) python categorizer_llm.py
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime

CHECKPOINT_PATH = Path("checkpoints") / "checkpoint_NephliumDark.json"
CATEGORIZED_DIR = Path("output") / "categorized"

CATEGORIES = [
    "Hair",
    "Skin & Face",
    "Supplements & Vitamins",
    "Fitness & Exercise",
    "Diet & Nutrition",
    "AI & Technology",
    "Masculinity & Mindset",
    "Male Health & Testosterone",
    "Longevity & Anti-Aging",
    "Dating & Relationships",
    "Oral Care & Hygiene",
    "Finance & Investing",
    "Entertainment & Media",
    "Self-Improvement & Productivity",
    "Other / Miscellaneous",
]

BATCH_SIZE = 40
MAX_RETRIES = 3

CATEGORY_DESCRIPTIONS = {
    "Hair": "Hair loss prevention, regrowth treatments, haircare products, styling",
    "Skin & Face": "Skincare routine, acne treatment, facial products, anti-aging skin",
    "Supplements & Vitamins": "Dietary supplements, vitamins, minerals, protein powders, nootropics",
    "Fitness & Exercise": "Workouts, gym routines, muscle building, cardio, exercise tips",
    "Diet & Nutrition": "Food choices, meal plans, recipes, diets, nutritional tips",
    "AI & Technology": "AI tools, programming, coding, software, tech news, developer tools",
    "Masculinity & Mindset": "Discipline, motivation, self-improvement mindset, masculine energy",
    "Male Health & Testosterone": "Testosterone optimization, TRT, sexual health, male physiology",
    "Longevity & Anti-Aging": "Living longer, reversing aging, longevity science, biohacking",
    "Dating & Relationships": "Dating advice, relationships, attraction tips, romance",
    "Oral Care & Hygiene": "Teeth whitening, fresh breath, dental care, grooming routines",
    "Finance & Investing": "Money management, investing, crypto, wealth building, business",
    "Entertainment & Media": "Movies, TV shows, music, gaming, entertainment news",
    "Self-Improvement & Productivity": "Habits, routines, learning, focus, skills, productivity hacks",
}

SYSTEM_PROMPT = f"""You are a social media post categorizer. Classify each post into exactly one category.

Categories (pick the BEST fit):
{chr(10).join(f'- **{c}**: {CATEGORY_DESCRIPTIONS.get(c, "")}' for c in CATEGORIES if c != "Other / Miscellaneous")}
- **Other / Miscellaneous**: If none of the above clearly fit

Rules:
- Understand the SEMANTIC MEANING of the post — don't just match keywords
- If a post could fit multiple, pick the MOST SPECIFIC one
- For product recommendations, pick the category that best describes the product's purpose
- Respond with ONLY a valid JSON array like: [{{"index": 0, "category": "Skin & Face"}}, ...]"""


def load_checkpoint():
    with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    posts = data.get("results", [])
    print(f"[LOAD] {len(posts)} posts loaded from checkpoint")
    return posts


def call_openai_api(batch_posts, api_key):
    """Send a batch of posts to OpenAI for categorization."""
    import openai
    openai.api_key = api_key

    user_prompt = "Classify each post into one category. Return JSON array.\n\n"
    for i, p in enumerate(batch_posts):
        text = (p.get("text") or "")[:300].replace("\n", " ").strip()
        handle = p.get("author_handle") or ""
        user_prompt += f"[{i}] @{handle}: \"{text}\"\n"

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=4096,
        )
    except Exception as e:
        raise RuntimeError(f"API error: {e}")

    response_text = response["choices"][0]["message"]["content"].strip()

    # Parse JSON from response
    json_match = re.search(r"\[.*?\]", response_text, re.DOTALL)
    if not json_match:
        raise RuntimeError(f"No JSON in response: {response_text[:300]}")

    try:
        classifications = json.loads(json_match.group(0))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"JSON parse error: {e}. Text: {response_text[:300]}")

    valid_categories = set(CATEGORIES)
    result_map = {}
    for item in classifications:
        idx = item.get("index")
        cat = item.get("category", "").strip()
        if cat not in valid_categories:
            cat = "Other / Miscellaneous"
        result_map[idx] = cat

    return result_map


def main():
    print("=" * 60)
    print("  LLM-POWERED X LIKES CATEGORIZER")
    print("  Using GPT-4o-mini for semantic understanding")
    print("=" * 60)

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("\n[ERROR] OPENAI_API_KEY not set!")
        print("  Run: OPENAI_API_KEY=$(echo %OPENAI_API_KEY%) python categorizer_llm.py")
        sys.exit(1)

    raw_posts = load_checkpoint()
    total_posts = len(raw_posts)
    total_batches = (total_posts + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"[BATCH] {total_posts} posts in {total_batches} batches of {BATCH_SIZE}")

    categorized = {name: [] for name in CATEGORIES}
    note_lookup = {}

    for batch_start in range(0, total_posts, BATCH_SIZE):
        batch = raw_posts[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1

        print(f"\n  Batch {batch_num}/{total_batches} (posts {batch_start+1}-{batch_start+len(batch)})...")

        for attempt in range(MAX_RETRIES):
            try:
                classifications = call_openai_api(batch, api_key)
                break
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    wait = 3 * (attempt + 1)
                    print(f"    Retry {attempt+1}/{MAX_RETRIES}: {e}")
                    time.sleep(wait)
                else:
                    print(f"    FAILED: {e}")
                    classifications = {}

        for i, post in enumerate(batch):
            url = post.get("url", "")
            cat = classifications.get(i, "Other / Miscellaneous")
            if cat not in categorized:
                cat = "Other / Miscellaneous"
            categorized[cat].append(url)

            text = post.get("text") or ""
            media = post.get("media") or {}
            note_lookup[url] = {
                "source_url": url,
                "author": post.get("author", ""),
                "author_handle": post.get("author_handle", ""),
                "date": post.get("timestamp", ""),
                "content": text,
                "has_images": len(media.get("images") or []) > 0,
                "has_videos": len(media.get("videos") or []) > 0,
                "has_links": len(media.get("urls") or []) > 0,
                "is_reply": post.get("is_reply", False),
                "stats": post.get("stats", {}),
            }

        print(f"    -> {batch_start + len(batch)}/{total_posts} done")

    # Summary
    print("\n" + "-" * 60)
    print("  CATEGORIZATION SUMMARY")
    print("-" * 60)
    total_categorized = 0
    for cat_name in sorted(categorized, key=lambda c: -len(categorized[c])):
        urls = categorized[cat_name]
        pct = len(urls) / max(total_posts, 1) * 100
        bar = "#" * max(1, int(pct / 3.3)) if pct > 0 else ""
        bar = bar + "." * max(0, 30 - len(bar))
        print(f"  {cat_name:38s} {bar} {len(urls):4d} ({pct:5.1f}%)")
        total_categorized += len(urls)
    print("-" * 60)
    print(f"  {'TOTAL':38s} {'#' * 30} {total_categorized:4d}")

    # Save
    CATEGORIZED_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n[SAVE] Writing to {CATEGORIZED_DIR}/")

    for old_file in CATEGORIZED_DIR.glob("*.json"):
        if old_file.name != "_index.json":
            old_file.unlink()

    export_date = datetime.now().strftime("%Y%m%d_%H%M%S")
    for cat_name in categorized:
        urls = categorized[cat_name]
        if len(urls) == 0:
            continue
        safe_name = cat_name.replace(" & ", "_").replace("/", "").replace(" ", "_").replace("__", "_")
        cat_data = {
            "category": cat_name,
            "description": CATEGORY_DESCRIPTIONS.get(cat_name, ""),
            "source_file": "checkpoint_NephliumDark.json",
            "export_date": export_date,
            "method": "llm_gpt4o_mini",
            "count": len(urls),
            "posts": [{
                "source_url": url,
                "author": note_lookup[url]["author"],
                "author_handle": note_lookup[url]["author_handle"],
                "date": note_lookup[url]["date"],
                "content": note_lookup[url]["content"],
                "has_images": note_lookup[url]["has_images"],
                "has_videos": note_lookup[url]["has_videos"],
                "has_links": note_lookup[url]["has_links"],
                "is_reply": note_lookup[url]["is_reply"],
                "stats": note_lookup[url]["stats"],
            } for url in urls],
        }
        filepath = CATEGORIZED_DIR / f"llm_{safe_name}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(cat_data, f, indent=2, ensure_ascii=False)
        print(f"  [OK] llm_{safe_name:35s} ({len(urls):3d} posts)")

    index_data = {
        "source_file": "checkpoint_NephliumDark.json",
        "export_date": export_date,
        "method": "llm_gpt4o_mini",
        "total_posts": total_posts,
        "total_categorized": total_categorized,
        "categories": {},
    }
    for cat_name in sorted(categorized, key=lambda c: -len(categorized[c])):
        urls = categorized[cat_name]
        safe_name = cat_name.replace(" & ", "_").replace("/", "").replace(" ", "_").replace("__", "_")
        index_data["categories"][cat_name] = {
            "file": f"llm_{safe_name}.json",
            "count": len(urls),
            "description": CATEGORY_DESCRIPTIONS.get(cat_name, ""),
        }

    with open(CATEGORIZED_DIR / "llm_index.json", "w", encoding="utf-8") as f:
        json.dump(index_data, f, indent=2, ensure_ascii=False)
    print(f"  [OK] llm_index{' ':<35s} (category index)")

    print(f"\nDone! Files saved to {CATEGORIZED_DIR.resolve()}")


if __name__ == "__main__":
    main()
