# Scrappy · Lead finder

Scrappy searches public Google results for LinkedIn profile pages that may indicate interest in data analytics courses, jobs, or a career change. It scores matching snippets and can export results as a CSV.

Search results come from Google through [SerpAPI](https://serpapi.com/). They reflect what Google has indexed; they are not a live feed from LinkedIn, and profile details may be incomplete or out of date. Review each profile and message before contacting anyone.

## Use the hosted app

Open [scrappy-alpha-ten.vercel.app](https://scrappy-alpha-ten.vercel.app/), choose a search intent and filters, then select **Find leads**. Use **Try sample results** to see example data without running a live search. Select **Download CSV** to save the displayed results.

Available intents are all searches, bootcamp and course seekers, data analyst job seekers, and career switchers. By default, profiles without a visible India location hint are excluded; the location option can include profiles without a visible location. Google snippets may be incomplete, so this is only a rough location filter.

## Run locally

Requirements: Python 3.12 or newer and a SerpAPI key for live searches.

1. Clone the repository and enter its directory.
2. Install dependencies:

   ```sh
   python -m pip install -r requirements.txt
   ```

3. Start the app with the Vercel development server:

   ```sh
   npx vercel dev
   ```

   The first run may ask you to link this directory to a Vercel project. To run live searches locally, add `SERPAPI_KEY` to the linked Vercel project's environment variables, or provide it to the local process using your shell's environment-variable syntax.

4. Open the local URL printed by Vercel. The sample results work without an API key.

### Command-line search

The original Python script can also write results directly to a CSV:

```sh
export SERPAPI_KEY="your-serpapi-key"
python leads_finder.py --out leads.csv --pages 1 --min-score 2
```

For sample data, no key is needed:

```sh
python leads_finder.py --demo --out demo_leads.csv
```

Use `python leads_finder.py --help` to see all command-line options. The web app limits pages per search to one or two; the standalone script accepts the number passed to `--pages`.

## Deploy to Vercel

1. Import this GitHub repository into Vercel.
2. Add `SERPAPI_KEY` under the Vercel project's **Settings → Environment Variables**. Mark it sensitive and enable it for the environments you plan to use.
3. Deploy or redeploy the project.
4. Open the deployment and run a sample search, then a live search.

The API reads the key on the server; do not put it in `index.html` or commit it to Git. Keep `.env*` files out of version control.

## Current service limits and considerations

- Live web searches use your SerpAPI account and may consume its search quota. The public `/api/search` endpoint currently has no sign-in or rate limit, so anyone who can access the app can trigger searches against that account. Add access control or rate limiting before sharing the app widely.
- A full search can make up to 12 provider requests per page. Each request may take up to 30 seconds; Vercel is configured with a 60-second function limit. Slow provider responses can cause a search to time out or return incomplete results.
- Some provider failures are reported as a warning alongside results. An empty result does not necessarily mean no matching profiles exist.
- Lead scores are simple text matches against public search titles and snippets. They are not verified intent or an endorsement to contact someone.
- Suggested outreach messages include course details and pricing that should be checked and updated in `leads_finder.py` if the offer changes.
- The CSV export is intended for review. Treat profile information as personal data and follow applicable privacy, platform, and outreach rules.

## Project files

- `index.html` — browser interface and CSV export.
- `api/search.py` — Vercel search endpoint.
- `leads_finder.py` — search, filtering, scoring, and CLI implementation.
- `vercel.json` — Vercel function configuration.
