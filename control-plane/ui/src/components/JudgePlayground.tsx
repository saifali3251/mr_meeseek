import React, { useState, useEffect, useCallback } from "react";
import { 
  Play, 
  ExternalLink, 
  ShieldAlert, 
  Eye, 
  GitPullRequest, 
  CheckCircle2, 
  ArrowRight, 
  Sparkles, 
  Clock, 
  RefreshCw,
  Terminal,
  Key,
  Copy,
  Check,
  Globe
} from "lucide-react";
import { strikeEnvironment, verifyJiraTicket, fetchJiraOtp, JiraVerifyResult } from "../api";
import { JiraOtpState } from "../types";
import judgeMeeseek from "../assets/judge-meeseek.webp";

interface JudgePlaygroundProps {
  onRefresh: () => void;
  jiraBaseUrl: string;
  onNavigateToWorkspaces?: (ticket?: string) => void;
}

export const JudgePlayground: React.FC<JudgePlaygroundProps> = ({
  onRefresh,
  jiraBaseUrl,
  onNavigateToWorkspaces,
}) => {
  const [ticketKey, setTicketKey] = useState<string>("FSA-32");
  const [jiraTicketKey, setJiraTicketKey] = useState<string>("FSA-65");
  const [jiraData, setJiraData] = useState<JiraVerifyResult | null>(null);
  const [loadingJira, setLoadingJira] = useState<boolean>(true);
  const [isTriggering, setIsTriggering] = useState<boolean>(false);
  const [lastTriggeredTicket, setLastTriggeredTicket] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<{
    type: "success" | "queued" | "error";
    text: string;
    ticket?: string;
  } | null>(null);

  // OTP listener state
  const [otpState, setOtpState] = useState<JiraOtpState | null>(null);
  const [otpLoading, setOtpLoading] = useState<boolean>(false);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  // Poll for Atlassian Verification OTP every 3 seconds
  const loadOtp = useCallback(async () => {
    try {
      const data = await fetchJiraOtp();
      setOtpState(data);
    } catch {
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

  // Fetch live ticket details from Jira
  const loadJiraDetails = async (ticket: string) => {
    setLoadingJira(true);
    try {
      const data = await verifyJiraTicket(ticket);
      setJiraData(data);
    } catch (err) {
      console.warn("Failed to fetch Jira ticket details:", err);
      setJiraData(null);
    } finally {
      setLoadingJira(false);
    }
  };

  useEffect(() => {
    if (ticketKey.trim()) {
      loadJiraDetails(ticketKey);
    }
  }, [ticketKey]);

  // Extract repo from Jira description if formatted as `Repo: <name>`
  const extractedRepo = React.useMemo(() => {
    if (!jiraData?.description && !jiraData?.summary) return null;
    const text = `${jiraData?.summary || ""} ${jiraData?.description || ""}`;
    const match = text.match(/Repo:\s*([a-zA-Z0-9_\-]+)/i);
    return match ? match[1] : null;
  }, [jiraData]);

  const handleTrigger = async () => {
    const cleanTicket = ticketKey.trim().toUpperCase();
    if (!cleanTicket) return;

    setIsTriggering(true);
    setStatusMessage(null);
    try {
      const res = await strikeEnvironment(cleanTicket, "full-stack-application", 3600);
      setLastTriggeredTicket(cleanTicket);

      if (res?.status === "queued") {
        setStatusMessage({
          type: "queued",
          text: `⏳ Max capacity reached! Workspace for ${cleanTicket} is QUEUED and will boot automatically when an active workspace is released.`,
          ticket: cleanTicket,
        });
      } else {
        setStatusMessage({
          type: "success",
          text: `🚀 Struck isolated Copy-on-Write workspace & started autonomous Meeseek agent for ${cleanTicket}!`,
          ticket: cleanTicket,
        });
      }
      onRefresh();
    } catch (err: any) {
      setStatusMessage({
        type: "error",
        text: `Error: ${err.message}`,
      });
    } finally {
      setIsTriggering(false);
    }
  };

  const jiraLink = jiraBaseUrl ? `${jiraBaseUrl.replace(/\/$/, "")}/browse/${ticketKey.trim().toUpperCase()}` : null;

  return (
    <div className="space-y-6">
      {/* 1. TOP: HERO EVALUATOR BANNER (KEPT AS IN MAIN BRANCH) */}
      <div className="glass-panel rounded-2xl p-8 md:pr-[300px] md:min-h-[380px] md:flex md:flex-col md:justify-center border border-meeseek-border bg-gradient-to-br from-meeseek-900/90 via-meeseek-850/80 to-cyan-950/20 shadow-2xl relative overflow-hidden">
        <div className="absolute -top-12 -right-12 w-64 h-64 bg-cyan-500/10 rounded-full blur-3xl pointer-events-none" />
        {/* Final-demo Meeseek: "I ship the PR!" */}
        <figure className="judge-art hidden md:block absolute right-8 top-1/2 -translate-y-1/2 w-[230px] m-0">
          <svg viewBox="100 20 620 1020" role="img" aria-label="Mr. Meeseeks: I ship the PR!" className="block w-full h-auto overflow-visible">
            <image href={judgeMeeseek} x="109" y="389" width="560" height="642" />
            <g className="judge-burst">
              <polygon points="475,37 504,108 574,46 555,134 647,109 595,174 663,195 606,229 651,275 582,281 601,342 531,311 524,392 475,341 429,381 417,314 334,357 356,288 278,282 347,229 250,191 363,177 292,102 391,130 386,63 446,109" fill="#fff" stroke="#111" strokeWidth="5" strokeLinejoin="round" />
              <text x="475" y="203" textAnchor="middle" textLength="210" lengthAdjust="spacingAndGlyphs" className="judge-pow">I SHIP</text>
              <text x="475" y="277" textAnchor="middle" textLength="250" lengthAdjust="spacingAndGlyphs" className="judge-pow judge-pow-pr">THE PR!</text>
            </g>
          </svg>
          <figcaption className="text-[11px] font-mono text-slate-400 text-center mt-2">Notary exit 0 &middot; PR certified &middot; workspace gone</figcaption>
        </figure>

        <div className="flex items-center space-x-2 text-xs font-mono font-semibold uppercase text-cyan-400 tracking-wider">
          <Sparkles className="w-4 h-4 text-cyan-400" />
          <span>Hackathon Evaluator & Judge Quickstart</span>
        </div>
        <h2 className="text-2xl font-extrabold text-white mt-2">
          Experience Autonomous Engineering in 60 Seconds
        </h2>
        <p className="text-sm text-slate-300 mt-3 max-w-2xl leading-relaxed">
          No environment setup, no Docker builds, no API tokens required. Click the button below to watch Meeseek fetch ticket specifications directly from Jira Cloud, strike a 300ms Copy-on-Write sandbox, run autonomous coding passes, and certify a GitHub Pull Request via independent Host Notary proof!
        </p>

        {statusMessage && (
          <div className={`mt-4 p-4 rounded-xl border text-xs flex items-center space-x-2 ${
            statusMessage.type === "success"
              ? "bg-emerald-950/60 border-emerald-500/40 text-emerald-200"
              : statusMessage.type === "queued"
              ? "bg-amber-950/60 border-amber-500/40 text-amber-200"
              : "bg-red-950/60 border-red-500/40 text-red-200"
          }`}>
            <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
            <span>{statusMessage.text}</span>
          </div>
        )}
      </div>

      {/* 2. METHOD 1: FULL NATIVE JIRA EXPERIENCE */}
      <div className="glass-panel rounded-2xl border border-cyan-500/30 bg-meeseek-850/80 p-5 shadow-lg relative overflow-hidden">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-meeseek-border">
          <div className="flex items-center space-x-2.5">
            <div className="p-1.5 rounded-lg bg-cyan-500/10 text-cyan-400 border border-cyan-500/30">
              <Key className="w-4 h-4" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <span className="text-xs font-mono font-bold px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-300 border border-cyan-500/30 uppercase tracking-wider">
                  Method 1 · Full Native Jira Experience (Recommended)
                </span>
                <span className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded-full ${
                  otpState?.imap_active && !otpState?.last_error
                    ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                    : "bg-amber-500/20 text-amber-300 border border-amber-500/30"
                }`}>
                  {otpState?.imap_active && !otpState?.last_error ? "● 2FA Bridge Active" : "○ 2FA Standby"}
                </span>
                {otpState?.last_error && (
                  <span className="text-[10px] font-mono text-amber-300 bg-amber-950/60 border border-amber-500/30 px-2 py-0.5 rounded-full max-w-[220px] truncate" title={otpState.last_error}>
                    ⚠️ {otpState.last_error}
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-300 mt-1">
                Log into our Jira Cloud workspace to create or label tickets directly. Verification codes appear here in real-time.
              </p>
            </div>
          </div>

          {jiraBaseUrl && (
            <div className="flex items-center space-x-2 self-start sm:self-auto">
              <span className="text-xs font-mono text-slate-400 hidden sm:inline">Ticket Key:</span>
              <input
                type="text"
                value={jiraTicketKey}
                onChange={(e) => setJiraTicketKey(e.target.value.toUpperCase())}
                placeholder="FSA-65"
                className="w-24 px-2 py-1 rounded bg-slate-900 border border-slate-700 text-cyan-300 font-mono text-xs focus:outline-none focus:border-cyan-400"
              />
              <a
                href={`${jiraBaseUrl.replace(/\/$/, "")}/browse/${(jiraTicketKey || "FSA-65").trim()}`}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center space-x-1.5 px-3.5 py-1.5 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white font-bold text-xs shadow-md shadow-cyan-500/20 transition-all whitespace-nowrap"
              >
                <span>Open Jira ticket {jiraTicketKey.trim() || "FSA-65"}</span>
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
            </div>
          )}
        </div>

        {/* Compact Credentials & Live OTP Strip */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-3">
          {/* Email */}
          <div className="px-3.5 py-2.5 rounded-xl bg-slate-950/70 border border-slate-800 flex items-center justify-between">
            <div className="min-w-0 pr-2">
              <span className="text-[10px] font-mono text-slate-500 uppercase tracking-wider block">Jira Email</span>
              <span className="text-xs font-mono font-bold text-white truncate block">
                {otpState?.evaluator_email || "Configured via .env"}
              </span>
            </div>
            {otpState?.evaluator_email && (
              <button
                onClick={() => handleCopy(otpState.evaluator_email!, "email")}
                className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition-all text-xs flex-shrink-0"
                title="Copy email"
              >
                {copiedKey === "email" ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            )}
          </div>

          {/* Password */}
          <div className="px-3.5 py-2.5 rounded-xl bg-slate-950/70 border border-slate-800 flex items-center justify-between">
            <div className="min-w-0 pr-2">
              <span className="text-[10px] font-mono text-slate-500 uppercase tracking-wider block">Jira Password</span>
              <span className="text-xs font-mono font-bold text-cyan-300 truncate block">
                {otpState?.evaluator_password || "••••••••"}
              </span>
            </div>
            {otpState?.evaluator_password && (
              <button
                onClick={() => handleCopy(otpState.evaluator_password!, "password")}
                className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition-all text-xs flex-shrink-0"
                title="Copy password"
              >
                {copiedKey === "password" ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            )}
          </div>

          {/* Live OTP */}
          <div className="px-3.5 py-2.5 rounded-xl bg-gradient-to-r from-cyan-950/80 to-slate-950 border border-cyan-500/40 flex items-center justify-between">
            <div className="min-w-0 pr-2">
              <div className="flex items-center space-x-1.5">
                <span className="text-[10px] font-mono text-cyan-400 uppercase tracking-wider font-semibold block">
                  Verification OTP
                </span>
                <button
                  onClick={() => {
                    setOtpLoading(true);
                    loadOtp().finally(() => setTimeout(() => setOtpLoading(false), 500));
                  }}
                  className="text-slate-500 hover:text-cyan-300 transition-colors p-0.5"
                  title="Refresh OTP"
                >
                  <RefreshCw className={`w-3 h-3 ${otpLoading ? "animate-spin text-cyan-400" : ""}`} />
                </button>
              </div>

              {otpState?.latest_otp?.code ? (
                <div className="flex items-baseline space-x-1.5 mt-0.5">
                  <span className="font-mono text-base font-extrabold text-cyan-300 tracking-wider">
                    {otpState.latest_otp.code}
                  </span>
                  <span className="text-[9px] font-mono text-slate-500">
                    ({otpState.latest_otp.received_at_formatted})
                  </span>
                </div>
              ) : (
                <span className="text-[11px] font-mono text-slate-400 italic block mt-0.5">
                  Waiting for login...
                </span>
              )}
            </div>

            {otpState?.latest_otp?.code ? (
              <button
                onClick={() => handleCopy(otpState.latest_otp!.code, "otp")}
                className="px-2.5 py-1.5 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white font-bold text-xs shadow-md shadow-cyan-500/20 transition-all flex items-center space-x-1 flex-shrink-0"
              >
                {copiedKey === "otp" ? (
                  <>
                    <Check className="w-3.5 h-3.5 text-emerald-300" />
                    <span>Copied</span>
                  </>
                ) : (
                  <>
                    <Copy className="w-3.5 h-3.5" />
                    <span>Copy</span>
                  </>
                )}
              </button>
            ) : (
              <span className="w-2 h-2 rounded-full bg-amber-400 animate-ping mr-2" />
            )}
          </div>
        </div>

        {/* 30-Second Playbook in Jira */}
        <div className="mt-3 pt-3 border-t border-meeseek-border/70 flex flex-col md:flex-row md:items-center justify-between gap-2.5">
          <div className="flex items-center space-x-2 text-cyan-400 font-mono font-bold text-xs uppercase tracking-wider flex-shrink-0">
            <span>🎯 30-Second Jira Playbook:</span>
          </div>
          <div className="flex flex-wrap items-center gap-2 font-mono text-[11px] text-slate-300">
            <span className="flex items-center space-x-1.5 bg-slate-950/70 border border-slate-800 px-2.5 py-1 rounded-lg">
              <span className="w-4 h-4 rounded-full bg-cyan-500/20 text-cyan-300 flex items-center justify-center text-[10px] font-bold border border-cyan-500/30">1</span>
              <span>Open ticket (e.g. <span className="text-cyan-300 font-semibold">{jiraTicketKey.trim() || "FSA-65"}</span>)</span>
            </span>
            <span className="text-slate-600 hidden sm:inline">➔</span>
            <span className="flex items-center space-x-1.5 bg-slate-950/70 border border-slate-800 px-2.5 py-1 rounded-lg">
              <span className="w-4 h-4 rounded-full bg-purple-500/20 text-purple-300 flex items-center justify-center text-[10px] font-bold border border-purple-500/30">2</span>
              <span>Add label <code className="text-purple-300 bg-purple-950/80 px-1 py-0.5 rounded font-bold border border-purple-500/30">#meeseek</code></span>
            </span>
            <span className="text-slate-600 hidden sm:inline">➔</span>
            <span className="flex items-center space-x-1.5 bg-slate-950/70 border border-slate-800 px-2.5 py-1 rounded-lg">
              <span className="w-4 h-4 rounded-full bg-emerald-500/20 text-emerald-300 flex items-center justify-center text-[10px] font-bold border border-emerald-500/30">3</span>
              <span>After agent responds with implementation, comment <code className="text-emerald-300 bg-emerald-950/80 px-1 py-0.5 rounded font-bold border border-emerald-500/30">/meeseek finalize</code></span>
            </span>
          </div>
        </div>
      </div>

      {/* STYLED DIVIDER */}
      <div className="relative py-2 flex items-center justify-center">
        <div className="absolute inset-0 flex items-center">
          <div className="w-full border-t border-slate-800" />
        </div>
        <div className="relative px-4 bg-meeseek-950 text-[11px] font-mono font-bold uppercase tracking-wider text-cyan-400 flex items-center space-x-2 border border-slate-800 rounded-full py-1 shadow-sm">
          <span>⚡ OR EVALUATE INSTANTLY WITHOUT LOGIN</span>
        </div>
      </div>

      {/* 3. METHOD 2: 1-CLICK CONSOLE EVALUATION */}
      <div className="glass-panel-interactive rounded-2xl border border-meeseek-border p-6 md:p-8 space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-slate-800">
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="text-xs font-mono font-bold px-2.5 py-1 rounded bg-purple-500/10 text-purple-300 border border-purple-500/30 uppercase tracking-wider">
              Method 2 · 1-Click Console Evaluation (Zero Login Needed)
            </span>
            <span className="text-xs text-slate-400 font-mono flex items-center space-x-1">
              <Clock className="w-3.5 h-3.5 text-slate-500" />
              <span>Full E2E Workflow</span>
            </span>
          </div>


          <div className="flex items-center space-x-3 text-xs font-mono">
            {loadingJira ? (
              <span className="flex items-center space-x-1.5 text-slate-400">
                <RefreshCw className="w-3.5 h-3.5 animate-spin text-cyan-400" />
                <span>Syncing Jira...</span>
              </span>
            ) : jiraData?.exists ? (
              <span className="flex items-center space-x-1.5 text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                <span>Synced from Jira Cloud</span>
              </span>
            ) : (
              <span className="flex items-center space-x-1.5 text-slate-400 bg-slate-800/80 px-2 py-0.5 rounded border border-slate-700">
                <span>Standalone / Unsynced</span>
              </span>
            )}

            {jiraLink && (
              <a
                href={jiraLink}
                target="_blank"
                rel="noreferrer"
                className="text-cyan-400 hover:text-cyan-300 hover:underline flex items-center space-x-1 font-semibold"
              >
                <span>View on Jira</span>
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
            )}
          </div>
        </div>

        {/* Ticket Headline & Metadata */}
        <div>
          <div className="flex flex-wrap items-center gap-2.5 mb-2">
            <span className="text-lg font-mono font-bold text-cyan-400">
              {ticketKey}
            </span>
            {jiraData?.issuetype && (
              <span className="text-xs font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
                {jiraData.issuetype}
              </span>
            )}
            {extractedRepo && (
              <span className="text-xs font-mono px-2 py-0.5 rounded bg-cyan-950/60 text-cyan-300 border border-cyan-800/50">
                Target Repo: {extractedRepo}
              </span>
            )}
            {jiraData?.labels && jiraData.labels.length > 0 && (
              <div className="flex items-center gap-1.5">
                {jiraData.labels.map((l, idx) => (
                  <span key={idx} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-900 text-slate-400 border border-slate-800">
                    {l}
                  </span>
                ))}
              </div>
            )}
          </div>

          <h3 className="text-xl font-bold text-white tracking-tight">
            {jiraData?.summary || "Add System Health & Diagnostics Tab to Navigations"}
          </h3>
        </div>

        {/* Live Jira Description Box */}
        <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4 space-y-2">
          <div className="flex items-center justify-between text-xs text-slate-400 font-mono">
            <span className="font-semibold text-slate-300">Live Ticket Specification (from Jira):</span>
            <span className="text-[11px] text-slate-500">Read dynamically by Meeseek</span>
          </div>

          <div className="text-xs font-mono text-slate-300 bg-slate-900/80 p-3.5 rounded-lg border border-slate-800/80 max-h-48 overflow-y-auto whitespace-pre-wrap leading-relaxed select-text">
            {loadingJira ? (
              <div className="flex items-center space-x-2 text-slate-500 py-2">
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                <span>Fetching live description from Jira Cloud...</span>
              </div>
            ) : jiraData?.description ? (
              jiraData.description
            ) : (
              <span className="text-slate-500 italic">
                No description found on Jira or ticket not yet created. Meeseek will read the latest description from Jira when struck.
              </span>
            )}
          </div>
        </div>

        {/* Autonomous Pipeline Guarantee Badges */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 pt-2 text-xs">
          <div className="p-3 rounded-xl bg-slate-900/60 border border-slate-800 flex items-start space-x-2.5">
            <Eye className="w-4 h-4 text-purple-400 flex-shrink-0 mt-0.5" />
            <div>
              <div className="font-semibold text-white">Live HTTPS Preview</div>
              <div className="text-[11px] text-slate-400 mt-0.5">Spins up Vite dev server reverse-proxied with Caddy HTTPS</div>
            </div>
          </div>

          <div className="p-3 rounded-xl bg-slate-900/60 border border-slate-800 flex items-start space-x-2.5">
            <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0 mt-0.5" />
            <div>
              <div className="font-semibold text-white">Host Notary Proof</div>
              <div className="text-[11px] text-slate-400 mt-0.5">Strict Exit 0 test gate executed outside the LLM context</div>
            </div>
          </div>

          <div className="p-3 rounded-xl bg-slate-900/60 border border-slate-800 flex items-start space-x-2.5">
            <GitPullRequest className="w-4 h-4 text-cyan-400 flex-shrink-0 mt-0.5" />
            <div>
              <div className="font-semibold text-white">1-Click PR Delivery</div>
              <div className="text-[11px] text-slate-400 mt-0.5">Approve directly in console or comment on Jira to ship PR</div>
            </div>
          </div>
        </div>

        {/* Action Button & Navigation */}
        <div className="pt-4 border-t border-slate-800 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center space-x-2 w-full sm:w-auto">
            <label className="text-xs font-mono text-slate-400 whitespace-nowrap">Ticket Key:</label>
            <input
              type="text"
              value={ticketKey}
              onChange={(e) => setTicketKey(e.target.value.toUpperCase())}
              placeholder="e.g. FSA-32"
              className="px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs font-mono font-bold text-white focus:outline-none focus:border-cyan-400 w-32 uppercase"
            />
          </div>

          <div className="flex items-center space-x-3 w-full sm:w-auto justify-end">
            {lastTriggeredTicket === ticketKey && onNavigateToWorkspaces && (
              <button
                onClick={() => onNavigateToWorkspaces(ticketKey)}
                className="py-2.5 px-4 rounded-xl bg-slate-800 hover:bg-slate-700 text-cyan-300 font-semibold text-xs flex items-center space-x-2 border border-slate-700 transition-colors shadow-md"
              >
                <span>View Live DAG Stepper</span>
                <ArrowRight className="w-3.5 h-3.5" />
              </button>
            )}

            <button
              onClick={handleTrigger}
              disabled={isTriggering || !ticketKey.trim()}
              className="py-2.5 px-6 rounded-xl bg-gradient-to-r from-purple-600 to-cyan-600 hover:from-purple-500 hover:to-cyan-500 text-white font-bold text-xs flex items-center justify-center space-x-2 shadow-lg shadow-purple-500/20 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isTriggering ? (
                <>
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  <span>Summoning Meeseek for {ticketKey}...</span>
                </>
              ) : (
                <>
                  <Play className="w-3.5 h-3.5 fill-current" />
                  <span>Strike Meeseek for {ticketKey}</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
