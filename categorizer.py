"""
X Likes Data Categorizer
=======================
Reads the checkpoint JSON (which contains all scraped posts) and categorizes
each post into topic-specific JSON files for easy browsing.

Categories:
  - Hair
  - Skin & Face
  - Supplements & Vitamins
  - Fitness & Exercise
  - Diet & Nutrition
  - AI & Technology
  - Masculinity & Mindset
  - Male Health & Testosterone
  - Longevity & Anti-Aging
  - Dating & Relationships
  - Oral Care & Hygiene
  - Finance & Investing
  - Entertainment & Media
  - Self-Improvement & Productivity
  - Other / Miscellaneous
"""

import json
import re
from pathlib import Path

CHECKPOINT_PATH = Path("checkpoints") / "checkpoint_NephliumDark.json"
CATEGORIZED_DIR = Path("output") / "categorized"

# --- Category Definitions ---------------------------------------------------

CATEGORIES = {
    "Hair": {
        "keywords": [
            r"\bhair\b", r"bald", r"balding", r"hairline", r"norwood",
            r"minoxidil", r"finasteride", r"dutasteride", r"microneedling",
            r"hair loss", r"hair growth", r"receding", r"thinning hair",
            r"scalp", r"dandruff", r"baby hair", r"gray hair", r"grey hair",
            r"hair care", r"hair oil", r"hair mask", r"keratin",
            r"coconut oil.*hair", r"hair.*thick", r"hair.*density"
        ],
        "description": "Hair loss prevention, regrowth, and hair care"
    },
    "Skin & Face": {
        "keywords": [
            r"\bskin\b", r"skincare", r"acne", r"face", r"facial",
            r"glow(?:ing)? skin", r"glass skin", r"pores", r"sunscreen",
            r"\bspf\b", r"moisturizer", r"serum", r"niacinamide",
            r"hyaluronic acid", r"vitamin c", r"retinol", r"salicylic acid",
            r"glycolic acid", r"collagen", r"gua sha", r"dry brush",
            r"face fat", r"jawline", r"double chin", r"dark circles",
            r"scar", r"scarring", r"skin barrier", r"lymphatic drainage",
            r"ice roller", r"ice on face", r"steaming face",
            r"cleaner skin", r"clearer skin", r"clean skin",
            r"skin.*health", r"skin.*care", r"skin.*glow",
            r"brighten(?:ing)? skin", r"under-eye", r"undereye",
            r"puffy", r"puffiness", r"centella", r"vitamin e.*capsule",
            r"evion"
        ],
        "description": "Skincare, acne treatment, and facial care"
    },
    "Supplements & Vitamins": {
        "keywords": [
            r"\bsupplement", r"\bvitamin", r"\bmineral", r"\bzinc\b",
            r"\bmagnesium\b", r"\bvitamin d\b", r"\bomega-3\b",
            r"\bprobiotic", r"\bcolagen\b", r"\bcollagen\b",
            r"\bcoq10\b", r"\bcoenzyme\b", r"\badaptogen",
            r"\bashwagandha\b", r"\bmultivitamin\b",
            r"\bvitamin b", r"\bvitamin c\b", r"\bvitamin e\b",
            r"\bvitamin k\b", r"\biron\b", r"\bcalcium\b",
            r"\bpotassium\b", r"\bfolate\b", r"\bfolic acid\b",
            r"\bberberine\b", r"\bcreatine\b", r"\bprotein powder",
            r"\bfish oil", r"\bcod liver oil", r"\bnootropic",
            r"\btake.*vitamin", r"\bwellness.*pill",
            r"\bsupplement.*stack", r"\bherbal supplement"
        ],
        "description": "Vitamins, minerals, herbs, and nutritional supplements"
    },
    "Fitness & Exercise": {
        "keywords": [
            r"\bgym\b", r"\bworkout", r"\bexercise", r"\bmuscle",
            r"\bfitness", r"\blift weight", r"\bprogressive overload",
            r"\bpush.?up", r"\bsquat", r"\bdeadlift", r"\bbench press",
            r"\bcardio", r"\bhiit\b", r"\bvo2 max", r"\bzone 2",
            r"\bchest muscle", r"\bgain muscle", r"\bmuscle mass",
            r"\bbody fat", r"\bfat loss", r"\bweight loss",
            r"\bprotein.*intake", r"\bworkout.*routine",
            r"\bgym.*routine", r"\bfitness.*tip", r"\bstrong",
            r"\bstrength train", r"\bcalisthenic", r"\bphysique",
            r"\bbodybuilding", r"\bwork out", r"\bget fit",
            r"\bfit.*body"
        ],
        "description": "Workouts, gym routines, muscle building, and exercise"
    },
    "Diet & Nutrition": {
        "keywords": [
            r"\bfood\b", r"\bdiet\b", r"\bnutrition\b", r"\beat\b",
            r"\brecipe", r"\bmeal\b", r"\bfruit", r"\bvegetable",
            r"\bprotein\b", r"\bfiber\b", r"\bomega.?3\b",
            r"\bantioxidant", r"\bintermittent fast", r"\bfasting\b",
            r"\bcalorie", r"\bmacro", r"\bwhole food", r"\bseed oil",
            r"\bolive oil", r"\bcoconut oil", r"\bketo\b",
            r"\bmediterranean diet", r"\bsugar.*intake",
            r"\bzero sugar", r"\bno sugar",
            r"\bprotein source", r"\bhigh.*protein", r"\bprotein.*diet",
            r"\bmeal plan", r"\bhealthy eat", r"\bbest food",
            r"\bfood.*health", r"\beating.*habit", r"\bbreakfast",
            r"\blunch\b", r"\bdinner\b", r"\bsmoothie\b",
            r"\bjuice\b", r"\bdrink.*health"
        ],
        "description": "Food choices, diets, meal plans, and nutritional tips"
    },
    "AI & Technology": {
        "keywords": [
            r"\bai\b", r"\bartificial intelligence", r"\bclaude\b",
            r"\bchatgpt\b", r"\bgpt\b", r"\bllm\b", r"\blarge language model",
            r"\banthropic\b", r"\bopenai\b", r"\bqwen\b", r"\bdeepseek\b",
            r"\bgemini\b", r"\bcopilot\b", r"\bcursor\b",
            r"\bperplexity\b", r"\bagent\b", r"\bprompt\b",
            r"\bprogramming\b", r"\bcoding\b", r"\bdeveloper\b",
            r"\bsoftware\b", r"\bgithub\b", r"\bcode\b",
            r"\bllm prompt", r"\bai agent", r"\bai tool",
            r"\bmachine learning", r"\bdeep learning",
            r"\bneural network", r"\bopen source", r"\bosint\b",
            r"\bghidra\b", r"\breverse engineering", r"\bida pro\b",
            r"\bradare2\b", r"\bdebugger\b", r"\bdisassembler",
            r"\bsystem design", r"\btech stack", r"\bcloud\b",
            r"\bdevops\b", r"\bjavascript", r"\bpython\b",
            r"\btypescript\b", r"\bhtml\b", r"\bcss\b",
            r"\breact\b", r"\bnode\.?js\b", r"\bapi\b",
            r"\bstudent.*id.*benefit", r"\bgithub.*student"
        ],
        "description": "AI tools, coding, programming, and technology"
    },
    "Masculinity & Mindset": {
        "keywords": [
            r"\bmasculine\b",
            r"\bmindset\b", r"\bdiscipline\b", r"\bmotivation\b",
            r"\bgrind\b", r"\bhustle\b", r"\bstay.*hard\b",
            r"\bstick.*plan\b", r"\bglow.?up as a man\b",
            r"\bprivate\b", r"\bwork hard\b", r"\bdress well\b",
            r"\btalk less\b", r"\bstay humble\b", r"\bchase goal",
            r"\bmanhood\b", r"\bbe a man\b", r"\bmen.*purpose",
            r"\bpath.*man\b", r"\bguide.*man\b", r"\bking\b",
            r"\bsigma\b", r"\bred.?pill\b", r"\bno.?fap\b",
            r"\bnofap\b", r"\bself.*improve", r"\bbecome.*better"
        ],
        "description": "Masculine self-improvement, discipline, and mindset"
    },
    "Male Health & Testosterone": {
        "keywords": [
            r"\btestosterone\b", r"\btrt\b", r"\bsperm\b",
            r"\blibido\b", r"\berectile", r"\bpremature ejaculation",
            r"\bsexual health", r"\bmale health", r"\bman.*health",
            r"\bprostate\b", r"\bzinc.*testosterone",
            r"\bmagnesium.*testosterone", r"\bvitamin d.*testosterone",
            r"\be rectile dysfunction", r"\bed\b.*dysfunction",
            r"\bmen.*sexual", r"\bmale.*hormone"
        ],
        "description": "Testosterone optimization, sexual health, and male physiology"
    },
    "Longevity & Anti-Aging": {
        "keywords": [
            r"\baging\b", r"\banti.?aging\b", r"\bantiaging\b",
            r"\breverse.*age", r"\bbiological age", r"\blongevity\b",
            r"\byouth\b", r"\blook young", r"\bpreserve.*youth",
            r"\breverse.*aging", r"\bage reversal", r"\blive longer",
            r"\bhealth.*span", r"\blife.*span", r"\bhormesis\b",
            r"\bsirtuin", r"\bnad\+", r"\bresveratrol",
            r"\bmetformin\b", r"\brapamycin\b", r"\b60.*years.*old.*look"
        ],
        "description": "Longevity science, anti-aging treatments, and youth preservation"
    },
    "Dating & Relationships": {
        "keywords": [
            r"\bdating\b", r"\brelationship", r"\bgirlfriend\b",
            r"\bwife\b", r"\bwomen\b", r"\bwoman\b",
            r"\battract", r"\bconfident.*women",
            r"\bdate.*tip", r"\bget.*girl", r"\btake.*out",
            r"\bromance\b", r"\blove\b", r"\bpartner\b",
            r"\bchemistry\b", r"\bmagnetic\b"
        ],
        "description": "Dating advice, relationship tips, and attraction"
    },
    "Oral Care & Hygiene": {
        "keywords": [
            r"\bteeth\b", r"\btooth\b", r"\bsmile\b", r"\bwhiten",
            r"\bfloss\b", r"\btoothbrush\b", r"\bmouthwash\b",
            r"\bbreath\b", r"\boral.*care", r"\boral.*hygiene",
            r"\bcoconut oil.*pull", r"\boil pull", r"\bwater flosser",
            r"\bhygiene\b", r"\bgrooming\b", r"\bfresh breath"
        ],
        "description": "Dental care, oral hygiene, and grooming"
    },
    "Finance & Investing": {
        "keywords": [
            r"\bmoney\b", r"\binvest\b", r"\bstock\b", r"\bcrypto\b",
            r"\bbitcoin\b", r"\bethereum\b", r"\bportfolio\b",
            r"\bwealth\b", r"\bpassive income", r"\bfinancial.*free",
            r"\bdebt\b", r"\bloan\b", r"\bsaving\b", r"\bbudget\b",
            r"\btrade\b", r"\btrading\b", r"\bcash\b",
            r"\bfinance\b", r"\beconomy\b", r"\bmarket\b",
            r"\bretire\b", r"\bfire\b", r"\bentrepreneur",
            r"\bbusiness\b", r"\bstartup\b", r"\bbank\b",
            r"\bcredit\b", r"\breal estate"
        ],
        "description": "Personal finance, investing, crypto, and wealth building"
    },
    "Entertainment & Media": {
        "keywords": [
            r"\bmovie\b", r"\bfilm\b", r"\bcinema\b", r"\bshow\b",
            r"\bseries\b", r"\btv\b", r"\bdrama\b",
            r"\bbollywood\b", r"\bhollywood\b", r"\bkorean\b",
            r"\banime\b", r"\bmusic\b", r"\bactor\b", r"\bactress\b",
            r"\bcelebrity\b", r"\bhobby\b", r"\bgame\b", r"\bgaming\b",
            r"\bsudoku\b", r"\boffline hobby"
        ],
        "description": "Movies, TV shows, music, and entertainment"
    },
    "Self-Improvement & Productivity": {
        "keywords": [
            r"\bproductivity\b", r"\bhabit\b", r"\broutine\b",
            r"\bself.?improve", r"\bgrowth\b", r"\bmorning routine",
            r"\bsuccess\b", r"\bgoal\b", r"\bfocus\b",
            r"\blearn\b", r"\bstudy\b", r"\breading\b",
            r"\bskill\b", r"\bcommunication\b", r"\bspeak\b",
            r"\bpublic speaking", r"\bconfidence\b",
            r"\bpersonal growth", r"\bdevelop\b", r"\bimprove\b",
            r"\blife hack", r"\bjournal\b", r"\bmeditation\b",
            r"\bmindfulness\b", r"\bsleep\b", r"\benergy\b",
            r"\bio.*optimization"
        ],
        "description": "Personal development, habits, productivity, and life optimization"
    }
}

