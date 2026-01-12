import { useEffect, useState } from "react";
import { api } from "../api";
import { Box, Button, Container, TextField, Typography, Dialog, DialogTitle, DialogContent, DialogActions } from "@mui/material";
import DataTable from "../components/DataTable";

export default function TeacherDashboard() {
  const [subs, setSubs] = useState<any[]>([]);
  const [selected, setSelected] = useState<any | null>(null);
  const [overrideScore, setOverrideScore] = useState<string>("");

  const refresh = async () => {
    const r = await api.get("/teacher/submissions?limit=50");
    setSubs(r.data);
  };

  useEffect(() => { refresh(); }, []);

  const openSubmission = async (row: any) => {
    const r = await api.get(`/submissions/${row.id}`);
    setSelected(r.data);
    setOverrideScore(String(r.data.final_score ?? ""));
  };

  const saveOverride = async () => {
    if (!selected) return;
    await api.post(`/teacher/submissions/${selected.id}/override`, {
      final_score: Number(overrideScore),
      final_feedback: selected.final_feedback ?? ""
    });
    await refresh();
    setSelected(null);
  };

  return (
    <Container>
      <Typography variant="h5" gutterBottom>Teacher Dashboard</Typography>
      <Typography sx={{ mb: 2 }}>Recent submissions queue.</Typography>

      <DataTable
        columns={[
          { key: "id", label: "Submission ID" },
          { key: "assignment_id", label: "Assignment" },
          { key: "student_id", label: "Student" },
          { key: "status", label: "Status" },
          { key: "final_score", label: "Score" },
          { key: "created_at", label: "Created" }
        ]}
        rows={subs}
        onRowClick={openSubmission}
      />

      <Box sx={{ mt: 2 }}>
        <Button variant="outlined" onClick={refresh}>Refresh</Button>
      </Box>

      <Dialog open={!!selected} onClose={() => setSelected(null)} maxWidth="md" fullWidth>
        <DialogTitle>Review Submission</DialogTitle>
        <DialogContent>
          {selected && (
            <>
              <Typography sx={{ mb: 1 }}>Status: {selected.status}</Typography>
              <Typography sx={{ mb: 1 }}>Suggested Score: {selected.final_score ?? "—"}</Typography>

              <Typography variant="h6" sx={{ mt: 2 }}>AI Criteria</Typography>
              {(selected.ai_scores || []).map((s: any, i: number) => (
                <Box key={i} sx={{ mt: 1, p: 1, border: "1px solid #eee", borderRadius: 1 }}>
                  <Typography><b>{s.criterion}</b> — {s.score} (conf {s.confidence})</Typography>
                  <Typography>{s.rationale}</Typography>
                </Box>
              ))}

              <Typography variant="h6" sx={{ mt: 2 }}>Override</Typography>
              <TextField
                label="Final Score"
                fullWidth
                value={overrideScore}
                onChange={(e) => setOverrideScore(e.target.value)}
                sx={{ mt: 1 }}
              />
            </>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSelected(null)}>Close</Button>
          <Button variant="contained" onClick={saveOverride}>Save Override</Button>
        </DialogActions>
      </Dialog>
    </Container>
  );
}
