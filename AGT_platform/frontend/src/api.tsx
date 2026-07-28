/// <reference types="vite/client" />
function withAuthHeaders(extra?: HeadersInit): Headers {
  return new Headers(extra ?? {});
}

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

/** Public API origin for callers outside this module. */
export function apiBase(): string {
  return API_BASE;
}

async function fetchWithAuthRetry(path: string, init: RequestInit): Promise<Response> {
  const url = `${API_BASE}${path}`;
  return fetch(url, init);
}

/** Parse and validate API responses. */
async function handleResponse(res: Response, label: string): Promise<string> {
  const text = await res.text().catch(() => "");
  if (!res.ok) throw new Error(`${label} failed: ${res.status} ${text}`);
  return text;
}

export type Assignment = {
  id: string;
  filename: string;
  status: string;
  suggested_grade?: number | null;
  feedback?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

/**
 * Browser → MinIO (presigned PUT). Do not send auth headers; signature embeds access.
 */
export async function putToPresignedUrl(
  url: string,
  body: Blob,
  contentType: string
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(url, {
      method: "PUT",
      body,
      headers: { "Content-Type": contentType },
    });
  } catch (e: unknown) {
    let host = "";
    try {
      host = new URL(url).hostname;
    } catch {
      /* ignore */
    }
    const dockerish = host === "minio" || /\.svc\.cluster\.local$/i.test(host);
    const hint = dockerish
      ? " The upload URL uses a hostname only reachable inside Docker (e.g. minio). Set MINIO_PRESIGN_ENDPOINT to a URL your browser can open, such as http://127.0.0.1:9000, and restart the API."
      : " Check that object storage is running, CORS allows your app origin, and the URL is HTTPS/HTTP as expected.";
    if (e instanceof TypeError) {
      throw new Error(`Storage upload failed (network / blocked request).${hint} (${e.message})`);
    }
    throw e;
  }
  if (!res.ok) {
    const t = await res.text().catch(() => "");
    throw new Error(`Object-store PUT failed: ${res.status} ${t}`);
  }
}

export type DirectUploadStartResponse = {
  submission_id: number;
  assignment_id?: number;
  status: string;
  uploads: Array<{
    artifact_id: number;
    object_key: string;
    upload_url: string;
    content_type: string;
  }>;
};

/**
 * Presigned flow: start → PUT each file to MinIO → finalize. Keeps large files off the API EC2.
 */
export async function submitAssignmentDirect(
  assignmentId: number,
  files: File[],
  onProgress?: (fileIndex: number, fraction: number) => void
): Promise<{ submission_id: number; status: string }> {
  const start = (await api.post("/api/submissions/direct-upload/start", {
    assignment_id: assignmentId,
    files: files.map((f) => ({
      filename: f.name,
      content_type: f.type || "application/octet-stream",
    })),
  })) as DirectUploadStartResponse;

  for (let i = 0; i < start.uploads.length; i++) {
    const u = start.uploads[i];
    const file = files[i];
    if (!file) continue;
    await putToPresignedUrl(u.upload_url, file, u.content_type);
    onProgress?.(i, 1);
  }

  const done = (await api.post(`/api/submissions/${start.submission_id}/finalize`, {})) as {
    submission_id: number;
    status: string;
  };
  return done;
}

