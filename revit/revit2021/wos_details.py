# -*- coding: utf-8 -*-
"""Bounded WOS-8 drawing reconstruction. No arbitrary code, connector or shared edits."""
from __future__ import division
import copy,io,json,math,os,traceback,uuid
from System import Double
from System.Collections.Generic import List
from pyrevit import DB
from revit.revit2021 import active_family as audit
from revit.revit2021 import active_operations as ops
from revit.revit2021 import geometry_service as geo
from revit.revit2021.unit_converter import UnitConverter as Units

PREFIX='wos8/3400401/'
def mm(v): return Units.mm_to_internal(float(v))
def point(v): return DB.XYZ(*[mm(x) for x in v])
def axes(axis):
    return {'Z':(DB.XYZ.BasisX,DB.XYZ.BasisY),'X':(DB.XYZ.BasisY,DB.XYZ.BasisZ),'Y':(-DB.XYZ.BasisX,DB.XYZ.BasisZ),'-Y':(DB.XYZ.BasisX,DB.XYZ.BasisZ)}[axis]
def circular(c,u,v,r):
    loop=DB.CurveLoop()
    for a,b in [(0,math.pi),(math.pi,2*math.pi)]: loop.Append(DB.Arc.Create(c,mm(r),a,b,u,v))
    return loop
def ellipse(c,rx,ry):
    loop=DB.CurveLoop()
    for a,b in [(0,math.pi),(math.pi,2*math.pi)]:
        loop.Append(DB.Ellipse.CreateCurve(c,mm(rx),mm(ry),DB.XYZ.BasisX,DB.XYZ.BasisY,a,b))
    return loop
def poly(c,u,v,coords):
    pts=[c+u*mm(x)+v*mm(y) for x,y in coords]
    loop=DB.CurveLoop()
    for a,b in zip(pts,pts[1:]+pts[:1]):
        if a.DistanceTo(b)>1e-7: loop.Append(DB.Line.CreateBound(a,b))
    return loop
def rounded(c,u,v,w,h,r):
    w,h,r=mm(w/2),mm(h/2),mm(r)
    loop=DB.CurveLoop()
    # Counterclockwise straight sides and true quarter-circle corners.
    corners=[(w-r,h-r,0,math.pi/2),(-w+r,h-r,math.pi/2,math.pi),(-w+r,-h+r,math.pi,3*math.pi/2),(w-r,-h+r,3*math.pi/2,2*math.pi)]
    arcs=[DB.Arc.Create(c+u*x+v*y,r,a,b,u,v) for x,y,a,b in corners]
    for i,arc in enumerate(arcs):
        loop.Append(arc)
        a,b=arc.GetEndPoint(1),arcs[(i+1)%4].GetEndPoint(0)
        if a.DistanceTo(b)>1e-7: loop.Append(DB.Line.CreateBound(a,b))
    return loop
def loops_array(loops):
    result=DB.CurveArrArray()
    for loop in loops:
        curves=DB.CurveArray()
        for curve in loop: curves.Append(curve)
        result.Append(curves)
    return result
def extruded(loops,axis,depth):
    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(List[DB.CurveLoop](loops),axis,mm(depth))
def subtract(a,b): return DB.BooleanOperationsUtils.ExecuteBooleanOperation(a,b,DB.BooleanOperationsType.Difference)
def revolve(item):
    c=point(item['center_mm']);u=DB.XYZ.BasisX;v=DB.XYZ.BasisZ
    loop=poly(c,u,v,item['profile_mm'])
    frame=DB.Frame(c,DB.XYZ.BasisX,DB.XYZ.BasisY,DB.XYZ.BasisZ)
    solid=DB.GeometryCreationUtilities.CreateRevolvedGeometry(frame,List[DB.CurveLoop]([loop]),0,2*math.pi)
    if item.get('recess'):
        cut=extruded([circular(c+DB.XYZ.BasisZ*mm(650),u,DB.XYZ.BasisY,32)],DB.XYZ.BasisZ,35)
        solid=subtract(solid,cut)
    return solid
