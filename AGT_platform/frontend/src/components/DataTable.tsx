import { Table, TableHead, TableRow, TableCell, TableBody, Paper } from "@mui/material";

export default function DataTable({
  columns,
  rows,
  onRowClick,
}: {
  columns: { key: string; label: string }[];
  rows: any[];
  onRowClick?: (row: any) => void;
}) {
  return (
    <Paper>
      <Table size="small">
        <TableHead>
          <TableRow>
            {columns.map((c) => (
              <TableCell key={c.key}>{c.label}</TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r, idx) => (
            <TableRow
              key={idx}
              hover
              sx={{ cursor: onRowClick ? "pointer" : "default" }}
              onClick={() => onRowClick?.(r)}
            >
              {columns.map((c) => (
                <TableCell key={c.key}>{String(r[c.key] ?? "")}</TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Paper>
  );
}
