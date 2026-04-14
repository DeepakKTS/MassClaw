export interface PolicyRule {
  rule_id: string;
  name: string;
  description: string;
  action: "allow" | "deny" | "flag";
  enabled: boolean;
}
