import argparse

from morfeo.jar_scanner.cli import add_arguments, run

parser = argparse.ArgumentParser(prog="python -m morfeo.jar_scanner", description="Descubre los endpoints de un .jar")
add_arguments(parser)
raise SystemExit(run(parser.parse_args()))
