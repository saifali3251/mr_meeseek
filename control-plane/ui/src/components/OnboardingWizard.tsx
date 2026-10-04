import React, { useState } from "react";
import { 
  Plus, 
  GitBranch, 
  ShieldCheck, 
  Server, 
  Play, 
  CheckCircle2, 
  AlertCircle, 
  ArrowRight, 
  Layers,
  Terminal,
  FileCode
} from "lucide-react";
import { OnboardingRequest, Team, UserRole } from "../types";
import { createOnboardingRequest, triggerTrialRun } from "../api";

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
  const [appName, setAppName] = useState("");
  const [teamSlug, setTeamSlug] = useState(team?.slug || "core");
  const [contact, setContact] = useState("");
  
  // Repositories
  const [backendRepo, setBackendRepo] = useState({
    name: "test_backend",
    url: "https://github.com/saifali3251/test_backend.git",
    branch: "main",
    role: "backend",
  });
  const [frontendRepo, setFrontendRepo] = useState({
    name: "test_frontend",
    url: "https://github.com/saifali3251/test_frontend.git",
    branch: "main",
    role: "frontend",
  });

  // Notary commands
  const [testCmd, setTestCmd] = useState("PYTHONPATH=. pytest tests/");
  const [previewPort, setPreviewPort] = useState("18000");

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [activeTrialOutput, setActiveTrialOutput] = useState<string | null>(null);
  const [isTrialing, setIsTrialing] = useState(false);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    setErrorMessage(null);
    try {
      const res = await createOnboardingRequest({
        team_slug: teamSlug,
        app_name: appName || "custom-service",
        contact,
        repos: [backendRepo, frontendRepo],
      });
      setSuccessMessage(`Application "${res.app_name}" submitted for platform review!`);
      setStep(4);
      onRefresh();
    } catch (err: any) {
      setErrorMessage(err.message || "Failed to submit application");
    } finally {
      setIsSubmitting(false);
    }
  };

  const runTrial = async (requestId: string) => {
    setIsTrialing(true);
    try {
      const res = await triggerTrialRun(requestId);
      setActiveTrialOutput(res.trial_output || "Trial completed successfully: Exit 0");
      onRefresh();
    } catch (err: any) {
      setActiveTrialOutput(`Trial failed: ${err.message}`);
    } finally {
      setIsTrialing(false);
    }
  };

  return (
    <div className="space-y-8">
      {/* Onboarding Wizard Form Card */}
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
              Register your repository, define the Notary Exit 0 test contract, and generate a pre-seeded Copy-on-Write golden snapshot.
            </p>
          </div>

          {/* Stepper Progress */}
          <div className="flex items-center space-x-2 text-xs font-mono">
            {[1, 2, 3, 4].map((s) => (
              <div
                key={s}
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
            ))}
          </div>
        </div>

        {errorMessage && (
          <div className="mt-6 p-4 rounded-xl bg-red-950/40 border border-red-500/30 text-red-300 text-xs flex items-center space-x-2">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            <span>{errorMessage}</span>
          </div>
        )}

        {successMessage && (
          <div className="mt-6 p-4 rounded-xl bg-emerald-950/40 border border-emerald-500/30 text-emerald-300 text-xs flex items-center space-x-2">
            <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
            <span>{successMessage}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="mt-6 space-y-6">
          {/* STEP 1: App & Team Details */}
          {step === 1 && (
            <div className="space-y-4">
              <h4 className="text-sm font-semibold text-white flex items-center space-x-2">
                <span className="w-5 h-5 rounded-full bg-cyan-500/20 text-cyan-400 flex items-center justify-center text-xs">1</span>
                <span>Application Identity & Team Ownership</span>
              </h4>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div>
                  <label className="text-xs text-slate-400 block mb-1">Application Name</label>
                  <input
                    type="text"
                    required
                    value={appName}
                    onChange={(e) => setAppName(e.target.value)}
                    placeholder="e.g. billing-service"
                    className="w-full bg-meeseek-950 border border-meeseek-border rounded-xl px-3 py-2 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-cyan-500"
                  />
                </div>

                <div>
                  <label className="text-xs text-slate-400 block mb-1">Owning Team Slug</label>
                  <input
                    type="text"
                    required
                    value={teamSlug}
                    onChange={(e) => setTeamSlug(e.target.value)}
                    placeholder="e.g. core-eng"
                    className="w-full bg-meeseek-950 border border-meeseek-border rounded-xl px-3 py-2 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-cyan-500"
                  />
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
                </div>
              </div>

              <div className="pt-4 flex justify-end">
                <button
                  type="button"
                  onClick={() => setStep(2)}
                  className="px-5 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white font-medium text-xs flex items-center space-x-2 transition-all"
                >
                  <span>Next: Configure Repositories</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )}

          {/* STEP 2: Repositories */}
          {step === 2 && (
            <div className="space-y-4">
              <h4 className="text-sm font-semibold text-white flex items-center space-x-2">
                <span className="w-5 h-5 rounded-full bg-cyan-500/20 text-cyan-400 flex items-center justify-center text-xs">2</span>
                <span>Microservice Repositories</span>
              </h4>

              <div className="space-y-3">
                <div className="p-4 rounded-xl bg-meeseek-950 border border-slate-800 space-y-3">
                  <span className="text-xs font-mono text-cyan-400 font-semibold block">Primary Backend Service</span>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <input
                      type="text"
                      value={backendRepo.name}
                      onChange={(e) => setBackendRepo({ ...backendRepo, name: e.target.value })}
                      placeholder="Repo name (e.g. test_backend)"
                      className="bg-meeseek-900 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-white"
                    />
                    <input
                      type="text"
                      value={backendRepo.url}
                      onChange={(e) => setBackendRepo({ ...backendRepo, url: e.target.value })}
                      placeholder="Git URL (HTTPS)"
                      className="bg-meeseek-900 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-white md:col-span-2"
                    />
                  </div>
                </div>

                <div className="p-4 rounded-xl bg-meeseek-950 border border-slate-800 space-y-3">
                  <span className="text-xs font-mono text-purple-400 font-semibold block">Frontend UI Service (Optional)</span>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <input
                      type="text"
                      value={frontendRepo.name}
                      onChange={(e) => setFrontendRepo({ ...frontendRepo, name: e.target.value })}
                      placeholder="Repo name (e.g. test_frontend)"
                      className="bg-meeseek-900 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-white"
                    />
                    <input
                      type="text"
                      value={frontendRepo.url}
                      onChange={(e) => setFrontendRepo({ ...frontendRepo, url: e.target.value })}
                      placeholder="Git URL (HTTPS)"
                      className="bg-meeseek-900 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-white md:col-span-2"
                    />
                  </div>
                </div>
              </div>

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
                  onClick={() => setStep(3)}
                  className="px-5 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white font-medium text-xs flex items-center space-x-2 transition-all"
                >
                  <span>Next: Verification Contract</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )}

          {/* STEP 3: Notary Verification Contract */}
          {step === 3 && (
            <div className="space-y-4">
              <h4 className="text-sm font-semibold text-white flex items-center space-x-2">
                <span className="w-5 h-5 rounded-full bg-cyan-500/20 text-cyan-400 flex items-center justify-center text-xs">3</span>
                <span>Host Notary Verification Contract</span>
              </h4>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="text-xs text-slate-400 block mb-1">
                    Authoritative Notary Test Command (Exit 0 Gate)
                  </label>
                  <input
                    type="text"
                    required
                    value={testCmd}
                    onChange={(e) => setTestCmd(e.target.value)}
                    placeholder="PYTHONPATH=. pytest tests/"
                    className="w-full font-mono bg-meeseek-950 border border-meeseek-border rounded-xl px-3 py-2 text-xs text-cyan-300 placeholder-slate-600 focus:outline-none focus:border-cyan-500"
                  />
                  <p className="text-[11px] text-slate-500 mt-1">
                    Executed host-side outside agent control. A non-zero exit blocks PR creation.
                  </p>
                </div>

                <div>
                  <label className="text-xs text-slate-400 block mb-1">
                    Exposed Preview Port (HTTP Reverse Proxy)
                  </label>
                  <input
                    type="text"
                    required
                    value={previewPort}
                    onChange={(e) => setPreviewPort(e.target.value)}
                    placeholder="18000"
                    className="w-full font-mono bg-meeseek-950 border border-meeseek-border rounded-xl px-3 py-2 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-cyan-500"
                  />
                  <p className="text-[11px] text-slate-500 mt-1">
                    Allocated dynamically in pool for hot-reloaded browser testing.
                  </p>
                </div>
              </div>

              <div className="pt-4 flex justify-between">
                <button
                  type="button"
                  onClick={() => setStep(2)}
                  className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs transition-all"
                >
                  Back
                </button>
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="px-6 py-2 rounded-xl bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-medium text-xs flex items-center space-x-2 shadow-lg shadow-cyan-500/20 transition-all"
                >
                  <span>{isSubmitting ? "Submitting..." : "Submit for Platform Review"}</span>
                  <CheckCircle2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )}

          {/* STEP 4: Trial & Readiness Confirmation */}
          {step === 4 && (
            <div className="space-y-4">
              <div className="p-5 rounded-xl bg-meeseek-950 border border-slate-800 text-center">
                <CheckCircle2 className="w-10 h-10 text-emerald-400 mx-auto mb-2" />
                <h4 className="text-base font-bold text-white">Application Successfully Submitted</h4>
                <p className="text-xs text-slate-400 max-w-md mx-auto mt-1">
                  Your repository manifest has been staged. A platform engineer can now run an automated trial build before certifying it into the active golden pool.
                </p>

                <div className="mt-4 flex justify-center space-x-3">
                  <button
                    type="button"
                    onClick={() => setStep(1)}
                    className="px-4 py-1.5 rounded-lg bg-slate-800 text-slate-200 text-xs hover:bg-slate-700"
                  >
                    Register Another App
                  </button>
                </div>
              </div>
            </div>
          )}
        </form>
      </div>

      {/* Admin Review Table (Platform Leads) */}
      {isAdmin && (
        <div className="glass-panel rounded-2xl p-6 border border-meeseek-border">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h4 className="text-base font-bold text-white">Platform Onboarding Review Queue</h4>
              <p className="text-xs text-slate-400">
                Pending applications awaiting trial certification and golden image staging
              </p>
            </div>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-mono bg-purple-500/10 text-purple-400 border border-purple-500/30 font-semibold">
              {onboardingRequests.length} Pending
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
                    <th className="py-2.5 px-4">Repositories</th>
                    <th className="py-2.5 px-4">Status</th>
                    <th className="py-2.5 px-4 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {onboardingRequests.map((req) => (
                    <tr key={req.id} className="hover:bg-meeseek-900/40">
                      <td className="py-3 px-4 font-bold text-white">{req.app_name}</td>
                      <td className="py-3 px-4 text-slate-300 font-mono">{req.team_slug}</td>
                      <td className="py-3 px-4 text-slate-400 font-mono text-[11px]">
                        {req.repos.map((r) => r.name).join(", ")}
                      </td>
                      <td className="py-3 px-4">
                        <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-amber-500/10 text-amber-300 border border-amber-500/30">
                          {req.status}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-right">
                        <button
                          onClick={() => runTrial(req.id)}
                          disabled={isTrialing || (userRole !== undefined && userRole !== "superadmin")}
                          title={
                            userRole !== undefined && userRole !== "superadmin"
                              ? "Superadmin role required to certify onboarding builds"
                              : "Run automated trial build"
                          }
                          className={`px-3 py-1 rounded text-[11px] font-medium transition-all ${
                            userRole === "superadmin" || userRole === undefined
                              ? "bg-cyan-600/20 hover:bg-cyan-600/30 text-cyan-300 border border-cyan-500/30"
                              : "bg-slate-800/40 text-slate-500 border border-slate-700/40 cursor-not-allowed"
                          }`}
                        >
                          {isTrialing
                            ? "Running Trial..."
                            : userRole !== undefined && userRole !== "superadmin"
                            ? "Superadmin Only"
                            : "Run Trial Check"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {activeTrialOutput && (
            <div className="mt-4 p-4 rounded-xl bg-slate-950 border border-slate-800 font-mono text-xs text-slate-300">
              <span className="text-cyan-400 font-bold block mb-1">Trial Execution Log:</span>
              <pre className="whitespace-pre-wrap">{activeTrialOutput}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

