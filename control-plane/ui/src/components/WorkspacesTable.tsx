import React, { useState } from "react";
import { 
  GitPullRequest, 
  ExternalLink, 
  Trash2, 
  Terminal, 
  Check, 
  Clock, 
  ChevronDown, 
  ChevronUp,
  Activity,
  Globe
} from "lucide-react";
import { Lease, TaskRecord } from "../types";
import { WorkflowDAGStepper } from "./WorkflowDAGStepper";

interface WorkspacesTableProps {
  leases: Lease[];
  tasks: TaskRecord[];
  jiraBaseUrl: string;
  selectedLeaseId: string | null;
  onSelectLease: (leaseId: string) => void;
  onDestroyLease: (leaseId: string) => void;
  isDestroying: boolean;
  onExtendLease: (leaseId: string) => void;
  isExtending?: boolean;
  now?: number;
}

function formatTTL(expiresAt?: number, now: number = Date.now() / 1000): { text: string; color: "green" | "amber" | "red" } {
  if (!expiresAt) return { text: "No limit", color: "green" };
  const secs = Math.floor(expiresAt - now);
  if (secs <= 0) return { text: "Expired", color: "red" };
  const mins = Math.floor(secs / 60);
  const remainingSecs = secs % 60;
  if (mins < 10) return { text: `${mins}m ${remainingSecs}s`, color: "amber" };
  return { text: `${mins}m left`, color: "green" };
}

