#!/usr/bin/env python3
"""
LinkedIn lead finder (search-based, no direct LinkedIn scraping).

How it works:
  1. Runs Google searches restricted to linkedin.com/in (public profile pages)
     through SerpAPI (https://serpapi.com) for intent-based queries such as
     "aspiring data analyst" or "data analyst bootcamp".
  2. Keeps only linkedin.com/in/... URLs, de-duplicates them.
  3. Scores each result on intent signals found in the title/snippet and
     assigns a lead category (bootcamp seeker, job seeker, career switcher).
  4. Writes the leads to a CSV file.

Usage:
  export SERPAPI_KEY=your_key_here
  python leads_finder.py --out leads.csv --pages 2

  # Offline test with sample data (no API key needed):
  python leads_finder.py --demo --out demo_leads.csv
"""

import argparse
import csv
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from urllib.parse import urlparse, urlunparse

try:
    import requests
except ImportError:
    requests = None

# ---------------------------------------------------------------------------
# Search queries: each one targets a different intent.
# ---------------------------------------------------------------------------
QUERIES = [
    # Bootcamp seekers
    '"data analyst" "bootcamp" India site:linkedin.com/in',
    '"data analytics course" India site:linkedin.com/in',
    '"enrolled" "data analytics" course India site:linkedin.com/in',
    '"learning data analytics" India site:linkedin.com/in',
    # Job seekers
    '"aspiring data analyst" India site:linkedin.com/in',
    '"looking for" "data analyst" India site:linkedin.com/in',
    '"open to work" "data analyst" India site:linkedin.com/in',
    '"fresher" "data analyst" India site:linkedin.com/in',
    # Career switchers / non-tech
    '"career switch" "data analytics" India site:linkedin.com/in',
    '"transitioning into data" India site:linkedin.com/in',
    '"BCom" "data analyst" India site:linkedin.com/in',
    '"BBA" "data analytics" India site:linkedin.com/in',
]

# ---------------------------------------------------------------------------
# Intent signals: (regex, weight, category)
# Category decides what kind of lead this is.
# ---------------------------------------------------------------------------
SIGNALS = [
    (r"\bbootcamp\b", 3, "bootcamp_seeker"),
    (r"\b(data analytics|data analysis) course\b", 3, "bootcamp_seeker"),
    (r"\b(enrolled|enrolling|signed up)\b.*\b(bootcamp|course|program)\b", 3, "bootcamp_seeker"),
    (r"\b(aspiring|future|wannabe)\s+data (analyst|analytics)\b", 3, "job_seeker"),
    (r"\b(looking for|seeking|searching for|hunting for)\b.*\bdata (analyst|analytics)\b", 3, "job_seeker"),
    (r"\bopen to work\b", 2, "job_seeker"),
    (r"\b(entry[- ]level|junior|fresher|graduate)\b.*\bdata\b", 2, "job_seeker"),
    (r"\b(career switch|career change|transitioning|transition|pivot(ing)?)\b", 2, "career_switcher"),
    (r"\b(learning|studying|upskilling)\b.*\bdata (analytics|analysis|analyst)\b", 2, "career_switcher"),
    (r"\b(fresher|fresh graduate|recent graduate|btech|b\.?com|bba|mba)\b.*\b(data|analy)", 2, "job_seeker"),
    (r"\b(sql|excel|power bi|tableau)\b", 1, "skill_signal"),
]

# Negative signals: these people are probably already hired or selling courses.
NEGATIVES = [
    (r"\b(hiring|recruiter|talent acquisition|we are hiring)\b", -4),
    (r"\b(instructor|mentor|trainer|course creator|bootcamp (lead|coach))\b", -4),
    (r"\b(senior|lead|manager|director|head of)\b.*\bdata\b", -2),
]


# Only keep India-based profiles (subdomain or location words in the result).
INDIA_HINTS = [
    "india", "in.linkedin.com", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai",
    "chennai", "kolkata", "delhi", "new delhi", "gurugram", "gurgaon", "noida", "ghaziabad",
    "dadri", "chandigarh", "ahmedabad", "jaipur", "lucknow", "kochi", "indore", "bhopal",
    "coimbatore", "vadodara", "surat", "nagpur", "patna", "bhubaneswar",
]


