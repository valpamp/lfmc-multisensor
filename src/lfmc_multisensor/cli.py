"""Command-line access to explicit acquisition plans and experimental runs.

See LICENSE for PolyForm Noncommercial 1.0.0 and OUTPUT_TERMS.md for
the separate licence required for commercial use of generated LFMC maps.
"""
import argparse
import json
from .config import load_config
from .workflow import plan,access_check,run,discovery_summary


def main():
    parser=argparse.ArgumentParser(prog='lfmc-multisensor')
    parser.add_argument('command',choices=['plan','access-check','discover','run','statistics','gui'])
    parser.add_argument('config',nargs='?')
    args=parser.parse_args()
    if args.command=='gui':
        from .gui import main as gui_main
        gui_main(args.config);return
    if not args.config:parser.error('the config argument is required for this command')
    try:
        config=load_config(args.config)
        if args.command=='plan': result=plan(config)
        elif args.command=='access-check': result=access_check(config)
        elif args.command=='discover':
            result=discovery_summary(config)
        elif args.command=='statistics':
            from .statistics import analyse
            result=analyse(config)
        else: result=run(config)
    except (ValueError,RuntimeError,FileExistsError) as exc:
        parser.exit(2,str(exc)+'\n')
    print(json.dumps(result,indent=2,allow_nan=False))


if __name__=='__main__': main()