def loft(item):
    c=point(item['center_mm'])
    loops=[ellipse(c+DB.XYZ.BasisZ*mm(z),rx,ry) for z,rx,ry in item['sections']]
    return DB.GeometryCreationUtilities.CreateLoftGeometry(List[DB.CurveLoop](loops),DB.SolidOptions(DB.ElementId.InvalidElementId,DB.ElementId.InvalidElementId))
def foot(item):
    solid=loft(item);c=point(item['center_mm'])
    # Open underside and arched front/back passage. The upper bridge keeps one solid.
    hollow=extruded([ellipse(c-DB.XYZ.BasisZ*mm(1),119,103)],DB.XYZ.BasisZ,79)
    solid=subtract(solid,hollow)
    w,h,r=item['arch_width_mm'],item['arch_height_mm'],item['arch_radius_mm']
    u,v=DB.XYZ.BasisX,DB.XYZ.BasisZ
    cc=c-DB.XYZ.BasisY*mm(210)
    pts=[[-w/2,-2],[w/2,-2],[w/2,h-r]]
    loop=DB.CurveLoop()
    def line(a,b): loop.Append(DB.Line.CreateBound(cc+u*mm(a[0])+v*mm(a[1]),cc+u*mm(b[0])+v*mm(b[1])))
    line(pts[0],pts[1]);line(pts[1],pts[2])
    arc1=DB.Arc.Create(cc+u*mm(w/2-r)+v*mm(h-r),mm(r),0,math.pi/2,u,v)
    loop.Append(arc1);line([w/2-r,h],[-w/2+r,h])
    loop.Append(DB.Arc.Create(cc+u*mm(-w/2+r)+v*mm(h-r),mm(r),math.pi/2,math.pi,u,v))
    line([-w/2,h-r],pts[0])
    return subtract(solid,extruded([loop],DB.XYZ.BasisY,420))
def create_form(doc,item):
    kind=item['kind'];aux=[]
    if kind in ('foot','loft','revolved'):
        solid={'foot':foot,'loft':loft,'revolved':revolve}[kind](item)
        return DB.FreeFormElement.Create(doc,solid),aux
    c=point(item['center_mm']);u,v=axes(item['axis']);normal=u.CrossProduct(v)
    if kind=='cylinder': loops=[circular(c,u,v,item['radius_mm'])]
    elif kind=='rounded': loops=[rounded(c,u,v,item['width_mm'],item['height_mm'],item['corner_mm'])]
    else:
        r,n=item['radius_mm'],item['sides']
        loops=[poly(c,u,v,[(r*math.cos(2*math.pi*i/n),r*math.sin(2*math.pi*i/n)) for i in range(n)])]
    if item.get('inner_radius_mm',0)>0: loops.append(circular(c,u,v,item['inner_radius_mm']))
    if item.get('side_cuts'):
        solid=extruded(loops,normal,item['depth_mm'])
        for x in item['side_cuts']['centers_x']:
            cut=extruded([circular(point([x,0,item['center_mm'][2]-1]),DB.XYZ.BasisX,DB.XYZ.BasisY,item['side_cuts']['radius_mm'])],DB.XYZ.BasisZ,item['depth_mm']+2)
            solid=subtract(solid,cut)
        return DB.FreeFormElement.Create(doc,solid),aux
    plane=DB.SketchPlane.Create(doc,DB.Plane.CreateByNormalAndOrigin(normal,c));aux.append(plane)
    form=doc.FamilyCreate.NewExtrusion(True,loops_array(loops),plane,mm(item['depth_mm']))
    if item.get('height_parameter'):
        doc.FamilyManager.AssociateElementParameterToFamilyParameter(form.get_Parameter(DB.BuiltInParameter.EXTRUSION_END_PARAM),ops.find_parameter(doc,item['height_parameter']))
    return form,aux

