import argparse

from morfeo.jmeter_plan.cli import add_arguments, run

parser = argparse.ArgumentParser(prog="python -m morfeo.jmeter_plan", description="Genera un plan de JMeter (.jmx)")
add_arguments(parser)
raise SystemExit(run(parser.parse_args()))
