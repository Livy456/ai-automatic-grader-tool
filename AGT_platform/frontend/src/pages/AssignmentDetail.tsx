import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { Container, Typography, Button } from "@mui/material";

export default function AssignmentDetail() {
  const { id } = useParams();
  const [assignment, setAssignment] = useState<any>(null);

  useEffect(() => {
    api.get("/assignments").then(r => {
      const a = r.data.find((x: any) => String(x.id) === String(id));
      setAssignment(a);
    });
  }, [id]);

  if (!assignment) return null;

  return (
    <Container sx={{ mt: 3 }}>
      <Typography variant="h5">{assignment.title}</Typography>
      <Typography sx={{ mb: 2 }}>Modality: {assignment.modality}</Typography>
      <Button variant="contained" href={`/assignments/${assignment.id}/submit`}>
        Submit
      </Button>
    </Container>
  );
}
