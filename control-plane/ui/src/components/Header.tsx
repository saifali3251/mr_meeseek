import React from "react";
import { 
  LogOut,
  ShieldCheck,
  UserCheck
} from "lucide-react";
import { KPIs, Team, AuthUser } from "../types";
import meeseekIcon from "../assets/meeseek-icon.webp";

export type Role = "admin" | "team" | "judge";

interface HeaderProps {
  kpis?: KPIs;
  team: Team | null;
  lastUpdated: string;
  selectedRole?: Role;
  onSelectRole?: (role: Role) => void;
  authUser?: AuthUser | null;
  onSignOut?: () => void;
  isDark: boolean;
  onToggleTheme: () => void;
  isLiveConnected?: boolean;
}

export const Header: React.FC<HeaderProps> = ({
  authUser,
  onSignOut,
  isDark,
  onToggleTheme,
  isLiveConnected = false,
}) => {
  return (
    <header className="border-b border-meeseek-border bg-meeseek-900/70 backdrop-blur-md sticky top-0 z-40">
      <div className="w-full px-6 lg:px-12 2xl:px-16">
        <div className="flex items-center justify-between h-[72px]">
          {/* Brand: Meeseeks icon + serif wordmark (matches the landing page) */}
          <a href="./" className="mee-brand flex items-center space-x-3.5 no-underline">
            <img src={meeseekIcon} alt="" width={42} height={42} className="mee-logo" />
            <div>
              <div className="flex items-center space-x-2.5">
                <span className="mee-wordmark text-white">Meeseek</span>
                <span className="px-2.5 py-0.5 text-[11px] font-mono font-semibold rounded-full bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 uppercase tracking-wider">
                  Console
                </span>
              </div>
              <p className="text-[13px] text-slate-400 font-medium mt-0.5">
                Summoned for one task &middot; gone when it's done
              </p>
            </div>
          </a>

          {/* Right Bar: Connection indicator, Theme toggle & Auth Session */}
          <div className="flex items-center space-x-3">
            {/* Real-time SSE indicator */}
            <div className={`hidden sm:inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-xl text-xs font-mono font-medium border transition-colors ${
              isLiveConnected 
                ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30" 
                : "bg-amber-500/10 text-amber-400 border-amber-500/30"
            }`}>
              <span className={`w-2 h-2 rounded-full ${isLiveConnected ? "bg-emerald-400 animate-pulse" : "bg-amber-400"}`} />
              <span>{isLiveConnected ? "Real-time ⚡" : "Connecting..."}</span>
            </div>

            {/* Day / night toggle */}
            <button
              type="button"
              onClick={onToggleTheme}
              className="theme-toggle"
              aria-pressed={isDark}
              aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
              title={isDark ? "Light mode" : "Dark mode"}
            >
              <span className="tt-sky" />
              <span className="tt-stars" aria-hidden="true"><i /><i /><i /><i /></span>
              <span className="tt-clouds" aria-hidden="true"><i /><i /><i /></span>
              <span className="tt-knob" aria-hidden="true"><i className="crater c1" /><i className="crater c2" /><i className="crater c3" /></span>
            </button>

            {/* Authenticated User Pill (Simplified: Admin shown once) */}
            {authUser && (
              <div className="flex items-center space-x-2 pl-2 border-l border-meeseek-border">
                <div className="flex items-center space-x-1.5 px-3 py-1.5 rounded-xl bg-meeseek-900 border border-slate-700/80 text-xs shadow-sm">
                  {authUser.role === "superadmin" || authUser.username.toLowerCase() === "admin" ? (
                    <ShieldCheck className="w-3.5 h-3.5 text-purple-400" />
                  ) : (
                    <UserCheck className="w-3.5 h-3.5 text-cyan-400" />
                  )}
                  
                  {/* If username equals role (e.g. admin), show it once cleanly */}
                  {authUser.username.toLowerCase() === authUser.role.toLowerCase() || authUser.username.toLowerCase() === "admin" ? (
                    <span className="font-mono font-semibold text-slate-200 uppercase tracking-wide">
                      {authUser.username}
                    </span>
                  ) : (
                    <>
                      <span className="font-mono font-medium text-slate-200">
                        {authUser.username}
                      </span>
                      <span className="text-slate-500 font-mono">·</span>
                      <span className={`text-[10px] uppercase font-mono font-bold px-1.5 py-0.5 rounded ${
                        authUser.role === "superadmin" 
                          ? "bg-purple-500/20 text-purple-300 border border-purple-500/40" 
                          : "bg-cyan-500/20 text-cyan-300 border border-cyan-500/40"
                      }`}>
                        {authUser.role}
                      </span>
                    </>
                  )}
                </div>

                {onSignOut && (
                  <button
                    onClick={onSignOut}
                    title="Sign out of Console"
                    className="p-2 rounded-xl bg-meeseek-900 hover:bg-red-950/40 text-slate-400 hover:text-red-400 border border-meeseek-border hover:border-red-500/40 transition-colors focus:outline-none"
                  >
                    <LogOut className="w-4 h-4" />
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  );
};
