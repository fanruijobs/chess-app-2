export interface User {
  id: number;
  username: string;
  is_superuser: boolean;
  is_staff: boolean;
}

export interface TrainingSet {
  id: number;
  name: string;
  owner: string;
  created_at: string;
  total: number;
  pending: number;
  rework: number;
  passed: number;
  pass_rate: number | null;
}

export type TrainingItemStatus = "pending" | "passed" | "rework";

export interface TrainingSetItem {
  id: number;
  label: string;
  fen: string;
  status: TrainingItemStatus;
  attempts: number;
  order: number;
  puzzle_id: number | null;
  turning_point_id: number | null;
}

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}
