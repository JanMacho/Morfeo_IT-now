import argparse

from morfeo.traffic_model.cli import add_arguments, run

parser = argparse.ArgumentParser(prog="python -m morfeo.traffic_model", description="Entrena el modelo de tráfico")
add_arguments(parser)
raise SystemExit(run(parser.parse_args()))
