import React, { useState } from "react";
import { 
  Plus, 
  Trash2,
  GitBranch, 
  ShieldCheck, 
  Server, 
  Play, 
  CheckCircle2, 
  AlertCircle, 
  ArrowRight, 
  Layers,
  Terminal,
  FileCode,
  Check,
  RefreshCw,
  ExternalLink,
  Bot,
  HelpCircle,
  XCircle,
  MessageSquare,
  AlertTriangle
} from "lucide-react";
import { OnboardingRequest, Team, UserRole } from "../types";
import { 
  createOnboardingRequest, 
  triggerTrialRun, 
  validateGitRepository,
  submitOnboardingForReview,
  approveOnboardingRequest,
  rejectOnboardingRequest
} from "../api";

interface RepoRow {
  id: string;
  name: string;
  url: string;
  branch: string;
  role: string;
  test_cmd: string;
  isValidating?: boolean;
  validation?: {
    valid: boolean;
    commit_sha?: string;
    message?: string;
    error?: string;
  } | null;
}

const getPresetsForRole = (role: string): string[] => {
  switch (role) {
    case "frontend":
      return [
        "npm run lint && npx tsc -b",
        "npm test",
        "npm run build",
        "vitest",
      ];
    case "gateway":
      return [
        "go test ./...",
        "npm test",
        "PYTHONPATH=. pytest tests/",
      ];
    case "worker":
      return [
        "PYTHONPATH=. pytest tests/",
        "go test ./...",
        "npm test",
      ];
    case "db":
      return [
        "PYTHONPATH=. pytest tests/",
        "npm test",
      ];
    case "backend":
    default:
      return [
        "PYTHONPATH=. pytest tests/",
        "pytest",
        "poetry run pytest",
        "python -m unittest discover",
      ];
  }
};

interface OnboardingWizardProps {
  team: Team | null;
  onRefresh: () => void;
  onboardingRequests: OnboardingRequest[];
  isAdmin: boolean;
  userRole?: UserRole;
}

