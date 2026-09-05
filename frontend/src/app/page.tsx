"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Navbar from "@/components/Navbar";
import {
  api,
  type CaseListItem,
  type DashboardMetrics,
  type SimulationResult,
} from "@/lib/api";

export default function DashboardPage() {
  const [mode, setMode] = useState<"LIVE" | "SIMULATED">("SIMULATED");
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null);
  const [recentCases, setRecentCases] = useState<CaseListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Simulation form state
  const [simSeed, setSimSeed] = useState(42);
  const [simCount, setSimCount] = useState(10);
  const [simLoading, setSimLoading] = useState(false);
  const [simResult, setSimResult] = useState<SimulationResult | null>(null);
  const [simError, setSimError] = useState<string | null>(null);

  const handleRefresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const [m, c] = await Promise.all([
        api.getMetrics(mode),
        api.listCases({ mode, limit: 8 }),
      ]);
      setMetrics(m);
      setRecentCases(c);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Failed to connect to backend: ${msg}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let ignore = false;
    Promise.all([
      api.getMetrics(mode),
      api.listCases({ mode, limit: 8 }),
    ])
      .then(([m, c]) => {
        if (!ignore) {
          setMetrics(m);
          setRecentCases(c);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!ignore) {
          const msg = err instanceof Error ? err.message : String(err);
          setError(`Failed to connect to backend: ${msg}`);
          setLoading(false);
        }
      });

    return () => {
      ignore = true;
    };
  }, [mode]);

  const handleModeChange = (newMode: "LIVE" | "SIMULATED") => {
    setMode(newMode);
  };

  const handleRunSimulation = async (e: React.FormEvent) => {
    e.preventDefault();
    setSimLoading(true);
    setSimError(null);
    try {
      const result = await api.runSimulation(Number(simSeed), Number(simCount));
      setSimResult(result);
      // Refresh dashboard data
      await handleRefresh();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setSimError(msg);
    } finally {
      setSimLoading(false);
    }
  };

  const formatINR = (paise: number) => {
    const rupees = paise / 100;
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
      maximumFractionDigits: 2,
    }).format(rupees);
  };

  // Funnel calculations
  const totalCases = metrics?.total_cases || 0;
  const statusCounts = metrics?.cases_by_status || {};
  const recoveredCount = statusCounts["RECOVERED"] || 0;
  const linkSentCount = statusCounts["LINK_SENT"] || 0;
  const executingCount = statusCounts["EXECUTING"] || 0;
  const waitingCount = statusCounts["WAITING"] || 0;
  const analysingCount = statusCounts["ANALYSING"] || 0;
  const createdCount = statusCounts["CREATED"] || 0;
  const stoppedCount = statusCounts["STOPPED"] || 0;
  const escalatedCount = statusCounts["ESCALATED"] || 0;

  const funnelStages = [
    { label: "Created (Ingested)", count: totalCases, color: "stage-created" },
    {
      label: "Analysed by Policy Engine",
      count: totalCases - createdCount,
      color: "stage-analysing",
    },
    {
      label: "Intervention Dispatched (Link Sent / Waiting)",
      count: linkSentCount + waitingCount + executingCount + recoveredCount,
      color: "stage-link-sent",
    },
    {
      label: "Revenue Recovered",
      count: recoveredCount,
      color: "stage-recovered",
    },
  ];

  return (
    <div className="app-container">
      <Navbar currentMode={mode} onModeChange={handleModeChange} />

      <main className="main-content">
        <div className="page-header">
          <div>
            <h1 className="page-title">Autonomous Revenue Recovery Dashboard</h1>
            <p className="page-subtitle">
              Continuous failure interception, LLM decisioning, and automated
              reconciliation for Razorpay payment workflows.
            </p>
          </div>
          <button
            type="button"
            className="btn-secondary"
            onClick={handleRefresh}
            disabled={loading}
          >
            {loading ? "Refreshing..." : "Refresh Telemetry"}
          </button>
        </div>

        {error && (
          <div
            style={{
              backgroundColor: "var(--status-danger-bg)",
              border: "1px solid var(--status-danger-border)",
              color: "var(--status-danger-text)",
              padding: "1rem",
              borderRadius: "6px",
              marginBottom: "1.5rem",
              fontSize: "0.875rem",
            }}
          >
            {error}
          </div>
        )}

        {/* Top KPI Metrics Cards */}
        <section className="metrics-grid">
          <div className="metric-card">
            <span className="metric-title">Recovered Revenue</span>
            <div className="metric-value highlight-green">
              {formatINR(metrics?.amount_recovered_paise || 0)}
            </div>
            <span className="metric-sub">
              <strong>
                {metrics?.recovery_rate_by_amount !== null && metrics?.recovery_rate_by_amount !== undefined
                  ? (metrics.recovery_rate_by_amount * 100).toFixed(1)
                  : "0.0"}%
              </strong>{" "}
              of total failed volume recovered
            </span>
          </div>

          <div className="metric-card">
            <span className="metric-title">Revenue at Risk</span>
            <div className="metric-value">
              {formatINR(metrics?.amount_at_risk_paise || 0)}
            </div>
            <span className="metric-sub">
              Failed payments intercepted in {mode} mode
            </span>
          </div>

          <div className="metric-card">
            <span className="metric-title">Recovery Rate</span>
            <div className="metric-value highlight-blue">
              {metrics?.recovery_rate_by_count !== null && metrics?.recovery_rate_by_count !== undefined
                ? (metrics.recovery_rate_by_count * 100).toFixed(1)
                : "0.0"}%
            </div>
            <span className="metric-sub">
              {recoveredCount} of {totalCases} cases successfully resolved
            </span>
          </div>

          <div className="metric-card">
            <span className="metric-title">Payment Links Dispatched</span>
            <div className="metric-value">
              {metrics?.payment_links_created || 0}
            </div>
            <span className="metric-sub">
              Idempotent smart recovery links generated
            </span>
          </div>
        </section>

        {/* Middle Section: Funnel & Simulation Control */}
        <div className="dashboard-row">
          {/* Recovery Funnel & Efficiency */}
          <div className="card">
            <div className="card-header">
              <h2 className="card-title">Recovery Lifecycle Funnel</h2>
              <span className="badge badge-mode-simulated">
                {totalCases} Total Intercepted
              </span>
            </div>
            <div className="card-body">
              <p
                style={{
                  fontSize: "0.8125rem",
                  color: "var(--text-muted)",
                  marginBottom: "1.25rem",
                }}
              >
                Mathematically tracked state progression from initial webhook
                failure event to final terminal reconciliation.
              </p>

              <div className="funnel-container">
                {funnelStages.map((stage) => {
                  const pct =
                    totalCases > 0
                      ? Math.min(100, Math.max(0, (stage.count / totalCases) * 100))
                      : 0;
                  return (
                    <div key={stage.label} className="funnel-stage">
                      <div className="funnel-stage-header">
                        <span>{stage.label}</span>
                        <span>
                          {stage.count} cases ({pct.toFixed(0)}%)
                        </span>
                      </div>
                      <div className="funnel-bar-track">
                        <div
                          className={`funnel-bar-fill ${stage.color}`}
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Status Breakdown Pills */}
              <div
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: "0.5rem",
                  marginTop: "1.75rem",
                  paddingTop: "1rem",
                  borderTop: "1px solid var(--border-subtle)",
                }}
              >
                <span className="badge badge-recovered">
                  Recovered: {recoveredCount}
                </span>
                <span className="badge badge-link_sent">
                  Link Sent: {linkSentCount}
                </span>
                <span className="badge badge-waiting">
                  Waiting Window: {waitingCount}
                </span>
                <span className="badge badge-executing">
                  Executing: {executingCount}
                </span>
                <span className="badge badge-analysing">
                  Analysing: {analysingCount}
                </span>
                <span className="badge badge-stopped">
                  Stopped: {stoppedCount}
                </span>
                <span className="badge badge-escalated">
                  Escalated: {escalatedCount}
                </span>
              </div>
            </div>
          </div>

          {/* Simulation Control Panel */}
          <div className="card">
            <div className="card-header">
              <h2 className="card-title">Simulation Control Panel</h2>
              <span className="badge badge-mode-simulated">Deterministic</span>
            </div>
            <div className="card-body">
              <p
                style={{
                  fontSize: "0.8125rem",
                  color: "var(--text-muted)",
                  marginBottom: "1rem",
                }}
              >
                Trigger SHA-256 reproducible failure batches with synthetic customer
                profiles, heuristic decisioning, and fail-closed reconciliation.
              </p>

              <form onSubmit={handleRunSimulation} className="sim-form">
                <div className="form-group">
                  <label htmlFor="seed-input" className="form-label">Simulation Seed (Integer)</label>
                  <input
                    id="seed-input"
                    type="number"
                    className="form-input"
                    value={simSeed}
                    onChange={(e) => setSimSeed(parseInt(e.target.value) || 0)}
                    required
                  />
                  <span style={{ fontSize: "0.75rem", color: "var(--text-light)" }}>
                    Identical seed produces identical outcomes across runs.
                  </span>
                </div>

                <div className="form-group">
                  <label htmlFor="count-input" className="form-label">Scenario Count (1 - 100)</label>
                  <input
                    id="count-input"
                    type="number"
                    className="form-input"
                    min={1}
                    max={100}
                    value={simCount}
                    onChange={(e) => setSimCount(parseInt(e.target.value) || 1)}
                    required
                  />
                </div>

                <button
                  type="submit"
                  className="btn-primary"
                  disabled={simLoading}
                  style={{ width: "100%", marginTop: "0.5rem" }}
                >
                  {simLoading ? "Running Simulation..." : "Run Deterministic Batch"}
                </button>
              </form>

              {simError && (
                <div
                  style={{
                    marginTop: "1rem",
                    padding: "0.75rem",
                    backgroundColor: "var(--status-danger-bg)",
                    color: "var(--status-danger-text)",
                    borderRadius: "6px",
                    fontSize: "0.8125rem",
                  }}
                >
                  {simError}
                </div>
              )}

              {simResult && (
                <div
                  style={{
                    marginTop: "1rem",
                    padding: "0.75rem",
                    backgroundColor: "var(--status-success-bg)",
                    border: "1px solid var(--status-success-border)",
                    borderRadius: "6px",
                    fontSize: "0.8125rem",
                  }}
                >
                  <div style={{ fontWeight: 600, color: "var(--status-success-text)", marginBottom: "0.25rem" }}>
                    Simulation Batch Finished
                  </div>
                  <div>Processed: {simResult.new_cases_processed} new cases</div>
                  <div>Already Existed: {simResult.already_existed} (idempotent)</div>
                  <div>Duration: {simResult.duration_ms} ms</div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Failure Reason Breakdown Table */}
        {metrics?.recovery_by_failure_reason &&
          Object.keys(metrics.recovery_by_failure_reason).length > 0 && (
            <div className="card" style={{ marginBottom: "2rem" }}>
              <div className="card-header">
                <h2 className="card-title">Recovery Efficiency by Failure Reason</h2>
              </div>
              <div className="table-container">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Failure Reason</th>
                      <th>Total Cases</th>
                      <th>Recovered Cases</th>
                      <th>Recovery Rate (%)</th>
                      <th>Amount at Risk</th>
                      <th>Amount Recovered</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(metrics.recovery_by_failure_reason).map(
                      ([reason, data]) => {
                        const ratePct =
                          data.recovery_rate_by_count !== null &&
                          data.recovery_rate_by_count !== undefined
                            ? data.recovery_rate_by_count * 100
                            : 0;
                        return (
                          <tr key={reason}>
                            <td>
                              <span className="code-pill">{reason}</span>
                            </td>
                            <td>{data.total_cases}</td>
                            <td>{data.recovered_cases}</td>
                            <td>
                              <strong
                                style={{
                                  color:
                                    ratePct > 30
                                      ? "#059669"
                                      : "var(--text-muted)",
                                }}
                              >
                                {data.recovery_rate_by_count !== null &&
                                data.recovery_rate_by_count !== undefined
                                  ? `${ratePct.toFixed(1)}%`
                                  : "—"}
                              </strong>
                            </td>
                            <td>{formatINR(data.amount_at_risk_paise)}</td>
                            <td>
                              <strong style={{ color: "#059669" }}>
                                {formatINR(data.amount_recovered_paise)}
                              </strong>
                            </td>
                          </tr>
                        );
                      }
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

        {/* Recent Cases Preview */}
        <div className="card">
          <div className="card-header">
            <h2 className="card-title">Recent Intercepted Cases</h2>
            <Link href="/cases" className="btn-secondary">
              View All Cases in Explorer →
            </Link>
          </div>
          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Original Payment ID</th>
                  <th>Status</th>
                  <th>Mode</th>
                  <th>Amount at Risk</th>
                  <th>Recovered</th>
                  <th>Attempts</th>
                  <th>Created At</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {recentCases.length === 0 ? (
                  <tr>
                    <td colSpan={8} style={{ textAlign: "center", padding: "2rem", color: "var(--text-light)" }}>
                      No {mode} recovery cases recorded yet. Run a simulation batch above or ingest webhook events.
                    </td>
                  </tr>
                ) : (
                  recentCases.map((c) => (
                    <tr key={c.id}>
                      <td>
                        <span className="code-pill">{c.original_payment_id}</span>
                      </td>
                      <td>
                        <span
                          className={`badge badge-${c.status.toLowerCase()}`}
                        >
                          {c.status}
                        </span>
                      </td>
                      <td>
                        <span
                          className={`badge badge-mode-${c.mode.toLowerCase()}`}
                        >
                          {c.mode}
                        </span>
                      </td>
                      <td>{formatINR(c.amount_at_risk)}</td>
                      <td>
                        {c.amount_recovered > 0 ? (
                          <strong style={{ color: "#059669" }}>
                            {formatINR(c.amount_recovered)}
                          </strong>
                        ) : (
                          <span style={{ color: "var(--text-light)" }}>—</span>
                        )}
                      </td>
                      <td>{c.attempt_count}</td>
                      <td>
                        {c.created_at
                          ? new Date(c.created_at).toLocaleDateString("en-IN", {
                              month: "short",
                              day: "numeric",
                              hour: "2-digit",
                              minute: "2-digit",
                            })
                          : "—"}
                      </td>
                      <td>
                        <Link
                          href={`/cases/${c.id}`}
                          style={{
                            color: "var(--razorpay-dark-blue)",
                            fontWeight: 600,
                            fontSize: "0.8125rem",
                          }}
                        >
                          Inspect Case →
                        </Link>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </main>
    </div>
  );
}
