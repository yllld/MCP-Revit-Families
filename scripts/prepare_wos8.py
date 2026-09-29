import hashlib,json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
import argparse
parser=argparse.ArgumentParser(description="Prepare local dry-run JSON for the specific WOS-8 drawing 3400401. Does not call Revit.")
parser.add_argument('--drawing',type=Path,required=True,help='Local PDF drawing 3400401 (2013-12-12)')
parser.add_argument('--output',type=Path,default=ROOT/'output/wos8',help='Directory for generated JSON')
parser.add_argument('--accept-source',action='store_true',required=True,help='Explicitly accept this Omega Air drawing as the geometry source for REMEZA WOS-8')
args=parser.parse_args()
source=args.drawing.resolve()
if not source.is_file():parser.error('Drawing file does not exist')
if hashlib.sha256(source.read_bytes()).hexdigest() != "b688e4106732b0ede164f410987f4cb8819d7f3d783b1642e78902f72ee8cffd":
    parser.error('This adapter is calibrated for one exact PDF revision; drawing SHA256 differs. Review the geometry adapter before using another file.')
OUT=args.output.resolve();OUT.mkdir(parents=True,exist_ok=True)

sx=729.9/(459.0-148.68)
sz=677.0/(395.6-107.78)
cx=303.84
spacing=(394.29-213.21)*sx
xc=spacing/2
z=lambda p:round((395.6-p)*sz,3)
source_record={'file':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'page':1,'drawing':'3400401','date':'2013-12-12','accepted_for_REMEZA_by_user':True}
def evidence(method,description):
    return {'method':method,'drawing':'3400401','page':1,'description':description,'estimated_tolerance_mm':0.0 if method=='dimension' else 1.0}
items=[]
def add(name,kind,**kw):
    item={'logical_id':'wos8/3400401/'+name,'kind':kind,'material':'body','source':evidence('scaled_vector','Measured from front/side/plan PDF vectors; scale calibrated against 729.9 and 677 mm dimensions')}
    item.update(kw);items.append(item)
def cylinder(name,center,radius,depth,axis='Z',material='body',inner=0,**kw):
    add(name,'cylinder',center_mm=center,radius_mm=radius,depth_mm=depth,axis=axis,inner_radius_mm=inner,material=material,**kw)
def rounded(name,center,width,height,depth,axis='Z',radius=3,**kw):
    add(name,'rounded',center_mm=center,width_mm=width,height_mm=height,depth_mm=depth,axis=axis,corner_mm=radius,**kw)

for side,x in [('left',-xc),('right',xc)]:
    add(side+'/foot','foot',center_mm=[x,0,0],sections=[[0,151.992,132],[4,152.0,135],[12.7,151.4,137],[83.4,133,122],[88.49,132,132]],arch_width_mm=86,arch_height_mm=68,arch_radius_mm=18)
    cylinder(side+'/body',[x,0,z(357.98)],132,z(141.8)-z(357.98),source=evidence('dimension','Main body diameter explicitly 264 mm; top and bottom elevations measured from vector drawing'),height_parameter='h1',diameter_parameter='d1')
    cylinder(side+'/lower_seam',[x,0,z(360.14)],133.1,z(357.98)-z(360.14),material='cap')
    # Profile in radius/elevation mm, revolved analytically about the cylinder axis.
    profile=[[0,z(141.8)],[142.1,z(141.8)],[141.7,z(122.54)],[140.5,z(121.3)],[136.1,z(120.56)],[116.1,z(120.56)],[110.5,z(119.3)],[74.5,z(110.66)],[62.0,675.0],[49.5,677.0],[0,677.0]]
    add(side+'/lid','revolved',center_mm=[x,0,0],profile_mm=profile,material='cap',recess=side=='left')
    cylinder(side+'/lid_seam',[x,0,z(141.8)+1.2],142.25,1.2,material='seam',inner=141.1)

# Central downcomer and curved lower end, projected width 162.15 mm.
add('central/lower_round','loft',center_mm=[0,0,0],sections=[[0,46.5,32],[5,55,38],[25,68,47],[55,78,53],[88.49,81.08,55]],material='body')
rounded('central/stem',[0,0,88.49],162.16,110,390,'Z',radius=16)
cylinder('central/bottom_link',[-xc,0,45],39,spacing,'X',material='body')
rounded('central/head',[0,0,z(202.1)],268.42,249.8,590-z(202.1),'Z',radius=34,material='body',side_cuts={'centers_x':[-xc,xc],'radius_mm':158.0})
rounded('central/top_seam',[0,0,586.5],268.42,249.8,1.3,'Z',radius=34,material='seam',side_cuts={'centers_x':[-xc,xc],'radius_mm':158.0})
rounded('central/label_frame',[0,-125.05,z(189.5)+(z(155.48)-z(189.5))/2],149.87,80.02,1.0,'-Y',radius=5,material='cap')
rounded('central/label',[0,-126.05,524.72],143.0,73.2,1.0,'-Y',radius=3.5,material='label')
rounded('central/top_filter_frame',[0,-33.5,590.0],76.0,127.0,2.5,'Z',radius=5,material='cap')
rounded('central/top_filter',[0,-33.5,592.5],67.0,117.0,1.0,'Z',radius=3,material='filter')

