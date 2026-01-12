/// <reference types="vite/client" />

export type Assignment = {
  id: string;
  filename: string;
  status: string;
  suggested_grade?: number | null;
  feedback?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

/**
 * api: simple wrapper used by dashboards.
 * Usage: api.get("/api/assignments"), api.post("/api/assignments", formData)
 */
export const api = {
  async get(path: string) {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "GET",
      credentials: "include",
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`GET ${path} failed: ${res.status} ${text}`);
    }
    return res.json();
  },

  async post(path: string, body?: BodyInit) {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      body,
      credentials: "include",
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`POST ${path} failed: ${res.status} ${text}`);
    }
    return res.json();
  },
};

// Convenience functions (optional, nice for pages)
export async function listAssignments(): Promise<Assignment[]> {
  return api.get("/api/assignments");
}

export async function uploadAssignment(file: File): Promise<{ id: string }> {
  const fd = new FormData();
  fd.append("file", file);
  return api.post("/api/assignments", fd);
}

export async function getAssignment(id: string): Promise<Assignment> {
  return api.get(`/api/assignments/${id}`);
}

export async function startGrading(id: string): Promise<{ ok: boolean }> {
  return api.post(`/api/assignments/${id}/grade`);
}