# --- Helper Functions ---------------------------------------------------------

def load_checkpoint():
    """Load all posts from the checkpoint JSON."""
    if not CHECKPOINT_PATH.exists():
        raise FileNotFoundError(f"Checkpoint file not found: {CHECKPOINT_PATH}")
    print(f"[LOAD] Loading: {CHECKPOINT_PATH}")
    with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    posts = data.get("results", [])
    print(f"       -> {len(posts)} posts loaded from checkpoint")
    return "checkpoint_NephliumDark.json", posts, data.get("results_count", len(posts))


def normalize_post(post):
    """Convert a checkpoint post format to a unified dict for categorization and output."""
    media = post.get("media", {}) or {}
    images = media.get("images", []) or []
    videos = media.get("videos", []) or []
    urls = media.get("urls", []) or []

    text = post.get("text", "") or ""

    # Build a summary (first portion of text, plus media info)
    summary = text
    media_parts = []
    if images:
        media_parts.append(f"{len(images)} image(s)")
    if videos:
        media_parts.append(f"{len(videos)} video(s)")
    if urls:
        media_parts.append(f"{len(urls)} link(s)")
    if media_parts:
        summary += f"\n[Contains: {', '.join(media_parts)}]"

    return {
        "source_url": post.get("url", ""),
        "author": post.get("author", ""),
        "author_handle": post.get("author_handle", ""),
        "date": post.get("timestamp", ""),
        "content": text,
        "content_type": "post",
        "has_images": len(images) > 0,
        "has_videos": len(videos) > 0,
        "has_links": len(urls) > 0,
        "is_reply": post.get("is_reply", False),
        "stats": post.get("stats", {}),
        "summary": summary,
        # Keep raw media for OCR text extraction
        "_media": media,
    }


