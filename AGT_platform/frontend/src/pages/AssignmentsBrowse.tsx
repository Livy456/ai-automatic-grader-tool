import { useEffect, useState } from "react";
import { Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Typography } from "@mui/material";
import AssignmentOutlined from "@mui/icons-material/AssignmentOutlined";
import { Link } from "react-router-dom";
import { listAssignmentLibraryEntries, type AssignmentLibraryEntry } from "../api";

export default function AssignmentsBrowse() {
  const [assignments, setAssignments] = useState<AssignmentLibraryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listAssignmentLibraryEntries()
      .then((res) => {
        if (!cancelled) setAssignments(res);
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
  }, []);

  return (
    <Box>
      <Typography variant="h3" sx={{ mb: 2 }}>
        Submit Assignment
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Choose an assignment created via Assignment Creation to upload your submission.
      </Typography>

      {err && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {err}
        </Alert>
      )}

      {loading ? (
        <CircularProgress aria-label="Loading assignments" />
      ) : assignments.length === 0 ? (
        <Card>
          <CardContent sx={{ textAlign: "center", py: 6 }}>
            <AssignmentOutlined sx={{ fontSize: 56, color: "text.disabled", mb: 1 }} aria-hidden />
            <Typography variant="subtitle1" fontWeight={600}>
              No assignments yet
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Ask your instructor to create an assignment, or create one via Assignment Creation.
            </Typography>
          </CardContent>
        </Card>
      ) : (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {assignments.map((a) => (
            <Card key={a.id}>
              <CardContent sx={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 2 }}>
                <Box sx={{ flex: 1, minWidth: 200 }}>
                  <Typography variant="h3" component="h2">
                    {a.title}
                  </Typography>
                  <Chip label={a.modality} size="small" sx={{ mt: 1 }} aria-label={`Modality ${a.modality}`} />
                </Box>
                <Button
                  component={Link}
                  to={`/assignments/${a.id}/submit`}
                  variant="contained"
                  color="secondary"
                  aria-label={`Submit assignment ${a.title}`}
                >
                  Submit
                </Button>
              </CardContent>
            </Card>
          ))}
        </Box>
      )}
    </Box>
  );
}
