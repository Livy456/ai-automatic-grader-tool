import { useEffect, useState } from "react";
import { api } from "../api";
import { Container, Typography, List, ListItemButton, ListItemText } from "@mui/material";

export default function StudentDashboard() {
  const [assignments, setAssignments] = useState<any[]>([]);

  useEffect(() => {
    api.get("/assignments").then(r => setAssignments(r.data));
  }, []);

  return (
    <Container sx={{ mt: 3 }}>
      <Typography variant="h5" gutterBottom>Assignments</Typography>
      <List>
        {assignments.map(a => (
          <ListItemButton key={a.id} href={`/assignments/${a.id}`}>
            <ListItemText primary={a.title} secondary={`Modality: ${a.modality}`} />
          </ListItemButton>
        ))}
      </List>
    </Container>
  );
}
