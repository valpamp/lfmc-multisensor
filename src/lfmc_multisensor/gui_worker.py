"""Isolated background commands for the desktop interface."""
import argparse
import json
from pathlib import Path
from .config import load_config


def check_models(config):
    from .models import load_published_bundle,model_provenance
    provenance=model_provenance()
    result={}
    for vegetation in ('Grass','Shrub','Tree'):
        version=provenance['artifacts'][vegetation]['version']
        print(f'Checking {vegetation} model v{version}...',flush=True)
        bundle=load_published_bundle(config.models_dir/f'best_{vegetation}_gapfill_within_site.joblib',vegetation)
        result[vegetation]=dict(features=len(bundle['features']),status='verified',version=version,model_set_version=provenance['model_set']['version'])
        del bundle
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['plan','access-check','discover','models-check','run','statistics'])
    parser.add_argument('config',type=Path)
    args=parser.parse_args()
    try:
        config=load_config(args.config)
        from .workflow import plan,access_check,discovery_summary,run
        if args.command=='plan':result=plan(config)
        elif args.command=='access-check':result=access_check(config)
        elif args.command=='discover':result=discovery_summary(config)
        elif args.command=='models-check':result=check_models(config)
        elif args.command=='statistics':
            from .statistics import analyse
            result=analyse(config)
        else:
            check_models(config)
            print('Preparing inputs and predicting LFMC...',flush=True)
            result=run(config)
        payload=json.dumps(result,indent=2,allow_nan=False)
        args.config.with_suffix('.result.json').write_text(payload+'\n',encoding='utf-8')
        print(payload,flush=True)
    except Exception as exc:
        # Provider adapters already redact credential-bearing error details.
        print(f'{type(exc).__name__}: {exc}',flush=True)
        raise SystemExit(2) from None


if __name__=='__main__':main()
