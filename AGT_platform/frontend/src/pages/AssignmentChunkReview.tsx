import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  Divider,
  IconButton,
  Paper,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableRow,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import AddOutlined from "@mui/icons-material/AddOutlined";
import DeleteOutline from "@mui/icons-material/DeleteOutline";
import {
  getAssignmentLibraryEntry,
  getAssignmentMaterialView,
  saveAssignmentLibraryChunks,
  type AssignmentLibraryEntry,
  type AssignmentMaterialKind,
  type RubricCriterion,
} from "../api";

function MaterialViewer({
  assignmentId,
  kind,
}: {
  assignmentId: number;
  kind: AssignmentMaterialKind;
}) {
  const [data, setData] = useState<Awaited<ReturnType<typeof getAssignmentMaterialView>> | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    getAssignmentMaterialView(assignmentId, kind)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((e: unknown) => {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [assignmentId, kind]);

  if (loading) return <CircularProgress aria-label="Loading document" />;
  if (err) return <Alert severity="error">{err}</Alert>;
  if (!data) return null;

  const { view, download_url: downloadUrl, filename } = data;

  if (view.type === "notebook") {
    return (
      <Stack spacing={1.5}>
        {view.cells.map((cell, i) => (
          <Paper
            key={i}
            variant="outlined"
            sx={
              cell.cell_type === "code"
                ? { bgcolor: "#1e1e1e", color: "#d4d4d4", fontFamily: "monospace", fontSize: "0.85rem", p: 2, whiteSpace: "pre-wrap" }
                : { p: 2, whiteSpace: "pre-wrap" }
            }
          >
            {cell.source || <em>(empty cell)</em>}
          </Paper>
        ))}
      </Stack>
    );
  }

  if (view.type === "spreadsheet") {
    return (
      <Stack spacing={3}>
        {view.sheets.map((sheet, si) => (
          <Box key={si}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              {sheet.name}
            </Typography>
            <TableContainer component={Paper} variant="outlined" sx={{ maxHeight: 600 }}>
              <Table size="small" stickyHeader>
                <TableBody>
                  {sheet.rows.map((row, ri) => (
                    <TableRow key={ri}>
                      {row.map((cell, ci) => (
                        <TableCell key={ci}>{cell}</TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Box>
        ))}
      </Stack>
    );
  }

  if (view.type === "pdf") {
    return (
      <Box
        component="iframe"
        src={downloadUrl}
        title={filename}
        sx={{ width: "100%", height: "75vh", border: "none" }}
      />
    );
  }

  if (view.type === "text") {
    return (
      <Paper variant="outlined" sx={{ p: 2, whiteSpace: "pre-wrap", fontFamily: "monospace" }}>
        {view.text}
      </Paper>
    );
  }

  return (
    <Alert
      severity="info"
      action={
        <Button component="a" href={downloadUrl} target="_blank" rel="noreferrer" size="small">
          Download
        </Button>
      }
    >
      No preview available for {filename}.
    </Alert>
  );
}

type EditableChunk = {
  key: string;
  id?: number;
  question_id: string;
  question_text: string;
  answer_text: string;
  rubric_criteria: RubricCriterion[];
};

let _localKeySeq = 0;
function nextLocalKey(): string {
  _localKeySeq += 1;
  return `new-${_localKeySeq}`;
}

function criterionLabel(c: RubricCriterion): string {
  return (c.name || c.criterion || "Criterion").trim();
}

function criterionScore(c: RubricCriterion): number | null {
  const score = c.max_score ?? c.max_points;
  return typeof score === "number" && Number.isFinite(score) ? score : null;
}

function RubricCriteriaDisplay({ criteria }: { criteria: RubricCriterion[] }) {
  if (criteria.length === 0) {
    return (
      <Paper
        variant="outlined"
        sx={{ p: 2, bgcolor: "action.hover", color: "text.secondary", fontStyle: "italic" }}
      >
        No rubric criteria were routed to this question automatically.
      </Paper>
    );
  }

  return (
    <Stack spacing={1}>
      {criteria.map((c, i) => {
        const score = criterionScore(c);
        return (
          <Paper key={`${criterionLabel(c)}-${i}`} variant="outlined" sx={{ p: 2 }}>
            <Box sx={{ display: "flex", justifyContent: "space-between", gap: 2, mb: c.description ? 1 : 0 }}>
              <Typography sx={{ fontWeight: 600 }}>{criterionLabel(c)}</Typography>
              {score !== null && (
                <Typography variant="body2" color="text.secondary" sx={{ whiteSpace: "nowrap" }}>
                  Max {score} pts
                </Typography>
              )}
            </Box>
            {c.description ? (
              <Typography variant="body2" color="text.secondary" sx={{ whiteSpace: "pre-wrap" }}>
                {c.description}
              </Typography>
            ) : null}
          </Paper>
        );
      })}
    </Stack>
  );
}

export default function AssignmentChunkReview() {
  const { id } = useParams();
  const assignmentId = Number(id);
  const navigate = useNavigate();

  const [assignment, setAssignment] = useState<AssignmentLibraryEntry | null>(null);
  const [chunks, setChunks] = useState<EditableChunk[]>([]);
  const [tab, setTab] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadErr(null);
    try {
      const res = await getAssignmentLibraryEntry(assignmentId);
      setAssignment(res);
      setChunks(
        res.chunks.map((c) => ({
          key: `db-${c.id}`,
          id: c.id,
          question_id: c.question_id,
          question_text: c.question_text,
          answer_text: c.answer_text,
          rubric_criteria: c.rubric_criteria ?? [],
        })),
      );
    } catch (e: unknown) {
      setLoadErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [assignmentId]);

  useEffect(() => {
    if (Number.isFinite(assignmentId)) void load();
  }, [assignmentId, load]);

  const updateChunk = (key: string, patch: Partial<EditableChunk>) => {
    setSaved(false);
    setChunks((rows) => rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  };

  const removeChunk = (key: string) => {
    setSaved(false);
    setChunks((rows) => rows.filter((r) => r.key !== key));
  };

  const addChunk = () => {
    setSaved(false);
    setChunks((rows) => [
      ...rows,
      {
        key: nextLocalKey(),
        question_id: `q${rows.length + 1}`,
        question_text: "",
        answer_text: "",
        rubric_criteria: [],
      },
    ]);
  };

  const save = async () => {
    setSaving(true);
    setSaveErr(null);
    try {
      const res = await saveAssignmentLibraryChunks(
        assignmentId,
        chunks.map((c) => ({
          id: c.id,
          question_id: c.question_id,
          question_text: c.question_text,
          answer_text: c.answer_text,
        })),
      );
      setChunks(
        res.chunks.map((c) => ({
          key: `db-${c.id}`,
          id: c.id,
          question_id: c.question_id,
          question_text: c.question_text,
          answer_text: c.answer_text,
          rubric_criteria: c.rubric_criteria ?? [],
        })),
      );
      setSaved(true);
    } catch (e: unknown) {
      setSaveErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <CircularProgress aria-label="Loading assignment" />;
  }

  if (loadErr || !assignment) {
    return (
      <Alert severity="error">
        {loadErr || "Assignment not found."}{" "}
        <Button size="small" onClick={() => navigate("/assignment-creation")}>
          Back to Assignment Creation
        </Button>
      </Alert>
    );
  }

  return (
    <Box>
      <Typography variant="h3" sx={{ mb: 0.5 }}>
        {assignment.title}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Review the parsed question/answer pairs, or view the original uploaded documents.
      </Typography>

      <Tabs
        value={tab}
        onChange={(_, v) => setTab(v)}
        sx={{ mb: 3, borderBottom: 1, borderColor: "divider" }}
        aria-label="Assignment review sections"
      >
        <Tab label="Questions & Answers" id="assignment-review-tab-0" aria-controls="assignment-review-panel-0" />
        <Tab label="Blank Assignment" id="assignment-review-tab-1" aria-controls="assignment-review-panel-1" />
        <Tab label="Answer Key" id="assignment-review-tab-2" aria-controls="assignment-review-panel-2" />
      </Tabs>

      {tab === 0 && (
        <Box role="tabpanel" id="assignment-review-panel-0" aria-labelledby="assignment-review-tab-0">
          {saveErr && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {saveErr}
            </Alert>
          )}
          {saved && !saveErr && (
            <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSaved(false)}>
              Changes saved.
            </Alert>
          )}
          {chunks.length === 0 && (
            <Alert severity="info" sx={{ mb: 2 }}>
              No question/answer pairs were parsed automatically. Add them manually below, or
              check that the answer key upload is readable.
            </Alert>
          )}

          <Stack spacing={2} sx={{ mb: 3 }}>
            {chunks.map((c, i) => (
              <Card key={c.key} variant="outlined">
                <CardContent>
                  <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mb: 1 }}>
                    <Typography sx={{ fontSize: "2.75rem", fontWeight: 700, lineHeight: 1.1 }}>
                      {`Question ${i + 1}`}
                    </Typography>
                    <IconButton
                      aria-label={`Remove question ${i + 1}`}
                      color="error"
                      onClick={() => removeChunk(c.key)}
                    >
                      <DeleteOutline />
                    </IconButton>
                  </Box>
                  <TextField
                    fullWidth
                    multiline
                    minRows={2}
                    label="Question"
                    value={c.question_text}
                    onChange={(e) => updateChunk(c.key, { question_text: e.target.value })}
                    sx={{ mb: 2 }}
                    aria-label={`Question ${i + 1} text`}
                  />
                  <TextField
                    fullWidth
                    multiline
                    minRows={2}
                    label="Answer"
                    value={c.answer_text}
                    onChange={(e) => updateChunk(c.key, { answer_text: e.target.value })}
                    sx={{ mb: 2 }}
                    aria-label={`Answer ${i + 1} text`}
                  />
                  <Typography variant="subtitle2" sx={{ mb: 1 }}>
                    Rubric Criteria
                  </Typography>
                  <RubricCriteriaDisplay criteria={c.rubric_criteria} />
                </CardContent>
              </Card>
            ))}
          </Stack>

          <Button startIcon={<AddOutlined />} onClick={addChunk} sx={{ mb: 3 }} aria-label="Add question">
            Add question
          </Button>

          <Divider sx={{ mb: 2 }} />

          <Box sx={{ display: "flex", justifyContent: "space-between", gap: 2 }}>
            <Button onClick={() => navigate("/assignment-creation")} aria-label="Back to Assignment Creation">
              Back
            </Button>
            <Button
              variant="contained"
              size="large"
              disabled={saving}
              onClick={() => void save()}
              aria-label="Save changes"
            >
              {saving ? <CircularProgress size={22} color="inherit" aria-label="Saving" /> : "Save Changes"}
            </Button>
          </Box>
        </Box>
      )}

      {tab === 1 && (
        <Box role="tabpanel" id="assignment-review-panel-1" aria-labelledby="assignment-review-tab-1">
          <MaterialViewer assignmentId={assignmentId} kind="blank_assignment" />
        </Box>
      )}

      {tab === 2 && (
        <Box role="tabpanel" id="assignment-review-panel-2" aria-labelledby="assignment-review-tab-2">
          <MaterialViewer assignmentId={assignmentId} kind="answer_key" />
        </Box>
      )}
    </Box>
  );
}