export const api = {
  async get(path: string) {
    const res = await fetchWithAuthRetry(path, {
      method: "GET",
      headers: withAuthHeaders(),
      credentials: "include",
    });
    const text = await handleResponse(res, `GET ${path}`);
    return text ? JSON.parse(text) : null;
  },

  async post(path: string, body?: BodyInit | object) {
    const headers = withAuthHeaders();
    let payload: BodyInit | undefined;
    if (body !== undefined && body !== null && typeof body === "object" && !(body instanceof FormData)) {
      payload = JSON.stringify(body);
      headers.set("Content-Type", "application/json");
    } else {
      payload = body as BodyInit;
      if (typeof body === "string") headers.set("Content-Type", "application/json");
      if (body instanceof FormData) headers.delete("Content-Type");
    }

    const res = await fetchWithAuthRetry(path, {
      method: "POST",
      body: payload,
      headers,
      credentials: "include",
    });

    const text = await handleResponse(res, `POST ${path}`);
    return text ? JSON.parse(text) : null;
  },

  async patch(path: string, body: object) {
    const headers = withAuthHeaders();
    headers.set("Content-Type", "application/json");
    const res = await fetchWithAuthRetry(path, {
      method: "PATCH",
      body: JSON.stringify(body),
      headers,
      credentials: "include",
    });
    const text = await handleResponse(res, `PATCH ${path}`);
    return text ? JSON.parse(text) : null;
  },

  async put(path: string, body: object) {
    const headers = withAuthHeaders();
    headers.set("Content-Type", "application/json");
    const res = await fetchWithAuthRetry(path, {
      method: "PUT",
      body: JSON.stringify(body),
      headers,
      credentials: "include",
    });
    const text = await handleResponse(res, `PUT ${path}`);
    return text ? JSON.parse(text) : null;
  },

  async delete(path: string) {
    const res = await fetchWithAuthRetry(path, {
      method: "DELETE",
      headers: withAuthHeaders(),
      credentials: "include",
    });
    const text = await handleResponse(res, `DELETE ${path}`);
    return text ? JSON.parse(text) : null;
  },
};

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

// ── Course / Enrollment / Assignment types ────────────────────────────────

export type CourseListItem = {
  id: number;
  code: string;
  title: string;
  enrollment_role: "student" | "teacher" | null;
};

export type CourseDetail = {
  id: number;
  code: string;
  title: string;
  enrollments: Array<{
    enrollment_id?: number;
    user_id: number;
    email: string;
    name: string;
    role: "student" | "teacher";
  }>;
};

export type RubricCriterion = {
  criterion: string;
  max_score: number;
};

export type CourseAssignment = {
  id: number;
  course_id: number;
  title: string;
  description: string;
  modality: string;
  rubric: RubricCriterion[];
  due_date: string | null;
  created_at: string | null;
};

export type CreateAssignmentPayload = {
  title: string;
  description?: string;
  modality: string;
  rubric?: RubricCriterion[];
  due_date?: string | null;
};

export type CreateEnrollmentPayload = {
  course_id: number;
  user_id: number;
  role: "student" | "teacher";
};

// ── Course helpers ────────────────────────────────────────────────────────

export function listCourses(): Promise<CourseListItem[]> {
  return api.get("/api/courses");
}

export function getCourse(courseId: number): Promise<CourseDetail> {
  return api.get(`/api/courses/${courseId}`);
}

export function listCourseAssignments(courseId: number): Promise<CourseAssignment[]> {
  return api.get(`/api/courses/${courseId}/assignments`);
}

export function createCourseAssignment(
  courseId: number,
  payload: CreateAssignmentPayload
): Promise<{ id: number; title: string; course_id: number }> {
  return api.post(`/api/courses/${courseId}/assignments`, payload);
}

export function updateCourseAssignment(
  courseId: number,
  assignmentId: number,
  payload: Partial<CreateAssignmentPayload>
): Promise<{ id: number; title: string }> {
  return api.patch(`/api/courses/${courseId}/assignments/${assignmentId}`, payload);
}

// ── Admin enrollment helpers ──────────────────────────────────────────────

export function adminListCourseEnrollments(
  courseId: number
): Promise<CourseDetail["enrollments"]> {
  return api.get(`/api/admin/courses/${courseId}/enrollments`);
}

export function adminEnrollUser(payload: CreateEnrollmentPayload): Promise<{ id: number }> {
  return api.post("/api/admin/enrollments", payload);
}

export function adminRemoveEnrollment(enrollmentId: number): Promise<{ ok: boolean }> {
  return api.delete(`/api/admin/enrollments/${enrollmentId}`);
}

