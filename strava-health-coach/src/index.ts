#!/usr/bin/env node
import "dotenv/config";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { stravaClientFromEnv } from "./strava-client.js";

interface Athlete {
  id: number;
  firstname?: string;
  lastname?: string;
  sex?: string;
  weight?: number; // kg
  ftp?: number;
  city?: string;
  state?: string;
  country?: string;
  measurement_preference?: string;
}

interface Activity {
  id: number;
  name: string;
  type: string;
  sport_type?: string;
  distance: number; // meters
  moving_time: number; // seconds
  elapsed_time: number; // seconds
  total_elevation_gain: number; // meters
  start_date_local: string;
  average_heartrate?: number;
  max_heartrate?: number;
  average_watts?: number;
  kilojoules?: number;
  calories?: number;
  suffer_score?: number;
  average_speed?: number; // m/s
}

const server = new McpServer({
  name: "strava-health-coach",
  version: "1.0.0",
});

function jsonResult(data: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
}

function metersToKm(m: number) {
  return Math.round((m / 1000) * 100) / 100;
}

function secondsToMin(s: number) {
  return Math.round((s / 60) * 10) / 10;
}

function summarizeActivity(a: Activity) {
  return {
    id: a.id,
    name: a.name,
    type: a.sport_type ?? a.type,
    date: a.start_date_local,
    distance_km: metersToKm(a.distance ?? 0),
    moving_time_min: secondsToMin(a.moving_time ?? 0),
    elevation_gain_m: a.total_elevation_gain,
    avg_heartrate: a.average_heartrate ?? null,
    max_heartrate: a.max_heartrate ?? null,
    avg_watts: a.average_watts ?? null,
    kilojoules: a.kilojoules ?? null,
    calories: a.calories ?? null,
    perceived_effort_suffer_score: a.suffer_score ?? null,
  };
}

server.registerTool(
  "get_athlete_profile",
  {
    title: "Get Strava athlete profile",
    description:
      "Fetch the authenticated athlete's Strava profile: name, sex, body weight (kg), FTP, and location. " +
      "Note: Strava's stored 'weight' is a single manually-set value, not a history - do not treat it as a weigh-in log.",
    inputSchema: {},
  },
  async () => {
    const client = stravaClientFromEnv();
    const athlete = (await client.getAthlete()) as Athlete;
    return jsonResult({
      id: athlete.id,
      firstname: athlete.firstname,
      lastname: athlete.lastname,
      sex: athlete.sex,
      weight_kg: athlete.weight,
      ftp_watts: athlete.ftp,
      city: athlete.city,
      state: athlete.state,
      country: athlete.country,
      measurement_preference: athlete.measurement_preference,
    });
  }
);

server.registerTool(
  "get_athlete_stats",
  {
    title: "Get Strava athlete totals",
    description:
      "Fetch aggregate training totals for the authenticated athlete: recent (last 4 weeks), " +
      "year-to-date, and all-time totals for runs, rides, and swims (count, distance, moving time, elevation gain).",
    inputSchema: {},
  },
  async () => {
    const client = stravaClientFromEnv();
    const athlete = (await client.getAthlete()) as Athlete;
    const stats = await client.getAthleteStats(athlete.id);
    return jsonResult(stats);
  }
);

server.registerTool(
  "get_athlete_zones",
  {
    title: "Get Strava heart-rate / power zones",
    description: "Fetch the authenticated athlete's configured heart-rate and power training zones.",
    inputSchema: {},
  },
  async () => {
    const client = stravaClientFromEnv();
    const zones = await client.getAthleteZones();
    return jsonResult(zones);
  }
);

server.registerTool(
  "list_recent_activities",
  {
    title: "List recent Strava activities",
    description:
      "List the athlete's recent activities (runs, rides, weight training, swims, etc.) within a lookback window, " +
      "summarized with distance, duration, heart rate, power, calories, and perceived effort. " +
      "Use this to assess training volume, intensity, and consistency for goal recommendations.",
    inputSchema: {
      days: z.number().int().min(1).max(365).default(28).describe("How many days back to look, from today. Default 28."),
      perPage: z.number().int().min(1).max(200).default(50).describe("Max activities to return. Default 50."),
    },
  },
  async ({ days, perPage }) => {
    const client = stravaClientFromEnv();
    const after = Math.floor(Date.now() / 1000) - days * 86400;
    const activities = (await client.getActivities({ after, perPage, page: 1 })) as Activity[];
    return jsonResult({
      window_days: days,
      activity_count: activities.length,
      activities: activities.map(summarizeActivity),
    });
  }
);

server.registerTool(
  "get_activity_detail",
  {
    title: "Get full detail for one Strava activity",
    description:
      "Fetch full detail for a single activity by ID, including calories, average/max heart rate, power, " +
      "and perceived effort. Use after list_recent_activities to dig into a specific workout.",
    inputSchema: {
      activityId: z.number().int().describe("The Strava activity ID, from list_recent_activities."),
    },
  },
  async ({ activityId }) => {
    const client = stravaClientFromEnv();
    const activity = (await client.getActivity(activityId)) as Activity;
    return jsonResult(summarizeActivity(activity));
  }
);

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("strava-health-coach MCP server running on stdio");
}

main().catch((err) => {
  console.error("Fatal error starting strava-health-coach MCP server:", err);
  process.exit(1);
});
