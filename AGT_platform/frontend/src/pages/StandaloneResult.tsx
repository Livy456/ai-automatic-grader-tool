import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Box, Button, CircularProgress, Typography } from "@mui/material";
import ArrowBackOutlined from "@mui/icons-material/ArrowBackOutlined";
import {
  getStandaloneGradingReportUrl,
  getStandaloneSubmission,
  type StandaloneSubmissionDetail,
} from "../api";
import GradingResultView, { isPollingStatus } from "../components/GradingResultView";

export default function StandaloneResult() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [sub, setSub] = useState<StandaloneSubmissionDetail | null>(null);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | undefined;

    const tick = async () => {
      if (!id) return;
      try {
        const s = await getStandaloneSubmission(parseInt(id, 10));
        if (cancelled) return;
        setSub(s);
        setLoadError(false);
        if (!isPollingStatus(String(s.status))) {
          if (timer) clearInterval(timer);
        }
      } catch {
        if (!cancelled) setLoadError(true);
      }
    };

    void tick();
    timer = setInterval(() => void tick(), 4000);
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [id]);

  const handleDownloadReport = async () => {
    if (!id) return;
    const { download_url } = await getStandaloneGradingReportUrl(parseInt(id, 10));
    window.open(download_url, "_blank", "noopener,noreferrer");
  };

  if (loadError || !id) {
    return (
      <Box>
        <Typography color="error">Could not load this submission.</Typography>
        <Button startIcon={<ArrowBackOutlined />} onClick={() => navigate("/autograder")} sx={{ mt: 2 }}>
          Back to Autograder
        </Button>
      </Box>
    );
  }

  if (!sub) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress aria-label="Loading submission" />
      </Box>
    );
  }

  return (
    <GradingResultView
      title={sub.title}
      data={sub}
      onBack={() => navigate("/autograder")}
      backLabel="Autograder"
      onDownloadReport={handleDownloadReport}
    />
  );
}