// ── Assignment Creation ("Assignment Library") ─────────────────────────────
// Upload-context flow mirrors the standalone autograder (start → PUT to MinIO → finalize),
// but instead of grading, finalize runs the parsing + chunking agents and returns an editable
// question/answer bank for the review page.

export type AssignmentLibraryFileSpec = {
  filename: string;
  content_type: string;
  artifact_kind: "blank_assignment" | "answer_key" | "rubric";
};

export type AssignmentLibraryEntry = {
  id: number;
  title: string;
  description: string;
  modality: string;
  rubric: RubricCriterion[];
  created_at: string | null;
  blank_assignment_text: string;
  answer_key_text: string;
};

export type AssignmentQuestionChunk = {
  id: number;
  question_id: string;
  order_index: number;
  question_text: string;
  answer_text: string;
  is_edited: boolean;
};

export type AssignmentLibraryStartResponse = {
  assignment_id: number;
  status: string;
  uploads: DirectUploadStartResponse["uploads"];
};

export async function startAssignmentLibraryEntry(payload: {
  title: string;
  description?: string;
  modality?: string;
  files: AssignmentLibraryFileSpec[];
  rubric_text?: string;
  answer_key_text?: string;
}): Promise<AssignmentLibraryStartResponse> {
  return api.post("/api/assignment-library/start", payload);
}

export async function finalizeAssignmentLibraryEntry(
  assignmentId: number
): Promise<AssignmentLibraryEntry & { status: string; chunking_status: string }> {
  return api.post(`/api/assignment-library/${assignmentId}/finalize`, {});
}

/** Presigned flow: start → PUT each file to MinIO → finalize (runs the parse + chunk agents). */
export async function createAssignmentLibraryEntryDirect(
  payload: {
    title: string;
    description?: string;
    modality?: string;
    rubric_text?: string;
    answer_key_text?: string;
  },
  files: File[],
  fileSpecs: AssignmentLibraryFileSpec[],
  onProgress?: (fileIndex: number, fraction: number) => void
): Promise<AssignmentLibraryEntry & { status: string; chunking_status: string }> {
  const start = await startAssignmentLibraryEntry({ ...payload, files: fileSpecs });
  for (let i = 0; i < start.uploads.length; i++) {
    const u = start.uploads[i];
    const file = files[i];
    if (!file) continue;
    await putToPresignedUrl(u.upload_url, file, u.content_type);
    onProgress?.(i, 1);
  }
  return finalizeAssignmentLibraryEntry(start.assignment_id);
}

export function listAssignmentLibraryEntries(): Promise<AssignmentLibraryEntry[]> {
  return api.get("/api/assignment-library");
}

export function getAssignmentLibraryEntry(
  assignmentId: number
): Promise<AssignmentLibraryEntry & { chunks: AssignmentQuestionChunk[] }> {
  return api.get(`/api/assignment-library/${assignmentId}`);
}

export function saveAssignmentLibraryChunks(
  assignmentId: number,
  chunks: Array<Pick<AssignmentQuestionChunk, "question_id" | "question_text" | "answer_text"> & {
    id?: number;
  }>
): Promise<{ assignment_id: number; chunks: AssignmentQuestionChunk[] }> {
  return api.put(`/api/assignment-library/${assignmentId}/chunks`, { chunks });
}

export type AssignmentMaterialKind = "blank_assignment" | "answer_key";

export type AssignmentMaterialView =
  | { type: "notebook"; cells: { cell_type: string; source: string }[] }
  | { type: "spreadsheet"; sheets: { name: string; rows: string[][] }[] }
  | { type: "pdf" }
  | { type: "text"; text: string }
  | { type: "unsupported" };

export type AssignmentMaterialViewResponse = {
  filename: string;
  download_url: string;
  view: AssignmentMaterialView;
};

/** Original-form view (notebook cells / spreadsheet grid / embeddable PDF / plaintext) of an
 * uploaded blank template or answer key, for the review page's viewer tabs. */
