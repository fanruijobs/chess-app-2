import type {
  MarkResponse,
  Paginated,
  ReworkItem,
  SourceEntry,
  TrainingItemResponse,
  TrainingOptions,
  TrainingSet,
  TrainingSetItem,
  User,
} from "./types";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function getCookie(name: string): string {
  const prefix = `${name}=`;
  for (const part of document.cookie.split(";")) {
    const value = part.trim();
    if (value.startsWith(prefix)) {
      return decodeURIComponent(value.slice(prefix.length));
    }
  }
  return "";
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const method = (options.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-CSRFToken", getCookie("csrftoken"));
  }

  const response = await fetch(`/api${path}`, {
    credentials: "include",
    ...options,
    headers,
  });

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const data = (await response.json()) as { detail?: string };
      if (data.detail) message = data.detail;
    } catch {
      // non-JSON error body
    }
    throw new ApiError(response.status, message);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function ensureCsrf(): Promise<void> {
  await request<{ csrfToken: string }>("/auth/csrf/");
}

export async function login(username: string, password: string): Promise<User> {
  await ensureCsrf();
  return request<User>("/auth/login/", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export async function logout(): Promise<void> {
  await request<{ ok: boolean }>("/auth/logout/", { method: "POST" });
}

export async function getMe(): Promise<User> {
  return request<User>("/auth/me/");
}

export async function getTrainingSets(
  page: number,
): Promise<Paginated<TrainingSet>> {
  return request<Paginated<TrainingSet>>(`/train-sets/?page=${page}`);
}

export async function getTrainingSet(id: number): Promise<TrainingSet> {
  return request<TrainingSet>(`/train-sets/${id}/`);
}

export async function getTrainingSetItems(
  id: number,
  page: number,
  status?: string,
): Promise<Paginated<TrainingSetItem>> {
  const query = new URLSearchParams({ page: String(page) });
  if (status) query.set("status", status);
  return request<Paginated<TrainingSetItem>>(
    `/train-sets/${id}/items/?${query.toString()}`,
  );
}

export async function getTrainingOptions(): Promise<TrainingOptions> {
  return request<TrainingOptions>("/train-sets/options/");
}

export async function createTrainingSet(
  name: string,
  entries: SourceEntry[],
): Promise<TrainingSet> {
  return request<TrainingSet>("/train-sets/", {
    method: "POST",
    body: JSON.stringify({ name, entries }),
  });
}

export async function cloneTrainingSet(
  sourceId: number,
  name: string,
): Promise<TrainingSet> {
  return request<TrainingSet>("/train-sets/clone/", {
    method: "POST",
    body: JSON.stringify({ source_id: sourceId, name }),
  });
}

export async function addTrainingSetItems(
  id: number,
  entries: SourceEntry[],
): Promise<{ added: number }> {
  return request<{ added: number }>(`/train-sets/${id}/items/`, {
    method: "POST",
    body: JSON.stringify({ entries }),
  });
}

export async function markAll(
  id: number,
  result: "passed" | "rework",
  status: string,
): Promise<{ marked: number }> {
  return request<{ marked: number }>(`/train-sets/${id}/mark-all/`, {
    method: "POST",
    body: JSON.stringify({ result, status }),
  });
}

export async function getTrainingItem(
  setId: number,
  itemId: number,
  status: string,
): Promise<TrainingItemResponse> {
  return request<TrainingItemResponse>(
    `/train-sets/${setId}/items/${itemId}/?status=${encodeURIComponent(status)}`,
  );
}

export async function markTrainingItem(
  setId: number,
  itemId: number,
  result: "passed" | "rework",
  status: string,
): Promise<MarkResponse> {
  return request<MarkResponse>(
    `/train-sets/${setId}/items/${itemId}/mark/`,
    {
      method: "POST",
      body: JSON.stringify({ result, status }),
    },
  );
}

export async function trainingItemAction(
  setId: number,
  itemId: number,
  action: "rotate" | "toggle_turn" | "set_fen",
  fen?: string,
): Promise<{ ok: boolean; fen: string }> {
  return request<{ ok: boolean; fen: string }>(
    `/train-sets/${setId}/items/${itemId}/action/`,
    {
      method: "POST",
      body: JSON.stringify({ action, fen }),
    },
  );
}

export async function getReworkItems(
  page: number,
  setId?: string,
  book?: string,
): Promise<Paginated<ReworkItem>> {
  const query = new URLSearchParams({ page: String(page) });
  if (setId) query.set("set", setId);
  if (book) query.set("book", book);
  return request<Paginated<ReworkItem>>(`/rework/?${query.toString()}`);
}
