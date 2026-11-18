"""Provider-reported scene/granule percentages; missing values are never zero."""
import math


def percentage(value):
    if value is None or isinstance(value,bool):return None
    try:value=float(value)
    except (TypeError,ValueError,OverflowError):return None
    return value if math.isfinite(value) and 0<=value<=100 else None


def parse_limit(value):
    if value is None or isinstance(value,str) and not value.strip():return None
    number=percentage(value)
    if number is None:raise ValueError('Enter maximum cloud cover from 0 to 100 percent, or leave it blank for no limit')
    return number


def cdse_percentage(item):
    values={number for entry in item.get('Attributes',[]) if entry.get('Name')=='cloudCover'
        and (number:=percentage(entry.get('Value'))) is not None}
    return next(iter(values)) if len(values)==1 else None


def filter_cloud(items,maximum=None,include_unknown=False):
    if maximum is None:return list(items)
    return [item for item in items if (percentage(item.get('cloud_cover')) is None and include_unknown)
        or (percentage(item.get('cloud_cover')) is not None and percentage(item['cloud_cover'])<=maximum)]


def annotate(items,config,source,extract):
    result=[dict(item,cloud_cover=percentage(extract(item)),cloud_cover_source=source) for item in items]
    return filter_cloud(result,config.max_cloud_cover,config.include_unknown_cloud)