export function getAssignmentMaterialView(
  assignmentId: number,
  kind: AssignmentMaterialKind
): Promise<AssignmentMaterialViewResponse> {
  return api.get(`/api/assignment-library/${assignmentId}/materials/${kind}/view`);
}

// ── Standalone autograder (public API; optional Bearer when logged in) ────

async function standaloneAutograderFetch(path: string, init: RequestInit): Promise<Response> {
  const url = `${API_BASE}${path}`;
  return fetch(url, {
    ...init,
    credentials: "include",
  });
}

async function standaloneAutograderJson<T>(res: Response, label: string): Promise<T> {
  const text = await res.text().catch(() => "");
  if (!res.ok) {
    throw new Error(`${label} failed: ${res.status} ${text}`);
  }
  return (text ? JSON.parse(text) : null) as T;
}

export type StandaloneSubmissionSummary = {
  id: number;
  title: string;
  status: string;
  final_score: number | null;
  created_at: string | null;
};

export type StandaloneSubmissionDetail = {
  id: number;
  title: string;
  status: string;
  final_score: number | null;
  max_points?: number | null;
  rubric_points_earned?: number | null;
  grading_instructions?: string | null;
  grading_dispatch_at: string | null;
  created_at?: string | null;
  grading_report_object_key?: string | null;
  question_grades?: Array<{
    chunk_id?: string;
    source_chunk_id?: string;
    overall?: {
      score?: number | null;
      max_points?: number | null;
      rubric_points_earned?: number | null;
      confidence?: number | null;
    };
    question_payload?: Record<string, unknown>;
    criteria: Array<{
      criterion: string;
      score: number;
      max_points?: number | null;
      rubric_points_earned?: number | null;
      confidence: number;
      justification?: string;
      student_evidence?: string;
      evidence?: Record<string, unknown>;
    }>;
  }>;
  ai_scores: Array<{
    criterion: string;
    score: number;
    confidence: number;
    justification?: string;
    rationale: string;
    question?: string | null;
    student_evidence?: string;
    evidence?: Record<string, unknown>;
  }>;
};

export type StandaloneListResponse = {
  items: StandaloneSubmissionSummary[];
  total: number;
  page: number;
  per_page: number;
};

export type StandaloneFileSpec = {
  filename: string;
  content_type: string;
  artifact_kind?: "submission" | "rubric" | "answer_key" | "blank_assignment";
};

export async function startStandaloneSubmission(payload: {
  title: string;
  files: StandaloneFileSpec[];
  rubric_text?: string;
  answer_key_text?: string;
}): Promise<DirectUploadStartResponse> {
  const res = await standaloneAutograderFetch("/api/standalone/submissions/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return standaloneAutograderJson<DirectUploadStartResponse>(res, "POST standalone start");
}

export async function finalizeStandaloneSubmission(
  submissionId: number,
  options?: { enqueue_grading?: boolean },
): Promise<{
  submission_id: number;
  status: string;
  celery_task_id?: string;
  enqueue_grading?: boolean;
  already_enqueued?: boolean;
  already_finalized?: boolean;
}> {
  const body: Record<string, boolean> = {};
  if (options?.enqueue_grading === false) {
    body.enqueue_grading = false;
  }
  const res = await standaloneAutograderFetch(
    `/api/standalone/submissions/${submissionId}/finalize`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return standaloneAutograderJson(res, "POST standalone finalize");
}

export async function patchStandaloneContext(
  submissionId: number,
  payload: {
    rubric_text?: string;
    answer_key_text?: string;
    grading_instructions?: string;
  },
): Promise<{ submission_id: number; ok: boolean }> {
  const res = await standaloneAutograderFetch(
    `/api/standalone/submissions/${submissionId}/context`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  return standaloneAutograderJson(res, "PATCH standalone context");
}

export async function presignStandaloneContextFiles(
  submissionId: number,
  files: StandaloneFileSpec[],
): Promise<{ submission_id: number; uploads: DirectUploadStartResponse["uploads"] }> {
  const res = await standaloneAutograderFetch(
    `/api/standalone/submissions/${submissionId}/context_files/presign`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ files }),
    },
  );
  return standaloneAutograderJson(res, "POST standalone context presign");
}

