import React, { useState } from "react";
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
  Terminal
} from "lucide-react";
import { strikeEnvironment } from "../api";
import judgeMeeseek from "../assets/judge-meeseek.webp";

interface JudgePlaygroundProps {
  onRefresh: () => void;
  jiraBaseUrl: string;
}

export const JudgePlayground: React.FC<JudgePlaygroundProps> = ({
  onRefresh,
  jiraBaseUrl,
}) => {
  const [triggeringTicket, setTriggeringTicket] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  const handleTrigger = async (ticket: string) => {
    setTriggeringTicket(ticket);
    setStatusMessage(null);
    try {
      await strikeEnvironment(ticket, "full-stack-application", 3600);
      setStatusMessage(`🚀 Struck isolated Copy-on-Write workspace for ${ticket}! Switch to the Workspaces tab to watch the real-time DAG stepper.`);
      onRefresh();
    } catch (err: any) {
      setStatusMessage(`Error: ${err.message}`);
    } finally {
      setTriggeringTicket(null);
    }
  };

  return (
    <div className="space-y-8">
      {/* Hero Evaluator Banner */}
      <div className="glass-panel rounded-2xl p-8 md:pr-[300px] md:min-h-[420px] md:flex md:flex-col md:justify-center border border-meeseek-border bg-gradient-to-br from-meeseek-900/90 via-meeseek-850/80 to-cyan-950/20 shadow-2xl relative overflow-hidden">
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
          No environment setup, no Docker builds, no API tokens required. Click any curated demo card below to watch Meeseek strike a 300ms Copy-on-Write sandbox, dispatch coding tasks, and certify a GitHub Pull Request via independent Host Notary proof!
        </p>

        {statusMessage && (
          <div className="mt-4 p-4 rounded-xl bg-cyan-950/60 border border-cyan-500/40 text-cyan-200 text-xs flex items-center space-x-2">
            <CheckCircle2 className="w-4 h-4 flex-shrink-0 text-cyan-400" />
            <span>{statusMessage}</span>
          </div>
        )}
      </div>

      {/* 3 Interactive Demo Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* DEMO 1: Bug Fix & Exit 0 Notary */}
        <div className="glass-panel-interactive rounded-2xl p-6 border border-meeseek-border flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-mono font-bold px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-300 border border-cyan-500/30">
                DEMO 1 · SPEED & PROOF
              </span>
              <span className="text-[10px] text-slate-500 font-mono">~35s Turnaround</span>
            </div>

            <h3 className="text-base font-bold text-white mt-3">
              Fast Autonomous Bug Fix (Exit 0 Proof)
            </h3>
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
        <div className="glass-panel-interactive rounded-2xl p-6 border border-meeseek-border flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-mono font-bold px-2 py-0.5 rounded bg-purple-500/10 text-purple-300 border border-purple-500/30">
                DEMO 2 · VISUAL SANDBOX
              </span>
              <span className="text-[10px] text-slate-500 font-mono">Live HTTPS Preview</span>
            </div>

            <h3 className="text-base font-bold text-white mt-3">
              Full-Stack UI Feature with Live Preview
            </h3>
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
        <div className="glass-panel-interactive rounded-2xl p-6 border border-meeseek-border flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-mono font-bold px-2 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/30">
                DEMO 3 · SAFETY MANDATE
              </span>
              <span className="text-[10px] text-slate-500 font-mono">Plan-First Mode</span>
            </div>

            <h3 className="text-base font-bold text-white mt-3">
              Plan-Only Mode & AST Safety Guard
            </h3>
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
  );
};

