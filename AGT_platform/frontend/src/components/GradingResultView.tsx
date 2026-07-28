import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  LinearProgress,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tabs,
  Typography,
} from "@mui/material";
import ArrowBackOutlined from "@mui/icons-material/ArrowBackOutlined";
import DownloadOutlined from "@mui/icons-material/DownloadOutlined";
import StatusChip from "./StatusChip";

/**
 * Shared grading-results view for both the standalone autograder's results page
 * (`StandaloneResult.tsx`) and the course/library submission review page
 * (`SubmissionReview.tsx`) — one UI reading one common report shape (see
 * `app.grading.multimodal.grading_report` / `grading_report_view` on the backend), so both
 * "same UI and backend" for real instead of two hand-maintained copies of this page.
 */

export type GradingResultCriterion = {
  criterion: string;
  score: number;
  max_points?: number | null;
  rubric_points_earned?: number | null;
  confidence: number;
  justification?: string;
  student_evidence?: string;
  evidence?: unknown;
};

export type GradingResultQuestionGrade = {
  chunk_id?: string;
  source_chunk_id?: string;
  overall?: {
    score?: number | null;
    max_points?: number | null;
    rubric_points_earned?: number | null;
    confidence?: number | null;
  };
  question_payload?: Record<string, unknown>;
  criteria: GradingResultCriterion[];
};

export type GradingResultAiScore = {
  criterion: string;
  score: number;
  confidence: number;
  justification?: string;
  rationale?: string;
  question?: string | null;
  student_evidence?: string;
  evidence?: unknown;
};

export type GradingResultData = {
  status: string;
  final_score: number | null;
  max_points?: number | null;
  rubric_points_earned?: number | null;
  grading_report_object_key?: string | null;
  question_grades?: GradingResultQuestionGrade[];
  ai_scores: GradingResultAiScore[];
};

const POLL_STATUSES = new Set(["uploading", "uploaded", "queued", "grading"]);

function gradeBarColor(score: number): "success" | "warning" | "error" {
  if (score >= 90) return "success";
  if (score >= 70) return "warning";
  return "error";
}

function confidenceColor(conf: number): "success" | "warning" | "error" {
  if (conf >= 0.85) return "success";
  if (conf >= 0.7) return "warning";
  return "error";
}

function normalizeConfidence01(raw: unknown): number | null {
  const n = Number(raw);
  if (!Number.isFinite(n)) return null;
  const normalized = n > 1 ? n / 100 : n;
  return Math.max(0, Math.min(1, normalized));
}

function criterionChipColor(fraction: number): "success" | "warning" | "error" {
  if (fraction >= 80) return "success";
  if (fraction >= 60) return "warning";
  return "error";
}

// Deliberately surfaces only the student's own words: the grading decision's "evidence" must
// never be (or fall back to) the question or answer-key text, so this never reads
// `trio.answer_key_segment` or `trio.question`, even as a fallback.
function summarizeEvidence(ev: unknown): string {
  if (!ev || typeof ev !== "object") return "";
  const obj = ev as Record<string, unknown>;
  const trio = (obj.trio ?? {}) as Record<string, unknown>;
  const student = String(trio.student_response ?? "").trim();
  return student ? student.slice(0, 450) : "";
}

function studentEvidenceSnippet(row: GradingResultAiScore): string {
  const direct = String(row.student_evidence || "").trim();
  if (direct) return direct.slice(0, 450);
  return "Missing direct student evidence in grader output.";
}

export type GradingResultViewProps = {
  title: string;
  data: GradingResultData;
  onBack: () => void;
  backLabel: string;
  onDownloadReport?: () => void | Promise<void>;
};

export function isPollingStatus(status: string): boolean {
  return POLL_STATUSES.has(status);
}

