Renewal Radar

Lead prioritisation for partners selling Infor SunSystems. Upload a spreadsheet of named accounts and it tells you which ones show evidence of approaching a finance system decision — and, just as importantly, which ones don't.

Every claim it makes carries a verdict, a date and a source URL you can click.

What it does

For each account in the uploaded file, the tool gathers evidence from four public sources, classifies seven signals, and scores the account 1–5 in Python from those classifications.

The signals:

Signal	What it looks for
System in use	Which finance or ERP system the account runs
Competitor in place	Whether a competing system is already live, and whether that project is healthy
Maintenance expiry	When the current contract runs out
Leadership change	New CFO, FD or director appointments
Restructuring	Redundancies, M&A, reorganisation
Hiring signal	Finance recruitment volume against baseline
Replacement intent	A public statement of intent to replace or procure

Each gets one of: confirmed, likely / suspected / inferred, or unknown.

An empty cell means the evidence wasn't there. It is a real answer, not a gap.

Setup

Requires Python 3.11+.

bash
git clone https://github.com/zsheerani1/renewal-radar.git
cd renewal-radar
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env

Then fill in .env:

GOOGLE_API_KEY=
COMPANIES_HOUSE_KEY=
ADZUNA_APP_ID=
ADZUNA_APP_KEY=
Key	Where	Cost
GOOGLE_API_KEY	aistudio.google.com	Free tier, no card
COMPANIES_HOUSE_KEY	developer.company-information.service.gov.uk — create a Live application, then a REST key	Free
ADZUNA_APP_ID / ADZUNA_APP_KEY	developer.adzuna.com	Free tier, a few hundred calls a month

Google News needs no key.

Running it
bash
python server.py

Then open:

http://127.0.0.1:8000/?engine=evidence

The ?engine=evidence flag matters. Without it you get the legacy single-call pipeline, which is kept only for comparison.

Upload an .xlsx with a Sheet1 containing a Client Name column. Everything else is passed through untouched.

Input and output

In: the CRM template — Client Name, Client Contact, Contact Email, Heat Score, Analysis. It will also re-read its own output, so last week's results can go straight back in.

Out: the same file with 20 columns. Verdict cells are colour-coded — green confirmed, amber likely, grey unknown, blue-grey insufficient data. Unknown cells render empty.

The Hook column is the opening line for a call, capped at 15 words. It can only reference confirmed signals; an account with nothing confirmed gets no hook.

The Sources column carries the resolved publisher URLs behind each verdict.

How the score works

Arithmetic is done in Python, not by the model. The model returns verdicts; the code adds up.

CRITERIA
  leadership change confirmed, last 12 months      1.0
  restructuring confirmed, last 12 months          1.0
  maintenance expiry confirmed, within 12 months   0.5
  hiring signal elevated                           0.5
  competitive position                        0.25 – 1.0
      competitor confirmed, project troubled       1.0
      incumbent (our system already in place)      0.5
      competitor live and healthy                  0.25
      unknown / none found                         0.25

URGENCY
  expiry confirmed within 3 months                +1.0
  stated replacement intent confirmed             +1.0

heat = total, clamped 1–5

unknown never earns a point.

Evidence grade

Shown beside the heat score, never instead of it.

A — two or more confirmed verdicts from deterministic sources
B — confirmed verdicts resting only on general web search
C — nothing confirmed

Two accounts can score the same and mean different things. Hilton and Société Générale both scored 1 on the test set; Hilton was grade A, Société Générale grade B.

Sources
Source	Gives	Trust
Companies House	Dated director appointments, filing history	Highest — filed fact, can support grade A alone
Google News RSS	Restructuring, M&A, leadership news, multilingual	Medium
Adzuna	Job ads naming a finance system, hiring volume	Medium
Gemini + Google Search	General web, fills gaps the others miss	Lowest — caps an account at grade B

Gemini also performs the classification step, reading what the sources returned. It can only cite URLs the sources actually retrieved — invented citations are rejected in code and the verdict collapses to unknown.

Research is deterministic. Judgement is constrained. Arithmetic is in Python.

Limitations

Read this section before promising anything to anyone.

Maintenance expiry was not found for any account tested. This was the original premise of the tool and it has not held up. Expiry dates are rarely public. The tool is reliable on organisational change and competitive position; it is not a renewal-timing tool.

Companies House is UK-only, and for international groups it resolves to the UK subsidiary rather than the parent — so it may return the UK entity's directors, not the group CFO. Where a match is ambiguous the account is marked unresolved and the UI asks for a company number rather than guessing. On the six-account test set, four needed confirming by hand.

Adzuna has been thin. Zero ads naming a finance system across the accounts tested. The hiring signal needs at least three months of data and currently returns insufficient_data for everything.

LinkedIn is not a source and cannot be. Scraping it breaches their terms and is actively blocked.

GDELT is implemented but off by default, behind a config flag. It rate-limits hard and its coverage proved worse than Google News for these accounts.

Scores move if you re-fetch. The judge is deterministic given fixed evidence, but the web isn't. Runs replay from stored evidence by default; only Force refresh re-fetches.

Storage

Findings, entity resolutions and URL resolutions are cached in SQLite. Observations are append-only — each run stores the full evidence bundle and verdicts with a timestamp.

Bundles are stored, not scores. When the weights change, past observations re-score under the new rules instead of being invalidated, so history stays comparable.

Tests
bash
pytest                  # offline
pytest -m network       # also fetches cited URLs and asserts they resolve

97 tests. The adversarial set in tests/test_judge_rules.py feeds validate() the verdicts an over-confident model would produce and asserts they are downgraded: an invented URL collapses to unknown, an unresolved URL steps down to suspected, a hook leaning on an unconfirmed signal is stripped.

tests/test_invariants.py guards against silent blanket failures — a filter that classifies everything identically fails the suite rather than quietly returning nothing.

Layout
server.py          FastAPI app, both engines behind ?engine=
pipeline.py        orchestration
bundle.py          assembles the evidence bundle
judge.py           classification + code-enforced citation rules
scoring.py         pure functions, no IO
excel_io.py        openpyxl read/write, preserves template formatting
cache.py           SQLite: findings, resolutions, observations
sources/           one module per data source
prompts/           judge and analysis prompts
