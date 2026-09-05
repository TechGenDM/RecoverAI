/**
 * RecoverAI API Client
 * Environment-driven API client supporting LIVE and SIMULATED modes.
 */

/**
 * Resolves the backend API base URL.
 * - In production (NODE_ENV === "production"), NEXT_PUBLIC_API_BASE_URL is mandatory.
 *   If missing, it throws a clear configuration error.
 * - In development / testing, it falls back to http://localhost:8000 for local workflows.
 */
export function getApiBaseUrl(): string {
  const envUrl =
    process.env.NEXT_PUBLIC_API_BASE_URL || process.env.NEXT_PUBLIC_API_URL;
  if (envUrl && envUrl.trim() !== "") {
    return envUrl.trim().replace(/\/$/, "");
  }

  if (process.env.NODE_ENV === "production") {
    throw new Error(
      "Missing mandatory production configuration: NEXT_PUBLIC_API_BASE_URL is not set. " +
        "For production deployments (such as Vercel), set NEXT_PUBLIC_API_BASE_URL to point " +
        "to your deployed backend URL (e.g. https://your-backend.up.railway.app)."
    );
  }

  return "http://localhost:8000";
}

export interface FailureReasonMetrics {
  total_cases: number;
  recovered_cases: number;
  recovery_rate_by_count: number | null;
  amount_at_risk_paise: number;
  amount_recovered_paise: number;
}

export interface DashboardMetrics {
  mode: "ALL" | "LIVE" | "SIMULATED" | string;

  // Case-Level Funnel Metrics
  total_cases: number;
  amount_at_risk_paise: number;
  amount_recovered_paise: number;
  recovered_cases: number;
  stopped_cases: number;
  escalated_cases: number;
  recovery_rate_by_count: number | null;
  recovery_rate_by_amount: number | null;

  // Status distribution (all 8 lifecycle statuses guaranteed)
  cases_by_status: Record<string, number>;

  // Case-Level Interventions (latest decision)
  cases_intervened_link: number;
  cases_intervened_wait: number;
  cases_intervened_escalate: number;
  cases_intervened_stop: number;

  // Action/Decision Metrics (Attempt-Level)
  payment_links_created: number;
  payment_links_paid: number;
  payment_links_expired: number;
  payment_links_cancelled: number;

  decisions_total: number;
  decisions_send_link: number;
  decisions_wait: number;
  decisions_escalate: number;
  decisions_stop: number;

  // Breakdown by Original Payment Failure Reason
  recovery_by_failure_reason: Record<string, FailureReasonMetrics>;
}

export interface CaseListItem {
  id: string;
  original_payment_id: string;
  status:
    | "CREATED"
    | "ANALYSING"
    | "WAITING"
    | "EXECUTING"
    | "LINK_SENT"
    | "RECOVERED"
    | "STOPPED"
    | "ESCALATED";
  mode: "LIVE" | "SIMULATED";
  amount_at_risk: number;
  amount_recovered: number;
  recovered_payment_id: string | null;
  stop_reason: string | null;
  attempt_count: number;
  created_at: string | null;
  resolved_at: string | null;
}

export interface PaymentSafe {
  razorpay_payment_id: string;
  amount: number;
  currency: string;
  method: string;
  error_code: string | null;
  error_reason: string | null;
  error_source: string | null;
  error_step: string | null;
  status: string;
  failed_at: string | null;
}

export interface DecisionSafe {
  id: string;
  attempt_number: number;
  recommended_action: string;
  effective_action: string;
  policy_verdict: string;
  policy_reason: string | null;
  reason: string;
  llm_confidence: number | null;
  heuristic_recovery_likelihood: number | null;
  delay_hours: number;
  created_at: string | null;
}

export interface ActionSafe {
  id: string;
  attempt_number: number;
  action_type: string;
  status: string;
  outcome: string | null;
  razorpay_link_id: string | null;
  razorpay_link_reference_id: string | null;
  razorpay_link_short_url: string | null;
  executed_at: string | null;
  failure_reason: string | null;
  created_at: string | null;
}

