"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import Navbar from "@/components/Navbar";
import {
  api,
  type CaseDetail,
  type TimelineEvent,
} from "@/lib/api";

export default function CaseDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: caseId } = use(params);

  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copiedLinkId, setCopiedLinkId] = useState<string | null>(null);

  const handleCopyLink = async (linkUrl: string, actionId: string) => {
    try {
      await navigator.clipboard.writeText(linkUrl);
      setCopiedLinkId(actionId);
      setTimeout(() => setCopiedLinkId(null), 2000);
    } catch {
      // Clipboard fallback or ignore
    }
  };

  useEffect(() => {
    async function loadCaseData() {
      setLoading(true);
      setError(null);
      try {
        const [caseData, timelineData] = await Promise.all([
          api.getCaseDetail(caseId),
          api.getCaseTimeline(caseId),
        ]);
        setDetail(caseData);
        setTimeline(timelineData);
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        setError(`Failed to load case detail: ${msg}`);
      } finally {
        setLoading(false);
      }
    }

    loadCaseData();
  }, [caseId]);

  const formatINR = (paise: number) => {
    const rupees = paise / 100;
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
      maximumFractionDigits: 2,
    }).format(rupees);
  };

  const caseObj = detail?.case;
  const payment = detail?.original_payment;
  const customer = detail?.customer_capability;
  const decisions = detail?.decisions || [];
  const actions = detail?.actions || [];

  return (
    <div className="app-container">
      <Navbar currentMode={caseObj?.mode || "SIMULATED"} />

      <main className="main-content">
        {/* Navigation Breadcrumb */}
        <div style={{ marginBottom: "1.25rem" }}>
          <Link
            href="/cases"
            style={{
              color: "var(--razorpay-dark-blue)",
              fontSize: "0.875rem",
              fontWeight: 600,
              display: "inline-flex",
              alignItems: "center",
              gap: "0.4rem",
            }}
          >
            ← Back to Recovery Cases
          </Link>
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

        {loading ? (
          <div
            className="card"
            style={{ padding: "3rem", textAlign: "center", color: "var(--text-light)" }}
          >
            Loading case telemetry and audit timeline...
          </div>
        ) : !caseObj ? (
          <div
            className="card"
            style={{ padding: "3rem", textAlign: "center", color: "var(--text-light)" }}
          >
            Case not found.
          </div>
        ) : (
          <>
            {/* Header banner */}
            <div className="page-header" style={{ alignItems: "center" }}>
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
                  <h1 className="page-title">Case {caseObj.original_payment_id}</h1>
                  <span className={`badge badge-${caseObj.status.toLowerCase()}`}>
                    {caseObj.status}
                  </span>
                  <span className={`badge badge-mode-${caseObj.mode.toLowerCase()}`}>
                    {caseObj.mode}
                  </span>
                </div>
                <p className="page-subtitle">
                  Internal Case ID: <span className="code-pill">{caseObj.id}</span>
                </p>
              </div>

              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: "0.8125rem", color: "var(--text-muted)" }}>
                  Amount at Risk / Recovered
                </div>
                <div style={{ fontSize: "1.5rem", fontWeight: 700 }}>
                  <span style={{ color: "var(--text-main)" }}>
                    {formatINR(caseObj.amount_at_risk)}
                  </span>
                  {" / "}
                  <span style={{ color: caseObj.amount_recovered > 0 ? "#059669" : "var(--text-light)" }}>
                    {formatINR(caseObj.amount_recovered)}
                  </span>
                </div>
              </div>
            </div>

            {/* 4-Panel Grid of Case Facets */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
                gap: "1.25rem",
                marginBottom: "2rem",
              }}
            >
              {/* 1. Original Failed Payment */}
              <div className="card">
                <div className="card-header">
                  <h2 className="card-title">1. Original Failed Payment</h2>
                  <span className="badge badge-danger">FAILED</span>
                </div>
                <div className="card-body" style={{ fontSize: "0.875rem", lineHeight: "1.6" }}>
                  {payment ? (
                    <>
                      <div>
                        <strong>Payment ID:</strong>{" "}
                        <span className="code-pill">{payment.razorpay_payment_id}</span>
                      </div>
                      <div>
                        <strong>Amount:</strong> {formatINR(payment.amount)} {payment.currency}
                      </div>
                      <div>
                        <strong>Payment Method:</strong> {payment.method}
                      </div>
                      <div>
                        <strong>Error Reason:</strong>{" "}
                        <span className="code-pill">{payment.error_reason || "None"}</span>
                      </div>
                      <div>
                        <strong>Error Code:</strong> {payment.error_code || "None"}
                      </div>
                      <div>
                        <strong>Error Step / Source:</strong>{" "}
                        {payment.error_step || "—"} / {payment.error_source || "—"}
                      </div>
                      <div>
                        <strong>Failed At:</strong>{" "}
                        {payment.failed_at
                          ? new Date(payment.failed_at).toLocaleString("en-IN")
                          : "—"}
                      </div>
                    </>
                  ) : (
                    <span style={{ color: "var(--text-light)" }}>Payment data unavailable</span>
                  )}
                </div>
              </div>

              {/* 2. Customer Contactability */}
              <div className="card">
                <div className="card-header">
                  <h2 className="card-title">2. Customer Channels</h2>
                  <span className="badge badge-info">CHANNELS</span>
                </div>
                <div className="card-body" style={{ fontSize: "0.875rem", lineHeight: "1.6" }}>
                  <p style={{ color: "var(--text-muted)", marginBottom: "0.75rem" }}>
                    PII is redacted. Communication capability flags:
                  </p>
                  <div style={{ display: "flex", gap: "1rem", marginBottom: "1rem" }}>
                    <div
                      style={{
                        padding: "0.75rem",
                        backgroundColor: customer?.has_email
                          ? "var(--status-success-bg)"
                          : "var(--surface-subtle)",
                        borderRadius: "6px",
                        flex: 1,
                        textAlign: "center",
                      }}
                    >
                      <div style={{ fontWeight: 600 }}>Email Notification</div>
                      <div style={{ color: customer?.has_email ? "#059669" : "var(--text-light)", fontSize: "0.8125rem" }}>
                        {customer?.has_email ? "✓ Available" : "✗ Missing"}
                      </div>
                    </div>

                    <div
                      style={{
                        padding: "0.75rem",
                        backgroundColor: customer?.has_phone
                          ? "var(--status-success-bg)"
                          : "var(--surface-subtle)",
                        borderRadius: "6px",
                        flex: 1,
                        textAlign: "center",
                      }}
                    >
                      <div style={{ fontWeight: 600 }}>SMS / WhatsApp</div>
                      <div style={{ color: customer?.has_phone ? "#059669" : "var(--text-light)", fontSize: "0.8125rem" }}>
                        {customer?.has_phone ? "✓ Available" : "✗ Missing"}
                      </div>
                    </div>
                  </div>
                  <div>
                    <strong>Total Attempts Made:</strong> {caseObj.attempt_count}
                  </div>
                  {caseObj.stop_reason && (
                    <div style={{ marginTop: "0.5rem" }}>
                      <strong>Stop Reason:</strong>{" "}
                      <span className="badge badge-stopped">{caseObj.stop_reason}</span>
                    </div>
                  )}
                </div>
              </div>

              {/* 3. M2 Decision Pipeline */}
              <div className="card">
                <div className="card-header">
                  <h2 className="card-title">3. AI Decisioning (M2)</h2>
                  <span className="badge badge-analysing">{decisions.length} Decisions</span>
                </div>
                <div className="card-body" style={{ fontSize: "0.875rem" }}>
                  {decisions.length === 0 ? (
                    <span style={{ color: "var(--text-light)" }}>No decisions logged yet.</span>
                  ) : (
                    decisions.map((d) => (
                      <div
                        key={d.id}
                        style={{
                          borderBottom: "1px solid var(--border-subtle)",
                          paddingBottom: "0.75rem",
                          marginBottom: "0.75rem",
                        }}
                      >
                        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.25rem" }}>
                          <strong>Attempt #{d.attempt_number}</strong>
                          <span
                            className={`badge ${
                              d.policy_verdict === "APPROVED"
                                ? "badge-recovered"
                                : "badge-waiting"
                            }`}
                          >
                            {d.policy_verdict}
                          </span>
                        </div>
                        <div>
                          <strong>Action:</strong>{" "}
                          <span className="code-pill">{d.effective_action}</span>{" "}
                          {d.recommended_action !== d.effective_action && (
                            <span style={{ color: "var(--text-light)", fontSize: "0.75rem" }}>
                              (LLM recommended: {d.recommended_action})
                            </span>
                          )}
                        </div>
                        <div style={{ fontSize: "0.8125rem", color: "var(--text-muted)", marginTop: "0.25rem" }}>
                          {d.reason}
                        </div>
                        <div
                          style={{
                            display: "flex",
                            gap: "1rem",
                            marginTop: "0.5rem",
                            fontSize: "0.75rem",
                            color: "var(--text-light)",
                          }}
                        >
                          <span>
                            Confidence:{" "}
                            {d.llm_confidence ? `${(d.llm_confidence * 100).toFixed(0)}%` : "—"}
                          </span>
                          <span>
                            Heuristic Likelihood:{" "}
                            {d.heuristic_recovery_likelihood
                              ? `${(d.heuristic_recovery_likelihood * 100).toFixed(0)}%`
                              : "—"}
                          </span>
                          <span>Delay: {d.delay_hours}h</span>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>

              {/* 4. M3 Recovery Actions */}
              <div className="card">
                <div className="card-header">
                  <h2 className="card-title">4. Execution Engine (M3)</h2>
                  <span className="badge badge-executing">{actions.length} Actions</span>
                </div>
                <div className="card-body" style={{ fontSize: "0.875rem" }}>
                  {actions.length === 0 ? (
                    <span style={{ color: "var(--text-light)" }}>No actions dispatched yet.</span>
                  ) : (
                    actions.map((a) => (
                      <div
                        key={a.id}
                        style={{
                          borderBottom: "1px solid var(--border-subtle)",
                          paddingBottom: "0.75rem",
                          marginBottom: "0.75rem",
                        }}
                      >
                        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.25rem" }}>
                          <strong>Attempt #{a.attempt_number}: {a.action_type}</strong>
                          <span className={`badge badge-${a.status.toLowerCase()}`}>
                            {a.status}
                          </span>
                        </div>
                        {a.razorpay_link_id && (
                          <div>
                            <strong>Link ID:</strong>{" "}
                            <span className="code-pill">{a.razorpay_link_id}</span>
                          </div>
                        )}
                        {a.razorpay_link_reference_id && (
                          <div>
                            <strong>Reference ID:</strong>{" "}
                            <span className="code-pill">{a.razorpay_link_reference_id}</span>
                          </div>
                        )}
                        {a.outcome && (
                          <div>
                            <strong>Outcome:</strong> {a.outcome}
                          </div>
                        )}
                        {a.failure_reason && (
                          <div style={{ color: "#dc2626", fontSize: "0.8125rem" }}>
                            Failure: {a.failure_reason}
                          </div>
                        )}

                        {/* Razorpay Test Mode Payment Link Section */}
                        {a.action_type === "SEND_PAYMENT_LINK" && (
                          <div
                            style={{
                              marginTop: "0.75rem",
                              padding: "0.75rem",
                              backgroundColor: "var(--surface-subtle)",
                              borderRadius: "6px",
                              border: "1px solid var(--border-subtle)",
                            }}
                          >
                            <div
                              style={{
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "space-between",
                                marginBottom: "0.375rem",
                              }}
                            >
                              <span
                                style={{
                                  fontSize: "0.75rem",
                                  fontWeight: 600,
                                  color: "var(--text-muted)",
                                  textTransform: "uppercase",
                                  letterSpacing: "0.04em",
                                }}
                              >
                                Razorpay Test Mode Recovery Link
                              </span>
                              {a.outcome === "EXPIRED" && (
                                <span className="badge badge-stopped" style={{ fontSize: "0.7rem" }}>
                                  Link Expired
                                </span>
                              )}
                              {a.outcome === "CANCELLED" && (
                                <span className="badge badge-stopped" style={{ fontSize: "0.7rem" }}>
                                  Link Cancelled
                                </span>
                              )}
                            </div>

                            {a.razorpay_link_short_url ? (
                              <div>
                                <div style={{ wordBreak: "break-all", marginBottom: "0.5rem" }}>
                                  <span className="code-pill" style={{ fontSize: "0.8125rem" }}>
                                    {a.razorpay_link_short_url}
                                  </span>
                                </div>
                                <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
                                  <a
                                    href={a.razorpay_link_short_url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="btn-primary"
                                    style={{
                                      padding: "0.375rem 0.75rem",
                                      fontSize: "0.8125rem",
                                      textDecoration: "none",
                                      display: "inline-flex",
                                      alignItems: "center",
                                      gap: "0.25rem",
                                    }}
                                  >
                                    Open Payment Link &nearr;
                                  </a>
                                  <button
                                    type="button"
                                    onClick={() => handleCopyLink(a.razorpay_link_short_url!, a.id)}
                                    className="btn-secondary"
                                    style={{
                                      padding: "0.375rem 0.75rem",
                                      fontSize: "0.8125rem",
                                      display: "inline-flex",
                                      alignItems: "center",
                                      gap: "0.25rem",
                                    }}
                                  >
                                    {copiedLinkId === a.id ? "✓ Copied" : "Copy Link"}
                                  </button>
                                </div>
                              </div>
                            ) : a.status === "PENDING" || a.status === "EXECUTING" ? (
                              <div
                                style={{
                                  color: "var(--text-muted)",
                                  fontSize: "0.8125rem",
                                  display: "flex",
                                  alignItems: "center",
                                  gap: "0.4rem",
                                  marginTop: "0.25rem",
                                }}
                              >
                                <span className="status-dot status-dot-live" />
                                <span>Action executing — payment link creation in progress...</span>
                              </div>
                            ) : a.status === "FAILED" ? (
                              <div style={{ color: "#dc2626", fontSize: "0.8125rem", marginTop: "0.25rem" }}>
                                Payment link unavailable: execution failed.
                              </div>
                            ) : (
                              <div style={{ color: "var(--text-light)", fontSize: "0.8125rem", marginTop: "0.25rem" }}>
                                Payment link unavailable.
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>

            {/* Audit Event Timeline */}
            <div className="card">
              <div className="card-header">
                <h2 className="card-title">Chronological Audit Trail</h2>
                <span className="badge badge-info">{timeline.length} Events</span>
              </div>
              <div className="card-body">
                {timeline.length === 0 ? (
                  <span style={{ color: "var(--text-light)" }}>No audit events recorded.</span>
                ) : (
                  <div className="timeline">
                    {timeline.map((event) => (
                      <div key={event.id} className="timeline-item">
                        <div className="timeline-dot" />
                        <div className="timeline-content">
                          <div className="timeline-header">
                            <span className="timeline-title">
                              {event.event_type}
                            </span>
                            <span className="timeline-time">
                              {event.created_at
                                ? new Date(event.created_at).toLocaleString("en-IN")
                                : "—"}
                            </span>
                          </div>
                          <div style={{ fontSize: "0.75rem", color: "var(--text-light)", marginBottom: "0.25rem" }}>
                            Actor: <strong>{event.actor}</strong> | Mode: {event.mode}
                          </div>

                          {event.payload && Object.keys(event.payload).length > 0 && (
                            <pre className="timeline-payload">
                              {JSON.stringify(event.payload, null, 2)}
                            </pre>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
