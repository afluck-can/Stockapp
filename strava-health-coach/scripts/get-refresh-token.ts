#!/usr/bin/env node
/**
 * One-time local OAuth flow to obtain a Strava refresh token.
 * Run with: npm run auth
 */
import "dotenv/config";
import { createServer } from "node:http";
import { URL } from "node:url";

const PORT = 8721;
const REDIRECT_URI = `http://localhost:${PORT}/callback`;
const SCOPE = "read,activity:read_all,profile:read_all";

const clientId = process.env.STRAVA_CLIENT_ID;
const clientSecret = process.env.STRAVA_CLIENT_SECRET;

if (!clientId || !clientSecret) {
  console.error(
    "Missing STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET.\n" +
      "Copy .env.example to .env and fill them in from https://www.strava.com/settings/api first."
  );
  process.exit(1);
}

const authorizeUrl = new URL("https://www.strava.com/oauth/authorize");
authorizeUrl.searchParams.set("client_id", clientId);
authorizeUrl.searchParams.set("redirect_uri", REDIRECT_URI);
authorizeUrl.searchParams.set("response_type", "code");
authorizeUrl.searchParams.set("approval_prompt", "auto");
authorizeUrl.searchParams.set("scope", SCOPE);

const server = createServer(async (req, res) => {
  if (!req.url) return;
  const url = new URL(req.url, `http://localhost:${PORT}`);

  if (url.pathname !== "/callback") {
    res.writeHead(404);
    res.end();
    return;
  }

  const error = url.searchParams.get("error");
  if (error) {
    res.writeHead(400, { "Content-Type": "text/plain" });
    res.end(`Strava authorization failed: ${error}. You can close this tab.`);
    console.error(`\nAuthorization failed: ${error}`);
    server.close();
    process.exit(1);
  }

  const code = url.searchParams.get("code");
  if (!code) {
    res.writeHead(400, { "Content-Type": "text/plain" });
    res.end("Missing authorization code.");
    return;
  }

  try {
    const tokenRes = await fetch("https://www.strava.com/oauth/token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        client_id: clientId,
        client_secret: clientSecret,
        code,
        grant_type: "authorization_code",
      }),
    });

    if (!tokenRes.ok) {
      const body = await tokenRes.text();
      throw new Error(`Token exchange failed (${tokenRes.status}): ${body}`);
    }

    const token = (await tokenRes.json()) as { refresh_token: string; access_token: string; athlete?: { firstname?: string } };

    res.writeHead(200, { "Content-Type": "text/plain" });
    res.end("Authorization complete. You can close this tab and return to the terminal.");

    console.log(`\nAuthorized as: ${token.athlete?.firstname ?? "athlete"}`);
    console.log("\nAdd this to your .env file:\n");
    console.log(`STRAVA_REFRESH_TOKEN=${token.refresh_token}\n`);
  } catch (err) {
    res.writeHead(500, { "Content-Type": "text/plain" });
    res.end("Token exchange failed. Check the terminal for details.");
    console.error(err);
  } finally {
    server.close();
  }
});

server.listen(PORT, () => {
  console.log("Strava OAuth setup\n");
  console.log("Open this URL in your browser and click Authorize:\n");
  console.log(authorizeUrl.toString());
  console.log(`\nWaiting for the redirect to ${REDIRECT_URI} ...`);
});
