export type WalletActionType = "credit" | "debit" | "reserve" | "release";

export interface WalletBalance {
  workflow_id: string;
  budget_limit: number;
  budget_used: number;
  budget_remaining: number;
  reserved: number;
  available: number;
}

export interface WalletEvent {
  wallet_event_id: string;
  workflow_id: string;
  agent_id: string | null;
  action_type: WalletActionType;
  credit_delta: number;
  balance_after: number;
  reason: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}
