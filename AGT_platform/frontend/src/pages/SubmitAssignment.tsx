import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  LinearProgress,
  Step,
  StepLabel,
  Stepper,
  Typography,
} from "@mui/material";
import CloudUploadOutlined from "@mui/icons-material/CloudUploadOutlined";
import InsertDriveFileOutlined from "@mui/icons-material/InsertDriveFileOutlined";
import CloseOutlined from "@mui/icons-material/CloseOutlined";
import { submitAssignmentDirect } from "../api";

const STEPS = ["Upload Files", "Review & Submit"];

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export default function SubmitAssignment() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [activeStep, setActiveStep] = useState(0);
  const [mainFiles, setMainFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const assignmentId = id ? parseInt(id, 10) : NaN;
  const assignmentLabel = Number.isFinite(assignmentId) ? `Assignment #${assignmentId}` : "Assignment";

  const onDropMain = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const list = e.dataTransfer.files;
    if (list?.length) setMainFiles(Array.from(list));
  };

  const submit = async () => {
    if (!Number.isFinite(assignmentId) || mainFiles.length === 0) return;
    setErr(null);
    setBusy(true);
    setProgress(0);
    const n = mainFiles.length || 1;
    try {
      const result = await submitAssignmentDirect(assignmentId, mainFiles, (fileIndex, frac) => {
        setProgress(((fileIndex + frac) / n) * 100);
      });
      setProgress(100);
      navigate(`/submissions/${result.submission_id}`);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const canNextStep0 = mainFiles.length > 0;

  return (
    <Box>
      <Typography variant="h3" sx={{ mb: 2 }}>
        {assignmentLabel}
      </Typography>
      <Stepper activeStep={activeStep} sx={{ mb: 3 }} aria-label="Submission steps">
        {STEPS.map((label) => (
          <Step key={label}>
            <StepLabel>{label}</StepLabel>
          </Step>
        ))}
      </Stepper>

      {err && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {err}
        </Alert>
      )}

      {activeStep === 0 && (
        <Card>
          <CardContent>
            <Box
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={onDropMain}
              sx={{
                border: "2px dashed",
                borderColor: dragOver ? "secondary.main" : "divider",
                borderRadius: 2,
                p: 4,
                textAlign: "center",
                bgcolor: dragOver ? "action.hover" : "background.paper",
              }}
              aria-label="Drop zone for assignment files"
            >
              <CloudUploadOutlined sx={{ fontSize: 48, color: "text.secondary", mb: 1 }} aria-hidden />
              <Typography variant="subtitle1" gutterBottom>
                Drag your assignment here
              </Typography>
              <Button variant="outlined" component="label" sx={{ mt: 1 }} aria-label="Browse files">
                Browse files
                <input
                  type="file"
                  multiple
                  hidden
                  onChange={(e) => {
                    const f = e.target.files;
                    if (f?.length) setMainFiles(Array.from(f));
                  }}
                />
              </Button>
              <Typography variant="caption" display="block" color="text.secondary" sx={{ mt: 2 }}>
                Accepted: PDF, DOCX, TXT, IPYNB, ZIP, MP4, PNG, JPG · Max 1 GB per file (per institution policy)
              </Typography>
            </Box>
            {mainFiles.length > 0 && (
              <Box sx={{ mt: 2 }}>
                {mainFiles.map((f, i) => (
                  <Box
                    key={`${f.name}-${i}`}
                    sx={{ display: "flex", alignItems: "center", gap: 1, py: 1, borderBottom: 1, borderColor: "divider" }}
                  >
                    <InsertDriveFileOutlined color="action" aria-hidden />
                    <Box sx={{ flex: 1 }}>
                      <Typography variant="body2">{f.name}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {formatBytes(f.size)} · {f.type || "unknown type"}
                      </Typography>
                    </Box>
                    <Button
                      size="small"
                      aria-label={`Remove ${f.name}`}
                      onClick={() => setMainFiles((prev) => prev.filter((_, j) => j !== i))}
                    >
                      <CloseOutlined fontSize="small" />
                    </Button>
                  </Box>
                ))}
              </Box>
            )}
            <Box sx={{ display: "flex", justifyContent: "flex-end", mt: 2 }}>
              <Button
                variant="contained"
                disabled={!canNextStep0}
                onClick={() => setActiveStep(1)}
                aria-label="Go to review step"
              >
                Next
              </Button>
            </Box>
          </CardContent>
        </Card>
      )}

      {activeStep === 1 && (
        <Card>
          <CardContent>
            <Typography variant="h3" sx={{ mb: 2 }}>
              Review &amp; Submit
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Assignment: {assignmentLabel}
            </Typography>
            <Typography variant="body2" sx={{ mt: 1 }}>
              Files ({mainFiles.length}): {mainFiles.map((f) => f.name).join(", ") || "—"}
            </Typography>
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
              Total size ~ {formatBytes(mainFiles.reduce((s, f) => s + f.size, 0))}
            </Typography>
            {/* <Alert severity="info" sx={{ mt: 2 }}>
              The blank assignment and answer key on file for this assignment will be used
              automatically as grading context — no need to upload them again.
            </Alert> */}
            {busy && <LinearProgress variant="determinate" value={progress} sx={{ mt: 2 }} aria-label="Upload progress" />}
            <Button
              variant="contained"
              color="primary"
              size="large"
              fullWidth
              sx={{ mt: 3 }}
              disabled={busy || mainFiles.length === 0 || !Number.isFinite(assignmentId)}
              onClick={submit}
              aria-label="Submit for grading"
            >
              {busy ? <CircularProgress size={22} color="inherit" aria-label="Submitting" /> : "Submit for Grading"}
            </Button>
            <Box sx={{ display: "flex", justifyContent: "flex-start", mt: 2 }}>
              <Button onClick={() => setActiveStep(0)} disabled={busy} aria-label="Back to upload step">
                Back
              </Button>
            </Box>
          </CardContent>
        </Card>
      )}
    </Box>
  );
}
