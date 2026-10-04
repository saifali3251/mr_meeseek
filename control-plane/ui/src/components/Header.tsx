import React, { useState, useRef, useEffect } from "react";
import { 
  Rocket, 
  ChevronDown, 
  ShieldAlert, 
  Sparkles, 
  Users,
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
  selectedRole: Role;
  onSelectRole: (role: Role) => void;
  authUser?: AuthUser | null;
  onSignOut?: () => void;
  isDark: boolean;
  onToggleTheme: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  team,
  selectedRole,
  onSelectRole,
  authUser,
  onSignOut,
  isDark,
  onToggleTheme,
}) => {
  const [isRoleDropdownOpen, setIsRoleDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on click outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsRoleDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const roles = [
    {
      id: "team" as Role,
      label: team?.name || "Core Platform",
      badge: "Team",
      desc: "Standard developer view for assigned microservices & tickets",
      icon: Users,
      color: "text-cyan-400",
    },
    {
      id: "admin" as Role,
      label: "Platform Admin",
      badge: "Admin",
      desc: "DevOps lead view: golden template builds & app onboarding review",
      icon: ShieldAlert,
      color: "text-purple-400",
    },
    {
      id: "judge" as Role,
      label: "Judge Mode",
      badge: "Evaluation",
      desc: "Impartial hackathon evaluator view with 1-click test cards",
      icon: Sparkles,
      color: "text-amber-400",
    },
  ];

  const currentRole = roles.find((r) => r.id === selectedRole) || roles[0];
  const CurrentIcon = currentRole.icon;

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
                Summoned for one ticket &middot; gone when it's done
              </p>
            </div>
          </a>

          {/* Right Bar: Role Dropdown & Auth Session */}
          <div className="flex items-center space-x-3">
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

            {/* Single Role Dropdown */}
            <div className="relative" ref={dropdownRef}>
              <button
                onClick={() => setIsRoleDropdownOpen(!isRoleDropdownOpen)}
                className="flex items-center space-x-2.5 px-4 py-2 rounded-xl bg-meeseek-900/80 hover:bg-meeseek-850 text-slate-200 border border-meeseek-border text-sm font-medium transition-all shadow-sm focus:outline-none focus:ring-1 focus:ring-cyan-500"
              >
                <CurrentIcon className={`w-4 h-4 ${currentRole.color}`} />
                <span>{currentRole.label}</span>
                <span className="text-xs px-2 py-0.5 rounded bg-meeseek-800 text-slate-400 uppercase font-mono font-semibold">
                  {currentRole.badge}
                </span>
                <ChevronDown className="w-4 h-4 text-slate-400 ml-1" />
              </button>

              {/* Dropdown Menu */}
              {isRoleDropdownOpen && (
                <div className="absolute right-0 mt-2 w-80 rounded-2xl bg-meeseek-900 border border-meeseek-border shadow-2xl p-2 z-50 animate-fadeIn">
                  <div className="px-3 py-2 border-b border-meeseek-border mb-1 text-xs font-mono text-slate-400 uppercase font-semibold">
                    Select Persona / Role
                  </div>
                  <div className="space-y-1">
                    {roles.map((r) => {
                      const Icon = r.icon;
                      const isSelected = r.id === selectedRole;
                      return (
                        <button
                          key={r.id}
                          onClick={() => {
                            onSelectRole(r.id);
                            setIsRoleDropdownOpen(false);
                          }}
                          className={`w-full text-left p-2.5 rounded-xl flex items-start space-x-3 transition-colors ${
                            isSelected
                              ? "bg-meeseek-800 text-white border border-slate-700/60"
                              : "text-slate-300 hover:bg-meeseek-850"
                          }`}
                        >
                          <div className={`mt-0.5 p-1.5 rounded-lg bg-slate-950 border border-slate-800 ${r.color}`}>
                            <Icon className="w-4 h-4" />
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center justify-between">
                              <span className="text-sm font-semibold">{r.label}</span>
                              <span className="text-xs px-2 py-0.5 rounded font-mono bg-slate-950 text-slate-400 font-semibold">
                                {r.badge}
                              </span>
                            </div>
                            <p className="text-xs text-slate-400 mt-0.5 leading-snug">
                              {r.desc}
                            </p>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>

            {/* Authenticated User & Sign Out */}
            {authUser && (
              <div className="flex items-center space-x-2 pl-3 border-l border-meeseek-border">
                <div className="flex items-center space-x-1.5 px-3 py-1.5 rounded-xl bg-meeseek-900 border border-slate-700/80 text-xs">
                  {authUser.role === "superadmin" ? (
                    <ShieldCheck className="w-3.5 h-3.5 text-purple-400" />
                  ) : (
                    <UserCheck className="w-3.5 h-3.5 text-cyan-400" />
                  )}
                  <span className="font-mono font-medium text-slate-200">
                    {authUser.username}
                  </span>
                  <span className={`text-[10px] uppercase font-mono font-bold px-1.5 py-0.5 rounded ${
                    authUser.role === "superadmin" 
                      ? "bg-purple-500/20 text-purple-300 border border-purple-500/40" 
                      : "bg-cyan-500/20 text-cyan-300 border border-cyan-500/40"
                  }`}>
                    {authUser.role}
                  </span>
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
