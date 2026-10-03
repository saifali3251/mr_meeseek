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
  Sparkles
} from "lucide-react";
import { OpsState, Lease, TaskRecord, OnboardingRequest } from "./types";
import { fetchOpsState, releaseLease, extendLease, fetchOnboardingRequests } from "./api";
import { Header, Role } from "./components/Header";
import { WorkspacesTable } from "./components/WorkspacesTable";
import { OnboardingWizard } from "./components/OnboardingWizard";
import { JudgePlayground } from "./components/JudgePlayground";
import { StrikeModal } from "./components/StrikeModal";

type ActiveTab = "workspaces" | "onboarding" | "judge";

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<ActiveTab>("workspaces");
  const [selectedRole, setSelectedRole] = useState<Role>("team");
  const [state, setState] = useState<OpsState | null>(null);
  const [onboardingRequests, setOnboardingRequests] = useState<OnboardingRequest[]>([]);
  const [selectedLeaseId, setSelectedLeaseId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [lastUpdated, setLastUpdated] = useState<string>("—");
  const [isStrikeModalOpen, setIsStrikeModalOpen] = useState<boolean>(false);
  const [isDestroying, setIsDestroying] = useState<boolean>(false);
  const [isExtending, setIsExtending] = useState<boolean>(false);

  // Day / night theme (matches the landing page). Saved per browser.
  const [isDark, setIsDark] = useState<boolean>(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    document.documentElement.classList.toggle("dark", isDark);
    try { localStorage.setItem("meeseek-console-theme", isDark ? "dark" : "light"); } catch (e) {}
  }, [isDark]);

  // Load ops state & onboarding requests
  const loadState = useCallback(async () => {
    try {
      const data = await fetchOpsState(
        selectedRole === "team" && state?.team ? state.team.slug : undefined
      );
      setState(data);
      setLastUpdated(new Date().toLocaleTimeString());

      if (!selectedLeaseId && data.leases.length > 0) {
        setSelectedLeaseId(data.leases[0].lease_id);
      }

      if (selectedRole === "admin" || activeTab === "onboarding") {
        const onb = await fetchOnboardingRequests();
        setOnboardingRequests(onb);
      }
    } catch (err) {
      console.error("Error polling ops state:", err);
    } finally {
      setIsLoading(false);
    }
  }, [selectedRole, selectedLeaseId, activeTab, state?.team]);

  // Initial load and continuous quiet live polling
  useEffect(() => {
    loadState();
    const interval = setInterval(loadState, 2000);
    return () => clearInterval(interval);
  }, [loadState]);

  // Handle Destroy Lease
  const handleDestroyLease = async (leaseId: string) => {
    if (!confirm(`Are you sure you want to terminate workspace "${leaseId}"?`)) return;
    setIsDestroying(true);
    try {
      await releaseLease(leaseId);
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
            <div className="flex items-center space-x-4">
              <div className="hidden md:flex items-center space-x-3 text-xs text-slate-400 font-mono">
                <span className="flex items-center space-x-1.5">
                  <span className="w-2 h-2 rounded-full bg-emerald-400"></span>
                  <span>{state?.kpis?.live ?? 0} Live Workspaces</span>
                </span>
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
                {[
                  { label: "Live workspaces", value: state?.kpis?.live ?? 0, tone: "text-cyan-400" },
                  { label: "Golden images ready", value: `${state?.kpis?.environments_ready ?? 0}/${state?.kpis?.environments_total ?? 0}`, tone: "text-emerald-400" },
                  { label: "Capacity", value: state?.kpis?.max_leases ?? 0, tone: "text-purple-400" },
                ].map((k) => (
                  <div key={k.label} className="rounded-xl border border-meeseek-border bg-meeseek-850/70 px-4 py-3 min-w-[118px]">
                    <div className="mee-stat">{k.value}</div>
                    <div className={`text-[11px] font-mono uppercase tracking-wider mt-1.5 ${k.tone}`}>{k.label}</div>
                  </div>
                ))}
              </div>
            </section>

            <WorkspacesTable
              onSummon={() => setIsStrikeModalOpen(true)}
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
      <footer className="border-t border-meeseek-border py-5 text-xs text-slate-500 bg-meeseek-900/40 backdrop-blur-sm">
        <div className="w-full px-6 lg:px-12 2xl:px-16 flex flex-wrap items-center justify-between gap-2">
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
        onSuccess={loadState}
      />
    </div>
  );
};
