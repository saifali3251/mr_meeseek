import { OpsState, OnboardingRequest } from "./types";

const BASE_URL = "";

const EMPTY_OPS_STATE: OpsState = {
  provider: "Docker Compose",
  now: Date.now() / 1000,
  team: null,
  kpis: {
    live: 0,
    environments_ready: 0,
    environments_total: 0,
    max_leases: 4,
  },
  apps: [],
  default_app: null,
  jira_base: "",
  environments: [],
  leases: [],
  runs: [],
  pool: {
    enabled: false,
  },
};

export async function fetchOpsState(teamSlug?: string, token?: string): Promise<OpsState> {
  const params = new URLSearchParams();
  if (teamSlug) params.set("team", teamSlug);
  if (token) params.set("token", token);
  const qs = params.toString() ? `?${params.toString()}` : "";
  
  try {
    const res = await fetch(`${BASE_URL}/ops/state${qs}`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) {
      throw new Error(`Failed to load ops state: ${res.statusText}`);
    }
    return await res.json();
  } catch (err) {
    console.error("fetchOpsState error:", err);
    return EMPTY_OPS_STATE;
  }
}

export async function strikeEnvironment(
  ticket: string,
  app?: string,
  ttl_s: number = 3600
): Promise<any> {
  const res = await fetch(`${BASE_URL}/ops/strike`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticket, app: app || null, ttl_s }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to strike environment");
  }
  return res.json();
}

export async function extendLease(leaseId: string, ttl_s: number = 1800): Promise<any> {
  const res = await fetch(`${BASE_URL}/ops/leases/${leaseId}/extend`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ttl_s }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Failed to extend workspace: ${res.statusText}`);
  }
  return res.json();
}

export async function releaseLease(leaseId: string): Promise<any> {
  const res = await fetch(`${BASE_URL}/ops/releases/${leaseId}`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Failed to release workspace: ${res.statusText}`);
  }
  return res.json();
}

export async function createTeam(
  name: string,
  contact: string = "",
  slug?: string
): Promise<{ slug: string; name: string; token: string; share_url: string }> {
  const res = await fetch(`${BASE_URL}/ops/teams`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, contact, slug: slug || null }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to create team");
  }
  return res.json();
}

export async function fetchOnboardingRequests(team?: string): Promise<OnboardingRequest[]> {
  const url = team ? `${BASE_URL}/onboarding/requests?team=${encodeURIComponent(team)}` : `${BASE_URL}/onboarding/requests`;
  const res = await fetch(url).catch(() => null);
  if (!res || !res.ok) return [];
  const data = await res.json().catch(() => ({ requests: [] }));
  return data.requests || [];
}

export async function createOnboardingRequest(data: {
  team_slug: string;
  app_name: string;
  contact?: string;
  compose_file_path?: string;
  ports?: number[];
  seed_database?: boolean;
  seed_sql_path?: string;
  test_command?: string;
  repos: Array<{
    name: string;
    url: string;
    branch: string;
    role: string;
    test_cmd?: string;
  }>;
}): Promise<any> {
  const res = await fetch(`${BASE_URL}/onboarding/requests`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to submit onboarding request");
  }
  return res.json();
}

export async function approveOnboardingRequest(
  requestId: string,
  token: string
): Promise<any> {
  const res = await fetch(`${BASE_URL}/onboarding/requests/${requestId}/approve`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to approve onboarding request");
  }
  return res.json();
}

export async function triggerTrialRun(
  requestId: string,
  ticket: string = "ONB-TRIAL"
): Promise<any> {
  const res = await fetch(`${BASE_URL}/onboarding/requests/${requestId}/trial-strike`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticket }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to trigger trial strike");
  }
  return res.json();
}
