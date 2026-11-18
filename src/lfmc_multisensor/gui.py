"""Local desktop interface; AOI polygons are the default area selection.

See LICENSE for PolyForm Noncommercial 1.0.0 and OUTPUT_TERMS.md for
the separate licence required for commercial use of generated LFMC maps.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from .config import LANDSAT_COMBINED,SENTINEL3_COMBINED

PAPER_URL='https://doi.org/10.3390/rs18213744'
LANDSAT_LABEL = 'Landsat 8 / 9'
SENTINEL3_LABEL = 'Sentinel-3 SYNERGY'
GROUPED_LABELS = {'landsat_c2l2': LANDSAT_LABEL, 's3syn300': SENTINEL3_LABEL}

PRODUCTS={
    'Sentinel-2 L2A':('s2_l2a','',''),
    LANDSAT_LABEL:('landsat_c2l2',LANDSAT_COMBINED,''),
    'Terra MODIS':('modis','Terra MODIS',''),
    'Aqua MODIS':('modis_aqua','Aqua MODIS',''),
    'Suomi NPP VIIRS':('viirs','Suomi NPP VIIRS',''),
    SENTINEL3_LABEL:('s3syn300',SENTINEL3_COMBINED,'')}


PALETTE = {
    'surface': '#f8fafc', 'card': '#ffffff', 'border': '#cbd5e1',
    'ink': '#0f172a', 'muted': '#475569', 'disabled': '#64748b',
    'subtle': '#f1f5f9', 'hover': '#e2e8f0', 'accent': '#2563eb',
    'accent_hover': '#1d4ed8', 'accent_pressed': '#1e40af', 'selection': '#dbeafe',
}


def enable_high_dpi():
    """Set Windows DPI awareness before creating the application window."""
    import platform

    if platform.system() == 'Windows':
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass


def configure_theme(root, ttk):
    import platform
    from tkinter import font

    preferred = {
        'Windows': ('Segoe UI', 'Tahoma'),
        'Darwin': ('SF Pro Text', 'Helvetica Neue', 'Helvetica'),
    }.get(platform.system(), ('DejaVu Sans', 'Ubuntu', 'Liberation Sans'))
    families = set(font.families(root))
    family = next((name for name in preferred if name in families), font.nametofont('TkDefaultFont', root).actual('family'))
    mono = font.nametofont('TkFixedFont', root).actual('family')
    colours = PALETTE
    root.configure(background=colours['surface'])
    root.option_add('*Font', (family, 10))
    root.option_add('*TCombobox*Listbox.background', colours['card'])
    root.option_add('*TCombobox*Listbox.foreground', colours['ink'])
    root.option_add('*TCombobox*Listbox.selectBackground', colours['selection'])
    root.option_add('*TCombobox*Listbox.selectForeground', colours['ink'])

    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', background=colours['surface'], foreground=colours['ink'], font=(family, 10))
    style.configure('Card.TFrame', background=colours['card'])
    style.configure('Title.TLabel', font=(family, 22, 'bold'))
    style.configure('Subtitle.TLabel', foreground=colours['muted'])
    style.configure('Muted.TLabel', foreground=colours['muted'], font=(family, 9))
    style.configure('Card.TLabel', background=colours['card'])
    style.configure('Card.Muted.TLabel', background=colours['card'])
    style.configure('Section.TLabel', background=colours['card'], font=(family, 12, 'bold'))
    style.configure('Link.TLabel', foreground=colours['accent_hover'], font=(family, 9, 'underline'))
    style.map('Link.TLabel', background=[('focus', colours['selection'])])

    style.configure('TButton', background=colours['subtle'], padding=(14, 7), borderwidth=0, relief='flat')
    style.map('TButton',
              background=[('disabled', colours['subtle']), ('pressed', colours['border']), ('active', colours['hover'])],
              foreground=[('disabled', colours['disabled'])])
    style.configure('Accent.TButton', background=colours['accent'], foreground=colours['card'], font=(family, 10, 'bold'))
    style.map('Accent.TButton',
              background=[('disabled', colours['subtle']), ('pressed', colours['accent_pressed']), ('active', colours['accent_hover'])],
              foreground=[('disabled', colours['disabled']), ('!disabled', colours['card'])])
    for name in ('TEntry', 'TCombobox'):
        style.configure(name, fieldbackground=colours['card'], background=colours['subtle'],
                        bordercolor=colours['border'], lightcolor=colours['border'], darkcolor=colours['border'],
                        insertcolor=colours['ink'], padding=(8, 6))
        style.map(name,
                  bordercolor=[('focus', colours['accent'])],
                  lightcolor=[('focus', colours['accent'])],
                  darkcolor=[('focus', colours['accent'])],
                  fieldbackground=[('disabled', colours['subtle']), ('readonly', colours['card'])],
                  foreground=[('disabled', colours['disabled'])],
                  selectbackground=[('focus', colours['selection'])],
                  selectforeground=[('focus', colours['ink'])])
    for name in ('TCheckbutton', 'TRadiobutton'):
        style.configure('Card.' + name, background=colours['card'])
        style.map('Card.' + name, background=[('active', colours['subtle'])])
    style.configure('Treeview', background=colours['card'], fieldbackground=colours['card'],
                    rowheight=font.nametofont('TkDefaultFont', root).metrics('linespace') + 10,
                    bordercolor=colours['border'])
    style.configure('Treeview.Heading', background=colours['subtle'], font=(family, 10, 'bold'), padding=(8, 6))
    style.map('Treeview', background=[('selected', colours['selection'])], foreground=[('selected', colours['ink'])])
    style.configure('TProgressbar', background=colours['accent'], troughcolor=colours['subtle'],
                    bordercolor=colours['subtle'], lightcolor=colours['accent'], darkcolor=colours['accent'])
    style.configure('TScrollbar', background=colours['hover'], troughcolor=colours['surface'],
                    bordercolor=colours['surface'], arrowcolor=colours['muted'])
    style.map('TScrollbar', background=[('active', colours['border'])])
    style.configure('TSeparator', background=colours['border'])
    return mono, 9


def configuration_data(values):
    """Convert a form into the same explicit JSON contract used by the CLI."""
    from .config import RunConfig
    from .naming import default_label,validate_label
    from .providers.cloud import parse_limit
    product,sensor,_=PRODUCTS[values['satellite']]
    if product in GROUPED_LABELS:
        sensor = values.get('configured_sensor', sensor)
    day=values['day'].strip()
    end_day=values.get('end_day','').strip()
    maximum=parse_limit(values.get('max_cloud_cover'))
    include_unknown=bool(values.get('include_unknown_cloud',False))
    try:year=int(values.get('landcover_year','2021'))
    except ValueError:raise ValueError('Select WorldCover 2021 or 2020') from None
    def required_path(key):
        text=values[key].strip()
        if not text:raise ValueError(f'Choose {key.replace("_"," ")}')
        return Path(text).expanduser().resolve()
    paths={key:required_path(key) for key in ['models_dir','cache_dir','output']}
    if product!='s3syn300' and paths['output'].suffix.lower() not in ('.tif','.tiff'):
        raise ValueError('Choose a .tif or .tiff output for this satellite product')
    bounds=None;aoi_path=None;circle=None;square=None
    if values['area_mode']=='shapefile':aoi_path=required_path('aoi_path')
    elif values['area_mode']=='circle':
        try:circle=tuple(float(values[key]) for key in ['latitude','longitude','radius_km'])
        except ValueError:raise ValueError('Enter latitude, longitude and radius in kilometres as numbers') from None
    elif values['area_mode']=='square':
        try:square=tuple(float(values[key]) for key in ['latitude','longitude','side_km'])
        except ValueError:raise ValueError('Enter latitude, longitude and square side length in kilometres as numbers') from None
    elif values['area_mode']=='bbox':
        try:bounds=tuple(float(values[key]) for key in ['west','south','east','north'])
        except ValueError:raise ValueError('Bounding-box coordinates must be numbers in longitude/latitude degrees') from None
    else:raise ValueError('Choose a shapefile, bounding box, circle or square')
    label=validate_label(values['output_label']) if values.get('output_label') is not None else default_label(aoi_path,paths['output'])
    paths['output']=paths['output'].with_name(label+paths['output'].suffix)
    composite=values.get('composite_confirmed',False)
    try:block_length=int(values.get('statistics_block_length',3))
    except ValueError:raise ValueError('Statistics block length must be a number of dates') from None
    statistics=dict(statistics=bool(values.get('statistics',False)),statistics_by_vegetation=bool(values.get('statistics_by_vegetation',True)),
        statistics_centre=values.get('statistics_centre','median'),statistics_support=values.get('statistics_support','available'),
        statistics_comparisons=bool(values.get('statistics_comparisons',False)),statistics_block_length=block_length)
    if values.get('statistics_source','').strip():statistics['statistics_source']=required_path('statistics_source')
    if product in ('viirs','modis','modis_aqua') and not composite:
        raise ValueError('Confirm use of the eight-day composite containing the analysis date')
    ids=tuple(i.strip() for i in values.get('product_ids','').split(',') if i.strip())
    if len(ids)>1 and not end_day:raise ValueError('Select one scene ID for a single date, or set an end date to process a time series')
    configuration=RunConfig(bounds=bounds,day=day,end_day=end_day,landcover_year=year,product=product,sensor=sensor,max_cloud_cover=maximum,include_unknown_cloud=include_unknown,
        tile=values.get('tile','').strip(),product_ids=ids,downscale=bool(values.get('downscale',False)),
        composite_policy='contains-analysis-date' if product in ('viirs','modis','modis_aqua') else '',aoi_path=aoi_path,aoi_circle=circle,aoi_square=square,output_label=label,**statistics,**paths)
    data=dict(day=day,landcover_year=year,landcover_policy='fixed-year',product=product,sensor=sensor,
        tile=configuration.tile,product_ids=list(ids),downscale=configuration.downscale,output_label=label,max_cloud_cover=maximum,include_unknown_cloud=include_unknown,
        **{key:str(value) for key,value in paths.items()})
    if aoi_path:data['aoi_path']=str(aoi_path)
    elif circle:data['aoi_circle']=dict(zip(['latitude','longitude','radius_km'],configuration.aoi_circle))
    elif square:data['aoi_square']=dict(zip(['latitude','longitude','side_km'],configuration.aoi_square))
    else:data['bounds']=list(bounds)
    if configuration.composite_policy:data['composite_policy']=configuration.composite_policy
    if end_day:data['end_day']=end_day
    data.update({key:str(value) if isinstance(value,Path) else value for key,value in statistics.items()})
    return data


def worker_environment():
    environment=os.environ.copy()
    environment.update(PYTHONUNBUFFERED='1',PYTHONUTF8='1',LOKY_MAX_CPU_COUNT='2',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',
        MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',GDAL_NUM_THREADS='1',OPJ_NUM_THREADS='1')
    return environment


class Application:
    def __init__(self,root):
        import tkinter as tk
        from tkinter import ttk
        self.tk=tk;self.ttk=ttk;self.root=root;self.process=None;self.log_file=None;self.log_position=0;self.buttons=[];self.cancelled=False
        self._log_remainder = ''
        self._updating_label=False;self._label_is_auto=True
        self._configured_sensor = None
        self.vars={key:tk.StringVar(value=value) for key,value in dict(
            satellite='Sentinel-2 L2A',area_mode='shapefile',aoi_path='',west='8.75',south='40.0',east='8.77',north='40.02',
            latitude='40.01',longitude='8.76',radius_km='1.0',side_km='2.0',output_label='lfmc_map',
            statistics_centre='median',statistics_support='available',statistics_block_length='3',statistics_source='',
            day='2021-07-22',end_day='',landcover_year='2021',max_cloud_cover='',tile='',product_ids='',models_dir=str(Path.cwd()/'model-assets'),
            cache_dir=str(Path.cwd()/'cache'),output=str(Path.cwd()/'outputs/lfmc_map.tif')).items()}
        self.vars['downscale']=tk.BooleanVar(value=False);self.vars['composite_confirmed']=tk.BooleanVar(value=False)
        for key,value in [('statistics',False),('statistics_by_vegetation',True),('statistics_comparisons',False)]:self.vars[key]=tk.BooleanVar(value=value)
        self.vars['include_unknown_cloud']=tk.BooleanVar(value=False)
        self.status = tk.StringVar(value='Choose an area, then inspect the plan before running.')
        log_font = configure_theme(root, ttk)
        width = min(1220, max(780, root.winfo_screenwidth() - 80))
        height = min(900, max(580, root.winfo_screenheight() - 100))
        root.title('Multi-sensor Live Fuel Moisture Content Mapping')
        root.geometry(f'{width}x{height}')
        root.minsize(780, 580)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        shell = ttk.Frame(root, padding=16)
        shell.grid(sticky='nsew')
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)
        header = ttk.Frame(shell)
        header.grid(row=0, column=0, sticky='ew', pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text='Select an area and satellite to prepare an LFMC map or time series.',
                  style='Subtitle.TLabel', wraplength=410).grid(row=0, column=0, sticky='w')
        for column, (label, command) in enumerate((
            ('Load configuration', self.load_configuration),
            ('Save configuration', self.save_configuration),
        ), start=1):
            button = ttk.Button(header, text=label, command=command)
            button.grid(row=0, column=column, sticky='e', padx=(6, 0))
            self.buttons.append(button)

        workspace = ttk.Frame(shell)
        workspace.grid(row=1, column=0, sticky='nsew')
        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(0, weight=1)
        form_panel = ttk.Frame(workspace)
        form_panel.grid(row=0, column=0, sticky='nsew')
        form_panel.columnconfigure(0, weight=1)
        form_panel.rowconfigure(0, weight=1)
        canvas = tk.Canvas(form_panel, background=PALETTE['surface'], borderwidth=0, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky='nsew')
        self.form_canvas = canvas
        form_scrollbar = ttk.Scrollbar(form_panel, orient='vertical', command=canvas.yview)
        form_scrollbar.grid(row=0, column=1, sticky='ns')
        canvas.configure(yscrollcommand=form_scrollbar.set, yscrollincrement=28)
        form = ttk.Frame(canvas, padding=(0, 0, 8, 0))
        form.columnconfigure(0, weight=1)
        form_window = canvas.create_window((0, 0), window=form, anchor='nw')
        form.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox(form_window)))
        self._build_area(form)
        self._build_satellite(form)
        self._build_files(form)

        activity = ttk.Frame(workspace, style='Card.TFrame', padding=14)
        activity.columnconfigure(0, weight=1)
        activity.rowconfigure(1, weight=1)
        ttk.Label(activity, text='Activity', style='Section.TLabel').grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 8))
        self.log = tk.Text(
            activity, width=1, height=4, wrap='word', state='disabled', font=log_font,
            background=PALETTE['surface'], foreground=PALETTE['ink'], insertbackground=PALETTE['ink'],
            selectbackground=PALETTE['selection'], selectforeground=PALETTE['ink'],
            borderwidth=0, highlightthickness=1, highlightbackground=PALETTE['subtle'],
            highlightcolor=PALETTE['accent'], padx=10, pady=8,
        )
        self.log.grid(row=1, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(activity, orient='vertical', command=self.log.yview)
        scrollbar.grid(row=1, column=1, sticky='ns')
        self.log.configure(yscrollcommand=scrollbar.set)

        compact = None

        def arrange_workspace(event):
            nonlocal compact
            use_compact = event.width < 970
            if compact == use_compact:
                return
            compact = use_compact
            workspace.columnconfigure(1, minsize=0 if compact else 310)
            if compact:
                activity.grid(row=1, column=0, sticky='ew', padx=0, pady=(10, 0))
            else:
                activity.grid(row=0, column=1, sticky='nsew', padx=(12, 0), pady=0)

        workspace.bind('<Configure>', arrange_workspace)
        activity.grid(row=1, column=0, sticky='ew', pady=(10, 0))

        def scroll_form(event):
            if str(event.widget).startswith(str(form_panel)) and canvas.yview() != (0.0, 1.0):
                steps = {4: -1, 5: 1}.get(event.num, 0)
                if not steps and event.delta:
                    steps = -int(event.delta / 120) or (-1 if event.delta > 0 else 1)
                if steps:
                    canvas.yview_scroll(steps, 'units')
                    return 'break'

        def reveal_focus(widget):
            if not isinstance(widget, tk.Widget) or not str(widget).startswith(str(form) + '.'):
                return
            top = widget.winfo_rooty() - canvas.winfo_rooty()
            bottom = top + widget.winfo_height()
            if top < 0:
                canvas.yview_moveto((canvas.canvasy(0) + top - 8) / form.winfo_height())
            elif bottom > canvas.winfo_height():
                canvas.yview_moveto((canvas.canvasy(0) + bottom - canvas.winfo_height() + 8) / form.winfo_height())

        def resize_form(event):
            canvas.itemconfigure(form_window, width=event.width)
            root.after_idle(lambda: reveal_focus(root.focus_get()))

        for event in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            root.bind(event, scroll_form)
        root.bind('<FocusIn>', lambda event: reveal_focus(event.widget))
        canvas.bind('<Configure>', resize_form)

        footer = ttk.Frame(shell)
        footer.grid(row=2, column=0, sticky='ew', pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Separator(footer).grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 10))
        actions = ttk.Frame(footer)
        actions.grid(row=1, column=0, sticky='w')
        for label, command in (
            ('Plan', 'plan'), ('Check access', 'access-check'),
            ('Check models', 'models-check'), ('Find scenes', 'discover'),
        ):
            button = ttk.Button(actions, text=label, command=lambda action=command: self.start(action))
            button.pack(side='left', padx=(0, 6))
            self.buttons.append(button)

        run_actions = ttk.Frame(footer)
        run_actions.grid(row=1, column=1, sticky='e')
        self.cancel_button = ttk.Button(run_actions, text='Cancel', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='left', padx=(0, 6))
        run = ttk.Button(run_actions, text='Run', style='Accent.TButton', command=lambda: self.start('run'))
        run.pack(side='left')
        self.buttons.append(run)
        self.progress = ttk.Progressbar(footer, mode='indeterminate', length=1)
        self.progress.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(8, 5))
        status = ttk.Label(footer, textvariable=self.status, style='Muted.TLabel', wraplength=550, justify='left')
        status.grid(row=3, column=0, sticky='w')
        self.paper_link = ttk.Label(footer, text='Pampanoni et al. (2026)', style='Link.TLabel',
                                    cursor='hand2', takefocus=True)
        self.paper_link.grid(row=3, column=1, sticky='se', padx=(12, 0))
        footer.bind('<Configure>', lambda event: status.configure(wraplength=max(220, event.width - 170)))
        for event in ['<Button-1>', '<Return>', '<space>']:
            self.paper_link.bind(event, self._open_paper)
        for key in ['day','end_day']:self.vars[key].trace_add('write',self._dates_changed)
        for key in ['max_cloud_cover','include_unknown_cloud']:self.vars[key].trace_add('write',self._cloud_changed)
        self.vars['aoi_path'].trace_add('write',self._aoi_name_changed)
        self.vars['output_label'].trace_add('write',self._output_label_changed)
        self.vars['output'].trace_add('write',self._output_path_changed)
        root.protocol('WM_DELETE_WINDOW',self.close);self._area_changed();self._satellite_changed(initial=True)

    def _card(self, parent, row, title, columns=3):
        card = self.ttk.Frame(parent, style='Card.TFrame', padding=(16, 12))
        card.grid(row=row, column=0, sticky='ew', pady=(0, 12))
        self.ttk.Label(card, text=title, style='Section.TLabel').grid(
            row=0, column=0, columnspan=columns, sticky='w', pady=(0, 8))
        return card

    def _hint(self, parent, row, text, columns=3):
        label = self.ttk.Label(parent, text=text, style='Card.Muted.TLabel', justify='left', wraplength=490)
        label.grid(row=row, column=0, columnspan=columns, sticky='ew', pady=(5, 0))
        parent.bind('<Configure>', lambda event: label.configure(wraplength=max(150, event.width - 32)), add='+')

    def _coordinates(self, parent, fields):
        panel = self.ttk.Frame(parent, style='Card.TFrame')
        entries = []
        for column, (key, label) in enumerate(fields):
            panel.columnconfigure(column, weight=1, uniform='coordinates')
            self.ttk.Label(panel, text=label, style='Card.TLabel').grid(row=0, column=column, sticky='w', padx=(0, 6))
            entry = self.ttk.Entry(panel, textvariable=self.vars[key], width=8)
            entry.grid(row=1, column=column, sticky='ew', padx=(0, 6), pady=(4, 0))
            entries.append(entry)
        return panel, entries

    def _build_area(self, form):
        ttk = self.ttk
        area = self._card(form, 0, 'Area of interest', columns=2)
        for column in range(2):
            area.columnconfigure(column, weight=1, uniform='area')
        for index, (value, label) in enumerate((
            ('shapefile', 'Shapefile / polygon file'), ('bbox', 'Bounding box (WGS84)'),
            ('circle', 'Point and radius'), ('square', 'Point and square'),
        )):
            ttk.Radiobutton(area, text=label, value=value, variable=self.vars['area_mode'],
                            style='Card.TRadiobutton', command=self._area_changed).grid(
                row=1 + index // 2, column=index % 2, sticky='w', pady=(0, 4))

        polygon = ttk.Frame(area, style='Card.TFrame')
        polygon.columnconfigure(0, weight=1)
        self.aoi_entry = ttk.Entry(polygon, textvariable=self.vars['aoi_path'])
        self.aoi_entry.grid(row=0, column=0, sticky='ew', padx=(0, 6))
        self.browse_aoi = ttk.Button(polygon, text='Browse…', command=self._choose_aoi)
        self.browse_aoi.grid(row=0, column=1)
        self._hint(polygon, 1, 'Choose a shapefile or GeoJSON. Keep .shp, .shx, .dbf and .prj together.', columns=2)
        box, self.bound_entries = self._coordinates(area, [(key, key.title()) for key in ('west', 'south', 'east', 'north')])
        point = [('latitude', 'Latitude'), ('longitude', 'Longitude')]
        circle, self.circle_entries = self._coordinates(area, [*point, ('radius_km', 'Radius (km)')])
        square, self.square_entries = self._coordinates(area, [*point, ('side_km', 'Side (km)')])
        self.area_panels = {'shapefile': polygon, 'bbox': box, 'circle': circle, 'square': square}
        for panel in self.area_panels.values():
            panel.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(6, 0))
        self._hint(area, 4, 'AOIs can span up to 1° and must lie between 60°S and 60°N.', columns=2)

    def _build_satellite(self, form):
        ttk = self.ttk
        card = self._card(form, 1, 'Satellite and dates')
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text='Satellite', style='Card.TLabel').grid(row=1, column=0, sticky='w')
        satellite = ttk.Combobox(card, textvariable=self.vars['satellite'], values=list(PRODUCTS), state='readonly')
        satellite.grid(row=1, column=1, columnspan=2, sticky='ew', padx=(8, 0), pady=(0, 6))
        satellite.bind('<<ComboboxSelected>>', self._satellite_changed)
        self.satellite_scope = ttk.Label(card, style='Card.Muted.TLabel', wraplength=450, justify='left')
        self.satellite_scope.grid(row=2, column=0, columnspan=3, sticky='w', pady=(0, 6))
        date_fields, _ = self._coordinates(card, [('day', 'Single / start date (UTC)'), ('end_day', 'End date (optional)')])
        date_fields.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(2, 6))

        cloud = ttk.Frame(card, style='Card.TFrame')
        cloud.grid(row=4, column=0, columnspan=3, sticky='ew', pady=(4, 6))
        ttk.Label(cloud, text='Max cloud (%)', style='Card.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Entry(cloud, textvariable=self.vars['max_cloud_cover'], width=7).grid(row=0, column=1, padx=8)
        ttk.Checkbutton(cloud, text='Include unknown cloud cover', variable=self.vars['include_unknown_cloud'],
                        style='Card.TCheckbutton').grid(row=0, column=2, sticky='w')
        ttk.Label(cloud, text='Blank = no limit', style='Card.Muted.TLabel').grid(row=0, column=3, sticky='w', padx=8)
        self._field(card, 5, 'Optional tile / WRS filter', 'tile')
        self._field(card, 6, 'Selected scene IDs (optional)', 'product_ids')
        self.composite = ttk.Checkbutton(card, text='Use the eight-day composite (may include later observations)',
                                         variable=self.vars['composite_confirmed'], style='Card.TCheckbutton')
        self.composite.grid(row=7, column=0, columnspan=3, sticky='w', pady=(5, 0))
        self._hint(card, 8, 'Dates are inclusive. Leave the end date blank for one day. A date range writes one map per scene.')

    def _build_files(self, form):
        ttk = self.ttk
        card = self._card(form, 2, 'Models and outputs')
        card.columnconfigure(1, weight=1)
        self._field(card, 1, 'Published models directory', 'models_dir', lambda: self._choose_directory('models_dir'))
        self._field(card, 2, 'Input cache directory', 'cache_dir', lambda: self._choose_directory('cache_dir'))
        self._field(card, 3, 'Output path / filename prefix', 'output', self._choose_output)
        self._field(card, 4, 'Filename label', 'output_label', self._reset_output_label, 'Use AOI name')
        ttk.Label(card, text='WorldCover map', style='Card.TLabel').grid(row=5, column=0, sticky='w', pady=4)
        ttk.Combobox(card, textvariable=self.vars['landcover_year'], values=['2021', '2020'],
                     state='readonly', width=10).grid(row=5, column=1, sticky='w', padx=8, pady=4)
        ttk.Checkbutton(card, text='Downscale weather parameters',
                        variable=self.vars['downscale'], style='Card.TCheckbutton').grid(
            row=6, column=0, columnspan=3, sticky='w', pady=(8, 4))
        ttk.Checkbutton(card, text='Calculate AOI statistics and figures after mapping', variable=self.vars['statistics'],
                        style='Card.TCheckbutton').grid(row=7, column=0, columnspan=3, sticky='w', pady=4)
        options = ttk.Button(card, text='Statistics options / saved maps…', command=self.statistics_options)
        options.grid(row=8, column=0, columnspan=3, sticky='w', pady=(4, 0))
        self.buttons.append(options)

    def _field(self, frame, row, label, key, command=None, button_text='Browse…'):
        self.ttk.Label(frame, text=label, style='Card.TLabel').grid(row=row, column=0, sticky='w', pady=4)
        self.ttk.Entry(frame, textvariable=self.vars[key]).grid(
            row=row, column=1, columnspan=1 if command else 2, sticky='ew', padx=(8, 6 if command else 0), pady=4)
        if command:
            self.ttk.Button(frame, text=button_text, command=command).grid(row=row, column=2, sticky='ew', pady=4)

    def _dialog(self, title, width, height, minimum):
        window = self.tk.Toplevel(self.root, background=PALETTE['surface'])
        window.title(title)
        width = min(width, window.winfo_screenwidth() - 48)
        height = min(height, window.winfo_screenheight() - 80)
        window.geometry(f'{width}x{height}')
        window.minsize(*minimum)
        window.transient(self.root)
        window.grab_set()
        return window

    def statistics_options(self):
        if self.process:return
        from tkinter import filedialog
        window = self._dialog('AOI time-series statistics', 820, 550, (650, 500))
        body=self.ttk.Frame(window,style='Card.TFrame',padding=16);body.pack(fill='both',expand=True,padx=16,pady=16);body.columnconfigure(1,weight=1)
        self.ttk.Label(body,text='AOI time-series statistics',style='Section.TLabel').grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,12))
        self.ttk.Label(body,text='Existing series / LFMC map',style='Card.TLabel').grid(row=1,column=0,sticky='w')
        self.ttk.Entry(body,textvariable=self.vars['statistics_source']).grid(row=1,column=1,sticky='ew',padx=8)
        def browse():
            path=filedialog.askopenfilename(parent=window,title='Choose series manifest or LFMC map',filetypes=[('LFMC outputs','*.json *.tif *.tiff *.nc'),('All files','*.*')])
            if path:self.vars['statistics_source'].set(path)
        self.ttk.Button(body,text='Browse…',command=browse).grid(row=1,column=2)
        for row,key,label,options in [(2,'statistics_centre','Summary line',['median','mean']),(3,'statistics_support','Pixel support',['available','common'])]:
            self.ttk.Label(body,text=label,style='Card.TLabel').grid(row=row,column=0,sticky='w',pady=9)
            self.ttk.Combobox(body,textvariable=self.vars[key],values=options,state='readonly').grid(row=row,column=1,sticky='ew',padx=8)
        self.ttk.Checkbutton(body,text='Separate Grass, Shrub and Tree (also retain overall AOI summary)',variable=self.vars['statistics_by_vegetation'],style='Card.TCheckbutton').grid(row=4,column=0,columnspan=3,sticky='w',pady=5)
        def comparisons():
            if self.vars['statistics_comparisons'].get():self.vars['statistics_by_vegetation'].set(True);self.vars['statistics_support'].set('common')
        self.ttk.Checkbutton(body,text='Estimate exploratory paired-date vegetation contrast intervals',variable=self.vars['statistics_comparisons'],command=comparisons,style='Card.TCheckbutton').grid(row=5,column=0,columnspan=3,sticky='w',pady=5)
        self.ttk.Label(body,text='Block length (dates)',style='Card.TLabel').grid(row=6,column=0,sticky='w',pady=5)
        self.ttk.Entry(body,textvariable=self.vars['statistics_block_length'],width=8).grid(row=6,column=1,sticky='w',padx=8)
        self._hint(body, 7, 'Available support uses the valid pixels on each date. Common support uses the same valid pixels on every map and requires identical grids.\nIntervals require common support, at least 12 regularly spaced paired dates, and four blocks.\nFigures and CSV files use the current filename label. Vegetation comes from the saved maps.')
        buttons=self.ttk.Frame(body,style='Card.TFrame');buttons.grid(row=8,column=0,columnspan=3,sticky='e',pady=(12,0))
        self.ttk.Button(buttons,text='Apply options and close',command=window.destroy).pack(side='left',padx=6)
        def analyse_saved():
            if not self.vars['statistics_source'].get():browse()
            if not self.vars['statistics_source'].get():return
            try:self._data()
            except Exception as exc:self._error(exc);return
            window.destroy();self.start('statistics')
        self.ttk.Button(buttons,text='Analyse saved outputs',style='Accent.TButton',command=analyse_saved).pack(side='left')
        return window

    def _area_changed(self):
        polygon=self.vars['area_mode'].get()=='shapefile'
        self.aoi_entry.configure(state='normal' if polygon else 'disabled');self.browse_aoi.configure(state='normal' if polygon else 'disabled')
        for entry in self.bound_entries:entry.configure(state='normal' if self.vars['area_mode'].get()=='bbox' else 'disabled')
        for entry in self.circle_entries:entry.configure(state='normal' if self.vars['area_mode'].get()=='circle' else 'disabled')
        for entry in self.square_entries:entry.configure(state='normal' if self.vars['area_mode'].get()=='square' else 'disabled')
        for mode, panel in self.area_panels.items():
            if mode == self.vars['area_mode'].get():
                panel.grid()
            else:
                panel.grid_remove()
        self._aoi_name_changed()

    def _set_label(self,label,automatic):
        self._updating_label=True
        try:
            self.vars['output_label'].set(label)
            output=self.vars['output'].get()
            if output:self.vars['output'].set(str(Path(output).with_name(label+Path(output).suffix)))
            self._label_is_auto=automatic
        finally:self._updating_label=False

    def _aoi_name_changed(self,*args):
        if self._updating_label or not self._label_is_auto:return
        if self.vars['area_mode'].get()=='shapefile' and self.vars['aoi_path'].get():
            from .naming import default_label
            self._set_label(default_label(self.vars['aoi_path'].get(),self.vars['output'].get()),True)

    def _reset_output_label(self):
        from .naming import default_label
        aoi=self.vars['aoi_path'].get() if self.vars['area_mode'].get()=='shapefile' else None
        self._set_label(default_label(aoi,'lfmc.tif'),True)

    def _output_label_changed(self,*args):
        if self._updating_label:return
        self._label_is_auto=False
        from .naming import validate_label
        try:label=validate_label(self.vars['output_label'].get())
        except ValueError:return  # Keep editing; Plan/Run provides the validation error.
        self._set_label(label,False)

    def _output_path_changed(self,*args):
        if self._updating_label:return
        path=self.vars['output'].get()
        if path and Path(path).stem:self._set_label(Path(path).stem,False)

    def _open_paper(self,event=None):
        import webbrowser
        try:
            if not webbrowser.open_new_tab(PAPER_URL):self.status.set('Open the paper at '+PAPER_URL)
        except (OSError,webbrowser.Error):self.status.set('Open the paper at '+PAPER_URL)

    def _dates_changed(self,*args):
        if self.vars['product_ids'].get():
            self.vars['product_ids'].set('')
            self.status.set('Dates changed. Find scenes again, or leave IDs empty to process all matches in a range.')

    def _cloud_changed(self,*args):
        if self.vars['product_ids'].get():
            self.vars['product_ids'].set('')
            self.status.set('Cloud filter changed. Find scenes again, or leave IDs empty for all matching range scenes.')

    def _satellite_changed(self,event=None,initial=False):
        label=self.vars['satellite'].get()
        product,default_sensor,tile=PRODUCTS[label]
        if not initial:
            self.vars['tile'].set(tile);self.vars['product_ids'].set('');self.vars['composite_confirmed'].set(False)
            self._configured_sensor = None
        if product in GROUPED_LABELS and self._configured_sensor not in (None,default_sensor):
            sensor = self._configured_sensor
            spacecraft = f'Landsat {sensor[-1]}' if product == 'landsat_c2l2' else f'Sentinel-3{sensor[-1]}'
            self.satellite_scope.configure(text=f'Configuration limits search to {spacecraft}. Select {label} again to search both.')
            self.satellite_scope.grid()
        else:
            self.satellite_scope.grid_remove()
        if product in ('viirs','modis','modis_aqua'):
            self.composite.configure(state='normal')
            self.composite.grid()
        else:
            self.composite.configure(state='disabled')
            self.composite.grid_remove()
        output=self.vars['output'].get()
        if output:
            previous=self._updating_label;self._updating_label=True
            try:self.vars['output'].set(str(Path(output).with_suffix('.nc' if product=='s3syn300' else '.tif')))
            finally:self._updating_label=previous

    def _choose_aoi(self):
        from tkinter import filedialog
        path=filedialog.askopenfilename(parent=self.root,title='Choose area polygons',filetypes=[('ESRI Shapefile','*.shp'),('WGS84 GeoJSON','*.geojson'),('All files','*.*')])
        if path:self.vars['aoi_path'].set(path)

    def _choose_directory(self,key):
        from tkinter import filedialog
        path=filedialog.askdirectory(parent=self.root,title='Choose '+key.replace('_',' '))
        if path:self.vars[key].set(path)

    def _choose_output(self):
        from tkinter import filedialog
        native=PRODUCTS[self.vars['satellite'].get()][0]=='s3syn300'
        path=filedialog.asksaveasfilename(parent=self.root,title='Choose a new output file',defaultextension='.nc' if native else '.tif',filetypes=[('Native NetCDF','*.nc')] if native else [('GeoTIFF','*.tif')])
        if path:self.vars['output'].set(path)

    def values(self):
        values = {key: variable.get() for key, variable in self.vars.items()}
        if self._configured_sensor is not None and values['satellite'] in GROUPED_LABELS.values():
            values['configured_sensor'] = self._configured_sensor
        return values

    def _data(self):return configuration_data(self.values())

    def _error(self,exc):
        from tkinter import messagebox
        self.status.set(str(exc));messagebox.showerror('Check the request',str(exc),parent=self.root)

    def save_configuration(self):
        from tkinter import filedialog
        try:data=self._data()
        except Exception as exc:self._error(exc);return
        path=filedialog.asksaveasfilename(parent=self.root,title='Save request',defaultextension='.json',filetypes=[('JSON configuration','*.json')])
        if path:Path(path).write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8');self.status.set('Configuration saved.')

    def load_configuration(self,path=None):
        from tkinter import filedialog
        from .config import load_config
        path=path or filedialog.askopenfilename(parent=self.root,title='Load request',filetypes=[('JSON configuration','*.json')])
        if not path:return
        previous=self._updating_label;self._updating_label=True
        try:
            config=load_config(path)
            if config.product in GROUPED_LABELS:
                label = GROUPED_LABELS[config.product]
                self._configured_sensor = config.sensor
            else:
                label=next(label for label,domain in PRODUCTS.items() if domain[:2]==(config.product,config.sensor) or not config.sensor and domain[0]==config.product)
                self._configured_sensor = None
            for key in ['models_dir','cache_dir','output']:self.vars[key].set(str(getattr(config,key).resolve()))
            self.vars['satellite'].set(label);self._satellite_changed(initial=True)
            for key in ['day','tile']:self.vars[key].set(getattr(config,key))
            self.vars['end_day'].set(config.end_day);self.vars['landcover_year'].set(str(config.landcover_year))
            self.vars['max_cloud_cover'].set('' if config.max_cloud_cover is None else str(config.max_cloud_cover))
            self.vars['include_unknown_cloud'].set(config.include_unknown_cloud)
            for key in ['statistics','statistics_by_vegetation','statistics_centre','statistics_support','statistics_comparisons','statistics_block_length']:
                self.vars[key].set(getattr(config,key))
            self.vars['statistics_source'].set(str(config.statistics_source.resolve()) if config.statistics_source else '')
            self.vars['product_ids'].set(', '.join(config.product_ids));self.vars['downscale'].set(config.downscale)
            self.vars['composite_confirmed'].set(bool(config.composite_policy));self.vars['area_mode'].set('square' if config.aoi_square else 'circle' if config.aoi_circle else 'shapefile' if config.aoi else 'bbox')
            self.vars['aoi_path'].set(str(config.aoi_path) if config.aoi_path else '')
            if config.aoi_circle:
                for key,value in zip(['latitude','longitude','radius_km'],config.aoi_circle):self.vars[key].set(str(value))
            if config.aoi_square:
                for key,value in zip(['latitude','longitude','side_km'],config.aoi_square):self.vars[key].set(str(value))
            for key,value in zip(['west','south','east','north'],config.bounds):self.vars[key].set(str(value))
            from .naming import output_label
            self.vars['output_label'].set(output_label(config))
            self.vars['output'].set(str(config.output.with_name(output_label(config)+config.output.suffix).resolve()))
            self._label_is_auto=config.output_label is None
            self._area_changed();self.status.set('Configuration loaded. Inspect the plan before running.')
        except Exception as exc:self._error(exc)
        finally:self._updating_label=previous

    def _append(self,text):
        self.log.configure(state='normal');self.log.insert('end',text);self.log.see('end');self.log.configure(state='disabled')

    def _read_worker_log(self, text):
        self._append(text)
        *lines, self._log_remainder = (self._log_remainder + text).split('\n')
        if self.cancelled:
            return
        for line in lines:
            if line.startswith('CDSE download: '):
                value = line.removeprefix('CDSE download: ').partition('%')[0]
                if not value.isdecimal() or not 0 <= int(value) <= 100:
                    continue
                if str(self.progress['mode']) != 'determinate':
                    self.progress.stop()
                    self.progress.configure(mode='determinate')
                self.progress.configure(value=int(value))
                self.status.set(line.strip())
            elif line.startswith('CDSE download complete.'):
                self.progress.stop()
                self.progress.configure(mode='indeterminate', value=0)
                self.progress.start(12)
                self.status.set(line.strip())
            elif line.startswith(('CDSE: ', 'Sentinel-3: ')):
                self.status.set(line.strip())

    def start(self,command):
        if self.process:return
        try:
            data=self._data()
            folder=Path(data['cache_dir'])/'gui_requests';folder.mkdir(parents=True,exist_ok=True)
            stem=uuid.uuid4().hex;request=folder/(stem+'.json');request.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
            self.log_path=folder/(stem+'.log');self.result_path=request.with_suffix('.result.json');self.command=command;self.cancelled=False
            self.log_position=0;self._log_remainder='';self.log_file=self.log_path.open('wb')
            flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
            self.process=subprocess.Popen([sys.executable,'-u','-m','lfmc_multisensor.gui_worker',command,str(request)],stdout=self.log_file,stderr=subprocess.STDOUT,env=worker_environment(),creationflags=flags)
            for button in self.buttons:button.configure(state='disabled')
            self.cancel_button.configure(state='normal')
            self.progress.configure(mode='indeterminate', value=0);self.progress.start(12)
            self.status.set(command+' in progress.');self._append('\n'+command.upper()+'\n');self.root.after(150,self._poll)
        except Exception as exc:
            if self.log_file:self.log_file.close();self.log_file=None
            self._error(exc)

    def _poll(self):
        if self.process is None:return
        with self.log_path.open('rb') as log:
            log.seek(self.log_position);chunk=log.read();self.log_position=log.tell()
        if chunk:self._read_worker_log(chunk.decode('utf-8',errors='replace'))
        result=self.process.poll()
        if result is None:self.root.after(150,self._poll);return
        self.log_file.close();self.log_file=None
        with self.log_path.open('rb') as log:
            log.seek(self.log_position);tail=log.read();self.log_position=log.tell()
        if tail:self._read_worker_log(tail.decode('utf-8',errors='replace'))
        self.process=None;self.progress.stop();self.progress.configure(mode='indeterminate',value=0)
        for button in self.buttons:button.configure(state='normal')
        self.cancel_button.configure(state='disabled')
        self.status.set('Cancelled. Verified cached inputs can be reused.' if self.cancelled else 'Completed. Review the results above.' if result==0 else f'Stopped (exit {result}). Review the error above; verified cached inputs can be reused.')
        if result==0 and self.command=='discover' and self.result_path.exists():
            self._select_scene(json.loads(self.result_path.read_text(encoding='utf-8')))

    def _select_scene(self,items):
        from .providers.cloud import parse_limit,filter_cloud,percentage
        if not items:self.status.set('No scenes match date/area/cloud settings. If cloud metadata is unavailable, clear the limit or include unknown values.');return
        multiple=bool(self.vars['end_day'].get().strip())
        try:initial=parse_limit(self.vars['max_cloud_cover'].get())
        except ValueError as exc:self._error(exc);return
        def select(chosen):
            self.vars['product_ids'].set(', '.join(item.get('Id',item.get('id','')) for item in chosen))
            detail=''
            if len(chosen)==1:
                value=percentage(chosen[0].get('cloud_cover'));detail=' Cloud cover: '+('N/A' if value is None else f'{value:.2f}%')+'.'
            self.status.set(f'{len(chosen)} scene(s) selected.'+detail+' You can now run the request.')
        if len(items)==1:
            accepted=filter_cloud(items,initial,self.vars['include_unknown_cloud'].get())
            if accepted:select(accepted)
            else:self.status.set('The returned scene does not meet the current cloud settings. Search again or adjust the limit.')
            return
        window = self._dialog('Choose matching scenes', 1080, 530, (665, 395))
        self.ttk.Label(window,text='Choose matching scenes',style='Title.TLabel').pack(anchor='w',padx=16,pady=(12,4))
        self.ttk.Label(window,text='Cloud cover applies to the whole scene. N/A means unavailable.',style='Subtitle.TLabel').pack(anchor='w',padx=16)
        self.ttk.Label(window,text='Choose scenes for the time series, or select all visible matches.' if multiple else 'Choose one scene.',style='Muted.TLabel').pack(anchor='w',padx=16,pady=(2,6))
        filters=self.ttk.Frame(window);filters.pack(fill='x',padx=16,pady=6)
        maximum=self.tk.StringVar(value='' if initial is None else str(initial));unknown=self.tk.BooleanVar(value=self.vars['include_unknown_cloud'].get())
        self.ttk.Label(filters,text='Max cloud (%)').pack(side='left')
        self.ttk.Entry(filters,textvariable=maximum,width=8).pack(side='left',padx=6)
        self.ttk.Checkbutton(filters,text='Include unknown values',variable=unknown).pack(side='left',padx=6)
        count=self.tk.StringVar();self.ttk.Label(filters,textvariable=count,style='Muted.TLabel').pack(side='right')
        body=self.ttk.Frame(window,style='Card.TFrame');body.pack(fill='both',expand=True,padx=16)
        body.columnconfigure(0,weight=1);body.rowconfigure(0,weight=1)
        table=self.ttk.Treeview(body,columns=('date','cloud','name'),show='headings',selectmode='extended' if multiple else 'browse')
        table.heading('date',text='Acquisition / composite start');table.heading('cloud',text='Cloud (%)');table.heading('name',text='Scene name / ID')
        table.column('date',width=215,stretch=False);table.column('cloud',width=95,stretch=False,anchor='e')
        table.column('name',width=700,minwidth=540)
        table.tag_configure('unknown',foreground=PALETTE['muted'])
        table.grid(row=0,column=0,sticky='nsew')
        vertical=self.ttk.Scrollbar(body,orient='vertical',command=table.yview);vertical.grid(row=0,column=1,sticky='ns')
        horizontal=self.ttk.Scrollbar(body,orient='horizontal',command=table.xview);horizontal.grid(row=1,column=0,sticky='ew')
        table.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        def apply_filter():
            try:limit=parse_limit(maximum.get())
            except ValueError as exc:self._error(exc);return False
            selected=table.selection();table.delete(*table.get_children())
            for i,item in enumerate(items):
                if not filter_cloud([item],limit,unknown.get()):continue
                value=percentage(item.get('cloud_cover'))
                stamp=item.get('ContentDate',{}).get('Start') or item.get('datetime') or item.get('composite_start','')
                table.insert('','end',iid=str(i),values=(stamp,'N/A' if value is None else f'{value:.2f}',item.get('Name',item.get('id',''))),tags=('unknown',) if value is None else ())
            table.selection_set([key for key in selected if table.exists(key)])
            count.set(f'{len(table.get_children())} / {len(items)} returned scenes')
            return True
        descending=[False]
        def sort_cloud():
            order=sorted(table.get_children(),key=lambda key:(percentage(items[int(key)].get('cloud_cover')) is None,
                (-1 if descending[0] else 1)*(percentage(items[int(key)].get('cloud_cover')) or 0)))
            for index,key in enumerate(order):table.move(key,'',index)
            descending[0]=not descending[0]
        table.heading('cloud',command=sort_cloud)
        self.ttk.Button(filters,text='Apply list filter',command=apply_filter).pack(side='left',padx=6)
        apply_filter()
        hint = self.ttk.Label(window,text='Clear the original limit and search again to recover excluded scenes. Pixel-level cloud QA still applies.',style='Muted.TLabel',wraplength=635,justify='left')
        hint.pack(fill='x',padx=16,pady=(8,0))
        hint.bind('<Configure>',lambda event:hint.configure(wraplength=event.width))
        buttons=self.ttk.Frame(window);buttons.pack(fill='x',padx=16,pady=(8,16))
        def use(event=None):
            if not apply_filter():return
            selected=table.selection()
            if selected:
                limit=parse_limit(maximum.get());self.vars['max_cloud_cover'].set('' if limit is None else str(limit));self.vars['include_unknown_cloud'].set(unknown.get())
                select([items[int(key)] for key in selected]);window.destroy()
        def select_all():
            if apply_filter():table.selection_set(table.get_children())
        if multiple:self.ttk.Button(buttons,text='Select all visible',command=select_all).pack(side='left')
        self.ttk.Button(buttons,text='Use selected scenes' if multiple else 'Use selected scene',style='Accent.TButton',command=use).pack(side='right');table.bind('<Double-1>',use)
        return window

    def cancel(self):
        if self.process and self.process.poll() is None:self.cancelled=True;self.process.terminate();self.status.set('Cancelling; partial inputs will not be treated as complete.')

    def close(self):
        if self.process:
            from tkinter import messagebox
            if not messagebox.askyesno('Stop the active task?','Closing the window will cancel the current task.',parent=self.root):return
            self.cancel()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
            if self.log_file:self.log_file.close()
        self.root.destroy()


def main(config_path=None):
    import tkinter as tk
    enable_high_dpi()
    root=tk.Tk()
    app=Application(root)
    if config_path:app.load_configuration(config_path)
    root.mainloop()


if __name__=='__main__':main(sys.argv[1] if len(sys.argv)>1 else None)
