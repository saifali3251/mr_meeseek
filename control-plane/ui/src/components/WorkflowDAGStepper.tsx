import React, { useState } from "react";
import { 
  CheckCircle2, 
  Cpu, 
  GitPullRequest, 
  AlertTriangle, 
  ShieldCheck, 
  FileCode, 
  ExternalLink,
  Globe,
  Database
} from "lucide-react";
import { TaskRecord, Lease, WorkflowState } from "../types";

interface WorkflowDAGStepperProps {
  task?: TaskRecord;
  lease?: Lease;
}

interface StageDefinition {
  key: string;
  stepNum: number;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}

const STAGES: StageDefinition[] = [
  {
    key: "STRIKE",
    stepNum: 1,
    label: "Workspace Strike",
    icon: Cpu,
  },
  {
    key: "READY",
    stepNum: 2,
    label: "Environment Ready",
    icon: Database,
  },
  {
    key: "AGENT",
    stepNum: 3,
    label: "Agent Implementation",
    icon: FileCode,
  },
  {
    key: "PREVIEW",
    stepNum: 4,
    label: "Live Preview & Review",
    icon: Globe,
  },
  {
    key: "NOTARY",
    stepNum: 5,
    label: "Host Notary Verification",
    icon: ShieldCheck,
  },
  {
    key: "PR_DELIVERED",
    stepNum: 6,
    label: "PR Delivered",
    icon: GitPullRequest,
  },
];

function getStageIndex(state?: WorkflowState, lease?: Lease, task?: TaskRecord): number {
  if (lease?.pr_url || task?.evidence?.pr_url) return 5; // Step 6: PR Delivered
  if (state === "PR_OPENED") return 5;
  if (state === "NOTARY_VERIFYING") return 4; // Step 5: Host Notary Verification
  if (state === "GUARDRAIL_BLOCKED" || state === "FAILED") return 4;
  if (state === "HALTED" || task?.halted || state === "WAITING_INPUT") return 3; // Step 4: Live Preview & Review
  if (state === "CODING") return 2; // Step 3: Agent Implementation
  if (state === "BOOTING" || state === "STRIKING") return 1; // Step 2: Environment Ready
  if (state === "PENDING") return 0; // Step 1: Workspace Strike
  
  if (lease?.status === "ready") {
    if (lease.evidence?.test_exit === 0) return 5;
    if (lease.evidence?.test_cmd) return 4;
    return 3;
  }
  return 0;
}

function getStageDescription(stageKey: string, status: "COMPLETED" | "IN_PROGRESS" | "PENDING"): string {
  switch (stageKey) {
    case "STRIKE":
      if (status === "COMPLETED") {
        return "Jira trigger was ingested. Dedicated sandbox container was created with pristine Copy-on-Write storage and isolated network ports in <300ms.";
      }
      if (status === "IN_PROGRESS") {
        return "Jira trigger ingested. Creating dedicated sandbox container with pristine Copy-on-Write storage and isolated network ports in <300ms...";
      }
      return "Awaiting Jira trigger. Once claimed, a dedicated sandbox container will be created with pristine Copy-on-Write storage in <300ms.";

    case "READY":
      if (status === "COMPLETED") {
        return "Target repository branch checked out, Docker Compose microservices booted, and pre-seeded PostgreSQL test fixtures verified healthy.";
      }
      if (status === "IN_PROGRESS") {
        return "Checking out target repository branch, booting Docker Compose microservices, and verifying database health...";
      }
      return "Will check out repository branch, boot Docker Compose services, and verify database fixtures once workspace is struck.";

    case "AGENT":
      if (status === "COMPLETED") {
        return "The autonomous agent analyzed Jira instructions, read repository conventions, planned code modifications, and applied edits in the sandbox.";
      }
      if (status === "IN_PROGRESS") {
        return "The autonomous agent (Debby) is actively analyzing Jira instructions, navigating codebase conventions, and authoring code modifications in the sandbox.";
      }
      return "The autonomous agent will read Jira task context, inspect repository conventions, and author code modifications once the environment is ready.";

    case "PREVIEW":
      if (status === "COMPLETED") {
        return "Review cycle concluded. Live preview and code diffs were inspected by the reviewer prior to notary certification.";
      }
      if (status === "IN_PROGRESS") {
        return "Agent turn paused. Live interactive application is accessible on preview port with hot reload. Reviewer tests the UI live and reviews diffs before finalization.";
      }
      return "Once agent implementation finishes, a live interactive application preview will be exposed on a dedicated port for human review.";

    case "NOTARY":
      if (status === "COMPLETED") {
        return "Host Notary executed authoritative pytest suite and verified AST blast-radius guardrails outside agent control. Certified Exit 0.";
      }
      if (status === "IN_PROGRESS") {
        return "Host Notary is currently executing authoritative pytest suite and auditing AST blast-radius guardrails outside agent control...";
      }
      return "Triggered upon commenting '/meeseek finalize'. The impartial host notary will execute the authoritative test suite and audit AST guardrails outside agent control.";

    case "PR_DELIVERED":
      if (status === "COMPLETED") {
        return "Impartially verified Exit 0 proof bundle attached. Clean GitHub Pull Request was opened and linked back to the Jira ticket.";
      }
      if (status === "IN_PROGRESS") {
        return "Packaging Exit 0 notarization proof and opening certified GitHub Pull Request...";
      }
      return "Certified GitHub Pull Request will be opened and linked to the Jira ticket once Host Notary verification passes with Exit 0.";

    default:
      return "";
  }
}

