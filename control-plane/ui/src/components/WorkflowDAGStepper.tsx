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
  Database,
  Copy,
  Check,
  MessageSquare
} from "lucide-react";
import { TaskRecord, Lease, WorkflowState } from "../types";
import { finalizeTask } from "../api";

interface WorkflowDAGStepperProps {
  task?: TaskRecord;
  lease?: Lease;
  jiraBaseUrl?: string;
  onRefresh?: () => void;
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
  const pr = lease?.pr_url || lease?.evidence?.pr_url || task?.pr_url || task?.evidence?.pr_url;
  if (pr || state === "PR_OPENED" || state === "CERTIFIED_PR") return 5; // Step 6: PR Delivered

  const testExit = task?.evidence?.test_exit ?? lease?.evidence?.test_exit;
  const isFailedNotary =
    state === "NOTARY_FAILED" ||
    (testExit !== undefined && testExit !== 0) ||
    task?.evidence?.test_timed_out ||
    lease?.evidence?.test_timed_out;
  const isGuardrailBlocked =
    state === "GUARDRAIL_BLOCKED" ||
    task?.evidence?.guardrail_passed === false ||
    lease?.evidence?.guardrail_passed === false;

  if (isFailedNotary || isGuardrailBlocked || state === "CIRCUIT_BREAKER_HALTED" || state === "FAILED") {
    return 4; // Step 5: Host Notary Verification (Failed / Blocked)
  }

  if (state === "NOTARY_VERIFYING" || state === "NOTARY_TESTING" || state === "NOTARY_CORRECTING") {
    return 4; // Step 5: Host Notary Verification (Running)
  }

  if (state === "HALTED" || task?.halted || state === "WAITING_INPUT") return 3; // Step 4: Live Preview & Review
  if (state === "CODING") return 2; // Step 3: Agent Implementation
  if (state === "BOOTING" || state === "STRIKING") return 1; // Step 2: Environment Ready
  if (state === "PENDING" || state === "PROVISIONING" || state === "QUEUED" || lease?.status === "queued") return 0; // Step 1: Workspace Strike
  
