import type {
  Paginated,
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
): Promise<Paginated<TrainingSetItem>> {
  return request<Paginated<TrainingSetItem>>(
    `/train-sets/${id}/items/?page=${page}`,
  );
}