def india_status(raw_link: str, text: str):
    """Return True (India shown), False (another country shown), or None (no location info)."""
    if urlparse(raw_link).netloc.lower().startswith("in."):
        return True
    text = text.lower()
    if any(re.search(r"\b" + re.escape(h) + r"\b", text) for h in INDIA_HINTS):
        return True
    return None


# First-message templates per lead category. {first_name} is filled in.
# Facts used here come from quat.one: 4 weeks, live Zoom, ₹10,000, EMI available,
# placement assistance (hiring decisions rest with employers).
TEMPLATES = {
    "bootcamp_seeker": (
        "Hi {first_name}, I saw you're looking into a data analytics course. "
        "QUAT runs a 4-week live Zoom Data Analyst Bootcamp (Excel, SQL, Power BI, "
        "Tableau, Python, GenAI) for ₹10,000, with EMI available. Happy to share the "
        "syllabus if useful. Reply STOP and I won't message again."
    ),
    "job_seeker": (
        "Hi {first_name}, I noticed you're aiming for a data analyst role. "
        "We run a 4-week live Zoom bootcamp focused on job-ready skills: Excel, SQL, "
        "Power BI, Tableau, Python, 5+ portfolio projects and interview prep, for ₹10,000 "
        "(EMI available). Want the details? Reply STOP and I won't message again."
    ),
    "career_switcher": (
        "Hi {first_name}, you mentioned moving into data analytics. Our 4-week live "
        "Zoom bootcamp is built for career switchers and non-tech grads: Excel, SQL, "
        "Power BI, Tableau, Python and projects for your portfolio, for ₹10,000 "
        "(EMI available). Happy to send the brochure. Reply STOP to opt out."
    ),
}


def first_name(full_name: str) -> str:
    parts = full_name.strip().split()
    return parts[0] if parts else "there"


@dataclass
class Lead:
    name: str
    headline: str
    profile_url: str
    category: str
    score: int
    matched_query: str
    snippet: str
    intent_phrase: str = ""
    suggested_message: str = ""


def normalize_linkedin_url(url: str):
    """Return a clean https://www.linkedin.com/in/<slug> URL, or None."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = parsed.netloc.lower()
    if "linkedin.com" not in host:
        return None
    m = re.match(r"^/in/([^/?#]+)", parsed.path)
    if not m:
        return None  # ignore company pages, posts, jobs, etc.
    slug = m.group(1).lower()
    return urlunparse(("https", "www.linkedin.com", f"/in/{slug}", "", "", ""))


def extract_name(title: str) -> str:
    # Google titles look like "Jane Doe - Data Analyst - LinkedIn"
    name = re.split(r"\s[-|–]\s", title)[0]
    name = re.sub(r"\s*\|?\s*LinkedIn\s*$", "", name, flags=re.I)
    return name.strip()


def score_text(text: str):
    """Return (score, category, matched_phrase) based on intent signals in the text."""
    text = text.lower()
    score = 0
    categories = {}
    phrase = ""
    for pattern, weight, cat in SIGNALS:
        m = re.search(pattern, text)
        if m:
            if not phrase or cat != "skill_signal":
                phrase = phrase or m.group(0)
            score += weight
            if cat != "skill_signal":
                categories[cat] = categories.get(cat, 0) + weight
    for pattern, weight in NEGATIVES:
        if re.search(pattern, text):
            score += weight
    category = max(categories, key=categories.get) if categories else "unclassified"
    return score, category, phrase


def search_serpapi(query: str, api_key: str, page: int = 0):
    """Call SerpAPI's Google engine and return organic results."""
    if requests is None:
        sys.exit("The 'requests' package is required: pip install requests")
    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "num": 10,
        "start": page * 10,
    }
    resp = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("organic_results", [])


