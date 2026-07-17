const STRAVA_API_BASE = "https://www.strava.com/api/v3";
const STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token";

interface StravaCredentials {
  clientId: string;
  clientSecret: string;
  refreshToken: string;
}

interface TokenResponse {
  access_token: string;
  refresh_token: string;
  expires_at: number; // unix seconds
}

export class StravaAuthError extends Error {}

export class StravaClient {
  private creds: StravaCredentials;
  private accessToken: string | null = null;
  private accessTokenExpiresAt = 0; // unix seconds

  constructor(creds: StravaCredentials) {
    this.creds = creds;
  }

  private async getAccessToken(): Promise<string> {
    const now = Math.floor(Date.now() / 1000);
    if (this.accessToken && now < this.accessTokenExpiresAt - 60) {
      return this.accessToken;
    }

    const res = await fetch(STRAVA_TOKEN_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        client_id: this.creds.clientId,
        client_secret: this.creds.clientSecret,
        grant_type: "refresh_token",
        refresh_token: this.creds.refreshToken,
      }),
    });

    if (!res.ok) {
      const body = await res.text();
      throw new StravaAuthError(
        `Failed to refresh Strava access token (${res.status}): ${body}. ` +
          `Check STRAVA_CLIENT_ID/STRAVA_CLIENT_SECRET/STRAVA_REFRESH_TOKEN, or re-run 'npm run auth'.`
      );
    }

    const token = (await res.json()) as TokenResponse;
    this.accessToken = token.access_token;
    this.accessTokenExpiresAt = token.expires_at;
    // Strava may rotate the refresh token; keep using the latest one for this process.
    this.creds.refreshToken = token.refresh_token;
    return this.accessToken;
  }

  private async request<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
    const accessToken = await this.getAccessToken();
    const url = new URL(`${STRAVA_API_BASE}${path}`);
    if (params) {
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined) url.searchParams.set(key, String(value));
      }
    }

    const res = await fetch(url, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });

    if (res.status === 429) {
      throw new Error("Strava API rate limit hit. Strava allows 200 requests per 15 minutes / 2000 per day. Try again shortly.");
    }
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`Strava API error (${res.status}) on ${path}: ${body}`);
    }

    return (await res.json()) as T;
  }

  getAthlete() {
    return this.request<unknown>("/athlete");
  }

  getAthleteZones() {
    return this.request<unknown>("/athlete/zones");
  }

  getAthleteStats(athleteId: number) {
    return this.request<unknown>(`/athletes/${athleteId}/stats`);
  }

  getActivities(opts: { before?: number; after?: number; page?: number; perPage?: number }) {
    return this.request<unknown>("/athlete/activities", {
      before: opts.before,
      after: opts.after,
      page: opts.page,
      per_page: opts.perPage,
    });
  }

  getActivity(activityId: number) {
    return this.request<unknown>(`/activities/${activityId}`, { include_all_efforts: "false" as unknown as string });
  }
}

export function stravaClientFromEnv(): StravaClient {
  const clientId = process.env.STRAVA_CLIENT_ID;
  const clientSecret = process.env.STRAVA_CLIENT_SECRET;
  const refreshToken = process.env.STRAVA_REFRESH_TOKEN;

  const missing = [
    !clientId && "STRAVA_CLIENT_ID",
    !clientSecret && "STRAVA_CLIENT_SECRET",
    !refreshToken && "STRAVA_REFRESH_TOKEN",
  ].filter(Boolean);

  if (missing.length > 0) {
    throw new StravaAuthError(
      `Missing required environment variable(s): ${missing.join(", ")}. ` +
        `Copy .env.example to .env, fill in STRAVA_CLIENT_ID/STRAVA_CLIENT_SECRET from https://www.strava.com/settings/api, ` +
        `then run 'npm run auth' to obtain a refresh token.`
    );
  }

  return new StravaClient({ clientId: clientId!, clientSecret: clientSecret!, refreshToken: refreshToken! });
}
