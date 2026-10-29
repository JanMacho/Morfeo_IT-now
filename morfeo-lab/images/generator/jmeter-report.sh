#!/usr/bin/env bash
# Builds the official JMeter HTML dashboard from a CSV results file.
#   jmeter-report.sh <results.jtl> <output-dir> [apdex-satisfied-ms] [apdex-tolerated-ms] [granularity-ms] [title]
# The lab calls it once per scenario, after the results of every generator were merged.
set -eu
IN=$1; OUT=$2; SAT=${3:-500}; TOL=${4:-1500}; GRAN=${5:-60000}; TITLE=${6:-MORFEO}
rm -rf "$OUT"
export HEAP="${MORFEO_REPORT_HEAP:--Xms256m -Xmx1g}"
jmeter -g "$IN" -o "$OUT" -j "$(dirname "$OUT")/jmeter-report.log" \
  -Jjmeter.reportgenerator.apdex_satisfied_threshold="$SAT" \
  -Jjmeter.reportgenerator.apdex_tolerated_threshold="$TOL" \
  -Jjmeter.reportgenerator.overall_granularity="$GRAN" \
  -Jjmeter.reportgenerator.report_title="$TITLE" \
  -Jjmeter.save.saveservice.print_field_names=true