def plan(before,spec,generated):
    errors=[]
    if spec.get('profile')!='wos8_drawing_3400401': errors.append('WOS8 profile required')
    if spec.get('preserve_connectors') is not True or spec.get('preserve_adsk') is not True: errors.append('Preservation flags required')
    target=spec.get('replace_logical_id')
    if target!='wos8_overall_envelope': errors.append('Only the original generated WOS8 envelope may be replaced')
    marked=[g for g in generated if g['logical_id']==target or g['logical_id'].startswith(target+'/support/')]
    if not any(g['logical_id']==target for g in marked): errors.append('Original tagged WOS8 envelope missing')
    ids=[g['logical_id'] for g in spec.get('details',[])]
    if not 1<=len(ids)<=150 or len(ids)!=len(set(ids)) or any(not n.startswith(PREFIX) for n in ids): errors.append('Bounded unique WOS8 detail IDs required')
    if any(g['logical_id'].startswith(PREFIX) for g in generated): errors.append('WOS8 details already exist; automatic duplication is disabled')
    if len(spec.get('source_documents',[]))!=1: errors.append('Exactly one hashed source drawing required')
    for source in spec.get('source_documents',[]):
        if source.get('drawing')!='3400401' or not source.get('accepted_for_REMEZA_by_user'): errors.append('Accepted source drawing required')
        if ops.hash_file(source['file'])!=source.get('sha256'): errors.append('Drawing hash mismatch')
    for item in spec.get('details',[]):
        if item.get('kind') not in ('cylinder','rounded','polygon','revolved','loft','foot'): errors.append('Unsupported primitive')
        if item.get('material') not in ('body','cap','seam','metal','label','filter','ink'): errors.append('Invalid material key')
        if len(item.get('center_mm',[]))!=3 or any(abs(float(v))>2000 for v in item['center_mm']): errors.append('Out of bounds center')
        if not item.get('source'): errors.append('Source evidence required for every body')
    for name in ('A','B','C'):
        p=next((p for p in before['parameters'] if p['name']==name),None)
        if not p or p['is_shared'] or p['is_instance'] or p.get('formula') or p['parameter_type']!='Length': errors.append('Unexpected overall parameter '+name)
    return {'success':True,'ready':not errors,'errors':errors,'warnings':list(spec.get('limitations',[])),'context_token':before['context_token'],'replacement_elements':marked,'detail_count':len(ids),'source_documents':spec.get('source_documents',[]),'calibration':spec.get('calibration'),'overall_dimensions_mm':spec.get('overall_dimensions_mm'),'family_parameters':spec.get('family_parameters',[]),'dry_run':True}

def presentation_view(doc,name,forward,up):
    typ=next(v for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType) if v.ViewFamily==DB.ViewFamily.ThreeDimensional)
    view=DB.View3D.CreateIsometric(doc,typ.Id);view.Name=name
    view.DetailLevel=DB.ViewDetailLevel.Fine;view.Scale=5
    view.DisplayStyle=DB.DisplayStyle.ShadingWithEdges
    view.CropBoxActive=False;view.CropBoxVisible=False;view.IsSectionBoxActive=False
    f=DB.XYZ(*forward).Normalize();u=DB.XYZ(*up).Normalize()
    view.SetOrientation(DB.ViewOrientation3D(point([0,0,340])-f*8,u,f))
    # Thin model projection lines.
    override=DB.OverrideGraphicSettings();override.SetProjectionLineWeight(1)
    view.SetCategoryOverrides(doc.OwnerFamily.FamilyCategory.Id,override)
    return view

