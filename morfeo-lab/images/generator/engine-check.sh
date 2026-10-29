#!/usr/bin/env bash
# Build-time check of this image: JMeter runs a plan with every kind of thread group and timer the lab reads (core,
# setUp, tearDown, Concurrency, Arrivals, Ultimate, Open Model, Throughput Shaping Timer, Dummy Sampler, Groovy),
# writes the CSV results the lab expects and builds its HTML report. A problem fails the build: better here than in
# the middle of a test.
set -u
D=/tmp/engine-check
rm -rf "$D"; mkdir -p "$D/www"
cp /opt/morfeo/engine-check.jmx /opt/morfeo/engine-check.csv "$D/"
echo ok > "$D/www/index.html"
cd "$D/www" && python3 -m http.server 18077 --bind 127.0.0.1 >/dev/null 2>&1 &
SERVER=$!
sleep 1
cd "$D" || exit 1
timeout 180 jmeter -n -t engine-check.jmx -l kpi.jtl -j jmeter.log > console.log 2>&1
RC=$?
kill "$SERVER" 2>/dev/null
fail() {
  echo "ENGINE CHECK FAILED: $*" >&2
  tail -40 "$D/jmeter.log" >&2 2>/dev/null
  cat "$D/console.log" >&2 2>/dev/null
  exit 1
}
[ "$RC" -eq 0 ] || fail "JMeter exited with code $RC"
grep -q "All Setup Threads have ended" "$D/jmeter.log" || fail "the setUp marker the lab waits for is not in jmeter.log"
python3 - "$D/kpi.jtl" <<'PY' || fail "the results are not what the lab expects"
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1], newline="", encoding="utf-8")))
need = {"timeStamp", "elapsed", "label", "responseCode", "responseMessage", "threadName", "success", "bytes",
        "allThreads", "Connect", "Latency", "URL"}
missing = need - set(rows[0] if rows else [])
labels = {r["label"] for r in rows}
want = {"setup-get", "core-get", "core-post", "tx", "concurrency-get", "arrivals-get", "ultimate-get", "shaping-get",
        "open-get", "dummy-sample", "groovy-get", "teardown-get"}
problems = []
if missing:
    problems.append("missing columns: " + ", ".join(sorted(missing)))
if want - labels:
    problems.append("no samples from: " + ", ".join(sorted(want - labels)))
if not any(r["label"] == "tx" and r["responseMessage"].startswith("Number of samples in transaction") for r in rows):
    problems.append("transaction samples are not recognisable")
if any(r["label"].startswith("core-get-") for r in rows):
    problems.append("sub-results are written (jmeter.save.saveservice.subresults)")
if not any(r["label"] == "groovy-get" and r["URL"].endswith("answer=42") for r in rows):
    problems.append("Groovy scripts do not run")
print(f"{len(rows)} samples, labels: {', '.join(sorted(labels))}")
if problems:
    print("; ".join(problems))
    sys.exit(1)
PY
/opt/morfeo/jmeter-report.sh "$D/kpi.jtl" "$D/report" 500 1500 1000 check > /dev/null 2>&1 || fail "the JMeter HTML report could not be built"
[ -f "$D/report/index.html" ] || fail "the JMeter HTML report is empty"
echo "Engine check OK: $(jmeter --version 2>/dev/null | grep -o '[0-9]\+\.[0-9]\+\(\.[0-9]\+\)\?' | head -1) with plugins: $(ls "$JMETER_HOME"/lib/ext | grep -o 'jmeter-plugins-[a-z-]*-[0-9.]*' | sort -u | tr '\n' ' ')"
rm -rf "$D"
