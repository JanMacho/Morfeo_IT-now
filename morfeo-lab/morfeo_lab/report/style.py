"""The CSS shared by the pages of the lab: light and dark themes, narrow screens and print."""

_DARK = """
    color-scheme: dark;
    --page:#0f1012; --surface:#17181b; --surface-2:#1e2024; --ink:#f2f2f0; --ink-2:#c0c0bb; --muted:#8a8a85;
    --grid:#2a2c30; --axis:#45474d; --ring:rgba(255,255,255,.09); --shadow:none;
    --s1:#4b93ea; --s2:#ef7a45; --s3:#25b585; --s4:#e0a51c; --s5:#e07aa3; --s6:#3fae3f; --s7:#9d93f0; --s8:#ef6b6b;
    --good:#2fbf71; --good-ink:#4fd18b; --warning:#e7b416; --serious:#f08a5d; --critical:#f05a5a;
    --users:#4b93ea; --ok:#2fbf71; --err:#f05a5a; --p50:#4b93ea; --p95:#e7b416; --p99:#f08a5d;
    --shade:rgba(255,255,255,.04); --good-bg:rgba(47,191,113,.10); --bad-bg:rgba(240,90,90,.10);
    --warn-bg:rgba(231,180,22,.10);"""

CSS = r"""
:root{
  color-scheme: light;
  --page:#f5f5f2; --surface:#ffffff; --surface-2:#f4f4f0; --ink:#121212; --ink-2:#4d4c48; --muted:#85847e;
  --grid:#e6e5df; --axis:#c4c3bb; --ring:rgba(18,18,18,.09); --shadow:0 1px 2px rgba(18,18,18,.04);
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#d49a00; --s5:#e87ba4; --s6:#008300; --s7:#4a3aa7; --s8:#e34948;
  --good:#16a34a; --good-ink:#0f7a37; --warning:#e3a008; --serious:#e5703f; --critical:#d33b3b;
  --users:#2a78d6; --ok:#16a34a; --err:#d33b3b; --p50:#2a78d6; --p95:#d49a00; --p99:#e5703f;
  --shade:rgba(18,18,18,.035); --good-bg:rgba(22,163,74,.08); --bad-bg:rgba(211,59,59,.07);
  --warn-bg:rgba(227,160,8,.10);
}
@media (prefers-color-scheme: dark){ :root:where(:not([data-theme="light"])){""" + _DARK + r"""} }
:root[data-theme="dark"]{""" + _DARK + r"""}
*{box-sizing:border-box}
html{background:var(--page)}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  -webkit-text-size-adjust:100%}
main{max-width:1200px;margin:0 auto;padding:28px 16px 64px}
a{color:var(--s1);text-decoration-thickness:1px;text-underline-offset:2px}
h1{font-size:24px;margin:0;font-weight:680;letter-spacing:-.015em;word-break:break-word}
h2{font-size:17px;margin:36px 0 12px;font-weight:650;letter-spacing:-.005em}
h3{font-size:12px;margin:22px 0 8px;font-weight:650;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
h4{font-size:12px;color:var(--ink-2);margin:0 0 6px;font-weight:600}
p{margin:6px 0}
.muted{color:var(--muted)}
.strong{font-weight:650}
.mono,code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px}

/* header */
header.top{display:flex;gap:16px;align-items:flex-start;justify-content:space-between;
  padding-bottom:20px;border-bottom:1px solid var(--grid)}
header.top .title{min-width:0;flex:1}
.brand{display:flex;align-items:center;gap:8px;font-size:11.5px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--muted);margin-bottom:8px;font-weight:600}
.brand .logo{width:14px;height:14px;border-radius:4px;background:linear-gradient(135deg,var(--s1),var(--s7))}
.meta{display:flex;gap:6px 16px;flex-wrap:wrap;color:var(--ink-2);font-size:13px;margin-top:8px}
.meta b{color:var(--ink);font-weight:600}
button.theme{flex:none;border:1px solid var(--ring);background:var(--surface);color:var(--ink-2);border-radius:8px;
  width:34px;height:32px;cursor:pointer;font-size:15px;line-height:1}
button.theme .i-moon{display:none}
:root[data-theme="dark"] button.theme .i-sun{display:none} :root[data-theme="dark"] button.theme .i-moon{display:inline}
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]) button.theme .i-sun{display:none}
  :root:not([data-theme="light"]) button.theme .i-moon{display:inline} }
nav.toc{display:flex;gap:6px;flex-wrap:wrap;margin-top:14px}
nav.toc a{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;color:var(--ink-2);text-decoration:none;
  border:1px solid var(--ring);background:var(--surface);border-radius:999px;padding:3px 10px}
nav.toc a:hover{color:var(--ink);border-color:var(--axis)}

/* verdicts and dots */
.verdict{display:inline-flex;align-items:center;gap:8px;padding:6px 14px;border-radius:999px;font-weight:650;
  border:1px solid var(--ring);background:var(--surface);white-space:nowrap;font-size:14px}
.verdict.sm{padding:2px 9px;font-size:12px;gap:6px;font-weight:600}
.verdict .dot,.dot{width:9px;height:9px;border-radius:50%;display:inline-block;flex:none;background:var(--muted)}
.v-PASS{background:var(--good-bg);color:var(--good-ink)} .v-PASS .dot,.d-PASS{background:var(--good)}
.v-FAIL,.v-ERROR{background:var(--bad-bg);color:var(--critical)}
.v-FAIL .dot,.v-ERROR .dot,.d-FAIL,.d-ERROR{background:var(--critical)}
.v-PASS_WITH_WARNINGS{background:var(--warn-bg)} .v-PASS_WITH_WARNINGS .dot,.d-PASS_WITH_WARNINGS{background:var(--warning)}
.counts{display:flex;gap:6px;flex-wrap:wrap}
.count{font-size:12.5px;border-radius:999px;padding:3px 10px;border:1px solid var(--ring);color:var(--ink-2)}
.count b{font-weight:700;color:var(--ink)}
.count.sm{font-size:12px;padding:1px 8px}
.count.c-PASS{background:var(--good-bg)} .count.c-FAIL,.count.c-ERROR{background:var(--bad-bg)}
.count.c-PASS_WITH_WARNINGS{background:var(--warn-bg)}

/* summary */
.exec{background:var(--surface);border:1px solid var(--ring);border-radius:14px;padding:16px 18px;margin-top:22px;
  box-shadow:var(--shadow)}
.exec h2{margin:0;font-size:17px}
.exec-head{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.exec .table-wrap{box-shadow:none}

/* scenario cards */
.scn{background:var(--surface);border:1px solid var(--ring);border-radius:16px;padding:20px 20px 12px;margin-top:24px;
  box-shadow:var(--shadow);border-top:4px solid var(--muted)}
.v-border-PASS{border-top-color:var(--good)} .v-border-FAIL,.v-border-ERROR{border-top-color:var(--critical)}
.v-border-PASS_WITH_WARNINGS{border-top-color:var(--warning)}
.scn-head{display:flex;gap:12px;align-items:flex-start;justify-content:space-between}
.scn-head>div{min-width:0}
.scn-head h2{margin:0;font-size:19px}
.scn .table-wrap{margin-bottom:6px}

/* tiles */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(138px,1fr));gap:8px;margin-top:16px}
.tile{background:var(--surface-2);border-radius:12px;padding:10px 12px;border:1px solid transparent}
.tile .k{font-size:12px;color:var(--ink-2);font-weight:550}
.tile .v{font-size:23px;font-weight:650;margin-top:1px;letter-spacing:-.015em;font-variant-numeric:tabular-nums;
  white-space:nowrap}
.tile .v small{font-size:12.5px;font-weight:500;color:var(--ink-2);margin-left:3px}
.tile .d{font-size:12px;color:var(--muted);min-height:18px}
.tile.bad{background:var(--bad-bg);border-color:rgba(211,59,59,.25)} .tile.bad .v{color:var(--critical)}
.tile.good .d::before{content:"✓ ";color:var(--good)}

/* links */
.links{display:flex;gap:6px;flex-wrap:wrap;margin:14px 0 4px}
.links a{font-size:12.5px;text-decoration:none;border:1px solid var(--ring);border-radius:8px;padding:4px 10px;
  color:var(--ink-2);background:var(--surface)}
.links a:hover{color:var(--ink);border-color:var(--axis)}
.links a.primary{background:var(--s1);color:#fff;border-color:transparent;font-weight:600}

/* tables */
.table-wrap{overflow-x:auto;background:var(--surface);border:1px solid var(--ring);border-radius:12px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:7px 12px;text-align:left;border-bottom:1px solid var(--grid);white-space:nowrap}
th{font-weight:600;color:var(--muted);font-size:11.5px;background:var(--surface-2);text-transform:uppercase;
  letter-spacing:.04em}
tr:last-child td{border-bottom:none}
tbody tr:hover td{background:var(--shade)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
td.wrap{white-space:normal;min-width:220px}
td.wrap .mono{word-break:break-all}
.status{display:inline-flex;align-items:center;gap:6px;font-weight:600}
.status::before{content:"";width:8px;height:8px;border-radius:50%;display:inline-block}
.st-pass::before{background:var(--good)} .st-fail::before{background:var(--critical)} .st-warn::before{background:var(--warning)}
.st-pass{color:var(--good-ink)} .st-fail{color:var(--critical)}
.delta-good{color:var(--good-ink);font-weight:600} .delta-bad{color:var(--critical);font-weight:600}
.delta-same{color:var(--muted);font-weight:600}
.share{display:inline-block;width:46px;height:6px;border-radius:3px;background:var(--grid);margin-right:8px;
  vertical-align:middle;overflow:hidden}
.share i{display:block;height:100%;background:var(--s1)}
.cls{font-size:12px;font-weight:600}
.cls-server_5xx,.cls-network{color:var(--critical)} .cls-assertion{color:var(--serious)} .cls-client_4xx{color:var(--s1)}
.history tr.run-row td{background:var(--surface-2);padding-top:9px;padding-bottom:9px;white-space:normal}
.history td.chips{text-align:right}
.history td.chips .count{margin-left:4px}

/* diagnosis */
.diag{display:grid;gap:8px}
.diag .item{display:grid;grid-template-columns:96px 1fr;gap:12px;align-items:start;background:var(--surface);
  border:1px solid var(--ring);border-left:4px solid var(--muted);border-radius:10px;padding:10px 14px}
.sev{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;padding-top:2px}
.diag .sev-critical{border-left-color:var(--critical)} .diag .sev-critical .sev{color:var(--critical)}
.diag .sev-serious{border-left-color:var(--serious)} .diag .sev-serious .sev{color:var(--serious)}
.diag .sev-warning{border-left-color:var(--warning)} .diag .sev-warning .sev{color:var(--ink-2)}
.diag .sev-info{border-left-color:var(--s1)} .diag .sev-info .sev{color:var(--s1)}
.diag .sev-good{border-left-color:var(--good)} .diag .sev-good .sev{color:var(--good-ink)}
.diag .t{font-weight:600} .diag .dd{color:var(--ink-2);margin-top:2px}
.callout{border:1px solid var(--ring);border-left:4px solid var(--s1);border-radius:10px;padding:12px 14px;margin-top:14px}
.callout.bad{border-left-color:var(--critical);background:var(--bad-bg)}
.callout.good{border-left-color:var(--good);background:var(--good-bg)}
.callout ul.why{margin:6px 0 0;padding-left:20px}
.callout ul.why li{margin:2px 0}
.callout.warn{border-left-color:var(--warning);background:var(--warn-bg)}
.cap{font-size:13px;color:var(--ink-2);margin:8px 0}

/* charts */
.charts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:16px}
.charts.one{grid-template-columns:minmax(0,1fr)}
figure.chart{margin:0;background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:10px 12px 6px;
  overflow-x:auto}
figure.chart figcaption{font-size:13px;font-weight:600;color:var(--ink);margin:0 2px 2px}
.chart.empty{padding:28px;text-align:center;color:var(--muted);background:var(--surface);border:1px dashed var(--grid);border-radius:12px}
.chart-svg{width:100%;height:auto;display:block;overflow:visible}
.charts.one .chart-svg{min-width:640px}
.chart-svg .tick{fill:var(--muted);font-size:10.5px;font-variant-numeric:tabular-nums}
.chart-svg .tick.cat{fill:var(--ink-2);font-size:11.5px}
.chart-svg .tick.val{fill:var(--ink-2);font-size:10px}
.chart-svg .tick.axis{fill:var(--ink-2);font-size:11px}
.chart-svg .grid{stroke:var(--grid);stroke-width:1} .chart-svg .grid.v{stroke-dasharray:2 4}
.chart-svg .axis{stroke:var(--axis);stroke-width:1}
.chart-svg .line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.chart-svg .line.dash{stroke-dasharray:6 4;stroke-width:1.6}
.chart-svg .area{stroke:none;opacity:.85} .chart-svg .area.soft{opacity:.12}
.chart-svg .shade{fill:var(--shade)} .chart-svg .shade-t{fill:var(--muted);font-size:10px}
.chart-svg .slo{stroke:var(--critical);stroke-width:1.3;stroke-dasharray:6 4}
.chart-svg .slo-t{fill:var(--critical);font-size:10px}
.chart-svg .pt{stroke:var(--surface);stroke-width:2} .chart-svg .pt2{stroke:var(--surface);stroke-width:2}
.line.c-users{stroke:var(--users)} .line.c-muted{stroke:var(--muted)} .line.c-ok{stroke:var(--ok)} .line.c-err{stroke:var(--err)}
.line.c-p50{stroke:var(--p50)} .line.c-p95{stroke:var(--p95)} .line.c-p99{stroke:var(--p99)}
.area.c-ok{fill:var(--ok)} .area.c-err{fill:var(--err)} .area.c-p50{fill:var(--p50)}
.pt.c-p50,.dotpt.c-p50{fill:var(--p50)} .pt.c-p95,.dotpt.c-p95{fill:var(--p95)} .pt.c-ok,.dotpt.c-ok{fill:var(--ok)}
.dotpt.c-users{fill:var(--users)} .dotpt.c-p99{fill:var(--p99)}
.pt-pass{fill:var(--good)} .pt-fail{fill:var(--critical)} .pt-other{fill:var(--muted)}
.legend{display:flex;gap:4px 14px;flex-wrap:wrap;margin:0 2px 6px;font-size:12px;color:var(--ink-2)}
.legend-line{font-size:12.5px;color:var(--ink-2)}
.key{display:inline-flex;align-items:center;gap:6px}
i.sw{display:inline-block;width:10px;height:10px;border-radius:2px}
i.sw.c-users{background:var(--users)} i.sw.c-ok{background:var(--ok)} i.sw.c-err{background:var(--err)}
i.sw.c-p50{background:var(--p50)} i.sw.c-p95{background:var(--p95)} i.sw.c-p99{background:var(--p99)}
i.sw.dash{background:none;border-top:2px dashed currentColor;height:0;color:var(--ink-2)}
i.sw.dash.c-users{color:var(--users)} i.sw.dash.c-ok{color:var(--ok)} i.sw.dash.c-muted{color:var(--muted)}
i.sw.slo-sw{background:none;border-top:2px dashed var(--critical);height:0}
i.sw.dot-pass{background:var(--good);border-radius:50%;width:9px;height:9px}
i.sw.dot-fail{background:var(--critical);border-radius:50%;width:9px;height:9px}
.bar.s1{fill:var(--s1)} .bar.s2{fill:var(--s2)} .bar.s3{fill:var(--s3)} .bar.s4{fill:var(--s4)}
.bar.s5{fill:var(--s5)} .bar.s6{fill:var(--s6)} .bar.s7{fill:var(--s7)} .bar.s8{fill:var(--s8)}
i.sw.s1{background:var(--s1)} i.sw.s2{background:var(--s2)} i.sw.s3{background:var(--s3)} i.sw.s4{background:var(--s4)} i.sw.s5{background:var(--s5)} i.sw.s6{background:var(--s6)}
i.sw.s7{background:var(--s7)} i.sw.s8{background:var(--s8)}
i.sw.ln{width:14px;height:3px} i.sw.sq{width:10px;height:10px;border-radius:3px}
i.sw.ln.dash,i.sw.slo-sw{height:0}
.legend i.sw.s1,.legend i.sw.s2,.legend i.sw.s3,.legend i.sw.s4,.legend i.sw.s5,.legend i.sw.s6,.legend i.sw.s7,
.legend i.sw.s8{width:10px;height:10px;border-radius:3px}

/* history trends */
.trends{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
.trend{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:12px 14px;box-shadow:var(--shadow)}
.trend-h{display:flex;flex-direction:column;gap:1px;margin-bottom:8px}
.trend-h .muted{font-size:12px}
.trend-dots{display:flex;gap:3px;flex-wrap:wrap;margin-bottom:8px}
.trend-dots .dot{width:8px;height:8px}
.trend-row{display:grid;grid-template-columns:42px 1fr auto;gap:8px;align-items:center;font-size:12.5px}
.trend-row .k{color:var(--muted)} .trend-row .v{font-variant-numeric:tabular-nums;font-weight:600}
svg.spark{width:100%;height:28px;display:block}
svg.spark .line{fill:none;stroke-width:1.8}
svg.spark .pt{stroke:none} svg.spark .pt.c-p95{fill:var(--p95)}

/* profile */
.stack{margin:8px 0 12px}
.stack-t{font-size:12px;color:var(--ink-2);margin-bottom:6px}
.stack-bar{display:flex;height:12px;border-radius:6px;overflow:hidden}
.stack-bar .seg{display:block;height:100%}
.seg.s1{background:var(--s1)} .seg.s2{background:var(--s2)} .seg.s3{background:var(--s3)} .seg.s4{background:var(--s4)}
.seg.s5{background:var(--s5)} .seg.s6{background:var(--s6)} .seg.s7{background:var(--s7)} .seg.s8{background:var(--s8)}
.stack .legend{margin:8px 0 0}
.stack .legend i.sw{width:10px;height:10px;border-radius:3px}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:12px}

.back{margin-top:28px}
footer{margin-top:48px;color:var(--muted);font-size:12px}

@media (max-width:820px){ .charts{grid-template-columns:minmax(0,1fr)} }
@media (max-width:560px){
  main{padding:20px 12px 48px} h1{font-size:20px} .tile .v{font-size:19px}
  .scn{padding:16px 14px 10px} .scn-head{flex-direction:column} .diag .item{grid-template-columns:1fr;gap:2px}
  .two{grid-template-columns:minmax(0,1fr)} .tiles{grid-template-columns:repeat(2,minmax(0,1fr))}
  .charts:not(.one) .chart-svg .tick{font-size:18px} .charts:not(.one) .chart-svg .tick.val,
  .charts:not(.one) .chart-svg .shade-t,.charts:not(.one) .chart-svg .slo-t{font-size:15px}
}
@media print{
  :root{--page:#fff;--surface:#fff}
  nav.toc, button.theme, .links{display:none}
  .scn, .table-wrap, figure.chart, .exec, .trend{break-inside:avoid;box-shadow:none}
  main{max-width:none;padding:0}
}
"""
