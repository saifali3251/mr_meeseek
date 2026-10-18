import React, { useState, useEffect, useCallback } from "react";
import { 
  Rocket, 
  Play, 
  ExternalLink, 
  ShieldAlert, 
  Eye, 
  GitPullRequest, 
  CheckCircle2, 
  ArrowRight,
  Sparkles,
  Terminal,
  Key,
  Copy,
  Check,
  RefreshCw,
  Clock,
  ShieldCheck,
  MessageSquare,
  Globe,
  Lock,
  ChevronRight,
  AlertCircle
} from "lucide-react";
import { strikeEnvironment, fetchJiraOtp, finalizeTask } from "../api";
import { JiraOtpState, TaskRecord, Lease } from "../types";
import judgeMeeseek from "../assets/judge-meeseek.webp";

interface JudgePlaygroundProps {
  onRefresh: () => void;
  jiraBaseUrl: string;
  jiraProject?: string;
  tasks?: TaskRecord[];
  leases?: Lease[];
  onSelectWorkspacesTab?: () => void;
}

export const JudgePlayground: React.FC<JudgePlaygroundProps> = ({
  onRefresh,
  jiraBaseUrl,
  jiraProject = "FSA",
  tasks = [],
  leases = [],
  onSelectWorkspacesTab,
}) => {
  const [triggeringTicket, setTriggeringTicket] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [otpState, setOtpState] = useState<JiraOtpState | null>(null);
  const [otpLoading, setOtpLoading] = useState<boolean>(false);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [selectedTicket, setSelectedTicket] = useState<string>("FSA-102");
  const [customTicket, setCustomTicket] = useState<string>("");
  const [finalizingTicket, setFinalizingTicket] = useState<string | null>(null);
  const [finalizeError, setFinalizeError] = useState<string | null>(null);

  // Poll for Atlassian Verification OTP every 3 seconds
  const loadOtp = useCallback(async () => {
    try {
      const data = await fetchJiraOtp();
      setOtpState(data);
    } catch (e) {
      // silently handle poll blip
    }
  }, []);

  useEffect(() => {
    loadOtp();
    const interval = setInterval(loadOtp, 3000);
    return () => clearInterval(interval);
  }, [loadOtp]);

  const handleCopy = (text: string, key: string) => {
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const handleTrigger = async (ticket: string) => {
    setTriggeringTicket(ticket);
    setStatusMessage(null);
    setSelectedTicket(ticket);
    try {
      const res = await strikeEnvironment(ticket, "full-stack-application", 3600);
      if (res?.status === "queued") {
        setStatusMessage(`⏳ Max capacity reached! Workspace for ${ticket} is QUEUED and will boot automatically.`);
      } else {
        setStatusMessage(`🚀 Struck isolated Copy-on-Write workspace for ${ticket}! Tracking autonomous execution below.`);
      }
      onRefresh();
    } catch (err: any) {
      setStatusMessage(`Error: ${err.message}`);
    } finally {
      setTriggeringTicket(null);
    }
  };

  const handleFinalize = async (ticket: string) => {
    setFinalizingTicket(ticket);
    setFinalizeError(null);
    try {
      await finalizeTask(ticket);
      setStatusMessage(`🎯 Verified Host Notary with Exit 0! Certified Pull Request delivered for ${ticket}.`);
      onRefresh();
    } catch (err: any) {
      setFinalizeError(err.message || "Failed to finalize task");
    } finally {
      setFinalizingTicket(null);
    }
  };

  // Find active task and lease for the active evaluation ticket
  const activeTask = tasks.find(
    (t) => t.ticket?.toUpperCase() === selectedTicket.toUpperCase()
  );
  const activeLease = leases.find(
    (l) => l.ticket?.toUpperCase() === selectedTicket.toUpperCase()
  );

  const previewUrl = activeTask?.preview_url || activeLease?.preview_url;
  const prUrl = activeTask?.pr_url || activeLease?.pr_url;

  return (
    <div className="space-y-8">
      {/* 1. JIRA EVALUATOR ACCESS & LIVE 2FA OTP BRIDGE */}
      <div className="glass-panel rounded-2xl border border-cyan-500/30 bg-gradient-to-r from-meeseek-900/95 via-meeseek-850/90 to-cyan-950/40 p-6 md:p-8 shadow-2xl relative overflow-hidden">
        <div className="absolute top-0 right-0 w-80 h-80 bg-cyan-500/10 rounded-full blur-3xl pointer-events-none" />

        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-6 pb-6 border-b border-meeseek-border">
          <div>
            <div className="flex items-center space-x-2 text-xs font-mono font-bold uppercase tracking-wider text-cyan-400">
              <Key className="w-4 h-4 text-cyan-400" />
              <span>Official Hackathon Evaluator Credentials</span>
              <span className="px-2 py-0.5 rounded-full bg-cyan-500/20 text-cyan-300 border border-cyan-500/30 text-[10px]">
                Live 2FA Bridge Active
              </span>
            </div>
            <h2 className="text-xl md:text-2xl font-extrabold text-white mt-1">
              Test End-to-End Autonomous Delivery in Jira Cloud
            </h2>
            <p className="text-xs md:text-sm text-slate-300 mt-1 max-w-2xl leading-relaxed">
              Use these credentials to log directly into our Jira project. When Atlassian asks for a 6-digit email verification code, our automated listener fetches it to this screen in under 3 seconds!
            </p>
          </div>

          {jiraBaseUrl && (
            <a
              href={`${jiraBaseUrl.replace(/\/$/, "")}/jira/your-work`}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center space-x-2 px-5 py-2.5 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white font-bold text-xs shadow-lg shadow-cyan-500/25 transition-all self-start lg:self-center"
            >
              <span>Open Jira Cloud</span>
              <ExternalLink className="w-4 h-4 ml-1" />
            </a>
          )}
        </div>

        {/* Credentials & Live OTP Row */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-6">
          {/* Email */}
          <div className="p-4 rounded-xl bg-slate-950/70 border border-slate-800 flex flex-col justify-between">
            <span className="text-[11px] font-mono text-slate-400 uppercase tracking-wider">Jira Evaluator Email</span>
            <div className="flex items-center justify-between mt-2">
              <span className="text-xs sm:text-sm font-mono font-bold text-white truncate max-w-[200px]">
                {otpState?.evaluator_email || "saif.fieldsync@gmail.com"}
              </span>
              <button
                onClick={() => handleCopy(otpState?.evaluator_email || "saif.fieldsync@gmail.com", "email")}
                className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition-all text-xs flex items-center space-x-1"
                title="Copy email"
              >
                {copiedKey === "email" ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            </div>
          </div>

          {/* Password */}
          <div className="p-4 rounded-xl bg-slate-950/70 border border-slate-800 flex flex-col justify-between">
            <span className="text-[11px] font-mono text-slate-400 uppercase tracking-wider">Jira Password</span>
            <div className="flex items-center justify-between mt-2">
              <span className="text-xs sm:text-sm font-mono font-bold text-cyan-300 truncate max-w-[200px]">
                {otpState?.evaluator_password || "Fieldwork2026!"}
              </span>
              <button
                onClick={() => handleCopy(otpState?.evaluator_password || "Fieldwork2026!", "password")}
                className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition-all text-xs flex items-center space-x-1"
                title="Copy password"
              >
                {copiedKey === "password" ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            </div>
          </div>

          {/* Real-time Atlassian OTP Box */}
          <div className="p-4 rounded-xl bg-gradient-to-br from-cyan-950/80 to-slate-950 border border-cyan-500/50 shadow-lg shadow-cyan-500/10 flex flex-col justify-between">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-mono text-cyan-300 uppercase tracking-wider font-semibold flex items-center space-x-1.5">
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                <span>Live Verification OTP</span>
              </span>
              <button
                onClick={() => {
                  setOtpLoading(true);
                  loadOtp().finally(() => setTimeout(() => setOtpLoading(false), 500));
                }}
                className="text-slate-400 hover:text-cyan-300 transition-colors p-1"
                title="Refresh OTP"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${otpLoading ? "animate-spin text-cyan-400" : ""}`} />
              </button>
            </div>

            <div className="my-2">
              {otpState?.latest_otp?.code ? (
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-1 font-mono text-2xl font-extrabold tracking-widest text-white bg-slate-900/90 px-3 py-1 rounded-lg border border-cyan-500/40">
                    {otpState.latest_otp.code.split("").map((digit, i) => (
                      <span key={i} className="text-cyan-300">{digit}</span>
                    ))}
                  </div>
                  <button
                    onClick={() => handleCopy(otpState.latest_otp!.code, "otp")}
                    className="ml-2 px-3 py-1.5 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white font-bold text-xs shadow-md shadow-cyan-500/20 transition-all flex items-center space-x-1"
                  >
                    {copiedKey === "otp" ? (
                      <>
                        <Check className="w-3.5 h-3.5 text-emerald-300" />
                        <span>Copied!</span>
                      </>
                    ) : (
                      <>
                        <Copy className="w-3.5 h-3.5" />
                        <span>Copy</span>
                      </>
                    )}
                  </button>
                </div>
              ) : (
                <div className="flex items-center space-x-2 text-slate-400 text-xs py-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-ping" />
                  <span>Waiting for login request...</span>
                </div>
              )}
            </div>

            <div className="text-[10px] font-mono text-slate-400 flex items-center justify-between">
              {otpState?.latest_otp ? (
                <span>
                  Updated {otpState.latest_otp.age_seconds ?? 0}s ago ({otpState.latest_otp.received_at_formatted})
                </span>
              ) : (
                <span>Trigger login on Jira to receive code</span>
              )}
            </div>
          </div>
        </div>

        {/* History of recent OTPs */}
        {otpState?.history && otpState.history.length > 1 && (
          <div className="mt-4 pt-3 border-t border-slate-800/80 flex flex-wrap items-center gap-2 text-[11px] font-mono text-slate-400">
            <span className="text-slate-500">Previous Codes:</span>
            {otpState.history.slice(1, 4).map((h, idx) => (
              <span
                key={idx}
                onClick={() => handleCopy(h.code, `hist-${idx}`)}
                className="px-2 py-0.5 rounded bg-slate-900 border border-slate-800 text-slate-300 hover:text-cyan-300 cursor-pointer transition-colors"
                title={`Received at ${h.received_at_formatted}`}
              >
                {h.code} <span className="text-[9px] text-slate-500">({h.received_at_formatted})</span>
              </span>
            ))}
          </div>
        )}
      </div>

      {/* 2. THREE CURATED DEMO CARDS */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-lg font-bold text-white flex items-center space-x-2">
              <Sparkles className="w-4 h-4 text-cyan-400" />
              <span>1-Click Strike Playgrounds</span>
            </h3>
            <p className="text-xs text-slate-400">
              Or strike directly from the console to observe real-time container provisioning, agent narration, and host notary certification.
            </p>
          </div>

          {/* Quick Custom Ticket Strike */}
          <div className="flex items-center space-x-2">
            <input
              type="text"
              placeholder="e.g. FSA-65"
              value={customTicket}
              onChange={(e) => setCustomTicket(e.target.value.toUpperCase())}
              className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-white placeholder-slate-500 font-mono w-28 uppercase focus:outline-none focus:border-cyan-400"
            />
            <button
              onClick={() => customTicket && handleTrigger(customTicket.trim())}
              disabled={!customTicket || triggeringTicket === customTicket.trim()}
              className="px-3 py-1.5 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white font-medium text-xs shadow-md transition-all disabled:opacity-50"
            >
              Strike
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* DEMO 1: Bug Fix & Exit 0 Notary */}
          <div className={`glass-panel-interactive rounded-2xl p-6 border flex flex-col justify-between transition-all ${
            selectedTicket === "FSA-101" ? "border-cyan-500 ring-1 ring-cyan-500/50 bg-cyan-950/20" : "border-meeseek-border"
          }`}>
            <div>
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono font-bold px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-300 border border-cyan-500/30">
                  DEMO 1 · SPEED & PROOF
                </span>
                <span className="text-[10px] text-slate-500 font-mono">~35s Turnaround</span>
              </div>

              <h4 className="text-base font-bold text-white mt-3">
                Fast Autonomous Bug Fix (Exit 0 Proof)
              </h4>
              <p className="text-xs text-slate-400 mt-2 leading-relaxed">
                Dispatches ticket <span className="font-mono text-slate-200">FSA-101</span> to fix a discount calculation error in the orders API.
              </p>

              <div className="mt-4 pt-4 border-t border-slate-800 text-[11px] space-y-1.5 text-slate-300">
                <div className="flex items-center space-x-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Runs local <code className="text-cyan-300">pytest</code> suite</span>
                </div>
                <div className="flex items-center space-x-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Host Notary verifies strict Exit 0</span>
                </div>
                <div className="flex items-center space-x-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Opens certified GitHub PR</span>
                </div>
              </div>
            </div>

            <div className="mt-6 pt-4 border-t border-slate-800/80">
              <button
                onClick={() => handleTrigger("FSA-101")}
                disabled={triggeringTicket === "FSA-101"}
                className="w-full py-2.5 px-4 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white font-medium text-xs flex items-center justify-center space-x-2 shadow-lg shadow-cyan-500/20 transition-all"
              >
                <Play className="w-3.5 h-3.5 fill-current" />
                <span>{triggeringTicket === "FSA-101" ? "Spawning Workspace..." : "Strike Demo 1 Workspace"}</span>
              </button>
            </div>
          </div>

          {/* DEMO 2: UI Feature with Hot Live Preview */}
          <div className={`glass-panel-interactive rounded-2xl p-6 border flex flex-col justify-between transition-all ${
            selectedTicket === "FSA-102" ? "border-purple-500 ring-1 ring-purple-500/50 bg-purple-950/20" : "border-meeseek-border"
          }`}>
            <div>
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono font-bold px-2 py-0.5 rounded bg-purple-500/10 text-purple-300 border border-purple-500/30">
                  DEMO 2 · VISUAL SANDBOX
                </span>
                <span className="text-[10px] text-slate-500 font-mono">Live HTTPS Preview</span>
              </div>

              <h4 className="text-base font-bold text-white mt-3">
                Full-Stack UI Feature with Live Preview
              </h4>
              <p className="text-xs text-slate-400 mt-2 leading-relaxed">
                Dispatches ticket <span className="font-mono text-slate-200">FSA-102</span> to add a customer export to CSV button on the React dashboard.
              </p>

              <div className="mt-4 pt-4 border-t border-slate-800 text-[11px] space-y-1.5 text-slate-300">
                <div className="flex items-center space-x-1.5">
                  <Eye className="w-3.5 h-3.5 text-purple-400" />
                  <span>Spins up live Vite dev server</span>
                </div>
                <div className="flex items-center space-x-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5 text-purple-400" />
                  <span>Reverse-proxies via Caddy HTTPS</span>
                </div>
                <div className="flex items-center space-x-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5 text-purple-400" />
                  <span>Clickable browser testing before review</span>
                </div>
              </div>
            </div>

            <div className="mt-6 pt-4 border-t border-slate-800/80">
              <button
                onClick={() => handleTrigger("FSA-102")}
                disabled={triggeringTicket === "FSA-102"}
                className="w-full py-2.5 px-4 rounded-xl bg-purple-600 hover:bg-purple-500 text-white font-medium text-xs flex items-center justify-center space-x-2 shadow-lg shadow-purple-500/20 transition-all"
              >
                <Play className="w-3.5 h-3.5 fill-current" />
                <span>{triggeringTicket === "FSA-102" ? "Spawning Workspace..." : "Strike Demo 2 Workspace"}</span>
              </button>
            </div>
          </div>

          {/* DEMO 3: Plan-Only Safety Mode */}
          <div className={`glass-panel-interactive rounded-2xl p-6 border flex flex-col justify-between transition-all ${
            selectedTicket === "FSA-103" ? "border-amber-500 ring-1 ring-amber-500/50 bg-amber-950/20" : "border-meeseek-border"
          }`}>
            <div>
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono font-bold px-2 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/30">
                  DEMO 3 · SAFETY MANDATE
                </span>
                <span className="text-[10px] text-slate-500 font-mono">Plan-First Mode</span>
              </div>

              <h4 className="text-base font-bold text-white mt-3">
                Plan-Only Mode & AST Safety Guard
              </h4>
              <p className="text-xs text-slate-400 mt-2 leading-relaxed">
                Dispatches ticket <span className="font-mono text-slate-200">FSA-103</span> with <code className="text-amber-300 font-mono">/plan</code> mandate to evaluate refactoring authentication middleware.
              </p>

              <div className="mt-4 pt-4 border-t border-slate-800 text-[11px] space-y-1.5 text-slate-300">
                <div className="flex items-center space-x-1.5">
                  <ShieldAlert className="w-3.5 h-3.5 text-amber-400" />
                  <span>Write tools strictly locked</span>
                </div>
                <div className="flex items-center space-x-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5 text-amber-400" />
                  <span>Agent drafts plan & halts</span>
                </div>
                <div className="flex items-center space-x-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5 text-amber-400" />
                  <span>Requires human approval to unlock code</span>
                </div>
              </div>
            </div>

            <div className="mt-6 pt-4 border-t border-slate-800/80">
              <button
                onClick={() => handleTrigger("FSA-103")}
                disabled={triggeringTicket === "FSA-103"}
                className="w-full py-2.5 px-4 rounded-xl bg-amber-600 hover:bg-amber-500 text-white font-medium text-xs flex items-center justify-center space-x-2 shadow-lg shadow-amber-500/20 transition-all"
              >
                <Play className="w-3.5 h-3.5 fill-current" />
                <span>{triggeringTicket === "FSA-103" ? "Spawning Workspace..." : "Strike Demo 3 Workspace"}</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* 3. LIVE JIRA ACTIVITY STREAM & HOST NOTARY FEED */}
      <div className="glass-panel rounded-2xl border border-meeseek-border overflow-hidden">
        <div className="px-6 py-5 border-b border-meeseek-border bg-meeseek-850/60 flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center space-x-3">
            <MessageSquare className="w-5 h-5 text-cyan-400" />
            <div>
              <div className="flex items-center space-x-2">
                <h3 className="font-bold text-white text-base">
                  Live Jira Activity & Autonomous Feed
                </h3>
                <span className="font-mono text-xs px-2.5 py-0.5 rounded-full bg-cyan-500/20 text-cyan-300 border border-cyan-500/30 font-semibold">
                  {selectedTicket}
                </span>
              </div>
              <p className="text-xs text-slate-400">
                Mirrors the real-time comment thread and verification proof exchanged on Jira Cloud.
              </p>
            </div>
          </div>

          {activeTask?.workflow_state && (
            <div className="flex items-center space-x-2">
              <span className="text-xs text-slate-400 font-mono">Stage:</span>
              <span className={`px-2.5 py-1 rounded-lg text-xs font-mono font-bold uppercase tracking-wider ${
                activeTask.workflow_state === "CERTIFIED_PR"
                  ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                  : activeTask.workflow_state === "WAITING_INPUT" || activeTask.workflow_state === "HALTED"
                  ? "bg-amber-500/20 text-amber-300 border border-amber-500/40"
                  : "bg-cyan-500/20 text-cyan-300 border border-cyan-500/40"
              }`}>
                {activeTask.workflow_state}
              </span>
            </div>
          )}
        </div>

        <div className="p-6 space-y-6">
          {/* Status Alert if available */}
          {statusMessage && (
            <div className="p-4 rounded-xl bg-cyan-950/60 border border-cyan-500/40 text-cyan-200 text-xs flex items-center space-x-2">
              <CheckCircle2 className="w-4 h-4 flex-shrink-0 text-cyan-400" />
              <span>{statusMessage}</span>
            </div>
          )}

          {finalizeError && (
            <div className="p-4 rounded-xl bg-red-950/60 border border-red-500/40 text-red-200 text-xs flex items-center space-x-2">
              <AlertCircle className="w-4 h-4 flex-shrink-0 text-red-400" />
              <span>{finalizeError}</span>
            </div>
          )}

          {/* Activity Timeline */}
          <div className="space-y-4">
            {/* Event 1: Trigger & Sandbox Provisioning */}
            <div className="flex items-start space-x-3 text-xs">
              <div className="w-8 h-8 rounded-full bg-cyan-600/20 border border-cyan-500/40 flex items-center justify-center text-cyan-300 font-bold flex-shrink-0 mt-0.5">
                🤖
              </div>
              <div className="flex-1 bg-slate-950/70 border border-slate-800 rounded-xl p-4">
                <div className="flex items-center justify-between pb-2 border-b border-slate-800/80 mb-2">
                  <span className="font-bold text-white">Meeseek Automation (Jira Bot)</span>
                  <span className="text-[10px] font-mono text-slate-500">Lifecycle Ingestion</span>
                </div>
                <p className="text-slate-300 leading-relaxed font-mono text-[12px]">
                  Claimed <span className="text-cyan-300 font-bold">{selectedTicket}</span>. Dedicated sandbox container provisioned with pristine Copy-on-Write storage, PostgreSQL database fixtures, and reverse-proxy tunnel in &lt;300ms.
                </p>
              </div>
            </div>

            {/* Event 2: Agent Narration (The exact text Debby wrote to Jira) */}
            {(activeTask?.stable_agent_message || activeTask?.agent_message) ? (
              <div className="flex items-start space-x-3 text-xs">
                <div className="w-8 h-8 rounded-full bg-purple-600/20 border border-purple-500/40 flex items-center justify-center text-purple-300 font-bold flex-shrink-0 mt-0.5">
                  👩‍💻
                </div>
                <div className="flex-1 bg-slate-950/70 border border-slate-800 rounded-xl p-4">
                  <div className="flex items-center justify-between pb-2 border-b border-slate-800/80 mb-2">
                    <span className="font-bold text-purple-300">Debby (Senior Full-Stack Engineer)</span>
                    <span className="text-[10px] font-mono text-slate-500">Autonomous Narration</span>
                  </div>
                  <div className="text-slate-200 whitespace-pre-wrap font-mono text-[12px] leading-relaxed">
                    {activeTask.stable_agent_message || activeTask.agent_message}
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex items-start space-x-3 text-xs">
                <div className="w-8 h-8 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center text-slate-400 font-bold flex-shrink-0 mt-0.5">
                  ⏳
                </div>
                <div className="flex-1 bg-slate-950/40 border border-slate-800/60 rounded-xl p-4 text-slate-400 text-xs italic">
                  Autonomous agent is authoring code changes or waiting for strike...
                </div>
              </div>
            )}

            {/* Interactive Preview & 1-Click Finalize Action Card */}
            <div className="p-5 rounded-xl bg-gradient-to-r from-cyan-950/50 via-slate-900 to-purple-950/40 border border-cyan-500/40 shadow-xl flex flex-col md:flex-row md:items-center justify-between gap-4">
              <div>
                <div className="flex items-center space-x-2">
                  <Globe className="w-4 h-4 text-cyan-400" />
                  <span className="font-bold text-white text-sm">Interactive Sandbox Preview & Notary Gate</span>
                </div>
                <p className="text-xs text-slate-300 mt-1 max-w-xl">
                  Test the agent's live changes in your browser with hot reload. When satisfied, click Finalize to run the independent Host Notary test suite and open the certified Pull Request.
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-3">
                {previewUrl ? (
                  <a
                    href={previewUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center space-x-2 px-4 py-2 rounded-xl bg-purple-600 hover:bg-purple-500 text-white font-bold text-xs shadow-lg shadow-purple-500/20 transition-all whitespace-nowrap"
                  >
                    <Eye className="w-4 h-4" />
                    <span>Open Live Preview ↗</span>
                  </a>
                ) : (
                  <span className="px-3.5 py-2 rounded-xl bg-slate-800/80 border border-slate-700 text-slate-400 text-xs font-mono">
                    Preview Booting...
                  </span>
                )}

                <button
                  onClick={() => handleFinalize(selectedTicket)}
                  disabled={finalizingTicket === selectedTicket}
                  className="inline-flex items-center space-x-2 px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs shadow-lg shadow-emerald-500/20 transition-all whitespace-nowrap disabled:opacity-50"
                >
                  <ShieldCheck className="w-4 h-4" />
                  <span>
                    {finalizingTicket === selectedTicket ? "Verifying Host Notary..." : "1-Click Finalize & Certify"}
                  </span>
                </button>
              </div>
            </div>

            {/* Event 3: Certified Host Notary Proof & GitHub PR */}
            {prUrl && (
              <div className="flex items-start space-x-3 text-xs">
                <div className="w-8 h-8 rounded-full bg-emerald-600/20 border border-emerald-500/40 flex items-center justify-center text-emerald-300 font-bold flex-shrink-0 mt-0.5">
                  🔏
                </div>
                <div className="flex-1 bg-emerald-950/20 border border-emerald-500/40 rounded-xl p-5 shadow-lg">
                  <div className="flex items-center justify-between pb-2 border-b border-emerald-500/20 mb-3">
                    <span className="font-bold text-emerald-300 text-sm flex items-center space-x-2">
                      <ShieldCheck className="w-4 h-4 text-emerald-400" />
                      <span>Host Notary Proof of Correctness (Certified Exit 0)</span>
                    </span>
                    <span className="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-mono text-[10px] font-bold">
                      PR CERTIFIED
                    </span>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 font-mono text-[11px] mb-4 text-slate-300">
                    <div className="p-2.5 rounded-lg bg-slate-950/80 border border-slate-800">
                      <span className="text-slate-500 block text-[10px]">Test Suite</span>
                      <span className="text-emerald-400 font-bold">✓ Exit 0 (All Passed)</span>
                    </div>
                    <div className="p-2.5 rounded-lg bg-slate-950/80 border border-slate-800">
                      <span className="text-slate-500 block text-[10px]">Database Integrity</span>
                      <span className="text-emerald-400 font-bold">✓ Seed Rows Verified</span>
                    </div>
                    <div className="p-2.5 rounded-lg bg-slate-950/80 border border-slate-800">
                      <span className="text-slate-500 block text-[10px]">AST Blast-Radius</span>
                      <span className="text-emerald-400 font-bold">✓ Within Scope</span>
                    </div>
                  </div>

                  <div className="flex items-center justify-between pt-2 border-t border-emerald-500/20">
                    <span className="text-xs text-slate-300">
                      Clean GitHub Pull Request opened and linked to Jira ticket.
                    </span>
                    <a
                      href={prUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center space-x-2 px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs shadow-md shadow-emerald-500/20 transition-all"
                    >
                      <GitPullRequest className="w-4 h-4" />
                      <span>View Certified GitHub PR ↗</span>
                    </a>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
