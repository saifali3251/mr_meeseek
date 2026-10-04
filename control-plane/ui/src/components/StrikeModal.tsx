import React, { useState, useEffect } from "react";
import { X, Play, AlertCircle, CheckCircle2, AlertTriangle, Loader2 } from "lucide-react";
import { strikeEnvironment, verifyJiraTicket, JiraVerifyResult } from "../api";

interface StrikeModalProps {
  isOpen: boolean;
  onClose: () => void;
  apps: string[];
  defaultApp: string | null;
  onSuccess: () => void;
  jira?: {
    connected: boolean;
    base_url: string;
    project: string;
    webhook_mode?: boolean;
  };
}

export const StrikeModal: React.FC<StrikeModalProps> = ({
  isOpen,
  onClose,
  apps,
  defaultApp,
  onSuccess,
  jira,
}) => {
  const [ticket, setTicket] = useState("");
  const [selectedApp, setSelectedApp] = useState(defaultApp || (apps[0] ?? "full-stack-application"));
  const [isStriking, setIsStriking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Verification state
  const [isVerifying, setIsVerifying] = useState(false);
  const [verifyResult, setVerifyResult] = useState<JiraVerifyResult | null>(null);

  // Reset when modal opens/closes
  useEffect(() => {
    if (!isOpen) {
      setTicket("");
      setError(null);
      setVerifyResult(null);
      setIsVerifying(false);
    }
  }, [isOpen]);

  // Debounced Jira ticket verification
  useEffect(() => {
    const trimmed = ticket.trim().toUpperCase();
    if (!trimmed || !jira?.connected) {
      setVerifyResult(null);
      setIsVerifying(false);
      return;
    }

    // Only verify if looks like a ticket key (e.g. FSA-15)
    if (!/^[A-Za-z]+-\d+$/.test(trimmed)) {
      setVerifyResult(null);
      return;
    }

    const timer = setTimeout(async () => {
      setIsVerifying(true);
      try {
        const res = await verifyJiraTicket(trimmed);
        setVerifyResult(res);
      } catch {
        setVerifyResult(null);
      } finally {
        setIsVerifying(false);
      }
    }, 450);

    return () => clearTimeout(timer);
  }, [ticket, jira?.connected]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ticket.trim()) return;
    setIsStriking(true);
    setError(null);
    try {
      await strikeEnvironment(ticket.trim().toUpperCase(), selectedApp, 3600);
      setTicket("");
      onSuccess();
      onClose();
    } catch (err: any) {
      setError(err.message || "Failed to strike workspace");
    } finally {
      setIsStriking(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm">
      <div className="bg-meeseek-900 border border-meeseek-border rounded-2xl w-full max-w-md p-6 shadow-2xl relative">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-slate-400 hover:text-white"
        >
          <X className="w-5 h-5" />
        </button>

        <h3 className="text-base font-bold text-white flex items-center space-x-2">
          <span>Strike an Ephemeral Environment</span>
        </h3>
        <p className="text-xs text-slate-400 mt-1">
          Leases a fresh, warm, Copy-on-Write workspace cloned in under 300ms.
        </p>

        {/* Jira Connection Status Banner */}
        {jira?.connected ? (
          <div className="mt-3 px-3 py-2 rounded-xl bg-emerald-500/10 border border-emerald-500/25 flex items-center justify-between text-xs font-mono">
            <span className="flex items-center space-x-2 text-emerald-400">
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
              <span>Jira Connected ({jira.project || "FSA"})</span>
            </span>
            <span className="text-[11px] text-emerald-500/80 font-sans">Syncs comments & PR</span>
          </div>
        ) : (
          <div className="mt-3 px-3 py-2 rounded-xl bg-slate-800/80 border border-slate-700/80 flex items-center justify-between text-xs font-mono">
            <span className="flex items-center space-x-2 text-slate-400">
              <span className="w-2 h-2 rounded-full bg-slate-500" />
              <span>Standalone Mode</span>
            </span>
            <span className="text-[11px] text-slate-400 font-sans">Isolated local sandbox</span>
          </div>
        )}

        {error && (
          <div className="mt-4 p-3 rounded-lg bg-red-950/50 border border-red-500/30 text-red-300 text-xs flex items-center space-x-2">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="mt-5 space-y-4">
          <div>
            <label className="text-xs text-slate-400 block mb-1">Target Application</label>
            <select
              value={selectedApp}
              onChange={(e) => setSelectedApp(e.target.value)}
              className="w-full bg-meeseek-950 border border-meeseek-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-cyan-500"
            >
              {apps.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </div>

          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs text-slate-400">Jira Ticket Key</label>
              {isVerifying && (
                <span className="flex items-center space-x-1 text-[11px] font-mono text-cyan-400">
                  <Loader2 className="w-3 h-3 animate-spin" />
                  <span>Verifying Jira...</span>
                </span>
              )}
            </div>

            <input
              type="text"
              required
              value={ticket}
              onChange={(e) => setTicket(e.target.value)}
              placeholder="e.g. FSA-15"
              className="w-full uppercase font-mono bg-meeseek-950 border border-meeseek-border rounded-xl px-3 py-2 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-cyan-500"
            />

            {/* Jira Verification Feedback */}
            {verifyResult && verifyResult.exists === true && (
              <div className="mt-2 p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-xs flex items-start space-x-2">
                <CheckCircle2 className="w-4 h-4 flex-shrink-0 mt-0.5 text-emerald-400" />
                <div className="min-w-0">
                  <span className="font-semibold">{verifyResult.ticket}:</span>{" "}
                  <span className="text-emerald-200/90">{verifyResult.summary || "Found on Jira"}</span>
                </div>
              </div>
            )}

            {verifyResult && verifyResult.exists === false && (
              <div className="mt-2 p-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300 text-xs flex items-start space-x-2">
                <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5 text-amber-400" />
                <div>
                  <span>Ticket <strong>{verifyResult.ticket}</strong> not found in Jira project. Striking will proceed in standalone mode.</span>
                </div>
              </div>
            )}
          </div>

          <div className="pt-2 flex justify-end space-x-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isStriking || !ticket.trim()}
              className="px-5 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white font-medium text-xs flex items-center space-x-1.5 shadow-lg shadow-cyan-500/20 disabled:opacity-50"
            >
              <Play className="w-3.5 h-3.5 fill-current" />
              <span>{isStriking ? "Striking (CoW)..." : "Strike Environment"}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
