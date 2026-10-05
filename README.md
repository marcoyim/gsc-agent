# Local GSC SEO Agent

A local command-line tool that reads your Google Search Console data, stores it in a SQLite file on your computer, and writes a Markdown report from rules you configure.

It requests the read-only scope `https://www.googleapis.com/auth/webmasters.readonly`. It does not change your site, your Search Console settings, or the stored rows. It has no access to forms, calls, CRM, or sales, so it cannot tell you which query brought a customer.

Reports and command output are in Traditional Chinese. Configuration, dates, and property strings stay as you enter them.

## Requirements

- Python 3.11 or newer
- A Google account that can already open the Search Console property you want to read

License: MIT.

## Install

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

On Windows, activate with `.venv\Scripts\activate`.

## Try it without a Google account

```bash
gsc-agent demo
```

This writes `demo-data/report.md` from fictional data for `https://demo-harbour-services.example/`. That site is not real. Nothing is sent to Google or to a language model.

## Connect your own property

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project and enable the Google Search Console API.
2. Create an OAuth client of type **Desktop app**. Download the client JSON. Do not commit that file.
3. Copy the examples and edit only your local copies:

```bash
cp config.example.toml config.toml
cp .env.example .env
```

4. In `config.toml`, set your brand terms, service terms, and URL patterns. The sample brand “示範港灣” and the host `demo-harbour-services.example` are placeholders. Scoring thresholds in `[scoring]` apply to the whole date range you choose. Raise the impression threshold when you select a longer range.
5. Leave `client_secrets_file`, `token_file`, and `database_path` empty to use the application data directory, or set your own paths outside the repository.

Check the environment before authorizing:

```bash
gsc-agent doctor --config config.toml
```

Until authorization succeeds, doctor reports that the API is not connected.

Authorize in the browser. The saved token is readable only by your user account. The command does not print the token.

```bash
gsc-agent auth --config config.toml
gsc-agent doctor --config config.toml
gsc-agent properties --config config.toml
```

`properties` prints the exact property string. Use that full string later. A URL-prefix property looks like `https://example.com/`. A domain property looks like `sc-domain:example.com`.

Sync, then analyze, then write a report:

```bash
gsc-agent sync --config config.toml \
  --property "https://example.com/" \
  --start 2026-01-01 --end 2026-01-31

gsc-agent analyze --config config.toml \
  --property "https://example.com/" \
  --start 2026-01-01 --end 2026-01-31 \
  --target-country HKG

gsc-agent report --config config.toml \
  --property "https://example.com/" \
  --start 2026-01-01 --end 2026-01-31 \
  --target-country HKG \
  --output reports/example.md
```

Replace `https://example.com/` with the string from `properties`. Dates are Search Console calendar dates in Pacific Time, including both ends. The program does not convert them to your local time zone.

## Language model for the written recommendations

`analyze` and `report` do not call a language model. They only print recorded figures and the rule checks. The short priority list — which queries to change, and whether the current page matches the search — comes from `agent`.

Run setup once. It asks which platform you use. You do not need OpenAI. The choices are a local Ollama install, DeepSeek, Google Gemini, Qwen on the Hong Kong endpoint, or any other compatible URL you paste in. A remote platform then asks for its API key. The key is saved in `.env` with permissions limited to your user. It is not written into `config.toml` and it is not printed. Ollama does not ask for a key, and the search data stays on this computer.

```bash
./setup.sh
./start.sh "https://example.com/" 2026-01-01 2026-01-31
```

If you pick a remote platform, `./start.sh` sends queries, URLs, and metrics to that platform. If you pick Ollama, they stay on this computer. The agent can only call a fixed set of read tools. It cannot run shell commands or change the database. Its conclusion can be wrong; the recorded-figures section is the source for numbers. It still cannot say which query brought a customer. A non-local address is refused unless setup recorded your choice and the command includes `--allow-remote-llm`. `./start.sh` adds that flag for you.

## How the data is stored

Each sync stores five datasets separately. Do not add their clicks or impressions together and call the sum the site total.

| Dataset | Dimensions | aggregationType | Use |
| --- | --- | --- | --- |
| `property_daily` | date | byProperty | The only site total |
| `query` | query | byProperty | Brand and non-brand, only for queries the API returned |
| `page` | page | byPage | Service pages, articles, and other pages |
| `country` | country | byProperty | Target country compared with other countries |
| `query_page_country` | query, page, country | byPage | Page opportunities and internal-link checks. This total is not the site total |

Search Analytics requests use `type=web` and `dataState=final`. Each day is requested on its own. `rowLimit` is at most 25,000, and the tool pages with `startRow` until a page comes back empty. Search Console also exposes about 50,000 rows per day for a search type, ordered by clicks. A day that hits the cap is marked truncated. It is not a complete list of queries.

A query that does not appear was not returned. It is not an impression count of zero. The report states the gap between the site total and the returned queries, and it does not assign that gap to any missing query.

A successful re-sync replaces that day for that dataset. A failed request keeps the previous rows for that day, and analysis skips the failed day.

## What the report means

- **Recorded figures** come from the local database: clicks, impressions, CTR, average position, country, page, and date.
- **Rule results** are the “worth a manual look” lists from your thresholds. They are not a sales forecast.
- **Model text** appears only after `agent`. If it cites a number that no tool returned, the report marks that number as not a recorded figure. The model is not asked to invent a conversion rate.

A service-page opportunity must match the target country, a non-brand query, service intent, a service URL, a position band, and that band’s own click cap. Position 4–10 and 10–20 do not share one CTR cutoff. High impressions and low CTR alone are not enough. Items below the sample threshold stay in an unscored appendix.

Average position is weighted by impressions. It is not a single fixed rank.

## What stays on your computer

These paths are ignored by Git:

- `.env` and `config.toml`
- OAuth client JSON and token files
- SQLite databases
- `reports/`, `demo-data/`, `user-data/`, and table exports

Ignoring a file does not erase it from history if it was committed earlier. If a token or client JSON was ever committed, revoke it in Google Cloud, remove it from history, and treat the old remote copy as exposed. Deleting the latest commit is not enough.

Error text is redacted for access tokens, refresh tokens, and client secrets. Still avoid pasting a full terminal log into a public issue.

## Limits

- No Analytics, form, phone, or CRM data. This version does not attribute inquiries.
- It cannot show that a query produced a customer.
- The API is not a full query list and it is not live data. The default data state is `final`.
- A long date range sends one request series per day and can hit quota. Quota, an expired token, missing permission, an empty result, and a network failure are recorded separately.
- Each “query group” is one returned query. Similar wording is not merged.
- An internal-link note means an article URL and a service URL shared one returned query.
- The agent can be wrong. Use the recorded-figures section when they disagree.

## License

MIT. See [LICENSE](LICENSE).