def dispatch(uiapp,data,root):
    if data.get('spec',{}).get('operation')=='finish_wos8':
        return finish_wos8(uiapp,data,root)
    before=audit.preflight(uiapp);doc=audit.active_document(uiapp);spec=data.get('spec',{})
    result=plan(before,spec,geo.generated(doc));result.update(saved=False,model_committed=False,created=[],warnings=result['warnings'],parameter_bindings=[])
    if data.get('dry_run',True): return result
    audit.require(data.get('context_token')==before['context_token'],'Stale WOS8 context')
    if not result['ready']: result['success']=False;return result
    run=str(uuid.uuid4());out=os.path.join(root,'tests','output',run);backup=os.path.join(root,'backup',run)
    os.makedirs(out);os.makedirs(backup)
    result.update(run_id=run,dry_run=False,backup_path=os.path.join(backup,'WOS8_before_drawing_details.rfa'),output_path=os.path.join(out,'REMEZA_WOS-8_Detailed_3400401.rfa'))
    original_path=doc.PathName;original_hash=ops.hash_file(original_path);group=None
    try:
        result['stage']='backup';ops.save_as(doc,result['backup_path']);result['backup_saved']=True
        group=DB.TransactionGroup(doc,'WOS8 drawing 3400401');group.Start()
        allowed=set();removed_ids=set();removed_unique=set()
        def replace():
            requested=set(g['element_id'] for g in result['replacement_elements'])
            for g in result['replacement_elements']:
                el=doc.GetElement(DB.ElementId(g['element_id']))
                if el is not None: removed_unique.add(el.UniqueId)
            # Only tagged generated envelope/support elements. Dependent generated constraints are verified.
            for element_id in sorted(requested):
                if doc.GetElement(DB.ElementId(element_id)) is None: continue
                deleted=doc.Delete(DB.ElementId(element_id))
                removed_ids.update(e.IntegerValue for e in deleted)
            protected=set(p['element_id'] for p in before['reference_planes'] if p['unique_id'] not in removed_unique)
            audit.require(not protected.intersection(removed_ids),'Deletion affected an original reference plane')
            manager=doc.FamilyManager
            for definition in spec['family_parameters']:
                matches=[p for p in manager.Parameters if p.Definition.Name==definition['name']]
                audit.require(not matches,'New detail parameter already exists')
                manager.AddParameter(definition['name'],DB.BuiltInParameterGroup.PG_GEOMETRY,DB.ParameterType.Length,False)
            values=dict(zip(('A','B','C'),spec['overall_dimensions_mm']))
            values.update({p['name']:p['value'] for p in spec['family_parameters']})
            for typ in manager.Types:
                manager.CurrentType=typ
                for name,value in values.items():
                    manager.Set(ops.find_parameter(doc,name),Double(mm(value)));allowed.add((typ.Name,name))
            manager.CurrentType=next(t for t in manager.Types if t.Name==before['current_type'])
            doc.Regenerate()
        result['stage']='replace_envelope';ops.transaction(doc,'Replace generated envelope only',replace,result)
        materials={}
        def build():
            for key,color in {'body':(168,172,174),'cap':(62,65,67),'seam':(30,31,32),'metal':(174,178,183),'label':(222,222,216),'filter':(73,73,73),'ink':(15,15,15)}.items():
                name='WOS8 visual '+key;existing=[m for m in audit.collect(doc,DB.Material) if m.Name==name]
                material=existing[0] if existing else doc.GetElement(DB.Material.Create(doc,name))
                if not existing: material.Color=DB.Color(*color)
                materials[key]=material.Id
            for item in spec['details']:
                result['current_item']=item['logical_id']
                element,aux=create_form(doc,item)
                mat=element.get_Parameter(DB.BuiltInParameter.MATERIAL_ID_PARAM)
                audit.require(mat is not None and not mat.IsReadOnly,'Material unavailable');mat.Set(materials[item['material']])
                geo.tag(element,item['logical_id'],run)
                for i,el in enumerate(aux): geo.tag(el,item['logical_id']+'/support/'+str(i),run)
                result['created'].append({'element_id':element.Id.IntegerValue,'logical_id':item['logical_id'],'kind':str(element.GetType().Name),'source':item['source']})
                if item.get('height_parameter'):result['parameter_bindings'].append({'parameter':item['height_parameter'],'element_id':element.Id.IntegerValue,'binding':'Extrusion end'})
            doc.Regenerate()
        result['stage']='geometry';ops.transaction(doc,'Build WOS8 drawing geometry',build,result)
        def label_diameters():
            view=doc.GetElement(DB.ElementId(before['coordinate_system']['plan_view_id']))
            for item in spec['details']:
                if not item.get('diameter_parameter'):continue
                record=next(e for e in result['created'] if e['logical_id']==item['logical_id'])
                extrusion=doc.GetElement(DB.ElementId(record['element_id']))
                sketch=extrusion.Sketch
                curve=next(c for arr in sketch.Profile for c in arr if isinstance(c,DB.Arc))
                ref=curve.Reference
                audit.require(ref is not None,'Sketch circle has no reference')
                dimension=doc.FamilyCreate.NewDiameterDimension(view,ref,point([item['center_mm'][0],-180,item['center_mm'][2]]))
                dimension.FamilyLabel=ops.find_parameter(doc,'d1');geo.tag(dimension,item['logical_id']+'/diameter',run)
                result['parameter_bindings'].append({'parameter':'d1','element_id':record['element_id'],'binding':'Diameter dimension'})
        try:ops.transaction(doc,'Bind WOS8 body diameter',label_diameters,result)
        except Exception as e:result['warnings'].append('d1 diameter binding unavailable: '+str(e)+'; detailed forms remain at measured dimensions')
        def text_labels():
            types=list(DB.FilteredElementCollector(doc).OfClass(DB.ModelTextType))
            audit.require(bool(types),'No ModelTextType in base family')
            cache={}
            for i,item in enumerate(spec.get('texts',[])):
                size=item['size_mm']
                if size not in cache:
                    typ=types[0].Duplicate('WOS8 text '+str(size)+' '+run[:6]);typ.get_Parameter(DB.BuiltInParameter.MODEL_TEXT_SIZE).Set(Double(mm(size)));cache[size]=typ
                c=point(item['position_mm']);u,v=axes(item['axis'])
                plane=DB.SketchPlane.Create(doc,DB.Plane.CreateByOriginAndBasis(c,u,v))
                text=doc.FamilyCreate.NewModelText(item['text'],cache[size],plane,c,DB.HorizontalAlign.Center,mm(.25))
                p=text.get_Parameter(DB.BuiltInParameter.MATERIAL_ID_PARAM)
                if p and not p.IsReadOnly:p.Set(materials[item['material']])
                geo.tag(text,PREFIX+'text/'+str(i),run);geo.tag(plane,PREFIX+'text/'+str(i)+'/plane',run)
            doc.Regenerate()
        result['stage']='labels'
        try:ops.transaction(doc,'WOS8 visible labels',text_labels,result)
        except Exception as e:result['warnings'].append('Model text not created: '+str(e))
        result['stage']='validation'
        retained=copy.deepcopy(before)
        retained['existing_geometry']['elements']=[e for e in retained['existing_geometry']['elements'] if e['unique_id'] not in removed_unique]
        retained['reference_planes']=[p for p in retained['reference_planes'] if p['unique_id'] not in removed_unique]
        result['preservation']=ops.preserved(doc,retained,allowed)
        geometry=[]
        for record in result['created']:
            el=doc.GetElement(DB.ElementId(record['element_id']));box=audit.box_data(el)
            audit.require(box is not None,'Missing bounds: '+record['logical_id'])
            count=audit.solid_count(el.get_Geometry(DB.Options()));audit.require(count>=1,'Missing solid: '+record['logical_id'])
            geometry.append(dict(record,bbox=box,solids=count))
        lo=[min(e['bbox']['min_mm'][j] for e in geometry) for j in range(3)];hi=[max(e['bbox']['max_mm'][j] for e in geometry) for j in range(3)]
        actual=[hi[j]-lo[j] for j in range(3)]
        result['assembly_validation']={'min_mm':lo,'max_mm':hi,'size_mm':actual,'expected_mm':spec['overall_dimensions_mm'],'elements':len(geometry),'solids':sum(e['solids'] for e in geometry)}
        audit.require(max(abs(a-b) for a,b in zip(actual,spec['overall_dimensions_mm']))<2.0,'Assembly differs from measured envelope by over 2 mm')
        audit.require(abs(lo[2])<.01 and abs(hi[2]-677)<.01,'Base/top heights differ from dimensioned drawing')
        result['geometry_validation']=geometry
        result['parameter_values']={p['name']:p['current_value'] for p in audit.parameters(doc) if p['name'] in ('A','B','C','d1','h1')}
        views=[]
        def make_views():
            for name,f,u in [('3D',[-1,1,-.65],[0,0,1]),('Front',[0,1,0],[0,0,1]),('Side',[-1,0,0],[0,0,1]),('Top',[0,0,-1],[0,1,0])]:
                fvec=DB.XYZ(*f).Normalize();right=fvec.CrossProduct(DB.XYZ(*u)).Normalize();up=right.CrossProduct(fvec).Normalize()
                view=presentation_view(doc,'WOS8 '+name+' '+run[:6],f,[up.X,up.Y,up.Z]);views.append(view)
        result['stage']='views';ops.transaction(doc,'Create WOS8 inspection views',make_views,result)
        audit.require(group.Assimilate()==DB.TransactionStatus.Committed,'Group did not commit');group.Dispose();group=None
        result['model_committed']=True
        uiapp.ActiveUIDocument.ActiveView=views[0]
        for view in uiapp.ActiveUIDocument.GetOpenUIViews():
            if view.ViewId==views[0].Id:view.ZoomToFit()
        result['stage']='save';ops.save_as(doc,result['output_path']);result['saved']=True
        for view in views:
            options=DB.ImageExportOptions()
            try:
                options.FilePath=os.path.join(out,view.Name.replace(' ','_'))
                options.ExportRange=DB.ExportRange.SetOfViews;options.SetViewsAndSheets(List[DB.ElementId]([view.Id]))
                options.ZoomType=DB.ZoomFitType.FitToPage;options.PixelSize=2000
                options.HLRandWFViewsFileType=DB.ImageFileType.PNG;options.ShadowViewsFileType=DB.ImageFileType.PNG
                doc.ExportImage(options)
            finally:options.Dispose()
        result['preview_paths']=[os.path.join(out,f) for f in os.listdir(out) if f.endswith('.png')]
        result['original_file_unchanged']=ops.hash_file(original_path)==original_hash
        audit.require(result['original_file_unchanged'],'Original file changed')
        result['success']=True;result['stage']='complete'
    except Exception as error:
        result.update(success=False,error=unicode(error),traceback=traceback.format_exc());result['errors'].append(unicode(error))
    finally:
        if group is not None:
            if group.GetStatus()==DB.TransactionStatus.Started:result['rolled_back']=group.RollBack()==DB.TransactionStatus.RolledBack
            group.Dispose()
        result['active_rfa_path_after']=doc.PathName
        with io.open(os.path.join(out,'wos8-detail-result.json'),'w',encoding='utf-8') as stream:stream.write(unicode(json.dumps(result,ensure_ascii=False,indent=2)))
    return result

