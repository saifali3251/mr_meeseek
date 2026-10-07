import { OpsState, OnboardingRequest, AuthUser } from "./types";

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

function getAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  try {
    const raw = localStorage.getItem("meeseek_auth_user");
    if (raw) {
      const u = JSON.parse(raw);
      if (u.role) headers["X-Meeseek-Role"] = u.role;
      if (u.team) headers["X-Meeseek-Team"] = u.team;
    }
  } catch {}
  return headers;
}

export async function serverSignOut(): Promise<void> {
  try {
    await fetch(`${BASE_URL}/ops/auth/signout`, { method: "POST" });
  } catch (e) {
    console.warn("Server signout error:", e);
  }
}

export async function fetchOpsState(teamSlug?: string, token?: string, role?: string): Promise<OpsState> {
  const authHeaders = getAuthHeaders();
  const isAdmin = (role === "admin" || role === "superadmin") || 
                  authHeaders["X-Meeseek-Role"] === "admin" || 
                  authHeaders["X-Meeseek-Role"] === "superadmin";

  const params = new URLSearchParams();
  if (teamSlug) params.set("team", teamSlug);
  else if (isAdmin) params.set("team", "all");
  if (token) params.set("token", token);
  if (role) params.set("role", role);
  else if (isAdmin) params.set("role", "admin");
  const qs = params.toString() ? `?${params.toString()}` : "";
  
  try {
    const res = await fetch(`${BASE_URL}/ops/state${qs}`, {
      headers: { Accept: "application/json", ...authHeaders },
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
  const authHeaders = getAuthHeaders();
  const isAdmin = authHeaders["X-Meeseek-Role"] === "admin" || authHeaders["X-Meeseek-Role"] === "superadmin";
  const qs = isAdmin ? "?role=admin&team=all" : "";
  const res = await fetch(`${BASE_URL}/ops/strike${qs}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ ticket, app: app || null, ttl_s }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to strike environment");
  }
  return res.json();
}

export async function extendLease(leaseId: string, ttl_s: number = 1800): Promise<any> {
  const authHeaders = getAuthHeaders();
  const isAdmin = authHeaders["X-Meeseek-Role"] === "admin" || authHeaders["X-Meeseek-Role"] === "superadmin";
  const qs = isAdmin ? "?role=admin&team=all" : "";
  const res = await fetch(`${BASE_URL}/ops/leases/${leaseId}/extend${qs}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ ttl_s }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Failed to extend workspace: ${res.statusText}`);
  }
  return res.json();
}

export async function releaseLease(leaseId: string, ticket?: string): Promise<any> {
  const authHeaders = getAuthHeaders();
  const isAdmin = authHeaders["X-Meeseek-Role"] === "admin" || authHeaders["X-Meeseek-Role"] === "superadmin";
  const qs = isAdmin ? "?role=admin&team=all" : "";

  // If a ticket is provided, also release the console task
  if (ticket) {
    try {
      await fetch(`${BASE_URL}/console/tasks/${encodeURIComponent(ticket)}${qs}`, {
        method: "DELETE",
        headers: { Accept: "application/json", ...authHeaders },
      });
    } catch (e) {
      console.warn("console/tasks release error:", e);
    }
  }

  // Primary release on the operator endpoint
  let res = await fetch(`${BASE_URL}/ops/leases/${encodeURIComponent(leaseId)}${qs}`, {
    method: "DELETE",
    headers: { Accept: "application/json", ...authHeaders },
  });

  if (!res.ok) {
    // Fallback to /leases/{leaseId} directly
    res = await fetch(`${BASE_URL}/leases/${encodeURIComponent(leaseId)}${qs}`, {
      method: "DELETE",
      headers: { Accept: "application/json", ...authHeaders },
    });
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Failed to release workspace: ${res.statusText}`);
  }
  return res.json().catch(() => ({ status: "released" }));
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

export async function validateGitRepository(
  url: string,
  branch: string = "main"
): Promise<{ valid: boolean; commit_sha?: string; full_sha?: string; branch?: string; message?: string; error?: string }> {
  try {
    const res = await fetch(`${BASE_URL}/ops/onboard/validate-repo`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, branch }),
    });
    if (res.ok) return await res.json();
    // Fallback to /onboarding/validate-repo
    const fb = await fetch(`${BASE_URL}/onboarding/validate-repo`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, branch }),
    });
    if (fb.ok) return await fb.json();
    const err = await fb.json().catch(() => ({ detail: fb.statusText }));
    return { valid: false, error: err.detail || err.error || "Validation failed" };
  } catch (err: any) {
    return { valid: false, error: err.message || "Failed to reach git validation service" };
  }
}