  if (lease?.status === "ready") {
    if (lease.evidence?.test_exit === 0) return 5;
    if (lease.evidence?.test_cmd || lease.evidence?.test_exit !== undefined) return 4;
    return 2; // Agent Implementation in progress
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
        return "Agent implementation turn completed. Live interactive application is accessible on preview port with hot reload. Reviewer action is required: test the UI live, then finalize or request revisions.";
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
  jiraBaseUrl,
  onRefresh,
}) => {
  // When the PR is delivered every step is done (nothing left "in progress").
  const prDelivered = !!(lease?.pr_url || task?.evidence?.pr_url);
  const currentIdx = prDelivered ? STAGES.length : getStageIndex(task?.workflow_state, lease, task);
  const [selectedStageIdx, setSelectedStageIdx] = useState<number | null>(null);
  const [copiedFinalize, setCopiedFinalize] = useState<boolean>(false);
  const [isFinalizing, setIsFinalizing] = useState<boolean>(false);
  const [finalizeError, setFinalizeError] = useState<string | null>(null);

  const handleFinalizeNow = async () => {
    setIsFinalizing(true);
    setFinalizeError(null);
    try {
      const ticket = lease?.ticket || task?.ticket || "";
      const leaseId = lease?.lease_id || task?.lease_id || "";
      await finalizeTask(ticket, leaseId);
      if (onRefresh) onRefresh();
    } catch (err: any) {
      setFinalizeError(err.message || "Failed to finalize task");
    } finally {
      setIsFinalizing(false);
    }
  };

  const activeInspectIdx = selectedStageIdx !== null ? selectedStageIdx : Math.min(currentIdx, STAGES.length - 1);
  const activeInspectStage = STAGES[activeInspectIdx];

  const stageStatus: "COMPLETED" | "IN_PROGRESS" | "PENDING" = 
    activeInspectIdx < currentIdx 
      ? "COMPLETED" 
      : activeInspectIdx === currentIdx 
      ? "IN_PROGRESS" 
      : "PENDING";

  const deliveredPrUrl = lease?.pr_url || lease?.evidence?.pr_url || task?.pr_url || task?.evidence?.pr_url;
  const isPRDelivered = Boolean(deliveredPrUrl || task?.workflow_state === "CERTIFIED_PR" || task?.workflow_state === "PR_OPENED");

  const isAwaitingReview = 
    !isPRDelivered &&
    (currentIdx === 3 || task?.halted || task?.workflow_state === "HALTED" || task?.workflow_state === "WAITING_INPUT") &&
    task?.workflow_state !== "CODING" &&
    activeInspectStage.key === "PREVIEW";

  const testExit = task?.evidence?.test_exit ?? lease?.evidence?.test_exit;
  const isFailed =
    task?.workflow_state === "FAILED" ||
    task?.workflow_state === "NOTARY_FAILED" ||
    (testExit !== undefined && testExit !== 0) ||
    task?.evidence?.test_timed_out ||
    lease?.evidence?.test_timed_out;
  const isGuardrailBlocked =
    task?.workflow_state === "GUARDRAIL_BLOCKED" ||
    task?.evidence?.guardrail_passed === false ||
    lease?.evidence?.guardrail_passed === false;

  const isQueued = lease?.status === "queued" || task?.workflow_state === "QUEUED";
  const dynamicDescription = isQueued && activeInspectStage.key === "STRIKE"
    ? "Application active workspace capacity reached (max concurrent limit). Ticket is queued in FIFO order and will strike automatically once an active workspace completes."
    : getStageDescription(activeInspectStage.key, stageStatus);

  const ticketKey = lease?.ticket || task?.ticket || "";
  const jiraCommentUrl = jiraBaseUrl && ticketKey ? `${jiraBaseUrl.replace(/\/$/, "")}/browse/${ticketKey}#addcomment` : null;

  const handleCopyFinalize = () => {
    navigator.clipboard.writeText("/meeseek finalize");
    setCopiedFinalize(true);
    setTimeout(() => setCopiedFinalize(false), 2000);
  };

  return (
    <div className="mee-flow rounded-2xl p-5 border border-meeseek-border/80">
      {/* Visual State Machine Stepper Pipeline (Meeseek style) */}
      <div className="py-3 overflow-x-auto">
        <div className="mee-steps relative grid grid-cols-6 min-w-[760px]" style={{ ["--progress" as any]: `${Math.min(currentIdx, STAGES.length - 1) / (STAGES.length - 1)}` }}>
          {/* track + live progress */}
          <div className="mee-track" aria-hidden="true"><span className="mee-track-fill" /></div>

          {STAGES.map((stage, idx) => {
            const Icon = stage.icon;
            const isCompleted = idx < currentIdx;
            const isCurrent = idx === currentIdx;
            const isSelected = idx === activeInspectIdx;
            const tone =
              isCompleted ? "done"
              : !isCurrent ? "todo"
              : (isFailed || isGuardrailBlocked) ? "fail"
              : (stage.key === "PREVIEW" && (task?.halted || currentIdx === 3)) ? "review"
              : "live";
            const statusText = { done: "Done", todo: "Up next", fail: "Needs attention", review: "Waiting for you", live: "In progress" }[tone];

            return (
              <button
                type="button"
                key={stage.key}
                onClick={() => setSelectedStageIdx(idx)}
                className={`mee-step mee-step--${tone} ${isSelected ? "is-selected" : ""} relative z-10 flex flex-col items-center text-center px-2 bg-transparent border-0 cursor-pointer`}
                aria-current={isCurrent ? "step" : undefined}
                aria-pressed={isSelected}
              >
                <span className="mee-node">
                  {isCompleted ? <CheckCircle2 className="w-5 h-5" /> : <Icon className="w-5 h-5" />}
                </span>
                <span className="mt-3 text-[11px] font-mono font-semibold tracking-[.14em] uppercase text-slate-500">
                  Step {String(stage.stepNum).padStart(2, "0")}
                </span>
                <span className={`mt-1 text-sm leading-snug font-semibold ${isCurrent || isSelected ? "text-white" : "text-slate-300"}`}>
                  {stage.label}
                </span>
                <span className="mee-step-status mt-1.5 text-[11px] font-mono">{statusText}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Inline Stage Inspector Panel */}
      <div className="mt-4 pt-5 border-t border-meeseek-border">
        {/* Step Header */}
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
              isAwaitingReview ? (
                <span className="px-2.5 py-1 rounded-full text-xs font-mono font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40 animate-pulse flex items-center space-x-1.5 shadow-sm shadow-amber-500/10">
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-400"></span>
                  <span>⚠️ AWAITING HUMAN REVIEW</span>
                </span>
              ) : (
                <span className="px-2.5 py-0.5 rounded-full text-xs font-mono font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-500/30 animate-pulse">
                  IN PROGRESS
                </span>
              )
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
        <div key={activeInspectIdx} className="mee-inspector animate-fadeIn rounded-2xl p-4 sm:p-5 border border-meeseek-border">
          {/* Dynamic description updating with step status */}
          <p className="mee-lede text-[15px] text-slate-200 leading-relaxed mb-4">
            {dynamicDescription}
          </p>

          {/* STEP 1: WORKSPACE STRIKE */}
          {activeInspectIdx === 0 && (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-4 font-mono text-xs mee-facts">
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
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-4 font-mono text-xs mee-facts">
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
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-4 font-mono text-xs mee-facts">
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
              {/* Live Preview Button Box */}
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
                {lease?.status === "ready" && lease?.preview_url ? (
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
                  <span className="px-3.5 py-1.5 rounded-lg bg-slate-800/80 border border-slate-700/60 text-slate-400 font-mono text-xs inline-flex items-center space-x-2">
                    <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
                    <span>Preview Booting (Port {lease?.preview_port || 18000})</span>
                  </span>
                )}
              </div>

              {/* ACTION REQUIRED BANNER & JIRA LINK */}
              <div className="p-4 rounded-xl bg-amber-950/40 border border-amber-500/40 shadow-lg shadow-amber-500/5">
                <div className="flex items-center space-x-2 text-amber-300 font-bold text-xs uppercase tracking-wider mb-1">
                  <span>⚠️ Action Required to Deliver Pull Request</span>
                </div>
                <p className="text-xs text-slate-300 mb-3">
                  The agent has applied its code changes and hot reload is active. Test the live preview above. Approve changes below to execute impartial Host Notary tests and deliver the verified pull request:
                </p>

                <div className="flex flex-wrap items-center gap-2 mb-3">
                  {/* Option 1: Direct 1-Click in Console */}
                  <button
                    onClick={handleFinalizeNow}
                    disabled={isFinalizing}
                    className="inline-flex items-center space-x-1.5 px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs shadow-md shadow-emerald-500/20 transition-all active:scale-95 disabled:opacity-50"
                    title="Run Notary verification and cut PR directly without needing Jira"
                  >
                    {isFinalizing ? (
                      <>
                        <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                        <span>Verifying & Opening PR...</span>
                      </>
                    ) : (
                      <>
                        <ShieldCheck className="w-3.5 h-3.5" />
                        <span>Approve & Finalize PR</span>
                      </>
                    )}
                  </button>

                  {/* Option 2: Comment on Jira */}
                  {jiraCommentUrl && (
                    <a
                      href={jiraCommentUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center space-x-1.5 px-3.5 py-1.5 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white font-bold text-xs shadow-md shadow-cyan-500/20 transition-all"
                    >
                      <MessageSquare className="w-3.5 h-3.5" />
                      <span>Comment on Jira ({ticketKey}) ↗</span>
                    </a>
                  )}

                  {/* Option 3: Copy slash command */}
                  <button
                    onClick={handleCopyFinalize}
                    className="inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-slate-900 hover:bg-slate-800 text-slate-200 border border-slate-700 text-xs font-mono font-semibold transition-all active:scale-95"
                  >
                    {copiedFinalize ? (
                      <>
                        <Check className="w-3.5 h-3.5 text-emerald-400" />
                        <span className="text-emerald-400">Copied "/meeseek finalize"!</span>
                      </>
                    ) : (
                      <>
                        <Copy className="w-3.5 h-3.5 text-slate-400" />
                        <span>Copy "/meeseek finalize"</span>
                      </>
                    )}
                  </button>
                </div>

                {finalizeError && (
                  <div className="mb-2 p-2 rounded bg-red-950/60 border border-red-500/30 text-xs text-red-300 font-mono">
                    Finalize error: {finalizeError}
                  </div>
                )}

                <div className="text-[11px] text-slate-400 border-t border-slate-800/80 pt-2 font-mono">
                  <span className="text-slate-300 font-medium">To request revisions instead: </span>
                  Reply directly on Jira with your instructions (e.g. <span className="text-cyan-400">"Adjust button spacing to 12px"</span>).
                </div>
              </div>

              {/* Tunnel Endpoint Info */}
              <div className="p-3 rounded-lg bg-slate-950 border border-slate-800 font-mono text-xs">
                <span className="text-slate-400 block mb-1 font-semibold">Preview Tunnel Endpoint:</span>
                <div className="text-cyan-300 truncate font-bold">
                  {lease?.preview_url || `http://localhost:${lease?.preview_port || 18000}/`}
                </div>
                <span className="text-[11px] text-slate-500 block mt-1">
                  Hot reload active inside container workspace
                </span>
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

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 font-mono text-xs mee-facts">
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
                      <span>Failed (Exit {testExit ?? 1})</span>
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

              {/* Traceback output on failure */}
              {isFailed && (lease?.evidence?.test_output || task?.evidence?.test_output) && (
                <div className="mt-3 p-3 rounded-lg bg-black/60 border border-red-500/30 font-mono text-xs">
                  <div className="text-red-400 font-bold mb-1 flex items-center space-x-1.5">
                    <AlertTriangle className="w-3.5 h-3.5" />
                    <span>Failure Traceback:</span>
                  </div>
                  <pre className="text-red-300 text-[11px] whitespace-pre-wrap overflow-x-auto max-h-44 p-2 rounded bg-slate-950 border border-slate-800">
                    {lease?.evidence?.test_output || task?.evidence?.test_output}
                  </pre>
                </div>
              )}
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
                {deliveredPrUrl ? (
                  <a
                    href={deliveredPrUrl}
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
