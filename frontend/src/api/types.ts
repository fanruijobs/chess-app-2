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
  san_best: string;
  status: TrainingItemStatus;
  attempts: number;
  order: number;
  last_trained_at: string | null;
  puzzle_id: number | null;
  turning_point_id: number | null;
}

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface TrainingSetOption {
  id: number;
  name: string;
  rework: number;
}

export interface BookOption {
  slug: string;
  title: string;
}

export interface ClassificationOption {
  value: string;
  label: string;
}

export interface TrainingOptions {
  books: BookOption[];
  players: string[];
  classifications: ClassificationOption[];
  sets: TrainingSetOption[];
  rework_sets: TrainingSetOption[];
  rework_books: BookOption[];
}

export type SourceType = "book" | "turning_points" | "rework";

export interface SourceEntry {
  source_type: SourceType;
  book_slug?: string;
  pages_from?: string | number;
  pages_to?: string | number;
  numbers_from?: string;
  numbers_to?: string;
  numbers?: string[];
  player?: string;
  date_from?: string;
  date_to?: string;
  classifications?: string[];
  from_set?: string | number;
}

export interface TrainingItemResponse {
  item: TrainingSetItem;
  set: TrainingSet;
  category: TrainingItemStatus;
  next_item_id: number | null;
}

export interface TrainingStats {
  total: number;
  pending: number;
  rework: number;
  passed: number;
  pass_rate: number | null;
}

export interface MarkResponse {
  ok: boolean;
  item_status: TrainingItemStatus;
  attempts: number;
  next_item_id: number | null;
  stats: TrainingStats;
}

export interface ReworkItem {
  id: number;
  label: string;
  status: TrainingItemStatus;
  attempts: number;
  san_best: string;
  last_trained_at: string | null;
  set_id: number;
  set_name: string;
  book_title: string | null;
  book_slug: string | null;
  puzzle_id: number | null;
  turning_point_id: number | null;
}