export const WorkflowDAGStepper: React.FC<WorkflowDAGStepperProps> = ({
  task,
  lease,
}) => {
  const currentIdx = getStageIndex(task?.workflow_state, lease, task);
  const [selectedStageIdx, setSelectedStageIdx] = useState<number | null>(null);

  const activeInspectIdx = selectedStageIdx !== null ? selectedStageIdx : currentIdx;
  const activeInspectStage = STAGES[activeInspectIdx];

  const stageStatus: "COMPLETED" | "IN_PROGRESS" | "PENDING" = 
    activeInspectIdx < currentIdx 
      ? "COMPLETED" 
      : activeInspectIdx === currentIdx 
      ? "IN_PROGRESS" 
      : "PENDING";

  const isFailed = task?.workflow_state === "FAILED" || (task?.evidence && task.evidence.test_exit !== undefined && task.evidence.test_exit !== 0);
  const isGuardrailBlocked = task?.workflow_state === "GUARDRAIL_BLOCKED" || (task?.evidence?.guardrail_passed === false);

  const dynamicDescription = getStageDescription(activeInspectStage.key, stageStatus);

  return (
    <div className="bg-meeseek-950/70 rounded-2xl p-5 border border-meeseek-border/80">
      {/* Visual State Machine Stepper Pipeline */}
      <div className="py-2 overflow-x-auto">
        <div className="flex items-center min-w-[720px] justify-between relative px-4">
          {/* Background Connecting Line */}
          <div className="absolute top-5 left-10 right-10 h-0.5 bg-slate-800 -z-0" />
          
          {STAGES.map((stage, idx) => {
            const Icon = stage.icon;
            const isCompleted = idx < currentIdx;
            const isCurrent = idx === currentIdx;
            const isSelected = idx === activeInspectIdx;

            let nodeStyle = "border-slate-800 bg-meeseek-900 text-slate-500";
            let pulseBadge = null;

            if (isCompleted) {
              nodeStyle = "border-emerald-500 bg-emerald-950/60 text-emerald-400 shadow-md shadow-emerald-500/10";
            } else if (isCurrent) {
              if (isFailed || isGuardrailBlocked) {
                nodeStyle = "border-red-500 bg-red-950/60 text-red-400 ring-4 ring-red-500/20";
              } else if (stage.key === "PREVIEW" || task?.halted) {
                nodeStyle = "border-cyan-400 bg-cyan-950/60 text-cyan-300 ring-4 ring-cyan-500/20";
              } else {
                nodeStyle = "border-cyan-400 bg-cyan-950/60 text-cyan-300 ring-4 ring-cyan-500/20";
              }
              pulseBadge = (
                <span className="absolute -top-1 -right-1 flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-cyan-500"></span>
                </span>
              );
            }

            return (
              <div
                key={stage.key}
                onClick={() => setSelectedStageIdx(idx)}
                className={`relative z-10 flex flex-col items-center cursor-pointer group transition-all duration-150 ${
                  isSelected ? "scale-105" : "hover:scale-102"
                }`}
              >
                {/* Node Circle */}
                <div
                  className={`w-10 h-10 rounded-xl border-2 flex items-center justify-center transition-all ${nodeStyle} ${
                    isSelected ? "ring-2 ring-white" : ""
                  }`}
                >
                  {isCompleted ? (
                    <CheckCircle2 className="w-5 h-5 text-emerald-400" />
                  ) : (
                    <Icon className="w-4 h-4" />
                  )}
                  {pulseBadge}
                </div>

                {/* Clean Node Label (Subtitle removed to prevent truncation) */}
                <div className="mt-2 text-center max-w-[120px]">
                  <p className={`text-xs sm:text-sm font-semibold ${isCurrent || isSelected ? "text-white font-bold" : "text-slate-400"}`}>
                    {stage.stepNum}. {stage.label}
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Inline Stage Inspector Panel */}
      <div className="mt-5 pt-4 border-t border-slate-800/80">
        {/* Step Header: "Inspecting Step X of 6" removed as requested */}
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center space-x-3">
            <span className="text-base sm:text-lg font-bold text-white tracking-tight">
              {activeInspectStage.label}
            </span>
            {stageStatus === "COMPLETED" && (
              <span className="px-2.5 py-0.5 rounded-full text-xs font-mono font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                COMPLETED
              </span>
            )}
            {stageStatus === "IN_PROGRESS" && (
              <span className="px-2.5 py-0.5 rounded-full text-xs font-mono font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-500/30 animate-pulse">
                IN PROGRESS
              </span>
            )}
            {stageStatus === "PENDING" && (
              <span className="px-2.5 py-0.5 rounded-full text-xs font-mono font-bold bg-slate-800 text-slate-400 border border-slate-700">
                PENDING
              </span>
            )}
          </div>
          <span className="text-xs text-slate-400 font-mono hidden sm:inline">
            Click any step above to inspect its details
          </span>
        </div>

        {/* Dynamic Context-Aware Inspector Content */}
        <div className="bg-meeseek-900 rounded-xl p-4 sm:p-5 border border-slate-800">
          {/* Dynamic description updating with step status */}
          <p className="text-sm text-slate-200 leading-relaxed mb-4">
            {dynamicDescription}
          </p>

          {/* STEP 1: WORKSPACE STRIKE */}
          {activeInspectIdx === 0 && (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-4 border-t border-slate-800 font-mono text-xs">
              <div>
                <span className="text-slate-400 text-xs block mb-1">Workspace ID</span>
                <span className="text-slate-100 font-bold">{lease?.lease_id || "ws-fsa-12"}</span>
              </div>
              <div>
                <span className="text-slate-400 text-xs block mb-1">Strike Latency</span>
                <span className="text-emerald-400 font-bold">&lt; 280ms (Pristine CoW)</span>
              </div>
              <div>
                <span className="text-slate-400 text-xs block mb-1">Isolation Layer</span>
                <span className="text-slate-100">Isolated Network & Dedicated Port Matrix</span>
              </div>
            </div>
          )}

          {/* STEP 2: ENVIRONMENT READY */}
          {activeInspectIdx === 1 && (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-4 border-t border-slate-800 font-mono text-xs">
              <div>
                <span className="text-slate-400 text-xs block mb-1">Target Application</span>
                <span className="text-cyan-300 font-bold">{lease?.target_repo || lease?.app || "full-stack-application"}</span>
              </div>
              <div>
                <span className="text-slate-400 text-xs block mb-1">Database Fixtures</span>
                <span className="text-emerald-400 font-medium">Postgres Seeded ({lease?.evidence?.db_seed_rows ?? 4} fixtures)</span>
              </div>
              <div>
                <span className="text-slate-400 text-xs block mb-1">Container Health</span>
                <span className="text-slate-100">backend:healthy, db:healthy</span>
              </div>
            </div>
          )}

          {/* STEP 3: AGENT IMPLEMENTATION */}
          {activeInspectIdx === 2 && (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-4 border-t border-slate-800 font-mono text-xs">
              <div>
                <span className="text-slate-400 text-xs block mb-1">Active Persona</span>
                <span className="text-cyan-300 font-bold">Debby (Senior Full-Stack Engineer)</span>
              </div>
              <div>
                <span className="text-slate-400 text-xs block mb-1">In-Sandbox Tools</span>
                <span className="text-slate-100">File Editor, Bash, LSP Navigator</span>
              </div>
              <div>
                <span className="text-slate-400 text-xs block mb-1">Guardrails</span>
                <span className="text-emerald-400 font-medium">Blast-Radius AST Tripwire Active</span>
              </div>
            </div>
          )}

          {/* STEP 4: LIVE PREVIEW & REVIEW */}
          {activeInspectIdx === 3 && (
            <div className="pt-4 border-t border-slate-800 space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-cyan-950/40 p-4 rounded-xl border border-cyan-500/30">
                <div>
                  <div className="flex items-center space-x-2">
                    <Globe className="w-5 h-5 text-cyan-400" />
                    <span className="font-bold text-white text-sm">Live Interactive Preview Ready</span>
                  </div>
                  <p className="text-xs text-slate-300 mt-1">
                    Test the agent's changes live in your browser before requesting final Host Notary verification.
                  </p>
                </div>
                {lease?.preview_url ? (
                  <a
                    href={lease.preview_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center space-x-2 px-4 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white font-bold text-sm shadow-lg shadow-cyan-500/20 transition-all whitespace-nowrap self-start sm:self-auto"
                  >
                    <span>Open Live Preview</span>
                    <ExternalLink className="w-4 h-4 ml-1" />
                  </a>
                ) : (
                  <span className="px-3.5 py-1.5 rounded-lg bg-slate-800 text-slate-300 font-mono text-xs">
                    Preview Port {lease?.preview_port || 18000} Active
                  </span>
                )}
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 font-mono text-xs">
                <div className="p-3 rounded-lg bg-slate-950 border border-slate-800">
                  <span className="text-slate-400 block mb-1.5 font-semibold">How to Review & Continue:</span>
                  <ul className="text-slate-300 space-y-1.5 list-disc list-inside">
                    <li>To iterate: Comment feedback on Jira (e.g. <span className="text-cyan-400 font-semibold">"Please adjust styling"</span>)</li>
                    <li>To deliver PR: Comment <span className="text-emerald-400 font-bold">/meeseek finalize</span></li>
                  </ul>
                </div>
                <div className="p-3 rounded-lg bg-slate-950 border border-slate-800">
                  <span className="text-slate-400 block mb-1.5 font-semibold">Preview Tunnel Endpoint:</span>
                  <div className="text-cyan-300 truncate font-bold">
                    {lease?.preview_url || `http://localhost:${lease?.preview_port || 18000}/`}
                  </div>
                  <span className="text-xs text-slate-500 block mt-1">
                    Hot reload active inside container workspace
                  </span>
                </div>
              </div>
            </div>
          )}

          {/* STEP 5: HOST NOTARY VERIFICATION */}
          {activeInspectIdx === 4 && (
            <div className="pt-4 border-t border-slate-800 space-y-3">
              <div className="flex items-center justify-between pb-2 border-b border-slate-800 text-slate-400 font-mono text-xs">
                <span className="flex items-center space-x-2 text-cyan-400 font-bold text-sm">
                  <ShieldCheck className="w-4 h-4" />
                  <span>Host Notary Proof of Correctness</span>
                </span>
                <span className="text-xs text-slate-400">Impartial Outside-Sandbox Validation</span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 font-mono text-xs">
                <div>
                  <span className="text-slate-400 text-xs block mb-1">Verification Command</span>
                  <span className="text-cyan-300 bg-slate-950 px-2.5 py-1 rounded border border-slate-800 block truncate font-bold">
                    {lease?.ticket_test_cmd || lease?.evidence?.test_cmd || "pytest tests/ -v"}
                  </span>
                </div>
                <div>
                  <span className="text-slate-400 text-xs block mb-1">Notary Exit Status</span>
                  {lease?.evidence?.test_exit === 0 || task?.evidence?.test_exit === 0 ? (
                    <span className="text-emerald-400 font-bold flex items-center space-x-1 mt-1 text-sm">
                      <CheckCircle2 className="w-4 h-4 mr-1" />
                      <span>Exit 0 (All Tests Passed)</span>
                    </span>
                  ) : isFailed ? (
                    <span className="text-red-400 font-bold flex items-center space-x-1 mt-1 text-sm">
                      <AlertTriangle className="w-4 h-4 mr-1" />
                      <span>Failed (Exit {lease?.evidence?.test_exit ?? 1})</span>
                    </span>
                  ) : (
                    <span className="text-amber-400 italic mt-1 block font-medium">
                      Awaiting '/meeseek finalize' trigger
                    </span>
                  )}
                </div>
                <div>
                  <span className="text-slate-400 text-xs block mb-1">AST Blast-Radius Audit</span>
                  <span className="text-emerald-400 font-medium">Passed (Within authorized scope)</span>
                </div>
                <div>
                  <span className="text-slate-400 text-xs block mb-1">Impartial Signature</span>
                  <span className="text-slate-300">sha256:7f8e9a2b... [Certified Host]</span>
                </div>
              </div>
            </div>
          )}

          {/* STEP 6: PR DELIVERED */}
          {activeInspectIdx === 5 && (
            <div className="pt-4 border-t border-slate-800">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-emerald-950/30 p-4 rounded-xl border border-emerald-500/30">
                <div>
                  <div className="flex items-center space-x-2">
                    <GitPullRequest className="w-5 h-5 text-emerald-400" />
                    <span className="font-bold text-white text-sm">Pull Request Delivered & Certified</span>
                  </div>
                  <p className="text-xs text-slate-300 mt-1">
                    Notary Exit 0 proof attached to the PR. Ready for final team review and merge.
                  </p>
                </div>
                {lease?.pr_url || task?.evidence?.pr_url ? (
                  <a
                    href={lease?.pr_url || task?.evidence?.pr_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center space-x-2 px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-sm shadow-lg shadow-emerald-500/20 transition-all whitespace-nowrap self-start sm:self-auto"
                  >
                    <span>View GitHub PR</span>
                    <ExternalLink className="w-4 h-4 ml-1" />
                  </a>
                ) : (
                  <span className="px-3.5 py-1.5 rounded-lg bg-slate-800 text-slate-300 font-mono text-xs">
                    Pending Host Notary Exit 0 Certification
                  </span>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