export async function enqueueStandaloneGrading(submissionId: number): Promise<{
  submission_id: number;
  status: string;
  celery_task_id?: string;
  already_enqueued?: boolean;
}> {
  const res = await standaloneAutograderFetch(
    `/api/standalone/submissions/${submissionId}/enqueue_grading`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    },
  );
  return standaloneAutograderJson(res, "POST standalone enqueue");
}

/**
 * Standalone autograder: presigned start → MinIO PUTs → finalize with grading enqueued.
 * Mirrors {@link submitAssignmentDirect}; caller supplies parallel `files` and `fileSpecs` (with artifact_kind).
 */
export async function submitStandaloneDirect(
  title: string,
  files: File[],
  fileSpecs: StandaloneFileSpec[],
  onProgress?: (fileIndex: number, fraction: number) => void,
  options?: { rubric_text?: string; answer_key_text?: string },
): Promise<{ submission_id: number; status: string }> {
  const start = await startStandaloneSubmission({
    title,
    files: fileSpecs,
    rubric_text: options?.rubric_text,
    answer_key_text: options?.answer_key_text,
  });
  for (let i = 0; i < start.uploads.length; i++) {
    const u = start.uploads[i];
    const file = files[i];
    if (!file) continue;
    await putToPresignedUrl(u.upload_url, file, u.content_type);
    onProgress?.(i, 1);
  }
  return finalizeStandaloneSubmission(start.submission_id, { enqueue_grading: true });
}

export async function listStandaloneSubmissions(
  page = 1,
  perPage = 20,
): Promise<StandaloneListResponse> {
  const res = await standaloneAutograderFetch(
    `/api/standalone/submissions?page=${page}&per_page=${perPage}`,
    { method: "GET" },
  );
  return standaloneAutograderJson(res, "GET standalone list");
}

export async function getStandaloneSubmission(id: number): Promise<StandaloneSubmissionDetail> {
  const res = await standaloneAutograderFetch(`/api/standalone/submissions/${id}`, {
    method: "GET",
  });
  return standaloneAutograderJson(res, "GET standalone submission");
}

export async function getStandaloneGradingReportUrl(
  submissionId: number,
): Promise<{ download_url: string; object_key: string }> {
  const res = await standaloneAutograderFetch(
    `/api/standalone/submissions/${submissionId}/report`,
    { method: "GET" },
  );
  return standaloneAutograderJson(res, "GET standalone grading report");
}

export async function deleteStandaloneSubmission(id: number): Promise<{ ok: boolean } | null> {
  const res = await standaloneAutograderFetch(`/api/standalone/submissions/${id}`, {
    method: "DELETE",
  });
  return standaloneAutograderJson(res, "DELETE standalone submission");
}

/**
 * Course/library submission results — same response shape as {@link StandaloneSubmissionDetail}
 * (see `app.routes.submissions.get_submission`), so `SubmissionReview.tsx` can reuse
 * `GradingResultView` exactly like `StandaloneResult.tsx` does.
 */
export type CourseSubmissionDetail = {
  id: number;
  status: string;
  assignment_title?: string | null;
  student_id?: number | null;
  final_score: number | null;
  final_feedback?: string | null;
  max_points?: number | null;
  rubric_points_earned?: number | null;
  grading_dispatch_at?: string | null;
  created_at?: string | null;
  grading_report_object_key?: string | null;
  question_grades?: StandaloneSubmissionDetail["question_grades"];
  ai_scores: StandaloneSubmissionDetail["ai_scores"];
};

export function getCourseSubmission(id: number): Promise<CourseSubmissionDetail> {
  return api.get(`/api/submissions/${id}`);
}

export function getCourseSubmissionReportUrl(
  submissionId: number,
): Promise<{ download_url: string; object_key: string }> {
  return api.get(`/api/submissions/${submissionId}/report`);
}