export const OnboardingWizard: React.FC<OnboardingWizardProps> = ({
  team,
  onRefresh,
  onboardingRequests,
  isAdmin,
  userRole,
}) => {
  const [step, setStep] = useState<number>(1);
  
  // Step 1: App Identity & Team
  const [appName, setAppName] = useState("");
  const [teamSlug, setTeamSlug] = useState(team?.slug || "core");
  const [contact, setContact] = useState("");
  const [jiraProject, setJiraProject] = useState("");

  // Step 2: Dynamic Repositories & Quality Gates
  const [repos, setRepos] = useState<RepoRow[]>([
    {
      id: "repo-1",
      name: "test_backend",
      url: "https://github.com/saifali3251/test_backend.git",
      branch: "main",
      role: "backend",
      test_cmd: "PYTHONPATH=. pytest tests/",
      validation: null,
    },
    {
      id: "repo-2",
      name: "test_frontend",
      url: "https://github.com/saifali3251/test_frontend.git",
      branch: "main",
      role: "frontend",
      test_cmd: "npm run lint && npx tsc -b",
      validation: null,
    },
  ]);

  // Web entrypoint port
  const [previewPort, setPreviewPort] = useState("3000");
  const [showAdvancedPort, setShowAdvancedPort] = useState(false);

  // Step 3: Staged state & Trial
  const [stagedRequest, setStagedRequest] = useState<OnboardingRequest | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isTrialing, setIsTrialing] = useState(false);
  const [isFinalSubmitting, setIsFinalSubmitting] = useState(false);
  const [activeTrialOutput, setActiveTrialOutput] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Admin action state
  const [adminActionLoading, setAdminActionLoading] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  // Repo row management
  const addRepoRow = () => {
    const nextIdx = repos.length + 1;
    setRepos([
      ...repos,
      {
        id: `repo-${Date.now()}`,
        name: `service_${nextIdx}`,
        url: "",
        branch: "main",
        role: "worker",
        test_cmd: "PYTHONPATH=. pytest tests/",
        validation: null,
      },
    ]);
  };

  const removeRepoRow = (id: string) => {
    if (repos.length <= 1) return;
    setRepos(repos.filter((r) => r.id !== id));
  };

  const updateRepoField = (id: string, field: keyof RepoRow, value: any) => {
    setRepos(
      repos.map((r) => {
        if (r.id === id) {
          const updated = {
            ...r,
            [field]: value,
            validation: field === "url" || field === "branch" ? null : r.validation,
          };
          if (field === "role") {
            const currentPresets = getPresetsForRole(r.role);
            if (!r.test_cmd || currentPresets.includes(r.test_cmd)) {
              updated.test_cmd = getPresetsForRole(value)[0];
            }
          }
          return updated;
        }
        return r;
      })
    );
  };

  // Git Connectivity Validation (Optional check)
  const validateRepo = async (id: string) => {
    const target = repos.find((r) => r.id === id);
    if (!target || !target.url.trim()) return;

    setRepos((prev) =>
      prev.map((r) => (r.id === id ? { ...r, isValidating: true, validation: null } : r))
    );

    try {
      const res = await validateGitRepository(target.url.trim(), target.branch.trim() || "main");
      setRepos((prev) =>
        prev.map((r) => (r.id === id ? { ...r, isValidating: false, validation: res } : r))
      );
    } catch (err: any) {
      setRepos((prev) =>
        prev.map((r) =>
          r.id === id
            ? {
                ...r,
                isValidating: false,
                validation: { valid: false, error: err.message || "Reachability check failed" },
              }
            : r
        )
      );
    }
  };

  // Stage Draft Application (transitions directly to Step 3)
  const handleStageDraft = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    setIsSubmitting(true);
    setErrorMessage(null);
    try {
      if (!appName.trim()) {
        throw new Error("Application Slug is required.");
      }
      const invalidRepo = repos.find((r) => !r.name.trim() || !r.url.trim());
      if (invalidRepo) {
        throw new Error("Please specify both a service name and Git URL for all repositories.");
      }

      const inferredDefaultPort = repos.some((r) => r.role === "frontend" || r.role === "gateway") ? "3000" : "8000";

      const payload = {
        team_slug: teamSlug.trim().toLowerCase() || "core",
        app_name: appName.trim().toLowerCase() || "custom-service",
        contact: contact.trim(),
        jira_project: jiraProject.trim().toUpperCase() || undefined,
        preview_port: previewPort.trim() || inferredDefaultPort,
        repos: repos.map((r) => ({
          name: r.name.trim(),
          url: r.url.trim(),
          branch: r.branch.trim() || "main",
          role: r.role,
          test_cmd: r.test_cmd.trim() || undefined,
        })),
      };

      const res = await createOnboardingRequest(payload);
      setStagedRequest({ ...res, id: res.request_id || res.id });
      setSuccessMessage(`Application "${payload.app_name}" staged! Run a preflight trial below.`);
      setStep(3);
      onRefresh();
    } catch (err: any) {
      setErrorMessage(err.message || "Failed to stage application");
    } finally {
      setIsSubmitting(false);
    }
  };

  // Run in-wizard Trial Dry-Run
  const handleRunTrial = async (reqId: string) => {
    setIsTrialing(true);
    setErrorMessage(null);
    setActiveTrialOutput(null);
    try {
      const res = await triggerTrialRun(reqId);
      const out = res.trial_log || res.trial_output || "Trial build completed successfully: Exit 0";
      setActiveTrialOutput(out);
      if (res.status === "trial_passed") {
        setSuccessMessage("Preflight trial passed! You can now submit this application for platform certification.");
      } else if (res.status === "trial_failed") {
        setErrorMessage(res.trial_error || "Trial build failed. Review the terminal execution log below.");
      }
      setStagedRequest({ ...res, id: res.request_id || res.id });
      onRefresh();
    } catch (err: any) {
      const msg = err.message || "Trial execution encountered an error";
      setErrorMessage(msg);
      setActiveTrialOutput(`Trial error: ${msg}`);
    } finally {
      setIsTrialing(false);
    }
  };

  // Submit verified request for Platform Review
  const handleSubmitForReview = async () => {
    const targetId = stagedRequest?.request_id || stagedRequest?.id;
    if (!targetId) return;
    setIsFinalSubmitting(true);
    setErrorMessage(null);
    try {
      const res = await submitOnboardingForReview(targetId);
      setStagedRequest({ ...res, id: res.request_id || res.id });
      setSuccessMessage(`Application "${stagedRequest?.app_name}" submitted for Platform Review! A Superadmin can now certify and publish it.`);
      onRefresh();
    } catch (err: any) {
      setErrorMessage(err.message || "Failed to submit for review");
    } finally {
      setIsFinalSubmitting(false);
    }
  };

  // Chatbot copilot helper
  const askMrMeeseeksToDebug = () => {
    const errorDetail = stagedRequest?.trial_error || errorMessage || "Trial run failed";
    const prompt = `My onboarding trial for application "${stagedRequest?.app_name || appName}" failed with error: "${errorDetail}".\n\nHere is the recent trial log:\n${activeTrialOutput?.slice(-600) || "Exit Code 1"}\n\nCan you inspect the failure and tell me how to fix my manifest or test command?`;
    window.dispatchEvent(new CustomEvent("meeseek:ask", { detail: { prompt } }));
  };

  // Superadmin actions
  const handleApprove = async (reqId: string, reqAppName?: string) => {
    setAdminActionLoading(reqId);
    try {
      await approveOnboardingRequest(reqId);
      setSuccessMessage(`Application "${reqAppName || appName || reqId}" approved and published! Golden manifest created.`);
      onRefresh();
    } catch (err: any) {
      alert(`Approval failed: ${err.message}`);
    } finally {
      setAdminActionLoading(null);
    }
  };

  const handleReject = async (reqId: string) => {
    if (!rejectReason.trim()) {
      alert("Please provide a rejection reason.");
      return;
    }
    setAdminActionLoading(reqId);
    try {
      await rejectOnboardingRequest(reqId, rejectReason.trim());
      setRejectingId(null);
      setRejectReason("");
      setSuccessMessage("Application returned to team with feedback.");
      onRefresh();
    } catch (err: any) {
      alert(`Rejection failed: ${err.message}`);
    } finally {
      setAdminActionLoading(null);
    }
  };

  return (
    <div className="space-y-8">
      {/* Applications Queue & Platform Review (Visible for Admins or when applications exist) */}
      {(isAdmin || onboardingRequests.length > 0) && (
        <div className="glass-panel rounded-2xl p-6 border border-meeseek-border shadow-xl">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h4 className="text-base font-bold text-white flex items-center space-x-2">
                {isAdmin ? <ShieldCheck className="w-4 h-4 text-purple-400" /> : <Layers className="w-4 h-4 text-cyan-400" />}
                <span>{isAdmin ? "Platform Onboarding Review Queue" : "Your Team's Onboarded Applications"}</span>
              </h4>
              <p className="text-xs text-slate-400 mt-0.5">
                {isAdmin
                  ? "Applications awaiting preflight certification, trial verification, and golden image publishing"
                  : "Track registration status, preflight certification, and golden image publishing"}
              </p>
            </div>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-mono bg-purple-500/10 text-purple-400 border border-purple-500/30 font-semibold">
              {onboardingRequests.length} Applications
            </span>
          </div>

          {onboardingRequests.length === 0 ? (
            <div className="p-8 text-center bg-meeseek-950/40 rounded-xl border border-slate-800/80 text-xs text-slate-500">
              No applications waiting for platform review.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="bg-meeseek-950 text-slate-400 uppercase font-mono text-[10px] tracking-wider border-b border-meeseek-border">
                  <tr>
                    <th className="py-2.5 px-4">Application</th>
                    <th className="py-2.5 px-4">Team</th>
                    <th className="py-2.5 px-4">Jira Board</th>
                    <th className="py-2.5 px-4">Repositories</th>
                    <th className="py-2.5 px-4">Status</th>
                    <th className="py-2.5 px-4 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {onboardingRequests.map((req) => {
                    const reqId: string = (req.request_id || req.id || "").toString();
                    if (!reqId) return null;
                    const isBusy = adminActionLoading === reqId;
                    return (
                      <tr key={reqId} className="hover:bg-meeseek-900/40">
                        <td className="py-3 px-4 font-bold text-white">
                          <div className="flex items-center space-x-1.5">
                            <span>{req.app_name}</span>
                          </div>
                          {req.reject_reason && (
                            <div className="mt-1.5 p-1.5 rounded bg-amber-950/40 border border-amber-500/30 text-[11px] font-mono text-amber-300">
                              <span className="text-amber-400 font-semibold block text-[10px] uppercase tracking-wide">Reviewer Feedback:</span>
                              <span>{req.reject_reason}</span>
                            </div>
                          )}
                        </td>
                        <td className="py-3 px-4 text-slate-300 font-mono">{req.team_slug}</td>
                        <td className="py-3 px-4 text-slate-400 font-mono text-[11px]">
                          {req.jira_project ? (
                            <span className="px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20">
                              {req.jira_project}
                            </span>
                          ) : (
                            <span className="text-slate-600">—</span>
                          )}
                        </td>
                        <td className="py-3 px-4 text-slate-400 font-mono text-[11px]">
                          {req.repos?.map((r) => r.name).join(", ") || "—"}
                        </td>
                        <td className="py-3 px-4">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-mono ${
                            req.status === "published"
                              ? "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30"
                              : req.status === "pending_approval"
                              ? "bg-purple-500/15 text-purple-300 border border-purple-500/30 font-semibold"
                              : req.status === "trial_passed"
                              ? "bg-cyan-500/15 text-cyan-300 border border-cyan-500/30"
                              : req.status === "rejected"
                              ? "bg-red-500/15 text-red-300 border border-red-500/30"
                              : "bg-amber-500/10 text-amber-300 border border-amber-500/30"
                          }`}>
                            {req.status}
                          </span>
                        </td>
                        <td className="py-3 px-4 text-right">
                          <div className="flex items-center justify-end space-x-1.5">
                            {/* Run Trial Button */}
                            <button
                              onClick={() => handleRunTrial(reqId)}
                              disabled={isBusy || isTrialing}
                              className="px-2.5 py-1 rounded text-[11px] font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-all disabled:opacity-50"
                              title="Run preflight trial build"
                            >
                              Trial
                            </button>

                            {/* Approve & Publish Button (Superadmin Only) */}
                            {req.status === "pending_approval" && (
                              <button
                                onClick={() => handleApprove(reqId, req.app_name)}
                                disabled={isBusy || (userRole !== undefined && userRole !== "superadmin")}
                                title={
                                  userRole !== undefined && userRole !== "superadmin"
                                    ? "Superadmin role required to publish applications"
                                    : "Approve application and publish golden manifest"
                                }
                                className={`px-2.5 py-1 rounded text-[11px] font-medium transition-all ${
                                  userRole === "superadmin" || userRole === undefined
                                    ? "bg-emerald-600 hover:bg-emerald-500 text-white shadow-sm"
                                    : "bg-slate-800/40 text-slate-500 cursor-not-allowed"
                                }`}
                              >
                                {isBusy ? "Publishing..." : "Approve & Publish"}
                              </button>
                            )}

                            {/* Reject with Feedback Button */}
                            {req.status === "pending_approval" && isAdmin && (
                              <button
                                onClick={() => setRejectingId(rejectingId === reqId ? null : reqId)}
                                disabled={isBusy}
                                className="px-2.5 py-1 rounded text-[11px] font-medium bg-red-950/40 hover:bg-red-900/60 text-red-300 border border-red-500/30 transition-all flex items-center space-x-1"
                                title="Reject application and provide actionable feedback to team"
                              >
                                <MessageSquare className="w-3 h-3 text-red-400" />
                                <span>Reject with Feedback</span>
                              </button>
                            )}
                          </div>

                          {/* Rejection Reason Form */}
                          {rejectingId === reqId && (
                            <div className="mt-2 text-left bg-slate-950 p-3 rounded-xl border border-red-500/40 space-y-2.5 shadow-lg">
                              <div className="flex items-center justify-between">
                                <label className="text-[11px] text-red-300 font-semibold flex items-center space-x-1.5">
                                  <AlertTriangle className="w-3.5 h-3.5 text-red-400" />
                                  <span>Review Feedback & Change Request</span>
                                </label>
                                <span className="text-[10px] text-slate-500">Sent directly to team</span>
                              </div>
                              <textarea
                                rows={2}
                                value={rejectReason}
                                onChange={(e) => setRejectReason(e.target.value)}
                                placeholder="Explain what needs to be changed (e.g. Please add unit test command, or update port to 8000)..."
                                className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-red-500"
                              />
                              <div className="flex justify-end space-x-2">
                                <button
                                  type="button"
                                  onClick={() => {
                                    setRejectingId(null);
                                    setRejectReason("");
                                  }}
                                  className="px-2.5 py-1 rounded bg-slate-800 text-slate-400 hover:text-slate-200 text-[10px]"
                                >
                                  Cancel
                                </button>
                                <button
                                  type="button"
                                  onClick={() => handleReject(reqId)}
                                  className="px-3 py-1 rounded bg-red-600 hover:bg-red-500 text-white text-[10px] font-bold shadow-md shadow-red-600/20"
                                >
                                  Confirm Rejection & Send Feedback
                                </button>
                              </div>
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Onboarding Wizard Form Card (Self-serve registration for engineering teams; hidden for platform superadmins) */}
      {userRole !== "superadmin" && (
        <div className="glass-panel rounded-2xl p-8 border border-meeseek-border shadow-xl">
          <div className="flex items-center justify-between pb-6 border-b border-meeseek-border">
            <div>
              <span className="text-xs font-mono font-semibold uppercase text-cyan-400 tracking-wider">
                Self-Serve Catalog Registration
              </span>
            <h3 className="text-xl font-bold text-white mt-1">
              Onboard a New Microservice / Composite Application
            </h3>
            <p className="text-xs text-slate-400 mt-1">
              Register repositories, define pre-PR automated test gates, validate Git connectivity, and verify golden build readiness with a live preflight trial.
            </p>
          </div>

          {/* Stepper Progress */}
          <div className="flex items-center space-x-2 text-xs font-mono">
            {[
              { s: 1, label: "Identity" },
              { s: 2, label: "Repositories & Tests" },
              { s: 3, label: "Preflight Trial & Review" },
            ].map(({ s, label }) => (
              <div key={s} className="flex items-center space-x-1.5">
                <div
                  className={`w-7 h-7 rounded-lg flex items-center justify-center font-bold transition-all ${
                    step === s
                      ? "bg-cyan-500 text-black shadow-lg shadow-cyan-500/30"
                      : step > s
                      ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/40"
                      : "bg-slate-900 text-slate-600 border border-slate-800"
                  }`}
                >
                  {step > s ? "✓" : s}
                </div>
                <span className={`text-[11px] hidden md:inline ${step === s ? "text-cyan-400 font-semibold" : "text-slate-500"}`}>
                  {label}
                </span>
                {s < 3 && <span className="text-slate-700 hidden md:inline">/</span>}
              </div>
            ))}
          </div>
        </div>

        {errorMessage && (
          <div className="mt-6 p-4 rounded-xl bg-red-950/40 border border-red-500/30 text-red-300 text-xs flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              <span>{errorMessage}</span>
            </div>
            {step === 3 && (
              <button
                type="button"
                onClick={askMrMeeseeksToDebug}
                className="px-3 py-1 rounded-lg bg-red-900/60 hover:bg-red-800 text-red-200 text-xs font-medium flex items-center space-x-1.5 transition-all"
              >
                <Bot className="w-3.5 h-3.5 text-cyan-300" />
                <span>Ask Mr. Meeseeks</span>
              </button>
            )}
          </div>
        )}

        {successMessage && (
          <div className="mt-6 p-4 rounded-xl bg-emerald-950/40 border border-emerald-500/30 text-emerald-300 text-xs flex items-center space-x-2">
            <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
            <span>{successMessage}</span>
          </div>
        )}

        {stagedRequest?.status === "rejected" && stagedRequest?.reject_reason && (
          <div className="mt-6 p-4 rounded-xl bg-amber-950/40 border border-amber-500/40 text-amber-300 text-xs flex items-center space-x-2.5">
            <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0" />
            <div>
              <span className="font-bold uppercase tracking-wider text-[10px] text-amber-400 block">Review Feedback from Platform Superadmin</span>
              <span className="mt-0.5 block">{stagedRequest.reject_reason}</span>
            </div>
          </div>
        )}

        <form onSubmit={handleStageDraft} className="mt-6 space-y-6">
          {/* STEP 1: App & Team Details */}
          {step === 1 && (
            <div className="space-y-4">
              <h4 className="text-sm font-semibold text-white flex items-center space-x-2">
                <span className="w-5 h-5 rounded-full bg-cyan-500/20 text-cyan-400 flex items-center justify-center text-xs">1</span>
                <span>Application Identity & Team Ownership</span>
              </h4>

              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                <div>
                  <label className="text-xs text-slate-400 block mb-1">
                    Application Slug <span className="text-cyan-400">*</span>
                  </label>
                  <input
                    type="text"
                    required
                    value={appName}
                    onChange={(e) => setAppName(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "-"))}
                    placeholder="e.g. billing-service"
                    className="w-full bg-meeseek-950 border border-meeseek-border rounded-xl px-3 py-2 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-cyan-500 font-mono"
                  />
                  <p className="text-[10px] text-slate-500 mt-1">Unique manifest slug (used for golden caching)</p>
                </div>

                <div>
                  <label className="text-xs text-slate-400 block mb-1">
                    Owning Team Slug <span className="text-cyan-400">*</span>
                  </label>
                  <input
                    type="text"
                    required
                    value={teamSlug}
                    onChange={(e) => setTeamSlug(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "-"))}
                    placeholder="e.g. core or payments"
                    className="w-full bg-meeseek-950 border border-meeseek-border rounded-xl px-3 py-2 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-cyan-500 font-mono"
                  />
                  <p className="text-[10px] text-slate-500 mt-1">Multi-tenant RBAC boundary</p>
                </div>

                <div>
                  <label className="text-xs text-slate-400 block mb-1">Team Contact (Slack / Email)</label>
                  <input
                    type="text"
                    value={contact}
                    onChange={(e) => setContact(e.target.value)}
                    placeholder="#eng-billing or billing@org.com"
                    className="w-full bg-meeseek-950 border border-meeseek-border rounded-xl px-3 py-2 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-cyan-500"
                  />
                  <p className="text-[10px] text-slate-500 mt-1">Notification channel for PR notifications</p>
                </div>

                <div>
                  <label className="text-xs text-slate-400 block mb-1">
                    Jira Project Key / Board <span className="text-slate-500">(Optional)</span>
                  </label>
                  <input
                    type="text"
                    value={jiraProject}
                    onChange={(e) => setJiraProject(e.target.value.toUpperCase())}
                    placeholder="e.g. PAY or FSA"
                    className="w-full bg-meeseek-950 border border-meeseek-border rounded-xl px-3 py-2 text-xs text-cyan-300 placeholder-slate-600 focus:outline-none focus:border-cyan-500 font-mono"
                  />
                  <p className="text-[10px] text-slate-500 mt-1">Auto-route labelled Jira tickets to this app</p>
                </div>
              </div>

              <div className="pt-4 flex justify-end">
                <button
                  type="button"
                  onClick={() => {
                    if (!appName.trim()) {
                      setErrorMessage("Please enter an Application Slug to continue.");
                      return;
                    }
                    setErrorMessage(null);
                    setStep(2);
                  }}
                  className="px-5 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white font-medium text-xs flex items-center space-x-2 transition-all shadow-lg shadow-cyan-600/20"
                >
                  <span>Next: Configure Repositories</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )}

          {/* STEP 2: Microservices & Automated Test Gates */}
          {step === 2 && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h4 className="text-sm font-semibold text-white flex items-center space-x-2">
                    <span className="w-5 h-5 rounded-full bg-cyan-500/20 text-cyan-400 flex items-center justify-center text-xs">2</span>
                    <span>Microservices & Automated Test Gates</span>
                  </h4>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Register each microservice (Backend, Frontend, Gateway) and specify its automated verification test command.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={addRepoRow}
                  className="px-3 py-1.5 rounded-lg bg-cyan-600/20 hover:bg-cyan-600/30 text-cyan-300 border border-cyan-500/30 text-xs font-medium flex items-center space-x-1.5 transition-all"
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>Add Microservice / Repo</span>
                </button>
              </div>

              <div className="space-y-4">
                {repos.map((repo, idx) => (
                  <div
                    key={repo.id}
                    className="p-4 rounded-xl bg-meeseek-950 border border-slate-800 space-y-3 relative group"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2">
                        <span className="text-xs font-mono font-bold text-cyan-400">
                          #{idx + 1} {idx === 0 ? "(Primary Service / Entrypoint)" : `Microservice`}
                        </span>
                        <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-300">
                          role: {repo.role}
                        </span>
                      </div>

                      {repos.length > 1 && (
                        <button
                          type="button"
                          onClick={() => removeRepoRow(repo.id)}
                          className="text-slate-500 hover:text-red-400 transition-colors p-1"
                          title="Remove repository"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-12 gap-3">
                      <div className="md:col-span-3">
                        <label className="text-[11px] text-slate-400 block mb-1">Service Name</label>
                        <input
                          type="text"
                          required
                          value={repo.name}
                          onChange={(e) => updateRepoField(repo.id, "name", e.target.value)}
                          placeholder="e.g. test_backend"
                          className="w-full bg-meeseek-900 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-cyan-500"
                        />
                      </div>

                      <div className="md:col-span-5">
                        <label className="text-[11px] text-slate-400 block mb-1">Git HTTPS URL</label>
                        <input
                          type="text"
                          required
                          value={repo.url}
                          onChange={(e) => updateRepoField(repo.id, "url", e.target.value)}
                          placeholder="https://github.com/org/repo.git"
                          className="w-full bg-meeseek-900 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-cyan-500"
                        />
                      </div>

                      <div className="md:col-span-2">
                        <label className="text-[11px] text-slate-400 block mb-1">Branch</label>
                        <input
                          type="text"
                          required
                          value={repo.branch}
                          onChange={(e) => updateRepoField(repo.id, "branch", e.target.value)}
                          placeholder="main"
                          className="w-full bg-meeseek-900 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-cyan-500"
                        />
                      </div>

                      <div className="md:col-span-2">
                        <label className="text-[11px] text-slate-400 block mb-1">Role</label>
                        <select
                          value={repo.role}
                          onChange={(e) => updateRepoField(repo.id, "role", e.target.value)}
                          className="w-full bg-meeseek-900 border border-slate-800 rounded-lg px-2 py-1.5 text-xs text-white focus:outline-none focus:border-cyan-500"
                        >
                          <option value="backend">Backend API</option>
                          <option value="frontend">Frontend UI</option>
                          <option value="gateway">GraphQL Gateway</option>
                          <option value="worker">Worker / Consumer</option>
                          <option value="db">Database Service</option>
                        </select>
                      </div>
                    </div>

                    {/* Pre-PR Quality & Test Gate */}
                    <div className="pt-2 border-t border-slate-900/80 space-y-2">
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <label className="text-[11px] font-semibold text-slate-300 flex items-center space-x-1.5">
                            <ShieldCheck className="w-3.5 h-3.5 text-cyan-400" />
                            <span>Pre-PR Quality & Test Gate (Exit 0)</span>
                            <span className="text-cyan-400">*</span>
                          </label>
                          <span className="text-[10px] text-slate-500">
                            Executed host-side outside agent control. A non-zero exit blocks PR.
                          </span>
                        </div>
                        <input
                          type="text"
                          required
                          value={repo.test_cmd}
                          onChange={(e) => updateRepoField(repo.id, "test_cmd", e.target.value)}
                          placeholder={repo.role === "frontend" ? "npm run lint && npx tsc -b" : "PYTHONPATH=. pytest tests/"}
                          className="w-full bg-meeseek-900 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-cyan-300 font-mono focus:outline-none focus:border-cyan-500"
                        />
                        <div className="mt-1.5 flex items-center space-x-1.5 flex-wrap gap-y-1 text-[10px] font-mono">
                          <span className="text-slate-500">Presets:</span>
                          {getPresetsForRole(repo.role).map((preset) => (
                            <button
                              key={preset}
                              type="button"
                              onClick={() => updateRepoField(repo.id, "test_cmd", preset)}
                              className="px-2 py-0.5 rounded bg-slate-800/80 hover:bg-slate-700 text-slate-300 border border-slate-700 transition-all"
                            >
                              {preset}
                            </button>
                          ))}
                        </div>
                      </div>
                    </div>

                    {/* Git Connectivity Validation Bar */}
                    <div className="flex items-center justify-between pt-2 border-t border-slate-900/60 text-xs">
                      <div>
                        {repo.isValidating ? (
                          <span className="text-cyan-400 flex items-center space-x-1.5 text-[11px] font-mono">
                            <RefreshCw className="w-3 h-3 animate-spin" />
                            <span>Testing Git reachability via ls-remote...</span>
                          </span>
                        ) : repo.validation ? (
                          repo.validation.valid ? (
                            <span className="text-emerald-400 flex items-center space-x-1 text-[11px] font-mono">
                              <CheckCircle2 className="w-3.5 h-3.5" />
                              <span>Reachable (HEAD: {repo.validation.commit_sha})</span>
                            </span>
                          ) : (
                            <span className="text-amber-400 flex items-center space-x-1 text-[11px] font-mono">
                              <AlertCircle className="w-3.5 h-3.5" />
                              <span>{repo.validation.error || "Reachability check failed"} (Optional)</span>
                            </span>
                          )
                        ) : (
                          <span className="text-slate-500 text-[11px]">Git reachability check (Optional)</span>
                        )}
                      </div>

                      <button
                        type="button"
                        onClick={() => validateRepo(repo.id)}
                        disabled={repo.isValidating || !repo.url.trim()}
                        className="px-2.5 py-1 rounded-md bg-slate-800 hover:bg-slate-700 text-slate-200 text-[11px] font-mono transition-all disabled:opacity-50"
                      >
                        {repo.isValidating ? "Validating..." : "Validate Connection"}
                      </button>
                    </div>
                  </div>
                ))}
              </div>

              {/* Web Entrypoint Banner */}
              <div className="p-3.5 rounded-xl bg-meeseek-950 border border-slate-800/80 flex items-center justify-between flex-wrap gap-2">
                <div className="flex items-center space-x-3">
                  <div className="w-8 h-8 rounded-lg bg-blue-500/10 border border-blue-500/20 flex items-center justify-center text-blue-400">
                    <Server className="w-4 h-4" />
                  </div>
                  <div>
                    <div className="flex items-center space-x-2">
                      <span className="text-xs font-semibold text-white">Application Web Entrypoint Port</span>
                      <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-blue-500/20 text-cyan-300 border border-blue-500/30">
                        Auto-detected: Port {previewPort || (repos.some(r => r.role === "frontend" || r.role === "gateway") ? "3000" : "8000")}
                      </span>
                    </div>
                    <p className="text-[11px] text-slate-400 mt-0.5">
                      Target container port reverse-proxied for live ticket browser previews.
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setShowAdvancedPort(!showAdvancedPort)}
                  className="text-xs text-cyan-400 hover:text-cyan-300 font-medium px-2.5 py-1 rounded-lg hover:bg-slate-800 transition-all"
                >
                  {showAdvancedPort ? "Hide Override" : "Customize Port"}
                </button>
              </div>

              {showAdvancedPort && (
                <div className="p-3 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1">
                  <label className="text-[11px] text-slate-400 block">Custom Container Web Port</label>
                  <input
                    type="text"
                    value={previewPort}
                    onChange={(e) => setPreviewPort(e.target.value)}
                    placeholder="3000"
                    className="w-36 bg-meeseek-950 border border-slate-800 rounded-lg px-2.5 py-1 text-xs text-white font-mono focus:border-cyan-500"
                  />
                </div>
              )}

              <div className="pt-4 flex justify-between">
                <button
                  type="button"
                  onClick={() => setStep(1)}
                  className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs transition-all"
                >
                  Back
                </button>
                <button
                  type="button"
                  onClick={handleStageDraft}
                  disabled={isSubmitting}
                  className="px-6 py-2 rounded-xl bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-medium text-xs flex items-center space-x-2 shadow-lg shadow-cyan-500/20 transition-all disabled:opacity-50"
                >
                  <span>{isSubmitting ? "Staging Application..." : "Stage & Preflight Trial"}</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )}

          {/* STEP 3: Preflight Trial & Readiness Confirmation */}
          {step === 3 && (
            <div className="space-y-6">
              <div className="p-6 rounded-xl bg-meeseek-950 border border-slate-800 space-y-4">
                <div className="flex items-center justify-between border-b border-slate-800/80 pb-4">
                  <div>
                    <span className="text-[10px] font-mono uppercase text-cyan-400 tracking-wider">Preflight Certification</span>
                    <h4 className="text-base font-bold text-white mt-0.5">
                      {stagedRequest?.app_name || appName}
                    </h4>
                    <p className="text-xs text-slate-400 mt-0.5">
                      Team: <span className="font-mono text-slate-200">{stagedRequest?.team_slug || teamSlug}</span> · Repositories: {repos.map((r) => r.name).join(", ")}
                    </p>
                  </div>

                  <div>
                    <span className={`px-2.5 py-1 rounded-full text-xs font-mono font-semibold ${
                      stagedRequest?.status === "trial_passed"
                        ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/40"
                        : stagedRequest?.status === "pending_approval"
                        ? "bg-purple-500/20 text-purple-400 border border-purple-500/40"
                        : stagedRequest?.status === "trial_failed"
                        ? "bg-red-500/20 text-red-400 border border-red-500/40"
                        : "bg-amber-500/20 text-amber-300 border border-amber-500/40"
                    }`}>
                      {stagedRequest?.status || "draft"}
                    </span>
                  </div>
                </div>

                {/* Preflight Actions Bar */}
                <div className="flex items-center justify-between flex-wrap gap-3">
                  <div className="flex items-center space-x-2">
                    <button
                      type="button"
                      onClick={() => {
                        const targetId = stagedRequest?.request_id || stagedRequest?.id;
                        if (targetId) handleRunTrial(targetId);
                      }}
                      disabled={isTrialing || !stagedRequest}
                      className="px-4 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white font-medium text-xs flex items-center space-x-2 transition-all shadow-lg shadow-cyan-600/20 disabled:opacity-50"
                    >
                      <Play className="w-3.5 h-3.5 fill-current" />
                      <span>{isTrialing ? "Executing Trial Build..." : "Run Trial Dry Run"}</span>
                    </button>

                    {stagedRequest?.status === "trial_failed" && activeTrialOutput && (
                      <button
                        type="button"
                        onClick={askMrMeeseeksToDebug}
                        className="px-3 py-2 rounded-xl bg-purple-600/20 hover:bg-purple-600/30 text-purple-300 border border-purple-500/30 text-xs font-medium flex items-center space-x-1.5 transition-all"
                      >
                        <Bot className="w-3.5 h-3.5 text-cyan-300" />
                        <span>Debug with Mr. Meeseeks</span>
                      </button>
                    )}
                  </div>

                  {stagedRequest?.status === "trial_passed" && (
                    <button
                      type="button"
                      onClick={handleSubmitForReview}
                      disabled={isFinalSubmitting}
                      className="px-5 py-2 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-medium text-xs flex items-center space-x-2 shadow-lg shadow-emerald-600/20 transition-all"
                    >
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      <span>{isFinalSubmitting ? "Submitting..." : "Submit for Platform Review"}</span>
                    </button>
                  )}
                </div>

                {/* Live Trial Terminal Console */}
                {activeTrialOutput && (
                  <div className="rounded-xl bg-black border border-slate-800 p-4 font-mono text-xs overflow-x-auto text-slate-300">
                    <div className="flex items-center justify-between pb-2 mb-2 border-b border-slate-900 text-slate-500 text-[11px]">
                      <span className="flex items-center space-x-1.5 text-cyan-400">
                        <Terminal className="w-3.5 h-3.5" />
                        <span>Preflight Execution Output</span>
                      </span>
                      <span>Exit status: {stagedRequest?.status === "trial_passed" ? "0 (Passed)" : "Non-zero"}</span>
                    </div>
                    <pre className="whitespace-pre-wrap max-h-64 overflow-y-auto font-mono text-[11px] leading-relaxed">
                      {activeTrialOutput}
                    </pre>
                  </div>
                )}
              </div>

              <div className="flex justify-between">
                <button
                  type="button"
                  onClick={() => setStep(2)}
                  className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs transition-all"
                >
                  Back to Repositories & Tests
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setStep(1);
                    setAppName("");
                    setStagedRequest(null);
                    setActiveTrialOutput(null);
                    setSuccessMessage(null);
                  }}
                  className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs transition-all"
                >
                  Onboard Another Application
                </button>
              </div>
            </div>
          )}
        </form>
      </div>
    )}
  </div>
);
};
