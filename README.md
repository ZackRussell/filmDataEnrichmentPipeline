# Film Data Enrichment Pipeline

A schema-agnostic ETL tool that enriches any movie dataset — a CSV or a SQLite table — with data from [TMDB](https://www.themoviedb.org/) (release date, runtime, genres, budget, revenue, rating, popularity, original language).

Built as a companion to my [film awards database](https://github.com/ZackRussell/filmAwardAnalysis), but designed to work with any movie dataset, not just that one.

## The problem this solves

Most movie datasets only have a title, maybe a year. That's not enough to answer interesting questions like "do higher-budget films get nominated more often" or "is there a runtime sweet spot for award winners." TMDB has that data, but getting from "a list of titles" to "a reliably joined enrichment table" has two real problems:

**1. Titles don't match exactly.** Remakes, punctuation differences, "The" placement, international titles — a naive exact-string lookup against TMDB fails constantly. This pipeline uses fuzzy string matching ([rapidfuzz](https://github.com/rapidfuzz/RapidFuzz)) to score candidates instead of requiring an exact match.

**2. A "date" column doesn't always mean "release date."** If you're enriching an awards database, a date column might be the *ceremony* year, not the film's release year — and those routinely differ by a year. This pipeline never treats a provided date as a hard filter. It's used only as a soft disambiguator, with a tolerance window, when multiple title candidates are otherwise close. Every match is logged with the reasoning behind it, so you can audit exactly why a given row matched the way it did.

## How it works

1. **Extract** — read source rows from a CSV or any table in a SQLite database
2. **Match** — for each row, search TMDB by title (+ year hint if available), score every candidate on title similarity and date proximity, and accept the best match only if it clears a confidence threshold. Anything below threshold is logged as unmatched rather than guessed.
3. **Transform** — normalize the TMDB response into a consistent set of fields
4. **Load** — write the enriched rows to a new CSV or a brand-new SQLite table. Existing data is never modified or overwritten; the tool refuses to write to a table name that already exists.

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env   # then add your free TMDB API key
python app.py
```

Open `http://127.0.0.1:5000` and walk through:
1. Upload a CSV or point at a SQLite file (and pick a table)
2. Map which column is the title, and optionally which is a date and which is an ID to carry through
3. Confirm or override the suggested output destination
4. Run it — progress is shown live, with a final matched/unmatched count and a download link

## Design notes

- **Rate limiting and retries** — requests are throttled well under TMDB's free-tier ceiling, with exponential backoff on 429s and 5xxs, rather than hammering the API and hoping.
- **No source mutation** — output always goes to a new file or a new table. The tool will not append to or overwrite an existing table, by design.
- **Match transparency** — every output row carries a `match_status`, `match_confidence`, and `match_reason`, so a "matched" result isn't a black box. You can see exactly why the pipeline picked the record it did, or why it gave up.

## Project structure

```
app.py              # Flask web app (routes + job orchestration)
pipeline.py          # Core extract → match → transform flow
matcher.py           # Fuzzy title matching + soft date disambiguation
tmdb_client.py        # Rate-limited TMDB API wrapper
data_io.py            # CSV/SQLite read/write adapters + output suggestion
templates/index.html  # Single-page UI
requirements.txt
.env.example
```