def build_leads(results, query: str, seen: set, min_score: int, include_unknown: bool = False):
    leads = []
    for item in results:
        url = normalize_linkedin_url(item.get("link", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        score, category, phrase = score_text(f"{title} {snippet}")
        if score < min_score:
            continue
        india = india_status(item.get("link", ""), f"{title} {snippet}")
        if india is None and not include_unknown:
            continue  # location not shown in the result
        if india is False:
            continue
        name = extract_name(title)
        template = TEMPLATES.get(category, TEMPLATES["job_seeker"])
        leads.append(Lead(
            name=name,
            headline=title,
            profile_url=url,
            category=category,
            score=score,
            matched_query=query,
            snippet=snippet,
            intent_phrase=phrase,
            suggested_message=template.format(first_name=first_name(name)),
        ))
    return leads


DEMO_RESULTS = [
    {"title": "Priya Sharma - Aspiring Data Analyst - LinkedIn",
     "link": "https://in.linkedin.com/in/priya-sharma-123",
     "snippet": "Aspiring data analyst | Learning SQL and Power BI | Looking for my first role"},
    {"title": "Rahul Verma - Enrolled in Data Analytics Bootcamp - LinkedIn",
     "link": "https://www.linkedin.com/in/rahulverma",
     "snippet": "Career switch from sales to data. Enrolled in a 6-month data analytics bootcamp."},
    {"title": "Amit Kumar - Senior Data Analyst at Acme - LinkedIn",
     "link": "https://www.linkedin.com/in/amitkumar-acme",
     "snippet": "Senior Data Analyst | Hiring for my team | SQL, Tableau"},
    {"title": "Sneha Rao - Data Analytics Trainer - LinkedIn",
     "link": "https://www.linkedin.com/in/sneha-rao-trainer",
     "snippet": "Bootcamp instructor teaching data analytics. Mentor for career changers."},
    {"title": "Meera Iyer - Career Switch to Data Analytics - LinkedIn",
     "link": "https://in.linkedin.com/in/meera-iyer",
     "snippet": "BCom grad, Chennai. Career switch to data analytics, learning Excel and SQL."},
    {"title": "Karan Mehta - Open to Work - Data Analyst - LinkedIn",
     "link": "https://www.linkedin.com/in/karanmehta",
     "snippet": "Open to work: data analyst roles. Fresher with an Excel and SQL background."},
    {"title": "John Smith - Aspiring Data Analyst - LinkedIn",
     "link": "https://www.linkedin.com/in/john-smith-us",
     "snippet": "Aspiring data analyst in Austin, Texas. Looking for my first role."},
    {"title": "Some Company Page - LinkedIn",
     "link": "https://www.linkedin.com/company/some-company",
     "snippet": "We are hiring data analysts."},
]


def write_csv(leads, path: str):
    fields = ["name", "category", "score", "intent_phrase", "headline", "profile_url",
              "matched_query", "snippet", "suggested_message"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for lead in sorted(leads, key=lambda l: l.score, reverse=True):
            writer.writerow(asdict(lead))


def main():
    parser = argparse.ArgumentParser(description="Find LinkedIn leads via public search results.")
    parser.add_argument("--out", default="leads.csv", help="Output CSV path")
    parser.add_argument("--pages", type=int, default=1, help="Result pages per query (10 results each)")
    parser.add_argument("--min-score", type=int, default=2, help="Minimum intent score to keep")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between API calls")
    parser.add_argument("--include-unknown", action="store_true",
                        help="Keep profiles whose location isn't shown in the search result")
    parser.add_argument("--demo", action="store_true", help="Use built-in sample data (no API key)")
    args = parser.parse_args()

    seen: set = set()
    all_leads = []

    if args.demo:
        all_leads = build_leads(DEMO_RESULTS, "demo", seen, args.min_score, args.include_unknown)
    else:
        api_key = os.environ.get("SERPAPI_KEY")
        if not api_key:
            sys.exit("Set SERPAPI_KEY first (or run with --demo).")
        for query in QUERIES:
            for page in range(args.pages):
                try:
                    results = search_serpapi(query, api_key, page)
                except Exception as exc:  # keep going if one query fails
                    print(f"[warn] {query!r} page {page}: {exc}", file=sys.stderr)
                    continue
                found = build_leads(results, query, seen, args.min_score, args.include_unknown)
                all_leads.extend(found)
                print(f"[ok] {len(found):2d} new leads from {query}")
                time.sleep(args.delay)

    write_csv(all_leads, args.out)
    print(f"\nSaved {len(all_leads)} leads to {args.out}")


if __name__ == "__main__":
    main()
