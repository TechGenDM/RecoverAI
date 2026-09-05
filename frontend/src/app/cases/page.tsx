"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Navbar from "@/components/Navbar";
import { api, type CaseListItem } from "@/lib/api";

const STATUS_FILTERS = [
  "ALL",
  "RECOVERED",
  "LINK_SENT",
  "WAITING",
  "EXECUTING",
  "ANALYSING",
  "CREATED",
  "STOPPED",
  "ESCALATED",
];

export default function CasesExplorerPage() {
  const [mode, setMode] = useState<"LIVE" | "SIMULATED">("SIMULATED");
  const [selectedStatus, setSelectedStatus] = useState<string>("ALL");
  const [searchQuery, setSearchQuery] = useState("");
  const [cases, setCases] = useState<CaseListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const handleRefresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.listCases({
        mode,
        status: selectedStatus === "ALL" ? undefined : selectedStatus,
        limit: 100,
      });
      setCases(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Failed to fetch cases: ${msg}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let ignore = false;
    api
      .listCases({
        mode,
        status: selectedStatus === "ALL" ? undefined : selectedStatus,
        limit: 100,
      })
      .then((data) => {
        if (!ignore) {
          setCases(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!ignore) {
          const msg = err instanceof Error ? err.message : String(err);
          setError(`Failed to fetch cases: ${msg}`);
          setLoading(false);
        }
      });

    return () => {
      ignore = true;
    };
  }, [mode, selectedStatus]);

  const filteredCases = cases.filter((c) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (
      c.original_payment_id.toLowerCase().includes(q) ||
      (c.recovered_payment_id && c.recovered_payment_id.toLowerCase().includes(q)) ||
      c.id.toLowerCase().includes(q)
    );
  });

  const formatINR = (paise: number) => {
    const rupees = paise / 100;
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
      maximumFractionDigits: 2,
    }).format(rupees);
  };

  return (
    <div className="app-container">
      <Navbar currentMode={mode} onModeChange={setMode} />

      <main className="main-content">
        <div className="page-header">
          <div>
            <h1 className="page-title">Recovery Cases Explorer</h1>
            <p className="page-subtitle">
              Audit and inspect all intercepted failed payments, decision policies,
              and automated recovery actions.
            </p>
          </div>
          <button
            type="button"
            className="btn-secondary"
            onClick={handleRefresh}
            disabled={loading}
          >
            {loading ? "Loading..." : "Refresh Cases"}
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

        {/* Filter Controls Bar */}
        <div
          className="card"
          style={{ marginBottom: "1.5rem", padding: "1rem 1.25rem" }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: "1rem",
            }}
          >
            {/* Status Filter Pills */}
            <div style={{ display: "flex", gap: "0.35rem", flexWrap: "wrap" }}>
              {STATUS_FILTERS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setSelectedStatus(s)}
                  className="btn-secondary"
                  style={{
                    padding: "0.35rem 0.75rem",
                    fontSize: "0.75rem",
                    backgroundColor:
                      selectedStatus === s
                        ? "var(--primary-navy)"
                        : "var(--surface-white)",
                    color:
                      selectedStatus === s ? "#ffffff" : "var(--text-muted)",
                    borderColor:
                      selectedStatus === s
                        ? "var(--primary-navy)"
                        : "var(--border-subtle)",
                  }}
                >
                  {s}
                </button>
              ))}
            </div>

            {/* Search input */}
            <div style={{ minWidth: "260px" }}>
              <input
                type="text"
                placeholder="Search payment ID or case ID..."
                className="form-input"
                style={{ width: "100%", padding: "0.4rem 0.75rem" }}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>
          </div>
        </div>

        {/* Cases Data Table */}
        <div className="card">
          <div className="card-header">
            <h2 className="card-title">
              {filteredCases.length} {mode} Case
              {filteredCases.length === 1 ? "" : "s"} Found
            </h2>
            <span style={{ fontSize: "0.8125rem", color: "var(--text-light)" }}>
              Sorted by created date (newest first)
            </span>
          </div>

          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Original Payment ID</th>
                  <th>Status</th>
                  <th>Mode</th>
                  <th>Amount at Risk</th>
                  <th>Recovered Amount</th>
                  <th>Recovered Payment ID</th>
                  <th>Attempts</th>
                  <th>Created At</th>
                  <th>Resolved At</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td
                      colSpan={10}
                      style={{ textAlign: "center", padding: "3rem", color: "var(--text-light)" }}
                    >
                      Loading recovery cases...
                    </td>
                  </tr>
                ) : filteredCases.length === 0 ? (
                  <tr>
                    <td
                      colSpan={10}
                      style={{ textAlign: "center", padding: "3rem", color: "var(--text-light)" }}
                    >
                      No matching cases found for the selected criteria.
                    </td>
                  </tr>
                ) : (
                  filteredCases.map((c) => (
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
                      <td>
                        {c.recovered_payment_id ? (
                          <span
                            className="code-pill"
                            style={{
                              backgroundColor: "var(--status-success-bg)",
                              borderColor: "var(--status-success-border)",
                              color: "var(--status-success-text)",
                            }}
                          >
                            {c.recovered_payment_id}
                          </span>
                        ) : (
                          <span style={{ color: "var(--text-light)" }}>—</span>
                        )}
                      </td>
                      <td>{c.attempt_count}</td>
                      <td>
                        {c.created_at
                          ? new Date(c.created_at).toLocaleString("en-IN", {
                              month: "short",
                              day: "numeric",
                              hour: "2-digit",
                              minute: "2-digit",
                            })
                          : "—"}
                      </td>
                      <td>
                        {c.resolved_at
                          ? new Date(c.resolved_at).toLocaleString("en-IN", {
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
                            whiteSpace: "nowrap",
                          }}
                        >
                          Inspect →
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