export const WorkspacesTable: React.FC<WorkspacesTableProps> = ({
  leases,
  tasks,
  jiraBaseUrl,
  selectedLeaseId,
  onSelectLease,
  onDestroyLease,
  isDestroying,
  onExtendLease,
  isExtending = false,
  now = Date.now() / 1000,
}) => {
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [expandedLeaseId, setExpandedLeaseId] = useState<string | null>(() => {
    return leases.length > 0 ? leases[0].lease_id : null;
  });

  const toggleExpand = (leaseId: string) => {
    if (expandedLeaseId === leaseId) {
      setExpandedLeaseId(null);
    } else {
      setExpandedLeaseId(leaseId);
      onSelectLease(leaseId);
    }
  };

  const copyCommand = (ticket: string, id: string) => {
    const cmd = `docker compose -p ws-${ticket.toLowerCase()} exec backend bash`;
    navigator.clipboard.writeText(cmd);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const getTaskForLease = (lease: Lease) => {
    return tasks.find(
      (t) => t.lease_id === lease.lease_id || t.ticket === lease.ticket
    );
  };

  if (leases.length === 0) {
    return (
      <div className="glass-panel rounded-2xl p-12 text-center border border-meeseek-border">
        <div className="w-12 h-12 rounded-full bg-slate-900 flex items-center justify-center mx-auto mb-4 text-slate-400">
          <Activity className="w-6 h-6 text-cyan-400" />
        </div>
        <h3 className="text-base font-semibold text-white">No Active Workspaces</h3>
        <p className="text-sm text-slate-400 mt-1 max-w-sm mx-auto">
          Add the <span className="font-mono text-cyan-400 font-semibold">meeseek</span> label to any Jira ticket or click <span className="text-white font-semibold">Strike Workspace</span> to spawn an isolated sandbox.
        </p>
      </div>
    );
  }

  return (
    <div className="glass-panel rounded-2xl border border-meeseek-border overflow-hidden shadow-2xl">
      <div className="px-6 py-4 border-b border-meeseek-border flex items-center justify-between bg-meeseek-950/60">
        <div>
          <h3 className="text-lg font-bold text-white flex items-center space-x-2.5">
            <span>Live Ephemeral Workspaces</span>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-mono font-bold bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
              {leases.length} Active
            </span>
          </h3>
          <p className="text-sm text-slate-400 mt-0.5">
            Click any row to expand its live 6-step Autonomous Execution DAG & inspect runtime artifacts
          </p>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="bg-meeseek-950/90 text-slate-400 uppercase font-mono text-xs tracking-wider border-b border-meeseek-border">
            <tr>
              <th className="py-3.5 px-6">Ticket / Workspace</th>
              <th className="py-3.5 px-4">Target App</th>
              <th className="py-3.5 px-4">State & Stage</th>
              <th className="py-3.5 px-4">Live Preview</th>
              <th className="py-3.5 px-4">Pull Request</th>
              <th className="py-3.5 px-4">TTL / Auto-Destroy</th>
              <th className="py-3.5 px-6 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-meeseek-border">
            {leases.map((lease) => {
              const task = getTaskForLease(lease);
              const isExpanded = expandedLeaseId === lease.lease_id;
              const jiraLink = jiraBaseUrl ? `${jiraBaseUrl.replace(/\/$/, "")}/browse/${lease.ticket}` : null;

              return (
                <React.Fragment key={lease.lease_id}>
                  {/* Workspace Summary Row */}
                  <tr
                    onClick={() => toggleExpand(lease.lease_id)}
                    className={`cursor-pointer transition-colors ${
                      isExpanded
                        ? "bg-meeseek-900/90 border-l-4 border-l-cyan-400"
                        : "hover:bg-meeseek-900/50"
                    }`}
                  >
                    {/* Ticket & Lease ID */}
                    <td className="py-4 px-6">
                      <div className="flex items-center space-x-3">
                        <div className="text-slate-400 hover:text-cyan-400 transition-transform">
                          {isExpanded ? (
                            <ChevronUp className="w-4 h-4 text-cyan-400" />
                          ) : (
                            <ChevronDown className="w-4 h-4 text-slate-500" />
                          )}
                        </div>
                        <div>
                          <div className="flex items-center space-x-2">
                            {jiraLink ? (
                              <a
                                href={jiraLink}
                                target="_blank"
                                rel="noreferrer"
                                onClick={(e) => e.stopPropagation()}
                                className="font-bold text-white text-sm hover:text-cyan-400 hover:underline flex items-center space-x-1"
                              >
                                <span>{lease.ticket}</span>
                                <ExternalLink className="w-3.5 h-3.5 text-slate-500" />
                              </a>
                            ) : (
                              <span className="font-bold text-white text-sm">{lease.ticket}</span>
                            )}
                          </div>
                          <div className="text-xs text-slate-400 font-mono mt-0.5">
                            {lease.lease_id}
                          </div>
                        </div>
                      </div>
                    </td>

                    {/* Target Repo */}
                    <td className="py-4 px-4 font-mono text-slate-300 text-xs">
                      <span className="px-2.5 py-1 rounded bg-slate-900 border border-slate-800 font-medium">
                        {lease.target_repo || lease.app}
                      </span>
                    </td>

                    {/* State Badge */}
                    <td className="py-4 px-4">
                      {lease.pr_url || task?.evidence?.pr_url ? (
                        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 mr-1.5"></span>
                          PR Delivered (Exit 0)
                        </span>
                      ) : task?.halted || task?.workflow_state === "HALTED" || task?.workflow_state === "WAITING_INPUT" ? (
                        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-500/15 text-amber-300 border border-amber-500/40 shadow-sm shadow-amber-500/10">
                          <span className="relative flex h-2 w-2 mr-2">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
                            <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-400"></span>
                          </span>
                          Action Required: Awaiting Review
                        </span>
                      ) : task?.workflow_state === "CODING" ? (
                        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-purple-500/10 text-purple-300 border border-purple-500/30">
                          <span className="w-1.5 h-1.5 rounded-full bg-purple-400 mr-1.5 animate-pulse"></span>
                          Agent Coding Turn
                        </span>
                      ) : (
                        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-800 text-slate-300 border border-slate-700">
                          <span className="w-1.5 h-1.5 rounded-full bg-slate-400 mr-1.5"></span>
                          {lease.status.toUpperCase()}
                        </span>
                      )}
                    </td>

                    {/* Live Preview Link */}
                    <td className="py-4 px-4">
                      {lease.preview_url ? (
                        <a
                          href={lease.preview_url}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="inline-flex items-center space-x-1.5 text-cyan-400 hover:text-cyan-300 hover:underline font-mono text-xs font-medium"
                        >
                          <Globe className="w-3.5 h-3.5" />
                          <span>Port {lease.preview_port || 18000}</span>
                          <ExternalLink className="w-3 h-3" />
                        </a>
                      ) : (
                        <span className="text-slate-500 font-mono text-xs">Port {lease.preview_port || 18000}</span>
                      )}
                    </td>

                    {/* GitHub PR Link */}
                    <td className="py-4 px-4">
                      {lease.pr_url || task?.evidence?.pr_url ? (
                        <a
                          href={lease.pr_url || task?.evidence?.pr_url}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="inline-flex items-center space-x-1.5 text-emerald-400 hover:text-emerald-300 hover:underline font-medium text-xs"
                        >
                          <GitPullRequest className="w-3.5 h-3.5" />
                          <span>PR #1</span>
                          <ExternalLink className="w-3 h-3" />
                        </a>
                      ) : (
                        <span className="text-slate-500 italic text-xs">Pending Finalize</span>
                      )}
                    </td>

                    {/* TTL Remaining & Extend Button */}
                    <td className="py-4 px-4 font-mono text-xs" onClick={(e) => e.stopPropagation()}>
                      {(() => {
                        const ttl = formatTTL(lease.expires_at, now);
                        return (
                          <div className="flex items-center space-x-2">
                            <span
                              className={`inline-flex items-center px-2.5 py-1 rounded text-xs font-semibold ${
                                ttl.color === "red"
                                  ? "bg-red-500/10 text-red-400 border border-red-500/30"
                                  : ttl.color === "amber"
                                  ? "bg-amber-500/10 text-amber-300 border border-amber-500/30 font-bold animate-pulse"
                                  : "bg-slate-900 text-slate-300 border border-slate-800"
                              }`}
                            >
                              <Clock className="w-3.5 h-3.5 mr-1" />
                              <span>{ttl.text}</span>
                            </span>

                            <button
                              onClick={() => onExtendLease(lease.lease_id)}
                              disabled={isExtending}
                              title="Extend workspace by +30 minutes"
                              className="px-2.5 py-1 rounded bg-cyan-600/20 hover:bg-cyan-600/40 text-cyan-400 hover:text-cyan-300 border border-cyan-500/30 text-xs font-bold font-mono transition-all"
                            >
                              +30m
                            </button>
                          </div>
                        );
                      })()}
                    </td>

                    {/* Actions */}
                    <td className="py-4 px-6 text-right" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center justify-end space-x-2">
                        {/* Copy Command */}
                        <button
                          onClick={() => copyCommand(lease.ticket, lease.lease_id)}
                          title="Copy Docker Exec command"
                          className="p-2 rounded-lg bg-slate-900 hover:bg-slate-800 text-slate-300 transition-colors border border-slate-800"
                        >
                          {copiedId === lease.lease_id ? (
                            <Check className="w-4 h-4 text-emerald-400" />
                          ) : (
                            <Terminal className="w-4 h-4" />
                          )}
                        </button>

                        {/* Destroy Workspace Button */}
                        <button
                          onClick={() => onDestroyLease(lease.lease_id)}
                          disabled={isDestroying}
                          title="Destroy workspace immediately"
                          className="p-2 rounded-lg bg-red-950/40 hover:bg-red-900/60 text-red-400 border border-red-500/20 hover:border-red-500/40 transition-colors"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>

                  {/* CONCEPT A: INLINE EXPANDABLE ACCORDION ROW */}
                  {isExpanded && (
                    <tr className="bg-meeseek-950/95 animate-fadeIn">
                      <td colSpan={7} className="p-4 sm:p-6 border-b border-meeseek-border">
                        <WorkflowDAGStepper
                          lease={lease}
                          task={task}
                          jiraBaseUrl={jiraBaseUrl}
                        />
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};
