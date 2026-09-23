"""Print raw source output so the data can be checked before any AI reads it.

    python dump_sources.py "Corinthia Hotels" --domain corinthia.com
    python dump_sources.py "Corinthia Hotels" "Millennium Hotels" \
        --domain corinthia.com --domain millenniumhotels.com \
        --country Malta --country "United Kingdom"
    python dump_sources.py "Hilton" --no-grounded

Domains and countries pair with account names by position; names without one
are fine. The domain is the disambiguator the real pipeline gets free from
column C.
"""

import argparse
import json
import logging
import os

from sources import adzuna, companies_house, gdelt, google_news, grounded_search
from sources.entity_resolution import brand_token, matches

logging.basicConfig(level=logging.INFO, format="  ! %(message)s")

def mark(text, token):
    return "  <-- matches domain" if matches(text, token) else ""


def rule(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def section(title, result=None):
    status = f"   [status: {result.status}]" if result is not None else ""
    print(f"\n--- {title} ---{status}")
    if result is not None and result.status != "ok":
        print(f"  NO EVIDENCE AVAILABLE — not the same as no evidence existing")
        if result.detail:
            print(f"  detail: {result.detail}")


def show(records, limit, fields, token=None, match_on=None):
    if not records:
        print("  (nothing returned)")
        return
    for record in records[:limit]:
        line = " | ".join(str(record.get(f) or "")[:64] for f in fields)
        hit = mark(" ".join(str(record.get(f) or "") for f in (match_on or [])), token)
        print(f"  {line}{hit}")
    if len(records) > limit:
        print(f"  ... and {len(records) - limit} more")


def dump(account, domain=None, country=None, grounded=True, use_gdelt=False):
    token = brand_token(domain)
    header = account
    if domain:
        header += f"   [domain: {domain} -> token {token!r}]"
    if country:
        header += f"   [country: {country}]"
    rule(header)

    resolution = companies_house.resolve_entity(account, domain)
    section("COMPANIES HOUSE - entity resolution", resolution)
    data = resolution.data or {}
    print(f"  confidence: {data.get('confidence', '-')}   margin: {data.get('margin', '-')}"
          f"   confirmed_by: {data.get('confirmed_by') or '-'}")
    print(f"  resolved:   {data.get('matched_name') or '(nothing accepted)'} {data.get('company_number') or ''}")
    if resolution.detail:
        print(f"  detail:     {resolution.detail}")
    if data.get("candidates"):
        print("  candidates considered:")
        for candidate in data["candidates"][:6]:
            print(f"    {candidate['score']:.3f}  {(candidate.get('title') or '')[:52]:54}"
                  f" {candidate.get('company_status')}")

    number = data.get("company_number")

    if number:
        officers = companies_house.get_officers(number)
        section("COMPANIES HOUSE - officers", officers)
        print(f"  {len(officers.data)} officers on file")
        show(officers.data, 8, ["name", "role", "appointed_on", "resigned_on", "occupation"])

        filings = companies_house.get_filing_history(number)
        section("COMPANIES HOUSE - filing history", filings)
        print(f"  {len(filings.data)} filings on file")
        show(filings.data, 8, ["date", "type", "category", "description"])

    code = adzuna.country_for(country, domain)

    news = google_news.search_news(account, country=code, domain=domain)
    section(f"GOOGLE NEWS [{code}] - news, 12 months", news)
    print(f"  query: {news.detail}")
    meta = news.meta
    print(f"  retrieved: {meta.get('articles_retrieved', 0)}   matched: {meta.get('articles_matched', 0)}"
          f"   match_rate: {meta.get('match_rate')}"
          f"{'   ** NEEDS REVIEW - low match rate, not no-news **' if meta.get('needs_review') else ''}")
    for article in news.data[:12]:
        print(f"  [{article['name_match']:5}] {(article.get('seendate') or '')[:17]} |"
              f" {(article.get('source') or '')[:22]:24}| {(article.get('title') or '')[:52]}")
    if len(news.data) > 12:
        print(f"  ... and {len(news.data) - 12} more")

    if use_gdelt:
        legacy = gdelt.search_news(account)
        section("GDELT (legacy, off by default) - news, 12 months", legacy)
        print(f"  {len(legacy.data)} articles")
        show(legacy.data, 6, ["seendate", "sourcecountry", "domain", "title"], token, ["title", "url"])

    mentions = adzuna.find_system_mentions(account, country=country, domain=domain)
    section(f"ADZUNA [{code}] - ads naming a finance system", mentions)
    print(f"  {len(mentions.data)} ads mention a finance system")
    for ad in mentions.data[:8]:
        hit = mark(ad.get("company"), token)
        print(f"  {(ad.get('created') or '')[:10]} | {', '.join(ad['systems_mentioned'])} | {ad.get('title')}{hit}")
        print(f"      {ad.get('company')} - {ad.get('url')}")

    counts = adzuna.count_finance_roles(account, country=country, domain=domain)
    section(f"ADZUNA [{code}] - finance roles by month", counts)
    print(f"  {json.dumps(counts.data) if counts.data else '(nothing returned)'}")

    if grounded:
        claims = grounded_search.find_claims(account, domain=domain)
        section("GROUNDED SEARCH - claims (weaker evidence, never ruled_out)", claims)
        print(f"  {len(claims.data)} claims")
        for claim in claims.data:
            print(f"  [{claim['claim_type']}] {claim.get('date') or 'no date'} — {claim['statement']}")
            print(f"      {claim.get('source_title')} — {claim['source_url']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("accounts", nargs="+", help="Client names to look up")
    parser.add_argument("--domain", action="append", default=[],
                        help="Email domain for the account, paired by position. Repeatable.")
    parser.add_argument("--country", action="append", default=[],
                        help="Country for the account, paired by position. Repeatable.")
    parser.add_argument("--no-grounded", action="store_true",
                        help="Skip the grounded search source (it costs money).")
    parser.add_argument("--gdelt", action="store_true",
                        help="Also query the legacy GDELT source (off by default).")
    args = parser.parse_args()

    for name, values in (("--domain", args.domain), ("--country", args.country)):
        if len(values) > len(args.accounts):
            parser.error(f"more {name} values than account names")

    for index, account in enumerate(args.accounts):
        dump(
            account,
            args.domain[index] if index < len(args.domain) else None,
            args.country[index] if index < len(args.country) else None,
            grounded=not args.no_grounded,
            use_gdelt=args.gdelt or os.environ.get("ENABLE_GDELT") == "1",
        )
    print()


if __name__ == "__main__":
    main()
