import sys
sys.path.insert(0, __import__('pathlib').Path(__file__).resolve().parents[1].as_posix())
from routes.report import _validate_chart_spec
from routes.html_report import _render_extended_svg_chart

REGULAR = [
    'pie','bar','line','area','scatter','bubble','doughnut','histogram','boxplot','waterfall','funnel','radar','polar_area',
    'treemap','stacked_bar','grouped_bar','horizontal_bar','stacked_area','step_line','spline','combo','candlestick','ohlc',
    'gauge','bullet','pictogram','pareto','dot_plot','lollipop','violin','density','ridgeline','marimekko','sunburst','venn',
    'funnel_pyramid','population_pyramid','progress','kpi','sparkline','error_bar','control_chart'
]

def chart(t):
    data=[{'label':'A','value':10},{'label':'B','value':20},{'label':'C','value':15},{'label':'D','value':25}]
    axis_types={'bar','line','area','scatter','bubble','histogram','boxplot','waterfall','stacked_bar','grouped_bar','horizontal_bar','stacked_area','step_line','spline','combo','candlestick','ohlc','pareto','dot_plot','lollipop','violin','density','ridgeline','error_bar','control_chart','gantt','timeline','population_pyramid'}
    c={'type':t,'title':t,'series':[{'name':'V','data':data}], 'rationale':'fit','takeaway':'read'}
    if t in axis_types:
        c['xLabel']='Category'
        c['yLabel']='Value'
    if t=='boxplot': c['series']=[{'name':'V','data':[{'label':'A','min':5,'q1':8,'median':10,'q3':12,'max':15} , {'label':'B','min':6,'q1':9,'median':11,'q3':14,'max':17}]}]
    if t=='bullet' or t in {'gauge','progress','kpi'}: c['series']=[{'name':'V','data':[{'label':'K','value':70,'target':80}]}]
    if t=='scatter': c['series']=[{'name':'X','data':data},{'name':'Y','data':[{'label':'A','value':7},{'label':'B','value':13},{'label':'C','value':19},{'label':'D','value':24}]}]
    if t=='bubble': c['series']=[{'name':'X','data':data},{'name':'Y','data':data},{'name':'S','data':data}]
    if t=='radar': c['series']=[{'name':'A','data':data},{'name':'B','data':data}]
    if t=='heatmap': c={'type':'heatmap','title':'heat','rows':['A','B'],'columns':['X','Y'],'values':[[1,2],[3,4]],'rationale':'fit','takeaway':'read'}
    if t in {'candlestick','ohlc'}: c['series']=[{'name':'P','data':[{'label':'A','open':10,'high':12,'low':8,'close':11},{'label':'B','open':11,'high':14,'low':9,'close':13}]}]
    if t in {'waterfall'}: c['series']=[{'name':'V','data':[{'label':'Start','value':100,'isTotal':True},{'label':'Gain','value':20},{'label':'End','value':120,'isTotal':True}]}]
    if t in {'error_bar','control_chart'}: c['series']=[{'name':'V','data':[{'label':'A','value':10,'low':8,'high':12,'lcl':8,'ucl':12},{'label':'B','value':11,'low':9,'high':13,'lcl':8,'ucl':13}]}]
    if t in {'sankey','chord','network','tree','flowchart'}: c['nodes']=['A','B']; c['links']=[{'source':'A','target':'B','value':1}]
    if t in {'gantt','timeline'}: c['events']=[{'label':'A','start':0,'end':1}]
    if t=='venn': c['sets']=[{'label':'A'},{'label':'B'}]
    return c

def test_full_catalog_chart_validator_accepts_supported_shapes():
    # Heatmap and diagram types have their own structural contracts; regular types are checked here.
    for t in REGULAR:
        c=chart(t)
        if t in {'gantt','timeline'}: c['events']=[{'label':'A','start':0,'end':1}]
        if t in {'sankey','chord','network','tree','flowchart'}: c['nodes']=['A','B']; c['links']=[{'source':'A','target':'B','value':1}]
        if t=='venn': c['sets']=[{'label':'A'},{'label':'B'}]
        if t=='population_pyramid': c['series']=[{'name':'P','data':[{'label':'A','left':10,'right':8},{'label':'B','left':12,'right':9}]}]
        assert _validate_chart_spec(c), t

def test_extended_svg_renderers_cover_catalog():
    render_types=['funnel','funnel_pyramid','polar_area','gauge','pictogram','pareto','dot_plot','lollipop','violin','density','ridgeline','gantt','timeline','marimekko','sunburst','sankey','chord','network','tree','flowchart','venn','population_pyramid','progress','kpi','error_bar','control_chart','candlestick','ohlc']
    for t in render_types:
        c=chart(t)
        if t in {'gantt','timeline'}: c['events']=[{'label':'A','start':0,'end':1}]
        if t in {'sankey','chord','network','tree','flowchart'}: c['nodes']=['A','B']; c['links']=[{'source':'A','target':'B','value':1}]
        if t=='venn': c['sets']=[{'label':'A'},{'label':'B'}]; c['overlap']=1
        if t=='population_pyramid': c['series']=[{'name':'P','data':[{'label':'A','left':10,'right':8},{'label':'B','left':12,'right':9}]}]
        assert _render_extended_svg_chart(c,1,{})
