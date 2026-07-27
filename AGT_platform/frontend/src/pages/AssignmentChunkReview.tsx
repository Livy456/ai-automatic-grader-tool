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
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import AddOutlined from "@mui/icons-material/AddOutlined";
import DeleteOutline from "@mui/icons-material/DeleteOutline";
import {
  getAssignmentLibraryEntry,
  saveAssignmentLibraryChunks,
  type AssignmentLibraryEntry,
} from "../api";

type EditableChunk = {
  key: string;
  id?: number;
  question_id: string;
  question_text: string;
  answer_text: string;
};

let _localKeySeq = 0;
function nextLocalKey(): string {
  _localKeySeq += 1;
  return `new-${_localKeySeq}`;
}

export default function AssignmentChunkReview() {
  const { id } = useParams();
  const assignmentId = Number(id);
  const navigate = useNavigate();

  const [assignment, setAssignment] = useState<AssignmentLibraryEntry | null>(null);
  const [chunks, setChunks] = useState<EditableChunk[]>([]);
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
      { key: nextLocalKey(), question_id: `q${rows.length + 1}`, question_text: "", answer_text: "" },
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
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Review the parsed question/answer pairs below. Edit any question or answer, add missing
        ones, or remove pairs that were split incorrectly — then save.
      </Typography>

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
          No question/answer pairs were parsed automatically. Add them manually below, or check
          that the answer key upload is readable.
        </Alert>
      )}

      <Stack spacing={2} sx={{ mb: 3 }}>
        {chunks.map((c, i) => (
          <Card key={c.key} variant="outlined">
            <CardContent>
              <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mb: 1 }}>
                <TextField
                  variant="standard"
                  value={c.question_id}
                  onChange={(e) => updateChunk(c.key, { question_id: e.target.value })}
                  label={`Question ${i + 1} id`}
                  sx={{ maxWidth: 200 }}
                  aria-label={`Question ${i + 1} id`}
                />
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
                aria-label={`Answer ${i + 1} text`}
              />
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
  );
}
