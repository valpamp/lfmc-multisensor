"""Publication-style static figures; invoked only by the statistics worker."""
from datetime import datetime,timedelta
import numpy as np


def plot_summary(folder,stem,label,rows,differences,comparisons,config):
    import matplotlib
    matplotlib.use('Agg',force=True)
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from .statistics import COLOURS
    style={'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'pdf.fonttype':42}
    def dates(ax,days):
        locator=mdates.AutoDateLocator(minticks=3,maxticks=8);ax.xaxis.set_major_locator(locator);ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        if len(set(days))==1:ax.set_xlim(days[0]-timedelta(days=1),days[0]+timedelta(days=1))
    def save(fig,name):
        fig.savefig(folder/(name+'.png'),dpi=300);fig.savefig(folder/(name+'.pdf'));plt.close(fig)
    centre=config.statistics_centre
    with plt.rc_context(style):
        fig,(ax,coverage)=plt.subplots(2,1,figsize=(10,6.4),sharex=True,layout='constrained',height_ratios=[2.5,1])
        for group,colour in COLOURS.items():
            selected=sorted([row for row in rows if row['vegetation']==group],key=lambda row:(row['date'],row['map_index']))
            if not selected:continue
            x=[datetime.fromisoformat(row['date']) for row in selected]
            y=np.array([row[centre] if row[centre] is not None else np.nan for row in selected])
            if centre=='median':low=[row['p25'] for row in selected];high=[row['p75'] for row in selected]
            else:
                low=[row['mean']-row['std'] if row['std'] is not None else None for row in selected]
                high=[row['mean']+row['std'] if row['std'] is not None else None for row in selected]
            low=np.array([value if value is not None else np.nan for value in low]);high=np.array([value if value is not None else np.nan for value in high])
            ax.plot(x,y,color=colour,marker='o',ms=4,lw=1.7,ls='--' if group=='All' else '-',label=group)
            ax.fill_between(x,low,high,color=colour,alpha=.07 if group=='All' else .16,lw=0)
            coverage.plot(x,[row['valid_fraction'] if row['valid_fraction'] is not None else np.nan for row in selected],color=colour,marker='o',ms=3,lw=1.3)
        all_days=[datetime.fromisoformat(row['date']) for row in rows if row['vegetation']=='All']
        ax.set_title(label+' — estimated AOI live fuel moisture',loc='left',fontweight='bold')
        ax.set_ylabel(centre.capitalize()+' LFMC (% dry mass)');ax.legend(ncol=4,frameon=False,loc='best');ax.grid(axis='y',alpha=.17)
        if not any(row['n'] for row in rows):ax.text(.5,.5,'No valid LFMC predictions in the selected AOI',ha='center',transform=ax.transAxes)
        coverage.set_ylabel('Valid fraction');coverage.set_ylim(-.03,1.05);coverage.set_xlabel('Analysis / acquisition date (UTC)');coverage.grid(axis='y',alpha=.17)
        dates(coverage,all_days)
        spread='spatial 25th–75th percentiles' if centre=='median' else '±1 spatial standard deviation'
        fig.supxlabel(f'Shading: {spread}, not prediction confidence. Support: {config.statistics_support} native pixels.',fontsize=8)
        save(fig,stem+'_aoi_timeseries')
        if not differences:return
        fig,(ax,forest)=plt.subplots(1,2,figsize=(11,4.7),layout='constrained',gridspec_kw={'width_ratios':[1.8,1]})
        colours=['#26756c','#9c6125','#765ba7']
        labels=[]
        for number,entry in enumerate(comparisons):
            selected=sorted([row for row in differences if row['left']==entry['left'] and row['right']==entry['right']],key=lambda row:(row['date'],row['map_index']))
            x=[datetime.fromisoformat(row['date']) for row in selected]
            y=[row['difference'] if row['difference'] is not None else np.nan for row in selected]
            text=entry['left']+' − '+entry['right'];labels.append(text)
            ax.plot(x,y,'o-',color=colours[number],lw=1.5,ms=4,label=text)
            if entry['estimate'] is not None:
                estimate=entry['estimate'];forest.plot(estimate,number,'o',color=colours[number])
                if entry['lower'] is not None:forest.hlines(number,entry['lower'],entry['upper'],color=colours[number],lw=2)
            if entry['status']!='exploratory_interval':forest.annotate(entry['status'].replace('_',' '),xy=(.98,number),xycoords=('axes fraction','data'),ha='right',va='bottom',fontsize=7,color='#666666')
        ax.axhline(0,color='#555555',lw=.8);ax.set_title('Paired class differences by date',loc='left');ax.set_ylabel('LFMC difference (percentage points)');ax.set_xlabel('Analysis / acquisition date (UTC)');ax.legend(frameon=True,facecolor='white',edgecolor='none',framealpha=.95,fontsize=8);ax.grid(axis='y',alpha=.15);dates(ax,all_days)
        forest.axvline(0,color='#777777',lw=.8);forest.set_yticks(range(len(labels)),labels);forest.invert_yaxis();forest.set_title(centre.capitalize()+' across paired dates',loc='left',fontsize=10);forest.set_xlabel('Difference (percentage points)');forest.grid(axis='x',alpha=.15)
        fig.suptitle(label+' — vegetation contrasts',fontweight='bold')
        fig.supxlabel('95% familywise intervals, when available: exploratory paired-date block bootstrap. Pixels are not independent replicates.',fontsize=8)
        save(fig,stem+'_vegetation_differences')