export default function GradingResultView({
  title,
  data: sub,
  onBack,
  backLabel,
  onDownloadReport,
}: GradingResultViewProps) {
  const [reportBusy, setReportBusy] = useState(false);
  const [activeQuestionTab, setActiveQuestionTab] = useState(0);

  const handleDownloadReport = async () => {
    if (!onDownloadReport) return;
    setReportBusy(true);
    try {
      await onDownloadReport();
    } finally {
      setReportBusy(false);
    }
  };

  const scores = sub.ai_scores ?? [];
  const polling = isPollingStatus(sub.status);
  const baseConfidenceValues = scores
    .map((s) => normalizeConfidence01(s.confidence))
    .filter((c): c is number => c !== null);
  const baseAvgConfidence =
    baseConfidenceValues.length > 0
      ? baseConfidenceValues.reduce((sum, c) => sum + c, 0) / baseConfidenceValues.length
      : 0;
  const questionGrades: GradingResultQuestionGrade[] = (sub.question_grades ?? []).filter(
    (q): q is GradingResultQuestionGrade => Array.isArray(q.criteria),
  );
  const fallbackQuestionGrade: GradingResultQuestionGrade = {
    chunk_id: "fallback_chunk",
    source_chunk_id: "fallback_chunk",
    overall: {
      score: sub.final_score ?? null,
      max_points: sub.max_points ?? null,
      rubric_points_earned: sub.rubric_points_earned ?? null,
      confidence: baseAvgConfidence,
    },
    question_payload: {
      note: "Question payload unavailable; displaying assignment-level fallback.",
    },
    criteria: scores.map((s) => ({
      criterion: s.criterion,
      score: s.score,
      max_points: null,
      rubric_points_earned: null,
      confidence: s.confidence,
      justification: s.justification || s.rationale,
      student_evidence: s.student_evidence || studentEvidenceSnippet(s),
      evidence: s.evidence,
    })),
  };
  const effectiveQuestionGrades: GradingResultQuestionGrade[] = questionGrades.length
    ? questionGrades
    : [fallbackQuestionGrade];
  const confidenceValues = effectiveQuestionGrades
    .flatMap((q) => (q.criteria || []).map((c) => normalizeConfidence01(c.confidence)))
    .filter((c): c is number => c !== null);
  const fallbackConfidenceValues = baseConfidenceValues;
  const effectiveConfidenceValues =
    confidenceValues.length > 0 ? confidenceValues : fallbackConfidenceValues;
  const avgConfidence =
    effectiveConfidenceValues.length > 0
      ? effectiveConfidenceValues.reduce((sum, c) => sum + c, 0) / effectiveConfidenceValues.length
      : 0;
  const currentQuestion =
    effectiveQuestionGrades[Math.min(activeQuestionTab, effectiveQuestionGrades.length - 1)];
  const rubricTotals = effectiveQuestionGrades.reduce(
    (acc, q) => {
      const ov = q.overall || {};
      const earned = Number(ov.rubric_points_earned ?? 0);
      const max = Number(ov.max_points ?? 0);
      if (Number.isFinite(earned)) acc.earned += earned;
      if (Number.isFinite(max)) acc.max += max;
      return acc;
    },
    { earned: 0, max: 0 },
  );

  return (
    <Box>
      <Button startIcon={<ArrowBackOutlined />} onClick={onBack} sx={{ mb: 2 }} aria-label={backLabel}>
        {backLabel}
      </Button>

      <Box sx={{ display: "flex", alignItems: "center", gap: 2, mb: 2, flexWrap: "wrap" }}>
        <Typography variant="h3" component="h1">
          {title}
        </Typography>
        <StatusChip status={sub.status} />
      </Box>

      {sub.status === "needs_review" && (
        <Alert severity="warning" sx={{ mb: 2 }} role="status">
          One or more criteria have confidence below the recommended threshold. A human review is
          recommended before treating this grade as final.
        </Alert>
      )}

      {polling && <LinearProgress sx={{ mb: 2 }} aria-label="Grading in progress" />}

      {sub.final_score != null && !polling && (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="overline" color="text.secondary">
              Overall score
            </Typography>
            <Box sx={{ display: "flex", alignItems: "center", gap: 2, mt: 1, flexWrap: "wrap" }}>
              <Chip
                label={`${sub.final_score.toFixed(1)} / 100`}
                color={gradeBarColor(Number(sub.final_score))}
                sx={{ fontSize: "1.1rem", fontWeight: 700, height: 40 }}
              />
              {(rubricTotals.max > 0 || (sub.rubric_points_earned != null && sub.max_points != null)) && (
                <Chip
                  size="small"
                  label={`Rubric points ${(rubricTotals.earned > 0 ? rubricTotals.earned : Number(sub.rubric_points_earned || 0)).toFixed(1)} / ${(rubricTotals.max > 0 ? rubricTotals.max : Number(sub.max_points || 0)).toFixed(1)}`}
                  variant="outlined"
                />
              )}
              {effectiveConfidenceValues.length > 0 && (
                <Chip
                  size="small"
                  label={`Avg confidence ${(avgConfidence * 100).toFixed(1)}%`}
                  color={confidenceColor(avgConfidence)}
                  variant="outlined"
                />
              )}
              {sub.grading_report_object_key && onDownloadReport && (
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<DownloadOutlined />}
                  disabled={reportBusy}
                  onClick={() => void handleDownloadReport()}
                >
                  Download grading report (JSON)
                </Button>
              )}
            </Box>
          </CardContent>
        </Card>
      )}

      <Typography variant="h3" sx={{ mb: 1 }}>
        Parsed Questions
      </Typography>
      {effectiveQuestionGrades.length === 0 ? (
        <Typography color="text.secondary">
          {polling ? "Grading in progress…" : "No parsed questions yet."}
        </Typography>
      ) : (
        <>
          <Tabs
            value={Math.min(activeQuestionTab, effectiveQuestionGrades.length - 1)}
            onChange={(_, v) => setActiveQuestionTab(v)}
            variant="scrollable"
            scrollButtons="auto"
            sx={{ mb: 2 }}
            aria-label="Parsed question tabs"
          >
            {effectiveQuestionGrades.map((q, idx) => (
              <Tab key={`${q.chunk_id || "q"}-${idx}`} label={`Question ${idx + 1}`} />
            ))}
          </Tabs>

          <Card sx={{ mb: 2 }}>
            <CardContent>
              <Typography variant="overline" color="text.secondary">
                Question
              </Typography>
              <Typography variant="body2" sx={{ mt: 1, whiteSpace: "pre-wrap" }}>
                {String((currentQuestion.question_payload?.question as string) || "").trim() ||
                  String((currentQuestion.question_payload?.question_chunk_text as string) || "").trim() ||
                  "Question text not available for this parsed chunk."}
              </Typography>
            </CardContent>
          </Card>

          <Card sx={{ mb: 2 }}>
            <CardContent>
              <Typography variant="overline" color="text.secondary">
                Student Response
              </Typography>
              <Typography variant="body2" sx={{ mt: 1, whiteSpace: "pre-wrap" }}>
                {String((currentQuestion.question_payload?.response_text as string) || "").trim() ||
                  String((currentQuestion.question_payload?.student_response as string) || "").trim() ||
                  "Student response not available for this parsed chunk."}
              </Typography>
            </CardContent>
          </Card>

          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Criterion</TableCell>
                <TableCell>Score</TableCell>
                <TableCell>Justification</TableCell>
                <TableCell>Student Evidence</TableCell>
                <TableCell align="right">AI Confidence</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {(currentQuestion.criteria || []).map((row) => {
                const earned =
                  typeof row.rubric_points_earned === "number"
                    ? row.rubric_points_earned
                    : Number(row.score || 0);
                const total =
                  typeof row.max_points === "number" && Number.isFinite(row.max_points)
                    ? row.max_points
                    : 0;
                return (
                  <TableRow key={`${currentQuestion.chunk_id || "q"}-${row.criterion}`}>
                    <TableCell>{row.criterion}</TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={`${earned.toFixed(1)} / ${total.toFixed(1)}`}
                        color={criterionChipColor(total > 0 ? (earned / total) * 100 : 0)}
                        variant="outlined"
                      />
                    </TableCell>
                    <TableCell sx={{ maxWidth: 460, whiteSpace: "pre-wrap" }}>
                      {row.justification || "—"}
                    </TableCell>
                    <TableCell sx={{ maxWidth: 460, whiteSpace: "pre-wrap" }}>
                      {String(row.student_evidence || "").trim() ||
                        summarizeEvidence(row.evidence) ||
                        "Missing direct student evidence."}
                    </TableCell>
                    <TableCell align="right">
                      {(() => {
                        const rowConfidence = normalizeConfidence01(row.confidence) ?? 0;
                        return (
                          <Chip
                            size="small"
                            label={`${(rowConfidence * 100).toFixed(1)}%`}
                            color={confidenceColor(rowConfidence)}
                            variant="outlined"
                          />
                        );
                      })()}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </>
      )}
    </Box>
  );
}