export async function fetchOnboardingRequests(team?: string): Promise<OnboardingRequest[]> {
  try {
    // 1. Try admin onboarding state (unscoped platform review queue)
    const adminRes = await fetch(`${BASE_URL}/ops/admin/onboarding/state`).catch(() => null);
    if (adminRes && adminRes.ok) {
      const data = await adminRes.json();
      if (Array.isArray(data.requests)) {
        return data.requests.map((r: any) => ({ ...r, id: r.request_id || r.id }));
      }
    }

    // 2. Try team state if team provided
    if (team) {
      const teamRes = await fetch(`${BASE_URL}/ops/onboard/state?team=${encodeURIComponent(team)}`).catch(() => null);
      if (teamRes && teamRes.ok) {
        const data = await teamRes.json();
        if (Array.isArray(data.requests)) {
          return data.requests.map((r: any) => ({ ...r, id: r.request_id || r.id }));
        }
      }
    }

    // 3. Fallback to programmatic /onboarding/requests
    const url = team ? `${BASE_URL}/onboarding/requests?team=${encodeURIComponent(team)}` : `${BASE_URL}/onboarding/requests`;
    const res = await fetch(url).catch(() => null);
    if (res && res.ok) {
      const data = await res.json();
      return (data.requests || []).map((r: any) => ({ ...r, id: r.request_id || r.id }));
    }
  } catch (err) {
    console.warn("fetchOnboardingRequests error:", err);
  }
  return [];
}

export async function createOnboardingRequest(data: {
  team_slug: string;
  app_name: string;
  contact?: string;
  jira_project?: string;
  test_cmd?: string;
  preview_port?: string;
  repos: Array<{
    name: string;
    url: string;
    branch: string;
    role: string;
    test_cmd?: string;
    depends_on?: string[];
  }>;
}): Promise<any> {
  // Try /ops/onboard/requests first (session & cookie friendly)
  let res = await fetch(`${BASE_URL}/ops/onboard/requests`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });

  if (!res.ok && res.status === 404) {
    // Fallback to /onboarding/requests only if route not found
    res = await fetch(`${BASE_URL}/onboarding/requests`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || err.error || "Failed to submit onboarding request");
  }
  return res.json();
}

export async function approveOnboardingRequest(
  requestId: string,
  token?: string
): Promise<any> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let res = await fetch(`${BASE_URL}/ops/admin/onboarding/requests/${encodeURIComponent(requestId)}/approve`, {
    method: "POST",
    headers,
  });

  if (!res.ok && res.status === 404) {
    res = await fetch(`${BASE_URL}/onboarding/admin/requests/${encodeURIComponent(requestId)}/approve`, {
      method: "POST",
      headers,
    });
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to approve onboarding request");
  }
  return res.json();
}

export async function rejectOnboardingRequest(
  requestId: string,
  reason: string,
  token?: string
): Promise<any> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let res = await fetch(`${BASE_URL}/ops/admin/onboarding/requests/${encodeURIComponent(requestId)}/reject`, {
    method: "POST",
    headers,
    body: JSON.stringify({ reason }),
  });

  if (!res.ok && res.status === 404) {
    res = await fetch(`${BASE_URL}/onboarding/admin/requests/${encodeURIComponent(requestId)}/reject`, {
      method: "POST",
      headers,
      body: JSON.stringify({ reason }),
    });
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to reject onboarding request");
  }
  return res.json();
}

