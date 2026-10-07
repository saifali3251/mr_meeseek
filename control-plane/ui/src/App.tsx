import React, { useState, useEffect, useCallback } from "react";
import { 
  Rocket, 
  Layers, 
  Plus, 
  Gamepad2, 
  FolderPlus, 
  Activity,
  Server,
  Terminal,
  Sparkles,
  Clock
} from "lucide-react";
import { OpsState, Lease, TaskRecord, OnboardingRequest, AuthUser } from "./types";
import { fetchOpsState, releaseLease, extendLease, fetchOnboardingRequests, setCapacity, serverSignOut } from "./api";
import { Header, Role } from "./components/Header";
import { WorkspacesTable } from "./components/WorkspacesTable";
import { OnboardingWizard } from "./components/OnboardingWizard";
import { JudgePlayground } from "./components/JudgePlayground";
import { StrikeModal } from "./components/StrikeModal";
import { LoginPage } from "./components/LoginPage";
import { MeeseekChatbot } from "./components/MeeseekChatbot";

type ActiveTab = "workspaces" | "onboarding" | "judge";

function formatGoldenAge(ts?: number | null): string {
  if (!ts) return "Active snapshot";
  const diffSec = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (diffSec < 60) return "Just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
}

export const App: React.FC = () => {
  const [authUser, setAuthUser] = useState<AuthUser | null>(() => {
    try {
      const saved = localStorage.getItem("meeseek_auth_user");
      return saved ? JSON.parse(saved) : null;
    } catch {
      return null;
    }
  });

  const [activeTab, setActiveTab] = useState<ActiveTab>("workspaces");
  const [selectedRole, setSelectedRole] = useState<Role>(() => {
    try {
      const stored = localStorage.getItem("meeseek_auth_user");
      if (stored) {
        const u = JSON.parse(stored);
        if (u.role === "admin" || u.role === "superadmin") return "admin";
      }
    } catch {}
    return "team";
  });
  const [state, setState] = useState<OpsState | null>(null);
  const [onboardingRequests, setOnboardingRequests] = useState<OnboardingRequest[]>([]);
  const [selectedLeaseId, setSelectedLeaseId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [lastUpdated, setLastUpdated] = useState<string>("—");
  const [isStrikeModalOpen, setIsStrikeModalOpen] = useState<boolean>(false);
  const [isDestroying, setIsDestroying] = useState<boolean>(false);
  const [isExtending, setIsExtending] = useState<boolean>(false);

  const handleLogin = (user: AuthUser) => {
    setAuthUser(user);
    if (user.role === "admin" || user.role === "superadmin") {
      setSelectedRole("admin");
    } else {
      setSelectedRole("team");
    }
    try {
      localStorage.setItem("meeseek_auth_user", JSON.stringify(user));
    } catch (e) {
      console.error("Failed to persist auth user:", e);
    }
  };

  const handleSignOut = async () => {
    setAuthUser(null);
    setSelectedRole("team");
    try {
      localStorage.removeItem("meeseek_auth_user");
      document.cookie = "holo_team=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT";
      document.cookie = "holo_token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT";
      await serverSignOut();
    } catch (e) {
      console.error("Failed to clear auth user:", e);
    }
  };

  // Day / night theme (matches the landing page). Saved per browser.
  const [isDark, setIsDark] = useState<boolean>(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    document.documentElement.classList.toggle("dark", isDark);
    try { localStorage.setItem("meeseek-console-theme", isDark ? "dark" : "light"); } catch (e) {}
  }, [isDark]);

  const [isLiveConnected, setIsLiveConnected] = useState<boolean>(false);

  // Load ops state via one-shot fetch (used as immediate fetch or on explicit actions)
  const loadState = useCallback(async () => {
    try {
      const isAdmin = selectedRole === "admin" || authUser?.role === "admin" || authUser?.role === "superadmin";
      const teamSlug = !isAdmin && selectedRole === "team" && state?.team ? state.team.slug : undefined;
      const data = await fetchOpsState(
        teamSlug,
        undefined,
        isAdmin ? "admin" : undefined
      );
      setState(data);
      setLastUpdated(new Date().toLocaleTimeString());

      if (!selectedLeaseId && data.leases && data.leases.length > 0) {
        setSelectedLeaseId(data.leases[0].lease_id);
      }
    } catch (err) {
      console.error("Error fetching ops state:", err);
    } finally {
      setIsLoading(false);
    }
  }, [selectedRole, authUser?.role, selectedLeaseId, state?.team]);

  // Load onboarding requests when in onboarding tab or admin role
  useEffect(() => {
    if (selectedRole === "admin" || activeTab === "onboarding") {
      fetchOnboardingRequests()
        .then(setOnboardingRequests)
        .catch((err) => console.error("Error fetching onboarding requests:", err));
    }
  }, [selectedRole, activeTab]);

  // Real-Time Server-Sent Events (SSE) Stream
  useEffect(() => {
    let eventSource: EventSource | null = null;
    let fallbackInterval: ReturnType<typeof setInterval> | null = null;

    const connectSSE = () => {
      const isAdmin = selectedRole === "admin" || authUser?.role === "admin" || authUser?.role === "superadmin";
      const teamSlug = !isAdmin && selectedRole === "team" && state?.team ? state.team.slug : undefined;
      const sseUrl = teamSlug 
        ? `/ops/events?team=${encodeURIComponent(teamSlug)}` 
        : `/ops/events?team=all&role=admin`;

      try {
        eventSource = new EventSource(sseUrl);

        eventSource.onopen = () => {
          setIsLiveConnected(true);
          setIsLoading(false);
        };

        eventSource.onmessage = (event) => {
          try {
            const data: OpsState = JSON.parse(event.data);
            setState(data);
            setLastUpdated(new Date().toLocaleTimeString());
            setIsLoading(false);
            setIsLiveConnected(true);

            if (!selectedLeaseId && data.leases && data.leases.length > 0) {
              setSelectedLeaseId(data.leases[0].lease_id);
            }
          } catch (err) {
            console.error("Error parsing SSE event data:", err);
          }
        };

        eventSource.onerror = (err) => {
          console.warn("SSE connection error or interrupted, reconnecting...", err);
          setIsLiveConnected(false);
        };
      } catch (e) {
        console.error("Failed to initialize EventSource, using fallback polling:", e);
        loadState();
        fallbackInterval = setInterval(loadState, 10000);
      }
    };

    if (typeof EventSource !== "undefined") {
      connectSSE();
    } else {
      loadState();
      fallbackInterval = setInterval(loadState, 10000);
    }

    return () => {
      if (eventSource) {
        eventSource.close();
      }
      if (fallbackInterval) {
        clearInterval(fallbackInterval);
      }
    };
  }, [selectedRole, authUser?.role, state?.team?.slug]);

  // Handle Destroy Lease
  const handleDestroyLease = async (leaseId: string, ticket?: string) => {
    if (!confirm(`Are you sure you want to terminate workspace "${ticket || leaseId}"?`)) return;
    setIsDestroying(true);
    try {
      await releaseLease(leaseId, ticket);
      await loadState();
      if (selectedLeaseId === leaseId) {
        setSelectedLeaseId(null);
      }
    } catch (err: any) {
      alert(`Failed to destroy workspace: ${err.message}`);
    } finally {
      setIsDestroying(false);
    }
  };

  // Handle Extend Lease (+30m)
  const handleExtendLease = async (leaseId: string) => {
    setIsExtending(true);
    try {
      await extendLease(leaseId, 1800);
      await loadState();
    } catch (err: any) {
      alert(`Failed to extend lease: ${err.message}`);
    } finally {
      setIsExtending(false);
    }
  };

  // If not authenticated, render LoginPage
  if (!authUser) {
    return <LoginPage onLogin={handleLogin} isDark={isDark} onToggleTheme={() => setIsDark((d) => !d)} />;
  }

  return (
    <div className="min-h-screen text-slate-100 flex flex-col font-sans">
      {/* Top Header */}
      <Header
        kpis={state?.kpis}
        team={state?.team || null}
        lastUpdated={lastUpdated}
        selectedRole={selectedRole}
        isDark={isDark}
        onToggleTheme={() => setIsDark((d) => !d)}
        onSelectRole={(role) => {
          setSelectedRole(role);
          if (role === "judge") setActiveTab("judge");
        }}
        authUser={authUser}
        onSignOut={handleSignOut}
        isLiveConnected={isLiveConnected}
      />

      {/* Fluid Subheader Navigation & Action Bar */}
      <div className="border-b border-meeseek-border bg-meeseek-900/50 backdrop-blur-md sticky top-[73px] z-30">
        <div className="w-full px-6 lg:px-12 2xl:px-16">
          <div className="flex items-center justify-between h-14">
            {/* Primary Tabs */}
            <nav className="flex space-x-2 sm:space-x-3 text-xs font-medium">
              <button
                onClick={() => setActiveTab("workspaces")}
                className={`flex items-center space-x-2 px-3.5 py-2 rounded-xl transition-all ${
                  activeTab === "workspaces"
                    ? "bg-meeseek-800 text-white font-semibold shadow-inner border border-slate-700/60"
                    : "text-slate-400 hover:text-slate-200 hover:bg-meeseek-850/40"
                }`}
              >
                <Activity className="w-4 h-4 text-cyan-400" />
                <span>Workspaces & Runs</span>
                {state?.leases && state.leases.length > 0 && (
                  <span className="ml-1 px-1.5 py-0.2 rounded-full bg-cyan-500/20 text-cyan-300 font-mono text-[10px]">
                    {state.leases.length}
                  </span>
                )}
              </button>

              <button
                onClick={() => setActiveTab("onboarding")}
                className={`flex items-center space-x-2 px-3.5 py-2 rounded-xl transition-all ${
                  activeTab === "onboarding"
                    ? "bg-meeseek-800 text-white font-semibold shadow-inner border border-slate-700/60"
                    : "text-slate-400 hover:text-slate-200 hover:bg-meeseek-850/40"
                }`}
              >
                <FolderPlus className="w-4 h-4 text-purple-400" />
                <span>App Onboarding</span>
              </button>

              <button
                onClick={() => setActiveTab("judge")}
                className={`flex items-center space-x-2 px-3.5 py-2 rounded-xl transition-all ${
                  activeTab === "judge"
                    ? "bg-amber-500/10 text-amber-300 font-semibold border border-amber-500/30"
                    : "text-amber-400/70 hover:text-amber-300 hover:bg-amber-500/5"
                }`}
              >
                <Sparkles className="w-4 h-4 text-amber-400" />
                <span>Judge Demo</span>
              </button>
            </nav>

            {/* Quick Metrics & Strike Action */}
            <div className="flex items-center space-x-3">
              <div className="hidden md:flex items-center space-x-2.5 text-xs text-slate-400 font-mono">
                <span className="flex items-center space-x-1.5">
                  <span className="w-2 h-2 rounded-full bg-emerald-400"></span>
                  <span>{state?.kpis?.live ?? 0} Live</span>
                </span>

                {!!state?.kpis?.queued && state.kpis.queued > 0 && (
                  <span className="flex items-center space-x-1.5 px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-300 border border-amber-500/30 animate-pulse">
                    <Clock className="w-3 h-3 text-amber-400" />
                    <span>{state.kpis.queued} Queued</span>
                  </span>
                )}

                <div className="flex items-center space-x-1 px-2.5 py-1 rounded-lg bg-slate-800/80 border border-slate-700/80 text-slate-300 text-xs font-mono">
                  <span className="text-slate-400">Limit/App:</span>
                  {authUser.role === "superadmin" ? (
                    <select
                      value={state?.kpis?.max_app_leases ?? 3}
                      onChange={async (e) => {
                        const newCap = parseInt(e.target.value, 10);
                        try {
                          await setCapacity(newCap, authUser.role);
                          await loadState();
                        } catch (err: any) {
                          alert(`Failed to update capacity: ${err.message}`);
                        }
                      }}
                      title="Change max concurrent strikes per application at runtime (Superadmin only)"
                      className="bg-transparent text-cyan-400 font-bold focus:outline-none cursor-pointer"
                    >
                      <option value={1} className="bg-slate-900 text-slate-200">1</option>
                      <option value={2} className="bg-slate-900 text-slate-200">2</option>
                      <option value={3} className="bg-slate-900 text-slate-200">3</option>
                      <option value={4} className="bg-slate-900 text-slate-200">4</option>
                      <option value={5} className="bg-slate-900 text-slate-200">5</option>
                    </select>
                  ) : (
                    <span 
                      className="text-cyan-400 font-bold px-1.5 cursor-not-allowed" 
                      title="Cluster capacity limit can only be modified by Superadmin"
                    >
                      {state?.kpis?.max_app_leases ?? 3}
                    </span>
                  )}
                </div>
              </div>

              <button
                onClick={() => setIsStrikeModalOpen(true)}
                className="flex items-center space-x-1.5 px-4 py-1.5 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold shadow-lg shadow-cyan-500/20 transition-all active:scale-98"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>Strike Workspace</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Main Fluid Body */}
      <main className="flex-1 w-full px-6 lg:px-12 2xl:px-16 py-6">
        {/* TAB 1: WORKSPACES TABLE WITH EMBEDDED ACCORDION DAG */}
        {activeTab === "workspaces" && (
          <div className="space-y-6">
            {/* Hero strip: who's working right now */}
            <section className="mee-hero glass-panel rounded-2xl px-7 py-6 flex flex-wrap items-center justify-between gap-6">
              <div className="min-w-0">
                <span className="text-[11px] font-mono font-semibold uppercase tracking-[.14em] text-cyan-400">Meeseek Console</span>
                <h2 className="text-2xl text-white mt-1">
                  One ticket in. One verified PR out. <em>Nothing left behind.</em>
                </h2>
                <p className="text-sm text-slate-400 mt-2 max-w-xl">
                  Every workspace below is a Meeseek summoned for a single ticket. Watch it work, review the live preview, then let it go.
                </p>
              </div>
              <div className="flex items-stretch gap-3">
                {/* CARD 1: LIVE WORKSPACES */}
                <div className="rounded-xl border border-meeseek-border bg-meeseek-850/70 px-4 py-3 min-w-[130px] flex flex-col justify-between">
                  <div className="flex items-baseline justify-between">
                    <div className="mee-stat">{state?.kpis?.live ?? 0}</div>
                    {!!state?.kpis?.queued && state.kpis.queued > 0 && (
                      <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30">
                        +{state.kpis.queued} queued
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] font-mono uppercase tracking-wider text-cyan-400 mt-2">
                    Live Workspaces
                  </div>
                </div>

                {/* CARD 2: ACTIVE GOLDEN BUILD & FRESHNESS */}
                <div className="rounded-xl border border-meeseek-border bg-meeseek-850/70 px-4 py-3 min-w-[160px] flex flex-col justify-between">
                  <div className="flex items-center justify-between space-x-2">
                    <span 
                      className="text-sm font-bold text-white tracking-tight truncate max-w-[130px]" 
                      title={state?.golden?.app || state?.default_app || "full-stack-application"}
                    >
                      {(state?.golden?.app || state?.default_app || "Full-Stack App")
                        .replace("full-stack-application", "Full-Stack App")}
                    </span>
                    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 whitespace-nowrap">
                      Ready (CoW)
                    </span>
                  </div>
                  <div className="text-[11px] font-mono text-emerald-400/90 mt-2 flex items-center justify-between">
                    <span className="uppercase tracking-wider">Golden Build</span>
                    <span 
                      className="text-slate-400 font-mono text-[10px] ml-2" 
                      title={state?.golden?.updated_at ? new Date(state.golden.updated_at * 1000).toLocaleString() : undefined}
                    >
                      {formatGoldenAge(state?.golden?.updated_at)}
                    </span>
                  </div>
                </div>

                {/* CARD 3: JIRA INTEGRATION STATUS */}
                <div className="rounded-xl border border-meeseek-border bg-meeseek-850/70 px-4 py-3 min-w-[160px] flex flex-col justify-between">
                  <div className="flex items-center justify-between space-x-2">
                    <span className="text-sm font-bold text-white tracking-tight">
                      {state?.jira?.connected ? "Jira Cloud" : "Standalone"}
                    </span>
                    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-medium whitespace-nowrap ${
                      state?.jira?.connected
                        ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                        : "bg-slate-700/30 text-slate-400 border border-slate-700/50"
                    }`}>
                      {state?.jira?.connected ? `● ${state?.jira?.project || "FSA"}` : "○ Offline"}
                    </span>
                  </div>
                  <div className="text-[11px] font-mono text-purple-400 mt-2 flex items-center justify-between">
                    <span className="uppercase tracking-wider">Integration</span>
                    <span className="text-slate-400 font-mono text-[10px] ml-2">
                      {state?.jira?.connected ? "Real-time Sync" : "Local Sandbox"}
                    </span>
                  </div>
                </div>
              </div>
            </section>

            <WorkspacesTable
              onSummon={() => setIsStrikeModalOpen(true)}
              loading={!state && isLoading}
              leases={state?.leases || []}
              tasks={state?.runs || []}
              jiraBaseUrl={state?.jira_base || ""}
              selectedLeaseId={selectedLeaseId}
              onSelectLease={(id) => setSelectedLeaseId(id)}
              onDestroyLease={handleDestroyLease}
              isDestroying={isDestroying}
              onExtendLease={handleExtendLease}
              isExtending={isExtending}
              now={state?.now}
            />
          </div>
        )}

        {/* TAB 2: APP ONBOARDING WIZARD */}
        {activeTab === "onboarding" && (
          <OnboardingWizard
            team={state?.team || null}
            onRefresh={loadState}
            onboardingRequests={onboardingRequests}
            isAdmin={selectedRole === "admin"}
            userRole={authUser.role}
          />
        )}

        {/* TAB 3: JUDGE DEMO PLAYGROUND */}
        {activeTab === "judge" && (
          <JudgePlayground
            onRefresh={loadState}
            jiraBaseUrl={state?.jira_base || ""}
          />
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-meeseek-border pt-5 pb-24 sm:pb-5 text-xs text-slate-500 bg-meeseek-900/40 backdrop-blur-sm">
        <div className="w-full px-6 lg:px-12 2xl:px-16 sm:pr-[270px] lg:pr-[270px] 2xl:pr-[270px] flex flex-wrap items-center justify-between gap-2">
          <span>Meeseek &middot; Autonomous Junior Engineer &middot; AI Builder Cup Hackathon</span>
          <span className="font-mono">ticket &rarr; certified PR &rarr; live preview</span>
        </div>
      </footer>

      {/* Strike Modal */}
      <StrikeModal
        isOpen={isStrikeModalOpen}
        onClose={() => setIsStrikeModalOpen(false)}
        apps={state?.apps || ["full-stack-application"]}
        defaultApp={state?.default_app || "full-stack-application"}
        jira={state?.jira}
        onSuccess={loadState}
      />

      {/* Mr. Meeseeks Live Grounded Copilot */}
      <MeeseekChatbot />
    </div>
  );
};
