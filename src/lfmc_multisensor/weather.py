"""Appendix weather primitives extracted from audited terrain_weather.py.

No acquisition, raster IO or implicit downscaling; adjustment is explicit.
"""
import numpy as np
from .features import vpd_from_tmean as compute_vpd_from_tmean

TERRAIN_WEATHER_VERSION = "agera5_daily_constant_rh_lapse_20261006"
LAPSE_RATE_K_M = 0.0065

def saturation_hpa(t_k):
    t = np.asarray(t_k, float) - 273.15
    with np.errstate(all="ignore"):
        return 6.1078 * np.exp(17.269 * t / (237.3 + t))

def dewpoint_from_hpa(e):
    with np.errstate(all="ignore"):
        a = np.log(np.asarray(e, float) / 6.1078)
        return 273.15 + 237.3 * a / (17.269 - a)

def adjust_daily_weather(weather, local_elevation_m, reference_elevation_m):
    """Return a separate feature dictionary and validity diagnostics.

    Preserve source values exactly at zero height difference. Daily RH proxy
    VP/es(Tmean) must be in (0,1]; supersaturated proxies are flagged, not clipped.
    This constraint concerns the approximation, not an assertion that daily
    aggregate source observations are erroneous.
    """
    needed = ["Tmean", "Tmax", "VP", "dewpoint"]
    if any(n not in weather for n in needed):
        raise ValueError("Daily adjustment requires Tmean, Tmax, VP and dewpoint")
    tm, tx, vp, td, zl, zr = np.broadcast_arrays(*[np.asarray(weather[n], float) for n in needed],
                                                np.asarray(local_elevation_m, float), np.asarray(reference_elevation_m, float))
    dz = zl - zr
    ta = tm - LAPSE_RATE_K_M * dz
    tmax = tx - LAPSE_RATE_K_M * dz
    with np.errstate(all="ignore"):
        scale = saturation_hpa(ta) / saturation_hpa(tm)
        rhproxy = vp / saturation_hpa(tm)
        adjusted_vp = vp * scale
        adjusted_td = dewpoint_from_hpa(saturation_hpa(td) * scale)
    valid = (np.isfinite(tm+tx+vp+td+zl+zr+ta+tmax+adjusted_vp+adjusted_td)
             & (tm>0) & (tx>=tm) & (td>0) & (td<=tm+1e-8) & (ta>0)
             & (rhproxy>0) & (rhproxy<=1+1e-10))
    adjusted = {k:np.asarray(v, float).copy() for k,v in weather.items()}
    for name, value in {"Tmean":ta,"Tmax":tmax,"VP":adjusted_vp,"dewpoint":adjusted_td}.items():
        # Important: zero correction does not replace daily dewpoint by a VP-derived value.
        value = np.where(dz==0, np.broadcast_to(weather[name],tm.shape), value)
        adjusted[name] = np.where(valid,value,np.nan)
    adjusted["VPD"] = compute_vpd_from_tmean(adjusted["Tmean"],adjusted["VP"])
    return adjusted, {"valid":valid,"delta_z_m":dz,"daily_rh_proxy":rhproxy,
                      "version":TERRAIN_WEATHER_VERSION,"lapse_rate_K_m":LAPSE_RATE_K_M}

def strict_bilinear(data, x_index, y_index):
    """Interpolate array indices; only positive-weight neighbours are required.

    Missing or out-of-domain neighbours invalidate the output. No nearest-land
    substitution, extrapolation or weight renormalisation. Integer coordinates
    are source pixel centres; index arrays can have arbitrary common shape.
    """
    data=np.asarray(data,float)
    x,y=np.broadcast_arrays(np.asarray(x_index,float),np.asarray(y_index,float))
    finite=np.isfinite(x+y); x=np.where(finite,x,0);y=np.where(finite,y,0)
    x0=np.floor(x).astype(int);y0=np.floor(y).astype(int)
    dx=x-x0;dy=y-y0; result=np.zeros(x.shape);valid=finite.copy()
    for ox,oy,w in [(0,0,(1-dx)*(1-dy)),(1,0,dx*(1-dy)),(0,1,(1-dx)*dy),(1,1,dx*dy)]:
        xx=x0+ox; yy=y0+oy; needed=w>0
        inside=(xx>=0)&(xx<data.shape[1])&(yy>=0)&(yy<data.shape[0])
        value=data[np.clip(yy,0,data.shape[0]-1),np.clip(xx,0,data.shape[1]-1)]
        valid &= ~needed | (inside & np.isfinite(value))
        result += np.where(needed & inside & np.isfinite(value),w*value,0)
    return np.where(valid,result,np.nan)

WEATHER_FEATURE_VERSION = "agera5_v2_daily_totals_tmean_vpd_20260907"
WEATHER_SOURCE = "AgERA5 v2.0 (CDS sis-agrometeorological-indicators)"
