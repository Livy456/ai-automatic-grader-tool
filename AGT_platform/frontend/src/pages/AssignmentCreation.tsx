import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  FormControl,
  InputLabel,
  LinearProgress,
  MenuItem,
  Select,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import {
  createAssignmentLibraryEntryDirect,
  listAssignmentLibraryEntries,
  type AssignmentLibraryEntry,
  type AssignmentLibraryFileSpec,
} from "../api";

const MODALITIES = ["written", "code", "notebook", "video", "image"] as const;

function parseServerDateAsLocal(ts: string): Date {
  // Legacy backend rows may omit timezone; treat those as UTC to avoid local-shift errors.
  const hasZone = /[zZ]|[+-]\d{2}:\d{2}$/.test(ts);
  return new Date(hasZone ? ts : `${ts}Z`);
}

export default function AssignmentCreation() {
  const navigate = useNavigate();
  const [tab, setTab] = useState(0);

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [modality, setModality] = useState<string>("written");
  const [blankFile, setBlankFile] = useState<File | null>(null);
  const [keyFile, setKeyFile] = useState<File | null>(null);
  const [rubricFile, setRubricFile] = useState<File | null>(null);
  const [rubricText, setRubricText] = useState("");

  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [err, setErr] = useState<string | null>(null);

  const [history, setHistory] = useState<AssignmentLibraryEntry[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const res = await listAssignmentLibraryEntries();
      setHistory(res);
    } catch {
      setHistory([]);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    if (tab === 1) void loadHistory();
  }, [tab, loadHistory]);

  const hasBlank = Boolean(blankFile);
  const hasAnswerKey = Boolean(keyFile);
  const hasRubric = Boolean(rubricFile || rubricText.trim());
  const canSubmit = Boolean(title.trim()) && hasBlank && hasAnswerKey && hasRubric;

  const submit = async () => {
    if (!canSubmit || !blankFile) return;
    setErr(null);
    setBusy(true);
    setProgress(0);

    const files: File[] = [blankFile];
    const specs: AssignmentLibraryFileSpec[] = [
      { filename: blankFile.name, content_type: blankFile.type || "application/octet-stream", artifact_kind: "blank_assignment" },
    ];
    if (keyFile) {
      files.push(keyFile);
      specs.push({ filename: keyFile.name, content_type: keyFile.type || "application/octet-stream", artifact_kind: "answer_key" });
    }
    if (rubricFile) {
      files.push(rubricFile);
      specs.push({ filename: rubricFile.name, content_type: rubricFile.type || "application/octet-stream", artifact_kind: "rubric" });
    }
    const n = files.length || 1;

    try {
      const result = await createAssignmentLibraryEntryDirect(
        {
          title: title.trim(),
          description: description.trim() || undefined,
          modality,
          rubric_text: rubricText.trim() || undefined,
        },
        files,
        specs,
        (fileIndex, frac) => setProgress(((fileIndex + frac) / n) * 100),
      );
      setProgress(100);
      navigate(`/assignment-creation/${result.id}/review`);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box>
      <Typography variant="h3" sx={{ mb: 2 }}>
        Assignment Creation
      </Typography>

      <Tabs
        value={tab}
        onChange={(_, v) => setTab(v)}
        sx={{ mb: 2 }}
        aria-label="Assignment creation sections"
      >
        <Tab label="New Assignment" id="assignment-creation-tab-0" aria-controls="assignment-creation-panel-0" />
        <Tab label="History" id="assignment-creation-tab-1" aria-controls="assignment-creation-panel-1" />
      </Tabs>

      {tab === 1 && (
        <Card>
          <CardContent>
            <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 2 }}>
              <Typography variant="h3">Created assignments</Typography>
              <Button size="small" onClick={() => void loadHistory()} disabled={historyLoading}>
                Refresh
              </Button>
            </Box>
            {historyLoading ? (
              <CircularProgress size={28} aria-label="Loading history" />
            ) : history.length === 0 ? (
              <Typography color="text.secondary">No assignments created yet.</Typography>
            ) : (
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Title</TableCell>
                    <TableCell>Modality</TableCell>
                    <TableCell>Created</TableCell>
                    <TableCell align="right">Action</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {history.map((row) => (
                    <TableRow key={row.id}>
                      <TableCell>{row.title}</TableCell>
                      <TableCell>{row.modality}</TableCell>
                      <TableCell>
                        {row.created_at
                          ? parseServerDateAsLocal(row.created_at).toLocaleString(undefined, {
                              dateStyle: "short",
                              timeStyle: "short",
                            })
                          : "—"}
                      </TableCell>
                      <TableCell align="right">
                        <Button size="small" onClick={() => navigate(`/assignment-creation/${row.id}/review`)}>
                          Review
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {tab === 0 && (
        <Card>
          <CardContent>
            {err && (
              <Alert severity="error" sx={{ mb: 2 }}>
                {err}
              </Alert>
            )}

            <TextField
              label="Assignment title"
              fullWidth
              required
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              inputProps={{ maxLength: 255 }}
              sx={{ mb: 2 }}
              aria-label="Assignment title"
            />
            <TextField
              label="Description (optional)"
              fullWidth
              multiline
              minRows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              sx={{ mb: 2 }}
              aria-label="Assignment description"
            />
            <FormControl sx={{ mb: 3, minWidth: 220 }}>
              <InputLabel id="assignment-creation-modality-label">Modality</InputLabel>
              <Select
                labelId="assignment-creation-modality-label"
                label="Modality"
                value={modality}
                onChange={(e) => setModality(e.target.value)}
                aria-label="Assignment modality"
              >
                {MODALITIES.map((m) => (
                  <MenuItem key={m} value={m}>
                    {m}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            <Typography variant="subtitle1" gutterBottom>
              Blank Assignment Template (required)
            </Typography>
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
              The instructor copy with no student work — used to isolate each question.
            </Typography>
            <Button variant="outlined" component="label" size="small" sx={{ mb: 1 }} aria-label="Upload blank assignment template">
              Upload file
              <input type="file" hidden onChange={(e) => setBlankFile(e.target.files?.[0] ?? null)} />
            </Button>
            {blankFile && (
              <Typography variant="caption" display="block" color="text.secondary" sx={{ mb: 3 }}>
                {blankFile.name}{" "}
                <Button size="small" onClick={() => setBlankFile(null)} aria-label="Remove blank template file">
                  Remove
                </Button>
              </Typography>
            )}
            {!blankFile && <Box sx={{ mb: 2 }} />}

            <Typography variant="subtitle1" gutterBottom>
              Answer Key (required)
            </Typography>
            <Button variant="outlined" component="label" size="small" sx={{ mb: 1 }} aria-label="Upload answer key file">
              Upload file
              <input type="file" hidden onChange={(e) => setKeyFile(e.target.files?.[0] ?? null)} />
            </Button>
            {keyFile && (
              <Typography variant="caption" display="block" color="text.secondary" sx={{ mb: 3 }}>
                {keyFile.name}{" "}
                <Button size="small" onClick={() => setKeyFile(null)} aria-label="Remove answer key file">
                  Remove
                </Button>
              </Typography>
            )}
            {!keyFile && <Box sx={{ mb: 2 }} />}

            <Typography variant="subtitle1" gutterBottom>
              Rubric (required)
            </Typography>
            <Button variant="outlined" component="label" size="small" sx={{ mb: 1 }} aria-label="Upload rubric file">
              Upload file
              <input type="file" hidden onChange={(e) => setRubricFile(e.target.files?.[0] ?? null)} />
            </Button>
            {rubricFile && (
              <Typography variant="caption" display="block" color="text.secondary" sx={{ mb: 1 }}>
                {rubricFile.name}{" "}
                <Button size="small" onClick={() => setRubricFile(null)} aria-label="Remove rubric file">
                  Remove
                </Button>
              </Typography>
            )}
            <TextField
              fullWidth
              multiline
              minRows={3}
              label="Or paste the rubric"
              value={rubricText}
              onChange={(e) => setRubricText(e.target.value)}
              sx={{ mb: 1 }}
              aria-label="Paste rubric"
            />
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 3 }}>
              Upload a structured `.json` rubric to auto-populate grading criteria; other formats are
              stored for reference.
            </Typography>

            {!canSubmit && (
              <Alert severity="warning" sx={{ mb: 2 }}>
                A title, blank assignment template, answer key, and rubric are all required.
              </Alert>
            )}

            {busy && <LinearProgress variant="determinate" value={progress} sx={{ mb: 2 }} aria-label="Upload progress" />}

            <Button
              variant="contained"
              color="primary"
              size="large"
              fullWidth
              disabled={busy || !canSubmit}
              onClick={() => void submit()}
              aria-label="Create assignment"
            >
              {busy ? <CircularProgress size={22} color="inherit" aria-label="Creating" /> : "Create Assignment"}
            </Button>
          </CardContent>
        </Card>
      )}
    </Box>
  );
}
