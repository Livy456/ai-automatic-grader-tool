import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { Container, Typography, Button } from "@mui/material";

export default function SubmissionUpload() {
  const { id } = useParams();
  const [files, setFiles] = useState<FileList | null>(null);

  const submit = async () => {
    if (!files) return;
    const fd = new FormData();
    fd.append("assignment_id", String(id));
    for (const f of Array.from(files)) fd.append("files", f);

    const res = await api.post("/submissions", fd, {
      headers: { "Content-Type": "multipart/form-data" }
    });
    window.location.href = `/submissions/${res.data.submission_id}`;
  };

  return (
    <Container sx={{ mt: 3 }}>
      <Typography variant="h5" gutterBottom>Upload Submission</Typography>
      <input type="file" multiple onChange={(e) => setFiles(e.target.files)} />
      <div style={{ marginTop: 16 }}>
        <Button variant="contained" onClick={submit}>Submit</Button>
      </div>
    </Container>
  );
}
