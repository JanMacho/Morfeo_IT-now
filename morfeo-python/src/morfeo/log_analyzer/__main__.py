import argparse

from morfeo.log_analyzer.cli import add_arguments, run

parser = argparse.ArgumentParser(prog="python -m morfeo.log_analyzer")
add_arguments(parser)
raise SystemExit(run(parser.parse_args()))
