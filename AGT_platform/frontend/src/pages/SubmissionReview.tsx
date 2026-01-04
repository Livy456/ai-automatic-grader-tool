import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { Container, Typography, Card, CardContent } from "@mui/material";

export default function SubmissionReview() {
  const { id } = useParams();
  const [sub, setSub] = useState<any>(null);

  useEffect(() => {
    const interval = setInterval(() => {
      api.get(`/submissions/${id}`).then(r => setSub(r.data));
    }, 1500);
    return () => clearInterval(interval);
  }, [id]);

  if (!sub) return null;

  return (
    <Container sx={{ mt: 3 }}>
      <Typography variant="h5">Submission #{sub.id}</Typography>
      <Typography sx={{ mb: 2 }}>Status: {sub.status}</Typography>

      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="h6">Suggested Score</Typography>
          <Typography>{sub.final_score ?? "—"}</Typography>
          <Typography variant="h6" sx={{ mt: 2 }}>Summary Feedback</Typography>
          <Typography>{sub.final_feedback ?? "—"}</Typography>
        </CardContent>
      </Card>

      {sub.ai_scores?.map((s: any, idx: number) => (
        <Card key={idx} sx={{ mb: 1 }}>
          <CardContent>
            <Typography variant="subtitle1">
              {s.criterion} — {s.score} (conf {s.confidence})
            </Typography>
            <Typography>{s.rationale}</Typography>
          </CardContent>
        </Card>
      ))}
    </Container>
  );
}
