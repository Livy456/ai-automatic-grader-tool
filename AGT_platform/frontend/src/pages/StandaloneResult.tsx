import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
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
import {
  getStandaloneGradingReportUrl,
  getStandaloneSubmission,
  type StandaloneSubmissionDetail,
} from "../api";
import StatusChip from "../components/StatusChip";

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

function criterionChipColor(fraction: number): "success" | "warning" | "error" {
  if (fraction >= 80) return "success";
  if (fraction >= 60) return "warning";
  return "error";
}

function summarizeEvidence(ev: unknown): string {
  if (!ev || typeof ev !== "object") return "";
  const obj = ev as Record<string, unknown>;
  const trio = (obj.trio ?? {}) as Record<string, unknown>;
  const bits: string[] = [];
  const student = String(trio.student_response ?? "").trim();
  const key = String(trio.answer_key_segment ?? "").trim();
  const source = String((obj.chunker as string) || "").trim();
  if (student) bits.push(`Student: ${student.slice(0, 220)}`);
  if (key) bits.push(`Key: ${key.slice(0, 220)}`);
  if (source) bits.push(`Chunker: ${source}`);
  return bits.join("\n");
}

function studentEvidenceSnippet(row: StandaloneSubmissionDetail["ai_scores"][number]): string {
  const direct = String(row.student_evidence || "").trim();
  if (direct) return direct.slice(0, 450);
  return "Missing direct student evidence in grader output.";
}

function inferQuestionLabel(q: {
  chunk_id?: string;
  source_chunk_id?: string;
  question_payload?: Record<string, unknown>;
}, idx: number): string {
  const payloadQuestion = String((q.question_payload?.question as string) || "").trim();
  if (payloadQuestion) return payloadQuestion;
  const src = String(q.source_chunk_id || q.chunk_id || "");
  const match = src.match(/q(\d+)/i);
  if (match) return `Question ${match[1]}`;
  return `Question ${idx + 1}`;
}

export default function StandaloneResult() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [sub, setSub] = useState<StandaloneSubmissionDetail | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [reportBusy, setReportBusy] = useState(false);
  const [activeQuestionTab, setActiveQuestionTab] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | undefined;

    const tick = async () => {
      if (!id) return;
      try {
        const s = await getStandaloneSubmission(parseInt(id, 10));
        if (cancelled) return;
        setSub(s);
        setLoadError(false);
        const st = String(s.status);
        if (!POLL_STATUSES.has(st)) {
          if (timer) clearInterval(timer);
        }
      } catch {
        if (!cancelled) setLoadError(true);
      }
    };

    void tick();
    timer = setInterval(() => void tick(), 4000);
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [id]);

  const handleDownloadReport = async () => {
    if (!id) return;
    setReportBusy(true);
    try {
      const { download_url } = await getStandaloneGradingReportUrl(parseInt(id, 10));
      window.open(download_url, "_blank", "noopener,noreferrer");
    } catch {
      /* ignore */
    } finally {
      setReportBusy(false);
    }
  };

  if (loadError || !id) {
    return (
      <Box>
        <Typography color="error">Could not load this submission.</Typography>
        <Button startIcon={<ArrowBackOutlined />} onClick={() => navigate("/autograder")} sx={{ mt: 2 }}>
          Back to Autograder
        </Button>
      </Box>
    );
  }

  if (!sub) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress aria-label="Loading submission" />
      </Box>
    );
  }

  const scores = sub.ai_scores ?? [];
  const polling = POLL_STATUSES.has(sub.status);
  const avgConfidence =
    scores.length > 0 ? scores.reduce((sum, s) => sum + s.confidence, 0) / scores.length : 0;
  const questionGrades = (sub.question_grades ?? []).filter((q) => Array.isArray(q.criteria));
  const effectiveQuestionGrades = questionGrades.length
    ? questionGrades
    : [
        {
          chunk_id: "fallback_chunk",
          source_chunk_id: "fallback_chunk",
          overall: {
            score: sub.final_score ?? null,
            max_points: sub.max_points ?? null,
            rubric_points_earned: sub.rubric_points_earned ?? null,
            confidence: avgConfidence,
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
        },
      ];
  const currentQuestion = effectiveQuestionGrades[Math.min(activeQuestionTab, effectiveQuestionGrades.length - 1)];

  return (
    <Box>
      <Button
        startIcon={<ArrowBackOutlined />}
        onClick={() => navigate("/autograder")}
        sx={{ mb: 2 }}
        aria-label="Back to autograder"
      >
        Autograder
      </Button>

      <Box sx={{ display: "flex", alignItems: "center", gap: 2, mb: 2, flexWrap: "wrap" }}>
        <Typography variant="h3" component="h1">
          {sub.title}
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
              {sub.rubric_points_earned != null && sub.max_points != null && (
                <Chip
                  size="small"
                  label={`Rubric points ${Number(sub.rubric_points_earned).toFixed(1)} / ${Number(sub.max_points).toFixed(1)}`}
                  variant="outlined"
                />
              )}
              {scores.length > 0 && (
                <Chip
                  size="small"
                  label={`Avg confidence ${(avgConfidence * 100).toFixed(0)}%`}
                  color={confidenceColor(avgConfidence)}
                  variant="outlined"
                />
              )}
              {sub.grading_report_object_key && (
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
              <Tab key={`${q.chunk_id || "q"}-${idx}`} label={inferQuestionLabel(q, idx)} />
            ))}
          </Tabs>

          <Card sx={{ mb: 2 }}>
            <CardContent>
              <Typography variant="overline" color="text.secondary">
                Question Payload
              </Typography>
              <Typography variant="body2" sx={{ mt: 1, whiteSpace: "pre-wrap" }}>
                {JSON.stringify(currentQuestion.question_payload || {}, null, 2)}
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
                      <Chip
                        size="small"
                        label={`${(Number(row.confidence || 0) * 100).toFixed(0)}%`}
                        color={confidenceColor(Number(row.confidence || 0))}
                        variant="outlined"
                      />
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