def get_all_text(note):
    """Combine all text fields from a note for keyword matching."""
    texts = [
        note.get("content", ""),
        note.get("summary", ""),
        note.get("author", ""),
        note.get("author_handle", ""),
    ]
    # Add OCR text from downloaded images if available
    media = note.get("_media", {})
    for img in media.get("downloaded_images", []):
        if isinstance(img, dict) and "ocr_text" in img:
            texts.append(img["ocr_text"])
    return "\n".join(texts).lower()


def categorize_note(note):
    """Assign a note to one or more categories based on keyword matching."""
    text = get_all_text(note)
    matches = []
    for cat_name, cat_def in CATEGORIES.items():
        for pattern in cat_def["keywords"]:
            if re.search(pattern, text, re.IGNORECASE):
                matches.append(cat_name)
                break  # One match per category is enough
    return matches if matches else ["Other / Miscellaneous"]


# --- Main --------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  X LIKES DATA CATEGORIZER")
    print("=" * 60)

    # Load checkpoint data
    source_filename, raw_posts, total_posts = load_checkpoint()

    # Normalize all posts into a unified format
    print(f"\n[NORM] Normalizing {len(raw_posts)} posts...")
    notes = [normalize_post(p) for p in raw_posts]
    export_date = "checkpoint_dump"

    # Build lookup by URL
    note_lookup = {note["source_url"]: note for note in notes}

    # Initialize category stores
    categorized = {name: [] for name in CATEGORIES}
    categorized["Other / Miscellaneous"] = []

    # Process each note
    print(f"\n[CAT] Categorizing {len(notes)} posts...")
    for note in notes:
        cats = categorize_note(note)
        url = note["source_url"]
        # Assign to first matching category, or "Other"
        primary_cat = cats[0]
        if primary_cat not in categorized:
            primary_cat = "Other / Miscellaneous"
        categorized[primary_cat].append(url)

    # Print summary
    print("\n" + "-" * 60)
    print("  CATEGORIZATION SUMMARY")
    print("-" * 60)
    total_categorized = 0
    for cat_name, urls in sorted(categorized.items(), key=lambda x: -len(x[1])):
        pct = len(urls) / max(len(notes), 1) * 100
        if pct > 0:
            bar_len = int(pct / 3.3)
            bar = "#" * bar_len + "." * (30 - bar_len)
        else:
            bar = "." * 30
        name_padded = cat_name + " " * (38 - len(cat_name))
        print(f"  {name_padded} {bar} {len(urls):4d} ({pct:5.1f}%)")
        total_categorized += len(urls)
    print("-" * 60)
    print(f"  TOTAL {' ':<34} {'#' * 30} {total_categorized:4d}")

    # Create output directory
    CATEGORIZED_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n[SAVE] Saving categorized files to: {CATEGORIZED_DIR}/")

    # Remove old files before writing new ones
    for old_file in CATEGORIZED_DIR.glob("*.json"):
        if old_file.name != "_index.json":
            old_file.unlink()

    # Save individual category files
    for cat_name, urls in categorized.items():
        safe_name = cat_name.replace(" & ", "_").replace("/", "").replace(" ", "_").replace("__", "_")
        cat_data = {
            "category": cat_name,
            "description": CATEGORIES.get(cat_name, {}).get("description", "Miscellaneous items"),
            "source_file": source_filename,
            "export_date": export_date,
            "count": len(urls),
            "posts": []
        }
        for url in urls:
            note = note_lookup.get(url, {})
            cat_data["posts"].append({
                "source_url": url,
                "author": note.get("author", ""),
                "author_handle": note.get("author_handle", ""),
                "date": note.get("date", ""),
                "content": note.get("content", ""),
                "content_type": note.get("content_type", ""),
                "has_images": note.get("has_images", False),
                "has_videos": note.get("has_videos", False),
                "has_links": note.get("has_links", False),
                "is_reply": note.get("is_reply", False),
                "stats": note.get("stats", {}),
                "summary": note.get("summary", ""),
            })

        if len(urls) == 0:
            continue  # Skip empty categories
        filepath = CATEGORIZED_DIR / f"{safe_name}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(cat_data, f, indent=2, ensure_ascii=False)
        print(f"  [OK] {safe_name:40s} ({len(urls):3d} posts)")

    # Save index file
    index_data = {
        "source_file": source_filename,
        "export_date": export_date,
        "total_posts": total_posts,
        "total_categorized": total_categorized,
        "last_updated": export_date,
        "categories": {}
    }
    for cat_name, urls in sorted(categorized.items(), key=lambda x: -len(x[1])):
        safe_name = cat_name.replace(" & ", "_").replace("/", "").replace(" ", "_").replace("__", "_")
        index_data["categories"][cat_name] = {
            "file": f"{safe_name}.json",
            "count": len(urls),
            "description": CATEGORIES.get(cat_name, {}).get("description", "Miscellaneous items"),
        }

    index_path = CATEGORIZED_DIR / "_index.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_data, f, indent=2, ensure_ascii=False)
    print(f"  [OK] _index{' ':<37s} (category index)")

    print("\n" + "=" * 60)
    print("  CATEGORIZATION COMPLETE!")
    print("=" * 60)
    print(f"\n  All files saved to: {CATEGORIZED_DIR.resolve()}")
    print(f"  Open {CATEGORIZED_DIR / '_index.json'} for the full index")


if __name__ == "__main__":
    main()