export interface CaseDetail {
  case: {
    id: string;
    original_payment_id: string;
    status: string;
    mode: "LIVE" | "SIMULATED";
    amount_at_risk: number;
    amount_recovered: number;
    recovered_payment_id: string | null;
    attempt_count: number;
    stop_reason: string | null;
    created_at: string | null;
    resolved_at: string | null;
    recovery_window_expires_at: string | null;
  };
  original_payment: PaymentSafe | null;
  customer_capability: {
    has_email: boolean;
    has_phone: boolean;
  };
  decisions: DecisionSafe[];
  actions: ActionSafe[];
}

export interface TimelineEvent {
  id: string;
  event_type: string;
  actor: string;
  mode: string;
  payload: Record<string, unknown>;
  created_at: string | null;
}

export interface SimulationResult {
  seed: number;
  scenario_count: number;
  new_cases_processed: number;
  already_existed: number;
  duration_ms: number;
  outcomes: Record<string, number>;
}

class RecoverAIApi {
  private getBaseUrl(): string {
    return getApiBaseUrl();
  }

  private async fetchJson<T>(endpoint: string, options?: RequestInit): Promise<T> {
    const url = `${this.getBaseUrl()}${endpoint}`;
    const res = await fetch(url, {
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      ...options,
    });

    if (!res.ok) {
      let errorDetail = `HTTP ${res.status} ${res.statusText}`;
      try {
        const errorJson = await res.json();
        if (errorJson.detail) {
          errorDetail = typeof errorJson.detail === "string" 
            ? errorJson.detail 
            : JSON.stringify(errorJson.detail);
        }
      } catch {
        // use default errorDetail
      }
      throw new Error(errorDetail);
    }

    return res.json();
  }

  /**
   * Fetch aggregate recovery metrics, optionally filtered by mode.
   */
  async getMetrics(mode?: "LIVE" | "SIMULATED"): Promise<DashboardMetrics> {
    const query = mode ? `?mode=${encodeURIComponent(mode)}` : "";
    return this.fetchJson<DashboardMetrics>(`/v1/metrics/recovery${query}`);
  }

  /**
   * Run deterministic simulation batch.
   */
  async runSimulation(seed: number, scenarioCount: number): Promise<SimulationResult> {
    return this.fetchJson<SimulationResult>("/v1/simulation/run", {
      method: "POST",
      body: JSON.stringify({ seed, scenario_count: scenarioCount }),
    });
  }

  /**
   * List recovery cases with filters.
   */
  async listCases(params?: {
    mode?: "LIVE" | "SIMULATED";
    status?: string;
    limit?: number;
    offset?: number;
  }): Promise<CaseListItem[]> {
    const searchParams = new URLSearchParams();
    if (params?.mode) searchParams.set("mode", params.mode);
    if (params?.status && params.status !== "ALL") searchParams.set("status", params.status);
    if (params?.limit) searchParams.set("limit", params.limit.toString());
    if (params?.offset) searchParams.set("offset", params.offset.toString());

    const queryString = searchParams.toString() ? `?${searchParams.toString()}` : "";
    return this.fetchJson<CaseListItem[]>(`/v1/cases${queryString}`);
  }

  /**
   * Get detailed case breakdown.
   */
  async getCaseDetail(caseId: string): Promise<CaseDetail> {
    return this.fetchJson<CaseDetail>(`/v1/cases/${encodeURIComponent(caseId)}`);
  }

  /**
   * Get sanitized audit timeline.
   */
  async getCaseTimeline(caseId: string): Promise<TimelineEvent[]> {
    return this.fetchJson<TimelineEvent[]>(
      `/v1/cases/${encodeURIComponent(caseId)}/timeline`
    );
  }
}

export const api = new RecoverAIApi();
