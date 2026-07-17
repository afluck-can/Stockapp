# strava-health-coach

An MCP (Model Context Protocol) server that gives Claude read access to your
Strava training data, plus a coaching framework ([GOALS.md](./GOALS.md)) for
turning that data into recommendations toward two goals: **lose 5 lb of fat**
and **gain 5 lb of muscle**.

There's no separate app or dashboard — you talk to Claude, Claude calls this
MCP server to pull your Strava data, and reasons about it using the framework
in `GOALS.md`.

## What it exposes

Five read-only tools, backed by the Strava API:

- `get_athlete_profile` — name, sex, body weight (single value, not a log), FTP
- `get_athlete_stats` — recent/YTD/all-time totals for runs, rides, swims
- `get_athlete_zones` — configured heart-rate/power zones
- `list_recent_activities` — summarized activities over a lookback window (default 28 days)
- `get_activity_detail` — full detail for a single activity by ID

## 1. Create a Strava API application

1. Go to <https://www.strava.com/settings/api> and create an application.
   - **Authorization Callback Domain**: `localhost`
   - Everything else can be anything reasonable (name, website, icon).
2. Note the **Client ID** and **Client Secret** shown on that page.

## 2. Install and configure

```bash
cd strava-health-coach
npm install
cp .env.example .env
```

Fill in `STRAVA_CLIENT_ID` and `STRAVA_CLIENT_SECRET` in `.env` from step 1.

## 3. Authorize (one-time)

```bash
npm run auth
```

This starts a tiny local server on `http://localhost:8721`, prints an
authorization URL — open it in your browser and click **Authorize** (grants
read-only + activity + profile read scopes). The terminal will then print a
`STRAVA_REFRESH_TOKEN` — copy it into `.env`.

Strava access tokens expire every 6 hours; the server uses this refresh token
to silently mint new ones at runtime, so you only do this once (unless you
revoke access on Strava's end).

## 4. Build

```bash
npm run build
```

## 5. Point Claude at it

**Claude Desktop** — add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "strava-health-coach": {
      "command": "node",
      "args": ["/absolute/path/to/strava-health-coach/dist/src/index.js"],
      "env": {
        "STRAVA_CLIENT_ID": "...",
        "STRAVA_CLIENT_SECRET": "...",
        "STRAVA_REFRESH_TOKEN": "..."
      }
    }
  }
}
```

**Claude Code** — from this directory:

```bash
claude mcp add strava-health-coach --scope user -- node "$(pwd)/dist/src/index.js"
```

(Claude Code will inherit `.env` automatically if you run it from this
directory with `dotenv`-loaded env vars present, or you can pass `--env`
flags mirroring the JSON above.)

## 6. Ask for a recommendation

Start a chat, share [`GOALS.md`](./GOALS.md) as context (paste it in, or ask
Claude to read the file if it has repo access), then ask something like:

> Using my last 6 weeks of Strava activity, where do I stand against my
> goals of losing 5 lb and gaining 5 lb of muscle? What should I change?

Claude will call the MCP tools above to pull real data before answering. It
will likely ask follow-up questions (current body weight, whether you lift,
rough diet habits) since Strava alone doesn't track those — see
[GOALS.md](./GOALS.md) for why.

## Notes

- All tools are read-only; nothing is written back to Strava.
- Rate limits: Strava allows 200 requests/15min and 2000/day per app.
- `.env` is gitignored — never commit real credentials.
