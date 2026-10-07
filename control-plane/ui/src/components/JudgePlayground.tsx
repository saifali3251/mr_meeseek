import React, { useState, useEffect } from "react";
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
  Layers,
  Check
} from "lucide-react";
import { triggerTask, verifyJiraTicket, JiraVerifyResult } from "../api";
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
  const [jiraData, setJiraData] = useState<JiraVerifyResult | null>(null);
  const [loadingJira, setLoadingJira] = useState<boolean>(true);
  const [isTriggering, setIsTriggering] = useState<boolean>(false);
  const [lastTriggeredTicket, setLastTriggeredTicket] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<{
    type: "success" | "queued" | "error";
    text: string;
    ticket?: string;
  } | null>(null);

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
    loadJiraDetails(ticketKey);
  }, [ticketKey]);

  // Extract repo from Jira description if formatted as `Repo: <name>`
  const extractedRepo = React.useMemo(() => {
    if (!jiraData?.description) return null;
    const match = jiraData.description.match(/Repo:\s*([a-zA-Z0-9_\-]+)/i);
    return match ? match[1] : null;
  }, [jiraData?.description]);

  const handleTrigger = async () => {
    const cleanTicket = ticketKey.trim().toUpperCase();
    if (!cleanTicket) return;

    setIsTriggering(true);
    setStatusMessage(null);
    try {
      // Prompt and requirements come directly from Jira Cloud via the backend Jira bridge
      const res = await triggerTask(cleanTicket);
      setLastTriggeredTicket(cleanTicket);

      if (res?.task?.status === "queued" || res?.status === "queued") {
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
    <div className="space-y-8 max-w-5xl mx-auto">
      {/* Hero Evaluator Banner */}
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
          <div className={`mt-5 p-4 rounded-xl text-xs flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 border ${
            statusMessage.type === "error"
              ? "bg-red-950/60 border-red-500/40 text-red-200"
              : statusMessage.type === "queued"
              ? "bg-amber-950/60 border-amber-500/40 text-amber-200"
              : "bg-cyan-950/60 border-cyan-500/40 text-cyan-200"
          }`}>
            <div className="flex items-center space-x-2">
              {statusMessage.type === "error" ? (
                <ShieldAlert className="w-4 h-4 flex-shrink-0 text-red-400" />
              ) : statusMessage.type === "queued" ? (
                <Clock className="w-4 h-4 flex-shrink-0 text-amber-400" />
              ) : (
                <CheckCircle2 className="w-4 h-4 flex-shrink-0 text-cyan-400" />
              )}
              <span>{statusMessage.text}</span>
            </div>
            {statusMessage.ticket && onNavigateToWorkspaces && (
              <button
                onClick={() => onNavigateToWorkspaces(statusMessage.ticket)}
                className="px-3.5 py-1.5 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white font-semibold text-xs flex items-center space-x-1.5 transition-all shadow-md whitespace-nowrap self-end sm:self-auto"
              >
                <span>View Live DAG Stepper</span>
                <ArrowRight className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        )}
      </div>

      {/* Featured Single Interactive Demo Card */}
      <div className="glass-panel rounded-2xl p-7 border border-meeseek-border bg-meeseek-850/60 shadow-xl space-y-6">
        {/* Card Header & Sync Status */}
        <div className="flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-slate-800">
          <div className="flex items-center space-x-3">
            <span className="text-xs font-mono font-bold px-2.5 py-1 rounded bg-purple-500/10 text-purple-300 border border-purple-500/30">
              FEATURED EVALUATION DEMO
            </span>
            <span className="text-xs font-mono text-slate-400 flex items-center space-x-1.5">
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
            {jiraData?.summary || "Add Real-time System Telemetry & Insights Tab to Dashboard"}
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
