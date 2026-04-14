export interface AuditLogEntry {
  log_id: string;
  event_type: string;
  actor_id: string;
  input_summary: string | null;
  output_summary: string | null;
  created_at: string;
}
