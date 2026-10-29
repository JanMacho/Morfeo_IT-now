#!/usr/bin/env bash
# Entry point of a load-generator pod.
#
# The lab and the pod talk through marker files in /work, so the pod works on any Kubernetes cluster (no hostPath,
# no ConfigMap size limit):
#
#   lab                                        pod (this script)
#   ---                                        -----------------
#                                              starts the live exporter and waits for .go
#   copies the bundle to /work/in              (plan-<n>.jmx, data files, morfeo.properties, morfeo-meta.json)
#   copies the private files to /work/secret   (never copied back with the results)
#   touch /work/.hold (only when another generator runs the plan's setUp first: the wait for .go has no time limit)
#   touch /work/.go                            starts JMeter
#                                              touches .setup-done when the plan's setUp thread groups ended
#   touch /work/.stop (optional)               asks JMeter to stop; the results so far are kept
#                                              writes /work/out, the exit code, touches .done
#   copies /work/out to results/
#   touch /work/.collected                     the pod exits with the exit code of JMeter
set -u
WORK="${MORFEO_WORK:-/work}"
IN=$WORK/in
OUT=$WORK/out
SECRET=$WORK/secret
GENERATOR="${JOB_COMPLETION_INDEX:-0}"
GENERATORS="${MORFEO_GENERATORS:-1}"
START_TIMEOUT="${MORFEO_START_TIMEOUT:-600}"
COLLECT_TIMEOUT="${MORFEO_COLLECT_TIMEOUT:-3600}"

log() { echo "$(date -u +%H:%M:%S) [generator ${GENERATOR}/${GENERATORS}] $*"; }

wait_for() {   # wait_for <file> <seconds>; the time does not count while the lab asks to hold on (.hold)
  local f=$1 t=$2 waited=0
  while [ ! -f "$f" ]; do
    sleep 1 & wait $!
    [ -f "$WORK/.hold" ] && continue
    waited=$((waited + 1))
    if [ "$waited" -ge "$t" ]; then return 1; fi
  done
}

mkdir -p "$IN" "$OUT" "$SECRET"

# JMeter's own shutdown lets threads finish their current request and writes the results; if it does not
# finish in time it is terminated.
JM_PID=""
stop_jmeter() {
  [ -n "$JM_PID" ] || return 0
  "$JMETER_HOME/bin/shutdown.sh" > /dev/null 2>&1
  for _ in $(seq 1 "${1:-30}"); do kill -0 "$JM_PID" 2>/dev/null || return 0; sleep 1; done
  kill -TERM "$JM_PID" 2>/dev/null
}
# This script is PID 1: without a trap it would ignore SIGTERM (pod deleted, job cancelled).
on_term() {
  log "SIGTERM received: stopping JMeter"
  stop_jmeter 20
  exit 143
}
trap on_term TERM

python3 "$MORFEO_HOME/jtl_exporter.py" --dir "$OUT" --work "$WORK" --port 9270 \
  --label "run=${MORFEO_RUN_ID:-unknown}" --label "scenario=${MORFEO_SCENARIO:-unknown}" \
  --label "generator=${GENERATOR}" --meta "$IN/morfeo-meta.json" &

log "ready, waiting for the lab (at most ${START_TIMEOUT}s)"
if ! wait_for "$WORK/.go" "$START_TIMEOUT"; then
  log "ERROR: the start signal never arrived"; echo 97 > "$OUT/exit-code"; exit 97
fi

rm -f "$WORK/.hold"
cd "$IN" || exit 96
# JMeter properties: the lab's (morfeo_*) and the application's private ones, in a file outside /work/out
PROPS="$WORK/run.properties"
cat "$IN/morfeo.properties" "$SECRET"/client.properties > "$PROPS" 2>/dev/null
ARGS=(-n -t "plan-${GENERATOR}.jmx" -l "$OUT/kpi.jtl" -j "$OUT/jmeter.log" -q "$PROPS"
      "-Jmorfeo_generator=$GENERATOR" "-Jmorfeo_generators=$GENERATORS")
[ -f "$SECRET/system.properties" ] && ARGS+=(-S "$SECRET/system.properties")
log "starting JMeter"
date -u +%s > "$OUT/started-epoch"
START_EPOCH=$(date +%s)
jmeter "${ARGS[@]}" > "$OUT/jmeter-console.log" 2>&1 &
JM_PID=$!

TICK=0
while kill -0 "$JM_PID" 2>/dev/null; do
  sleep 1 & wait $!; TICK=$((TICK + 1))   # `wait` lets the TERM trap run at once
  if [ -f "$WORK/.stop" ] && [ ! -f "$WORK/.stopping" ]; then
    log "stop requested: shutting JMeter down"; touch "$WORK/.stopping"; stop_jmeter 60 &
  fi
  if [ ! -f "$WORK/.setup-done" ] && grep -q "All Setup Threads have ended" "$OUT/jmeter.log" 2>/dev/null; then
    touch "$WORK/.setup-done"
  fi
  [ $((TICK % 15)) -eq 0 ] && log "running for $(( $(date +%s) - START_EPOCH ))s"
done
wait "$JM_PID"; RC=$?
rm -f "$PROPS"

# JMeter exits with 0 even when it could not load the plan: look at what it wrote
ROWS=0
[ -f "$OUT/kpi.jtl" ] && ROWS=$(tail -n +2 "$OUT/kpi.jtl" | head -1 | wc -l)
if [ "$RC" -eq 0 ] && [ "$ROWS" -eq 0 ] && grep -qE "Error in NonGUIDriver|CannotResolveClassException|ConversionException|An error occurred" \
     "$OUT/jmeter.log" "$OUT/jmeter-console.log" 2>/dev/null; then
  RC=4
fi
if [ "$RC" -ne 0 ]; then
  { grep -hE "ERROR|Error|Exception|Caused by" "$OUT/jmeter-console.log" "$OUT/jmeter.log" 2>/dev/null | sort -u | head -20; } > "$OUT/error.txt"
fi
echo "$RC" > "$OUT/exit-code"
date -u +%s > "$OUT/finished-epoch"
log "JMeter finished with exit code $RC"
# one last scrape window, so that Prometheus sees the final counts
sleep 6
touch "$WORK/.done"

if ! wait_for "$WORK/.collected" "$COLLECT_TIMEOUT"; then
  log "WARNING: the results were not collected within ${COLLECT_TIMEOUT}s"
fi
exit "$RC"