export async function triggerTrialRun(
  requestId: string
): Promise<any> {
  let res = await fetch(`${BASE_URL}/ops/onboard/requests/${encodeURIComponent(requestId)}/trial`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });

  if (!res.ok && res.status === 404) {
    res = await fetch(`${BASE_URL}/onboarding/requests/${encodeURIComponent(requestId)}/trial`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || err.error || `Trial failed with status ${res.status}`);
  }
  return res.json();
}

export async function submitOnboardingForReview(
  requestId: string
): Promise<any> {
  let res = await fetch(`${BASE_URL}/ops/onboard/requests/${encodeURIComponent(requestId)}/submit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });

  if (!res.ok && res.status === 404) {
    res = await fetch(`${BASE_URL}/onboarding/requests/${encodeURIComponent(requestId)}/submit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || err.error || "Failed to submit request for review");
  }
  return res.json();
}

export async function deleteOnboardingRequest(
  requestId: string,
  isAdmin: boolean = false
): Promise<any> {
  const primaryUrl = isAdmin
    ? `${BASE_URL}/ops/admin/onboarding/requests/${encodeURIComponent(requestId)}`
    : `${BASE_URL}/ops/onboard/requests/${encodeURIComponent(requestId)}`;

  let res = await fetch(primaryUrl, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
  });

  if (!res.ok && (res.status === 404 || res.status === 405)) {
    const fallbackUrl = isAdmin
      ? `${BASE_URL}/ops/onboard/requests/${encodeURIComponent(requestId)}`
      : `${BASE_URL}/ops/admin/onboarding/requests/${encodeURIComponent(requestId)}`;
    res = await fetch(fallbackUrl, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
    });
    if (!res.ok && (res.status === 404 || res.status === 405)) {
      res = await fetch(`${BASE_URL}/onboarding/requests/${encodeURIComponent(requestId)}`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
      });
    }
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || err.error || `Failed to delete onboarding request (${res.status} ${res.statusText})`);
  }
  return res.json();
}

export async function triggerGoldenRebuild(app?: string): Promise<any> {
  const qs = app ? `?app=${encodeURIComponent(app)}&force=true` : `?force=true`;
  const res = await fetch(`${BASE_URL}/ops/golden/rebuild${qs}`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to trigger golden rebuild");
  }
  return res.json();
}

export async function setCapacity(maxAppLeases: number, role: string = "superadmin"): Promise<any> {
  const res = await fetch(`${BASE_URL}/ops/capacity`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ max_app_leases: maxAppLeases, role }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to update capacity");
  }
  return res.json();
}

export async function login(username: string, password: string): Promise<AuthUser> {
  const res = await fetch(`${BASE_URL}/ops/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Invalid credentials");
  }
  return res.json();
}

export interface JiraVerifyResult {
  connected: boolean;
  exists: boolean | null;
  ticket: string;
  summary?: string;
  issuetype?: string;
  labels?: string[];
  reason?: string;
  detail?: string;
}

export async function verifyJiraTicket(ticket: string): Promise<JiraVerifyResult> {
  try {
    const clean = encodeURIComponent(ticket.trim().toUpperCase());
    const res = await fetch(`${BASE_URL}/ops/jira/verify/${clean}`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) {
      return { connected: false, exists: null, ticket };
    }
    return await res.json();
  } catch (e) {
    return { connected: false, exists: null, ticket };
  }
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
}

export interface ChatResponse {
  reply: string;
  model?: string;
  grounded?: boolean;
}

export async function sendChatMessage(messages: { role: string; content: string }[]): Promise<ChatResponse> {
  const res = await fetch(`${BASE_URL}/ops/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Chat request failed: ${res.statusText}`);
  }
  return res.json();
}

