"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

interface NavbarProps {
  currentMode: "LIVE" | "SIMULATED";
  onModeChange?: (mode: "LIVE" | "SIMULATED") => void;
}

export default function Navbar({ currentMode, onModeChange }: NavbarProps) {
  const pathname = usePathname();

  return (
    <>
      <header className="navbar">
        <div className="nav-brand-group">
          <Link href="/" className="brand-title">
            <span style={{ color: "#ffffff" }}>Recover</span>
            <span style={{ color: "var(--razorpay-blue)" }}>AI</span>
          </Link>
          <span className="brand-badge">Autonomous Recovery</span>

          <nav className="nav-links" style={{ marginLeft: "1.5rem" }}>
            <Link
              href="/"
              className={`nav-link ${pathname === "/" ? "active" : ""}`}
            >
              Dashboard
            </Link>
            <Link
              href="/cases"
              className={`nav-link ${pathname?.startsWith("/cases") ? "active" : ""}`}
            >
              Cases Explorer
            </Link>
          </nav>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          {onModeChange ? (
            <div className="mode-switch-group">
              <button
                type="button"
                className={`mode-btn ${currentMode === "LIVE" ? "active-live" : ""}`}
                onClick={() => onModeChange("LIVE")}
              >
                <span className="status-dot" />
                LIVE
              </button>
              <button
                type="button"
                className={`mode-btn ${currentMode === "SIMULATED" ? "active-sim" : ""}`}
                onClick={() => onModeChange("SIMULATED")}
              >
                <span className="status-dot" />
                SIMULATED
              </button>
            </div>
          ) : (
            <div className="mode-switch-group">
              <span
                className={`mode-btn ${
                  currentMode === "LIVE" ? "active-live" : "active-sim"
                }`}
                style={{ cursor: "default" }}
              >
                <span className="status-dot" />
                {currentMode} MODE
              </span>
            </div>
          )}
        </div>
      </header>

      {currentMode === "SIMULATED" && (
        <div className="simulation-banner">
          <div>
            <strong>Simulation Sandbox Active:</strong> Operating in deterministic
            evaluation mode. Real Razorpay webhooks and API calls are isolated.
          </div>
          <span className="badge badge-mode-simulated">SHA-256 Engine</span>
        </div>
      )}
    </>
  );
}