def finish_wos8(uiapp,data,root):
    doc=audit.active_document(uiapp);before=audit.preflight(uiapp);spec=data['spec']
    tagged=geo.generated(doc);records={g['logical_id']:g for g in tagged}
    audit.require(all(s['logical_id'] in records for s in spec['details']),'Complete WOS8 geometry required')
    audit.require(spec.get('preserve_connectors') and spec.get('preserve_adsk'),'Preservation required')
    source=spec['source_documents'][0]
    audit.require(ops.hash_file(source['file'])==source['sha256'],'Source changed')
    result={'success':True,'ready':True,'dry_run':data.get('dry_run',True),'context_token':before['context_token'],'errors':[],'warnings':[],'model_committed':False,'saved':False}
    if result['dry_run']:
        result['texts']=[]
        for el in audit.collect(doc,DB.ModelText):
            opts=DB.Options();points=[]
            def walk(geometry,transform):
                for obj in geometry:
                    if isinstance(obj,DB.GeometryInstance):walk(obj.GetSymbolGeometry(),transform.Multiply(obj.Transform))
                    elif isinstance(obj,DB.Solid):
                        for face in obj.Faces:
                            for p in face.Triangulate().Vertices:points.append(transform.OfPoint(p))
            walk(el.get_Geometry(opts),DB.Transform.Identity)
            result['texts'].append({'id':el.Id.IntegerValue,'bbox':audit.box_data(el),'points_bounds':([audit.xyz(DB.XYZ(min(p.X for p in points),min(p.Y for p in points),min(p.Z for p in points)),True),audit.xyz(DB.XYZ(max(p.X for p in points),max(p.Y for p in points),max(p.Z for p in points)),True)] if points else None),'parameters':[(p.Definition.Name,p.AsValueString()) for p in el.Parameters]})
        return result
    audit.require(data.get('context_token')==before['context_token'],'Stale WOS8 context')
    run=str(uuid.uuid4());out=os.path.join(root,'tests','output',run);os.makedirs(out)
    result.update(output_path=os.path.join(out,'REMEZA_WOS-8_Detailed_3400401.rfa'),backup_path=os.path.join(out,'before_finish.rfa'))
    original=doc.PathName;original_hash=ops.hash_file(original);group=None
    try:
        ops.save_as(doc,result['backup_path'])
        group=DB.TransactionGroup(doc,'WOS8 drawing visual corrections');group.Start()
        changed=set()
        def repair():
            materials={m.Name:m.Id for m in audit.collect(doc,DB.Material)}
            # Replace only our generated model text. In Revit 2021 position is in sketch coordinates.
            for g in tagged:
                if g['logical_id'].startswith(PREFIX+'text/'):
                    changed.add(g['unique_id']);el=doc.GetElement(DB.ElementId(g['element_id']))
                    if el is not None:doc.Delete(el.Id)
            types=list(DB.FilteredElementCollector(doc).OfClass(DB.ModelTextType));cache={}
            for i,item in enumerate(spec['texts']):
                size=item['size_mm']
                if size not in cache:
                    typ=types[0].Duplicate('WOS8 label '+str(size)+' '+run[:6])
                    typ.get_Parameter(DB.BuiltInParameter.MODEL_TEXT_SIZE).Set(Double(mm(size)));cache[size]=typ
                c=point(item['position_mm']);u,v=axes(item['axis'])
                normal=u.CrossProduct(v)
                plane_origin=c-normal*c.DotProduct(normal)
                plane=DB.SketchPlane.Create(doc,DB.Plane.CreateByOriginAndBasis(plane_origin,u,v))
                placement=normal*(c.DotProduct(normal)+mm(.5))
                text=doc.FamilyCreate.NewModelText(item['text'],cache[size],plane,placement,DB.HorizontalAlign.Center,mm(1.0))
                text.get_Parameter(DB.BuiltInParameter.MATERIAL_ID_PARAM).Set(materials['WOS8 visual '+item['material']])
                doc.Regenerate()
                box=text.get_BoundingBox(None)
                DB.ElementTransformUtils.MoveElement(doc,text.Id,DB.XYZ(c.X-(box.Min.X+box.Max.X)/2,0,0))
                geo.tag(text,PREFIX+'text/'+str(i),run);geo.tag(plane,PREFIX+'text/'+str(i)+'/plane',run)
            # Refine the visible test-kit recess with an elliptical scoop measured from plan/front.
            liditem=next(s for s in spec['details'] if s['logical_id']==PREFIX+'left/lid')
            lid=doc.GetElement(DB.ElementId(records[liditem['logical_id']]['element_id']))
            solid=revolve(liditem)
            scoop={'center_mm':[liditem['center_mm'][0],-24,0],
                   'sections':[[646,8,24],[650,23,49],[657,37,74],[669,48,88],[686,55,97],[710,60,102]]}
            solid=subtract(solid,loft(scoop));lid.UpdateSolidGeometry(solid)
            changed.add(lid.UniqueId)
            # Preserve the measured 268.42 mm front width while following concave plan sides.
            for suffix,zbase,depth in [('head',455.144,134.856),('top_seam',586.5,1.3)]:
                head=doc.GetElement(DB.ElementId(records[PREFIX+'central/'+suffix]['element_id']))
                loop=DB.CurveLoop()
                def hp(x,y):return point([x,y,zbase])
                loop.Append(DB.Line.CreateBound(hp(-100.21,-124.9),hp(100.21,-124.9)))
                loop.Append(DB.Arc.Create(hp(100.21,-124.9),hp(134.21,-105),hp(122.0,-120.0)))
                loop.Append(DB.Arc.Create(hp(134.21,-105),hp(134.21,105),hp(55.0,0)))
                loop.Append(DB.Arc.Create(hp(134.21,105),hp(100.21,124.9),hp(122.0,120.0)))
                loop.Append(DB.Line.CreateBound(hp(100.21,124.9),hp(-100.21,124.9)))
                loop.Append(DB.Arc.Create(hp(-100.21,124.9),hp(-134.21,105),hp(-122.0,120.0)))
                loop.Append(DB.Arc.Create(hp(-134.21,105),hp(-134.21,-105),hp(-55.0,0)))
                loop.Append(DB.Arc.Create(hp(-134.21,-105),hp(-100.21,-124.9),hp(-122.0,-120.0)))
                head.UpdateSolidGeometry(extruded([loop],DB.XYZ.BasisZ,depth));changed.add(head.UniqueId)
            # The connecting pipe terminates inside the supports, clear of the front arch openings.
            linkitem=copy.deepcopy(next(s for s in spec['details'] if s['logical_id']==PREFIX+'central/bottom_link'))
            old=doc.GetElement(DB.ElementId(records[linkitem['logical_id']]['element_id']));changed.add(old.UniqueId);doc.Delete(old.Id)
            linkitem['center_mm'][0]+=95;linkitem['depth_mm']-=190
            link,aux=create_form(doc,linkitem);link.get_Parameter(DB.BuiltInParameter.MATERIAL_ID_PARAM).Set(materials['WOS8 visual body'])
            geo.tag(link,linkitem['logical_id'],run)
            for j,el in enumerate(aux):geo.tag(el,linkitem['logical_id']+'/finish_support/'+str(j),run)
            doc.Regenerate()
        ops.transaction(doc,'Correct WOS8 labels and visible contours',repair,result)
        retained=copy.deepcopy(before)
        retained['existing_geometry']['elements']=[e for e in retained['existing_geometry']['elements'] if e['unique_id'] not in changed]
        result['preservation']=ops.preserved(doc,retained,set())
        current=geo.generated(doc);forms=[];labels=[]
        for g in current:
            el=doc.GetElement(DB.ElementId(g['element_id']))
            if isinstance(el,(DB.GenericForm,DB.ModelText)):
                box=audit.box_data(el)
                if box:
                    rec=dict(g,bbox=box)
                    (labels if isinstance(el,DB.ModelText) else forms).append(rec)
        result['label_validation']=labels
        for label in labels:
            box=label['bbox'];audit.require(box['min_mm'][2]>=0 and box['max_mm'][2]<=677,'Label outside equipment height')
            audit.require(max(abs(box[k][0]) for k in ('min_mm','max_mm'))<365,'Label outside equipment width')
        lo=[min(e['bbox']['min_mm'][j] for e in forms+labels) for j in range(3)];hi=[max(e['bbox']['max_mm'][j] for e in forms+labels) for j in range(3)]
        result['assembly_validation']={'min_mm':lo,'max_mm':hi,'size_mm':[hi[j]-lo[j] for j in range(3)],'forms':len(forms),'labels':len(labels)}
        audit.require(max(abs(a-b) for a,b in zip(result['assembly_validation']['size_mm'],spec['overall_dimensions_mm']))<2,'Overall dimensions changed')
        audit.require(group.Assimilate()==DB.TransactionStatus.Committed,'Group not committed');group.Dispose();group=None;result['model_committed']=True
        ops.save_as(doc,result['output_path']);result['saved']=True
        views=[v for v in audit.collect(doc,DB.View3D) if v.Name.startswith('WOS8 ')]
        for view in views:
            options=DB.ImageExportOptions()
            try:
                options.FilePath=os.path.join(out,view.Name.replace(' ','_'))
                options.ExportRange=DB.ExportRange.SetOfViews;options.SetViewsAndSheets(List[DB.ElementId]([view.Id]))
                options.ZoomType=DB.ZoomFitType.FitToPage;options.PixelSize=2000
                options.HLRandWFViewsFileType=DB.ImageFileType.PNG;options.ShadowViewsFileType=DB.ImageFileType.PNG
                doc.ExportImage(options)
            finally:options.Dispose()
        for view in uiapp.ActiveUIDocument.GetOpenUIViews():
            if view.ViewId==uiapp.ActiveUIDocument.ActiveView.Id:view.ZoomToFit()
        result['preview_paths']=[os.path.join(out,f) for f in os.listdir(out) if f.endswith('.png')]
        result['original_file_unchanged']=ops.hash_file(original)==original_hash
        audit.require(result['original_file_unchanged'],'Original changed')
    except Exception as e:
        result.update(success=False,traceback=traceback.format_exc());result['errors'].append(unicode(e))
    finally:
        if group is not None:
            if group.GetStatus()==DB.TransactionStatus.Started:result['rolled_back']=group.RollBack()==DB.TransactionStatus.RolledBack
            group.Dispose()
        with io.open(os.path.join(out,'wos8-finish-result.json'),'w',encoding='utf-8') as stream:stream.write(unicode(json.dumps(result,ensure_ascii=False,indent=2)))
    return result
