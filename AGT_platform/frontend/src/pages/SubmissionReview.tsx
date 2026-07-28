import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Box, CircularProgress, Skeleton, Typography } from "@mui/material";
import { getCourseSubmission, getCourseSubmissionReportUrl, type CourseSubmissionDetail } from "../api";
import GradingResultView, { isPollingStatus } from "../components/GradingResultView";

export default function SubmissionReview() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [sub, setSub] = useState<CourseSubmissionDetail | null>(null);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | undefined;

    const tick = async () => {
      if (!id) return;
      try {
        const s = await getCourseSubmission(parseInt(id, 10));
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
    timer = setInterval(() => void tick(), 2000);
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [id]);

  const handleDownloadReport = async () => {
    if (!id) return;
    const { download_url } = await getCourseSubmissionReportUrl(parseInt(id, 10));
    window.open(download_url, "_blank", "noopener,noreferrer");
  };

  if (!id) return null;

  if (!sub && !loadError) {
    return (
      <Box sx={{ py: 6, textAlign: "center" }} aria-busy="true" aria-label="Loading submission">
        <CircularProgress sx={{ mb: 2 }} />
        <Skeleton variant="rectangular" height={160} sx={{ borderRadius: 2, maxWidth: 800, mx: "auto" }} />
      </Box>
    );
  }

  if (loadError || !sub) {
    return (
      <Typography color="error" role="alert">
        Could not load submission.
      </Typography>
    );
  }

  return (
    <GradingResultView
      title={sub.assignment_title || `Submission #${sub.id}`}
      data={sub}
      onBack={() => navigate(-1)}
      backLabel="Back"
      onDownloadReport={handleDownloadReport}
    />
  );
}
