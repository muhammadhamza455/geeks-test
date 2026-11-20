import { AlertTriangle, FileText, Gauge, Users } from "lucide-react";
import type { DashboardStats } from "../types";

interface VelocityChartProps {
  stats: DashboardStats | null;
}

export function VelocityChart({ stats }: VelocityChartProps) {
  const epm = stats?.edits_per_minute ?? 0;
  const edits5m = stats?.edits_last_5m ?? 0;
  const activePages = stats?.active_pages_5m ?? 0;
  const flagged5m = stats?.flagged_last_5m ?? 0;
  const human5m = stats?.human_edits_last_5m ?? 0;
  const bot5m = stats?.bot_edits_last_5m ?? 0;
  const humanPct = edits5m > 0 ? Math.round((human5m / edits5m) * 100) : 0;

  const history = stats?.epm_history ?? [];
  const maxVal = Math.max(5, ...history.map((b) => b.count));

  // SVG chart dimensions
  const width = 560;
  const height = 112;
  const padLeft = 30;
  const padRight = 14;
  const padTop = 12;
  const padBottom = 22;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  const points = history.map((bucket, idx) => {
    const x =
      history.length > 1
        ? padLeft + (idx / (history.length - 1)) * plotW
        : padLeft + plotW / 2;
    const y = padTop + plotH - (bucket.count / maxVal) * plotH;
    return { x, y, bucket };
  });

  const linePath = points
    .map((pt, i) => `${i === 0 ? "M" : "L"} ${pt.x.toFixed(1)} ${pt.y.toFixed(1)}`)
    .join(" ");

  const areaPath =
    points.length > 0
      ? `${linePath} L ${points[points.length - 1].x.toFixed(1)} ${(padTop + plotH).toFixed(1)} L ${points[0].x.toFixed(1)} ${(padTop + plotH).toFixed(1)} Z`
      : "";

  return (
    <section className="wp-metrics-section" aria-label="Live metrics and edit velocity">
      <div className="wp-kpi-grid">
        <div className="wp-kpi-card" data-testid="kpi-epm">
          <div className="wp-kpi-header">
            <span className="wp-kpi-label">Edits / Min (60s)</span>
            <Gauge size={16} className="wp-kpi-icon wp-text-cyan" />
          </div>
          <div className="wp-kpi-value" data-testid="epm-value">
            {epm.toLocaleString()}
          </div>
          <div className="wp-kpi-sub">Rolling 60-second velocity</div>
        </div>

        <div className="wp-kpi-card" data-testid="kpi-5m-volume">
          <div className="wp-kpi-header">
            <span className="wp-kpi-label">5-Min Volume</span>
            <Users size={16} className="wp-kpi-icon wp-text-indigo" />
          </div>
          <div className="wp-kpi-value">{edits5m.toLocaleString()}</div>
          <div className="wp-kpi-sub">
            {human5m} human ({humanPct}%) · {bot5m} bot
          </div>
        </div>

        <div className="wp-kpi-card" data-testid="kpi-active-pages">
          <div className="wp-kpi-header">
            <span className="wp-kpi-label">Active Pages (5m)</span>
            <FileText size={16} className="wp-kpi-icon wp-text-emerald" />
          </div>
          <div className="wp-kpi-value">{activePages.toLocaleString()}</div>
          <div className="wp-kpi-sub">Distinct articles edited</div>
        </div>

        <div
          className={`wp-kpi-card ${flagged5m > 0 ? "wp-kpi-alert" : ""}`}
          data-testid="kpi-flagged"
        >
          <div className="wp-kpi-header">
            <span className="wp-kpi-label">Vandalism Flags (5m)</span>
            <AlertTriangle
              size={16}
              className={`wp-kpi-icon ${flagged5m > 0 ? "wp-text-danger" : "wp-text-muted"}`}
            />
          </div>
          <div className="wp-kpi-value" data-testid="flagged-value">
            {flagged5m.toLocaleString()}
          </div>
          <div className="wp-kpi-sub">
            {flagged5m > 0 ? "Suspicious or rollback alerts" : "No alerts in last 5 min"}
          </div>
        </div>
      </div>

      <div className="wp-card wp-chart-card">
        <div className="wp-chart-header">
          <span className="wp-chart-title">Edit Velocity — Last 10 Minutes (UTC buckets)</span>
          <span className="wp-chart-legend">
            <span className="wp-legend-dot wp-legend-blue" /> Total edits
            <span className="wp-legend-dot wp-legend-red" /> Flagged
          </span>
        </div>

        {points.length > 0 ? (
          <svg
            className="wp-velocity-svg"
            viewBox={`0 0 ${width} ${height}`}
            role="img"
            aria-label="Edits per minute over the last 10 minutes"
          >
            <defs>
              <linearGradient id="wpAreaGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#38bdf8" stopOpacity="0.34" />
                <stop offset="100%" stopColor="#38bdf8" stopOpacity="0.02" />
              </linearGradient>
            </defs>

            {/* Horizontal reference lines & Y-axis labels */}
            {[0, 0.5, 1].map((frac) => {
              const y = padTop + plotH * (1 - frac);
              const tickVal = Math.round(maxVal * frac);
              return (
                <g key={frac}>
                  <line
                    x1={padLeft}
                    x2={width - padRight}
                    y1={y}
                    y2={y}
                    stroke="rgba(148, 163, 184, 0.14)"
                    strokeDasharray="3 4"
                  />
                  <text
                    x={padLeft - 5}
                    y={y + 3}
                    textAnchor="end"
                    className="wp-chart-axis-label"
                  >
                    {tickVal}
                  </text>
                </g>
              );
            })}

            {areaPath && <path d={areaPath} fill="url(#wpAreaGrad)" />}
            {linePath && (
              <path
                d={linePath}
                fill="none"
                stroke="#38bdf8"
                strokeWidth="2.2"
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            )}

            {points.map(({ x, y, bucket }) => (
              <g key={bucket.minute_ts}>
                <circle cx={x} cy={y} r="3.2" fill="#38bdf8">
                  <title>{`${bucket.label} UTC: ${bucket.count} edits (${bucket.flagged_count} flagged)`}</title>
                </circle>
                {bucket.flagged_count > 0 && (
                  <circle
                    cx={x}
                    cy={padTop + plotH - (bucket.flagged_count / maxVal) * plotH}
                    r="3.8"
                    fill="#f87171"
                  >
                    <title>{`${bucket.flagged_count} vandalism flag(s) at ${bucket.label} UTC`}</title>
                  </circle>
                )}
                <text
                  x={x}
                  y={height - 5}
                  textAnchor="middle"
                  className="wp-chart-axis-label"
                >
                  {bucket.label}
                </text>
              </g>
            ))}
          </svg>
        ) : (
          <div className="wp-empty-chart">Collecting minute-by-minute edit history...</div>
        )}
      </div>
    </section>
  );
}
