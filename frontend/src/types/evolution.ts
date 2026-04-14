export interface EvolutionRanking {
  agent_id: string;
  agent_name: string;
  rank: number;
  composite_score: number;
  total_tasks: number;
  delta_from_previous: number;
}