def nozzle(name,center,axis,length=32.0):
    x,y,zz=center
    def pos(t):
        v={'Z':[0,0,1],'Y':[0,1,0]}[axis]
        return [x+v[0]*t,y+v[1]*t,zz+v[2]*t]
    cylinder(name+'/seat',pos(0),13.0,2.0,axis,'metal',inner=5)
    add(name+'/hex','polygon',center_mm=pos(2),radius_mm=11.5,sides=6,depth_mm=6.0,axis=axis,material='metal',inner_radius_mm=5)
    cylinder(name+'/stem',pos(8),5.8,length-8,axis,'metal',inner=5)
    for j in range(4):
        cylinder(name+'/barb'+str(j),pos(9+j*4.8),6.7,1.35,axis,'metal',inner=5)
for i,x in enumerate([-40.0,40.0]):
    nozzle('inlet'+str(i+1),[x,85.1,590],'Z',32.0)
portx=-xc+70.0
cylinder('outlet/boss',[portx,109,410],25,31,'Y',material='body')
nozzle('outlet',[portx,140,410],'Y',32.75)
cylinder('test/boss',[portx,-110,300],38.0,21,'-Y',material='body')
cylinder('test/flange',[portx,-131,300],27.0,5,'-Y',material='cap',inner=9)
cylinder('test/valve_body',[portx,-136,300],15,27,'-Y',material='metal',inner=5)
add('test/hex','polygon',center_mm=[portx,-155,300],radius_mm=15.0,sides=6,depth_mm=15.95,axis='-Y',material='metal',inner_radius_mm=5)
rounded('test/handle',[portx,-137,318],7,21,13,'-Y',radius=1.5,material='cap')
rounded('test/label',[-xc,-133,300],30,30,1.0,'-Y',radius=0.8,material='label')

# Visible test-kit recess/insert on the second chamber lid.
cylinder('left/testkit_socket',[-xc,0,654],44.9,8,'Z',material='seam',inner=32.0)
cylinder('left/testkit_ring',[-xc,0,658],37.0,4,'Z',material='metal',inner=31.5)
cylinder('left/testkit_insert',[-xc,0,657.5],31.0,3,'Z',material='label')
rounded('left/testkit_pull',[-xc,-62,649.5],36,44,3.0,'Z',radius=16,material='cap')

texts=[{'text':'WOS - 8','position_mm':[0,-127.0,513.0],'axis':'-Y','size_mm':24,'material':'ink'},
       {'text':'TEST','position_mm':[-xc,-134,296],'axis':'-Y','size_mm':7,'material':'ink'},
       {'text':'IN','position_mm':[0,63,591],'axis':'Z','size_mm':10,'material':'ink'},
       {'text':'TEST SET','position_mm':[-xc,0,661.0],'axis':'Z','size_mm':7,'material':'ink'}]
for side,x,label in [('left',-xc,'2'),('right',xc,'1')]:
    texts.append({'text':label,'position_mm':[x,-143,610],'axis':'-Y','size_mm':14,'material':'ink'})
    texts.append({'text':label,'position_mm':[x,-132.8,579],'axis':'-Y','size_mm':14,'material':'ink'})
spec={'profile':'wos8_drawing_3400401','equipment':{'manufacturer':'REMEZA','model':'WOS-8'},'source_documents':[source_record],
      'replace_logical_id':'wos8_overall_envelope','preserve_connectors':True,'preserve_adsk':True,
      'overall_dimensions_mm':[729.9,343.7,677.0],'family_parameters':[{'name':'h1','value':z(141.8)-z(357.98),'type':'Length','unit':'mm','purpose':'Straight body extrusion length from drawing'},{'name':'d1','value':264.0,'type':'Length','unit':'mm','purpose':'Main body diameter from drawing'}],
      'calibration':{'PDF_units':'points','scale_x_mm_per_pt':sx,'scale_z_mm_per_pt':sz,'front_origin_pdf':[cx,395.6],'axes_distance_mm':spacing,'direct_dimensions':[729.9,343.7,677,264,590,410,300],'measured_coordinates_tolerance_mm':1.0},
      'details':items,'texts':texts,'limitations':['Scaled dimensions are estimates of visible contours; hidden passages, shell thickness and manufacturing details are not dimensioned.','FreeForm details are fixed for WOS-8; rebuilding is required after changing dimensions.','Colors are neutral presentation colors, not confirmed manufacturer finishes.']}
(OUT/'WOS8_detailed_spec.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'details':len(items),'texts':len(texts),'axes_distance_mm':spacing,'body_z':[z(357.98),z(141.8)],'calibration':spec['calibration']},ensure_ascii=False))

def write_request(name,request):
    (OUT/name).write_text(json.dumps(request,ensure_ascii=False,indent=2),encoding='utf-8')
params=[]
for name,value in zip(('A','B','C'),spec['overall_dimensions_mm']):
    params.append({'name':name,'type':'Length','purpose':'Overall drawing dimension '+name,
                   'used_for':['geometry'],'value':float(value),'unit':'mm',
                   'source':{'source_type':'direct','file':str(source),'page':1,'field':'Overall dimension '+name,
                             'designation':name,'origin':'user_provided','model_match':True}})
envelope={'mode':'CURRENT_TYPE','equipment':{'model':'WOS-8','manufacturer':'REMEZA'},
          'allow_add_to_existing_geometry':True,'family_parameters':params,
          'geometry':[{'kind':'box','logical_id':'wos8_overall_envelope',
                       'parameters':{'width':'A','depth':'B','height':'C'}}],
          'values':{},'overall_dimensions_mm':spec['overall_dimensions_mm']}
write_request('envelope.dry-run.json',{'dry_run':True,'spec':envelope})
write_request('detail.dry-run.json',{'dry_run':True,'spec':spec})
write_request('finish.dry-run.json',{'dry_run':True,'spec':dict(spec,operation='finish_wos8')})
print('Dry-run requests written to '+str(OUT))
