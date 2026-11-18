"""Class routing independent of field-site suitability filters."""
import numpy as np
DW_BANDS = ["water","trees","grass","flooded_vegetation","crops","shrub_and_scrub","built","bare","snow_and_ice"]
DW_MODEL_CODES = {1:10,2:30,5:20}  # Same class codes as WorldCover.


def worldcover_route(fractions,descriptions):
    """Reproduce the dominant-category routing used for published inference."""
    fractions=np.asarray(fractions)
    codes=np.array([int(name.removeprefix('wc_')) for name in descriptions])
    if not np.array_equal(codes,[10,20,30,40,50,60,70,80,90,95,100]):raise ValueError('WorldCover class order mismatch')
    good=np.isfinite(fractions).all(axis=0)&np.isclose(fractions.sum(axis=0),1,atol=1e-5)&(fractions>=0).all(axis=0)
    return np.where(good,codes[np.argmax(np.nan_to_num(fractions,nan=-1),axis=0)],0).astype('uint8')


def dynamic_world_route(probabilities):
    """Choose among ALL nine classes, then route trees/grass/shrub only.

    Inputs are temporal mean probabilities, spatially area-averaged BEFORE
    selection if the output is coarser than 10 m. Ties use original DW class
    order. Return class, confidence, finite support; do not impose training masks.
    """
    p=np.asarray(probabilities,float)
    if p.shape[0]!=9:raise ValueError("All nine Dynamic World probabilities required")
    good=np.isfinite(p).all(axis=0)&(p>=0).all(axis=0)&(p<=1+1e-6).all(axis=0)&np.isclose(p.sum(axis=0),1,atol=1e-4,rtol=0)
    dominant=np.argmax(np.nan_to_num(p,nan=-1),axis=0)
    route=np.zeros(dominant.shape,dtype=np.uint8)
    for label,code in DW_MODEL_CODES.items():route[good&(dominant==label)]=code
    confidence=np.where(good,np.max(p,axis=0),np.nan)
    return route,confidence,good


def area_probabilities(data,tf,x,y,width=300,*,band_count=9):
    """Exact rectangle/cell overlap; missing probability cells do not vote.

    Nominal 300 m supports, axis aligned in UTM, match prior coarse routing.
    Coverage is recorded, without introducing a suitability threshold.
    """
    bands=band_count
    if not isinstance(bands,int) or bands<1 or bands>data.shape[0]:raise ValueError('Invalid area aggregation band count')
    out=np.full((bands,)+x.shape,np.nan);support=np.zeros(x.shape)
    for i,(cx,cy) in enumerate(zip(x.ravel(),y.ravel())):
        left,right=cx-width/2,cx+width/2;bottom,top=cy-width/2,cy+width/2
        c0=max(0,int(np.floor((left-tf.c)/tf.a)));c1=min(data.shape[2],int(np.ceil((right-tf.c)/tf.a)))
        r0=max(0,int(np.floor((top-tf.f)/tf.e)));r1=min(data.shape[1],int(np.ceil((bottom-tf.f)/tf.e)))
        if c1<=c0 or r1<=r0:continue
        xs=tf.c+np.arange(c0,c1)*tf.a;ys=tf.f+np.arange(r0,r1)*tf.e
        wx=np.maximum(0,np.minimum(xs+tf.a,right)-np.maximum(xs,left))
        wy=np.maximum(0,np.minimum(ys,top)-np.maximum(ys+tf.e,bottom))
        weights=wy[:,None]*wx[None,:];block=data[:bands,r0:r1,c0:c1]
        valid=np.isfinite(block).all(axis=0);weights=np.where(valid,weights,0);total=weights.sum()
        if total>0:out.reshape(bands,-1)[:,i]=np.sum(np.where(valid[None],block,0)*weights[None],axis=(1,2))/total
        support.ravel()[i]=total/(width*width)
    return out,support
