export type WorkflowState =
  | "PENDING"
  | "STRIKING"
  | "BOOTING"
  | "CODING"
  | "WAITING_INPUT"
  | "HALTED"
  | "NOTARY_VERIFYING"
  | "PR_OPENED"
  | "GUARDRAIL_BLOCKED"
  | "FAILED"
  | "RELEASED";

export interface Evidence {
  test_cmd?: string;
  test_exit?: number;
  test_timed_out?: boolean;
  test_output?: string;
  guardrail_passed?: boolean;
  guardrail_reason?: string;
  diff?: string;
  pr_url?: string;
  db_seed_rows?: number;
  readiness_status?: string;
}

export interface Lease {
  lease_id: string;
  ticket: string;
  app: string;
  status: "pending" | "ready" | "released" | "queued" | string;
  created_at: number;
  expires_at: number;
  preview_port?: number;
  preview_url?: string;
  pr_url?: string;
  ticket_test_cmd?: string;
  target_repo?: string;
  evidence?: Evidence;
}

export interface TaskRecord {
  ticket: string;
  lease_id: string;
  workflow_state?: WorkflowState;
  status: string;
  session_id?: string;
  waiting?: boolean;
  question?: string;
  agent?: string;
  last_action?: string;
  last_action_at?: number;
  diff?: string;
  preview_url?: string;
  preview_port?: number;
  evidence?: Evidence;
  plan_only?: boolean;
  halted?: boolean;
}

export interface Environment {
  app: string;
  ready: boolean;
  problems: string[];
  active_leases: number;
}

export interface Team {
  slug: string;
  name: string;
  token?: string;
}

export interface KPIs {
  live: number;
  environments_ready: number;
  environments_total: number;
  max_leases: number;
}

export interface OpsState {
  provider: string;
  now: number;
  team: Team | null;
  kpis: KPIs;
  apps: string[];
  default_app: string | null;
  jira_base: string;
  preview_url?: string;
  environments: Environment[];
  leases: Lease[];
  runs: TaskRecord[];
  pool: {
    enabled: boolean;
    warm_count?: number;
  };
  golden_sync?: Record<string, any>;
}

export interface OnboardingRepoSpec {
  name: string;
  url: string;
  branch: string;
  role: string;
  depends_on?: string[];
}

export interface OnboardingRequest {
  id: string;
  team_slug: string;
  app_name: string;
  contact: string;
  status: "pending" | "trialing" | "ready" | "rejected";
  repos: OnboardingRepoSpec[];
  trial_output?: string;
  created_at: number;
}

