const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

async function browser() {
  const nodes = new Map();
  function element(tagName='div') {
    return {
      tagName:tagName.toUpperCase(), textContent:'', value:'', hidden:false,
      disabled:false, open:false, className:'', style:{}, children:[], handlers:{},
      firstChild:{textContent:''}, classList:{toggle(){}},
      setAttribute(name,value){this[name]=value;},
      addEventListener(name,fn){this.handlers[name]=fn;},
      append(...children){this.children.push(...children);},
      replaceChildren(...children){this.children=children;},
      click(){return this.handlers.click?.();},
      focus(){}, scrollIntoView(){}, showModal(){}, close(){},
      cloneNode(){const copy=element(tagName);copy.textContent=this.textContent;return copy;},
    };
  }
  const get=id=>{
    if(!nodes.has(id)) nodes.set(id,element());
    return nodes.get(id);
  };
  const context=vm.createContext({
    document:{getElementById:get,createElement:element,querySelector:()=>element(),querySelectorAll:()=>[],body:element()},
    window:{confirm:()=>true,scrollTo(){},print(){}},
    Map, console, Date, Number, Object, String, Math, JSON, Error, URL,
    encodeURIComponent, Option:function(name,value){this.value=value;this.textContent=name;},
    fetch:async()=>({ok:true,status:200,json:async()=>({token:'test',authenticated:false,regions:[]})}),
    setTimeout:()=>0,setInterval:()=>0,clearInterval(){},clearTimeout(){},Blob:function(){},
  });
  for (const name of ['app', 'habitat', 'deep-dive', 'report', 'species']) {
    vm.runInContext(fs.readFileSync(`wildlocate/web/${name}.js`, 'utf8'), context);
  }
  await new Promise(resolve=>setImmediate(resolve));
  return {context,get,run:code=>vm.runInContext(code,context)};
}

const textOf=node=>[node.textContent,...node.children.map(textOf)].join(' ');
const descendants=(node,tag)=>[...(node.tagName===tag?[node]:[]),...node.children.flatMap(child=>descendants(child,tag))];
const road={
  id:'way/123',rank:1,kind:'road',name:'College Street',latitude:42.37,longitude:-72.5,
  geometry:{type:'LineString',coordinates:[[-72.51,42.37],[-72.5,42.37]]},
  source:'OpenStreetMap',source_url:'https://www.openstreetmap.org/way/123',
  tags:{highway:'secondary'},length_m:830,score:.4,percentile:38,category:'Low',
  size_scope:'within_inspected_area',
  signals:[{feature:'distance_to_road_m',current:12,reference:530,effect:-.12}],
  strongest_signal_points:12,nearby_habitat:{distance_m:740,percentile:88},
  field_checks:['Check for safe crossing points and existing culverts'],
};
const places={
  status:'available',summary:'Mapped places with relevant limiting conditions',
  method:'Each comparison uses the model’s typical condition',
  limitations:'Model comparisons do not establish damage caused by an individual structure',
  sources:[{name:'OpenStreetMap',url:'https://www.openstreetmap.org/copyright'}],
  retrieved_at:'2026-10-10T12:00:00+00:00',
  searched_areas:[{latitude:42.37,longitude:-72.5,radius_m:1000}],
  mapped_features:45,assessed_features:9,failed_evaluations:0,sites:[road],warnings:[],
};
function setReport(b,value) {
  b.context.report={species:'Bobcat',latitude:42.37,longitude:-72.5,radius_km:10,model:'Test',overview:{evaluated_points:0},...value};
  b.run('renderDeepDive(report)');
}

test('Deep Dive map initializes and previews at a non-Amherst location',async()=>{
  const b=await browser();
  const centers=[], circles=[], fitted=[];
  const view={setView(center){centers.push(center);return this;},invalidateSize(){return this;},
    fitBounds(bounds){fitted.push(bounds);return this;}};
  const layer={addTo(){return this;},clearLayers(){return this;}};
  b.context.L={
    map:()=>view,layerGroup:()=>layer,
    tileLayer:()=>({on(){return this;},addTo(){return this;}}),
    circle:(center,opts)=>{circles.push({center,opts});return {
      addTo(){return this;},getBounds(){return {center};},
    };},
  };
  b.context.window.L=b.context.L;
  b.context.target={latitude:28.1,longitude:-81.6,radius_km:25};
  b.run('result=target;initDeepDiveMap();previewDeepDiveArea(target)');
  assert.equal(centers[0][0],28.1);
  assert.equal(centers[0][1],-81.6);
  assert.equal(circles[0].opts.radius,25000);
  assert.equal(fitted.length,1);
  assert.equal(b.get('deep-dive-places-map-key').hidden,true);
});

test('Specific place cards expose location, model comparison, measurements and field checks',async()=>{
  const b=await browser();
  setReport(b,{places});
  const text=textOf(b.get('deep-dive-places-list'));
  assert.match(text,/College Street/);
  assert.match(text,/Road/);
  assert.match(text,/12\.0.*score points/i);
  assert.match(text,/12 m.*530 m/);
  assert.match(text,/830 m/);
  assert.match(text,/culverts/);
  assert.doesNotMatch(text,/caused damage|damage score/i);
  assert.match(b.get('deep-dive-places-coverage').textContent,/1.*1 km.*neighbou?rhood/i);
  assert.match(b.get('deep-dive-places-coverage').textContent,/45.*mapped.*9.*evaluated/i);
  const links=descendants(b.get('deep-dive-places-list'),'A');
  assert.equal(links[0].href,'https://www.openstreetmap.org/way/123');
  assert.match(links[0].rel,/noopener/);
});

test('Mapped neutral, supporting and unscored places remain visible',async()=>{
  const b=await browser();
  const locations=[
    {...road,id:'supporting',signals:[],supporting_signals:[{feature:'distance_to_road_m',current:340,reference:60,effect:.04}],assessment_status:'available'},
    {...road,id:'neutral',signals:[],supporting_signals:[],assessment_status:'available'},
    {...road,id:'unscored',signals:[],supporting_signals:[],assessment_status:'unavailable',
      assessment_note:'Raster data unavailable for this location'},
  ];
  setReport(b,{places:{...places,sites:locations}});
  const cards=b.get('deep-dive-places-list').children;
  assert.equal(cards.length,3);
  assert.match(textOf(cards[0]),/supporting local condition/i);
  assert.match(textOf(cards[1]),/no substantial model limitation/i);
  assert.match(textOf(cards[2]),/assessment unavailable|Raster data unavailable/i);
});

test('External names stay plain text and unsafe source URLs never become links',async()=>{
  const b=await browser();
  setReport(b,{places:{...places,sites:[{...road,name:'<img src=x onerror=alert(1)>',source_url:'javascript:alert(1)'}],sources:[{name:'Unsafe',url:'data:text/html,test'}]}});
  assert.match(textOf(b.get('deep-dive-places-list')),/<img src=x onerror=alert\(1\)>/);
  assert.equal(descendants(b.get('deep-dive-places-list'),'IMG').length,0);
  assert.equal(descendants(b.get('deep-dive-places-list'),'A').length,0);
  assert.equal(descendants(b.get('deep-dive-places-evidence'),'A').length,0);
});

test('Partial mapping states preserve useful sites and disclose failed evaluations',async()=>{
  const b=await browser();
  setReport(b,{places:{...places,status:'partial',failed_evaluations:3,warnings:['Some road data could not be retrieved']}});
  assert.equal(b.get('deep-dive-places-list').children.length,1);
  assert.match(textOf(b.get('deep-dive-places-evidence')),/Some road data could not be retrieved/);
  assert.match(b.get('deep-dive-places-coverage').textContent,/3.*failed/i);
  assert.match(b.get('deep-dive-places-status').textContent,/partial|incomplete/i);
});

test('Older reports and unavailable mapping do not imply that damaging features are absent',async()=>{
  const b=await browser();
  setReport(b,{});
  assert.equal(b.get('deep-dive-places-list').children.length,0);
  assert.match(b.get('deep-dive-places-summary').textContent,/no specific-place analysis|saved report|not included/i);
  setReport(b,{places:{...places,status:'unavailable',sites:[],mapped_features:0,assessed_features:0}});
  assert.match(b.get('deep-dive-places-summary').textContent,/unavailable|could not/i);
  assert.doesNotMatch(b.get('deep-dive-places-summary').textContent,/no roads|no buildings|no damage/i);
  assert.equal(b.get('deep-dive-content').hidden,false);
});

test('A place action focuses its geometry and opens its own map marker',async()=>{
  const b=await browser();
  const focused=[];
  let opened=0;
  b.context.view={fitBounds:(bounds,options)=>focused.push({bounds,options}),setView(){throw Error('Should focus geometry');}};
  b.context.featureLayer={getBounds:()=>({isValid:()=>true,label:'road extent'})};
  b.context.featureMarker={openPopup:()=>opened++};
  b.run("deepDiveMap=view;deepDiveOverlay={clearLayers(){}};deepDivePlaceLayers.set('way/123',{geometry:featureLayer,marker:featureMarker})");
  b.context.places=places;
  b.run('renderDeepDivePlaces({places})');
  const action=descendants(b.get('deep-dive-places-list'),'BUTTON')[0];
  action.click();
  assert.equal(focused[0].bounds.label,'road extent');
  assert.equal(opened,1);
});

test('Malformed places are skipped without breaking the landscape report',async()=>{
  const b=await browser();
  setReport(b,{places:{...places,sites:[{...road,latitude:Infinity},{...road,latitude:42.37,longitude:400},{...road,id:'valid'}]}});
  assert.equal(b.get('deep-dive-places-list').children.length,1);
  setReport(b,{places:{...places,status:'no_signal',sites:[]}});
  assert.match(b.get('deep-dive-places-summary').textContent,/no.*mapped|no.*supported|incomplete/i);
  assert.doesNotMatch(b.get('deep-dive-places-summary').textContent,/no damage|safe/i);
});

test('Mapped road geometry and footprint use distinct overlays with P markers',async()=>{
  const b=await browser();
  const overlays=[];
  const icons=[];
  const layer=()=>({addTo(){return this;},bindPopup(){return this;},getBounds(){return {isValid:()=>true};}});
  b.context.L={
    circle:layer,
    geoJSON:(geometry,options)=>{overlays.push({geometry,options});return layer();},
    marker:layer,
    divIcon:options=>{icons.push(options);return options;},
  };
  b.context.window.L=b.context.L;
  b.context.view={fitBounds(){},invalidateSize(){}};
  b.run('deepDiveMap=view;deepDiveOverlay={clearLayers(){}}');
  const building={...road,id:'way/124',kind:'building',geometry:{type:'Polygon',coordinates:[[[-72.5,42.37],[-72.499,42.37],[-72.499,42.371],[-72.5,42.37]]]}};
  const terrain={...road,id:'terrain',kind:'habitat',terrain_sample:true,geometry:{type:'Point',coordinates:[-72.5,42.37]}};
  setReport(b,{places:{...places,sites:[road,building,terrain]}});
  assert.equal(overlays.length,2);
  assert.equal(overlays[0].geometry.type,'LineString');
  assert.equal(overlays[1].geometry.type,'Polygon');
  assert.notEqual(overlays[0].options.style.color,overlays[1].options.style.color);
  assert.equal(icons[0].html,'P1');
  assert.equal(icons[1].html,'P2');
  assert.equal(icons[2].html,'P3');
});

test('Leaving the report while print tiles load suppresses the print dialog',async()=>{
  const b=await browser();
  let loaded;
  let prints=0;
  b.context.window.print=()=>prints++;
  b.context.view={
    stop(){},invalidateSize(){},getCenter:()=>({lat:42.37,lng:-72.5}),getZoom:()=>10,setView(){},
    eachLayer:fn=>fn({isLoading:()=>true,once:(_event,fn)=>{loaded=fn;},off(){}}),
  };
  b.run('deepDiveMap=view;deepDiveResult={species:"Bobcat"}');
  b.get('deep-dive-page').hidden=false;
  b.get('explore-page').hidden=true;
  b.get('deep-dive-content').hidden=false;
  b.get('app-shell').hidden=false;
  const printing=b.run('printCurrentReport("deep-dive-content","deep-dive-print")');
  b.run('showSection("explore")');
  loaded();
  await printing;
  assert.equal(prints,0);
  assert.equal(b.get('deep-dive-print').disabled,false);
});

test('Mapped habitat polygons and local terrain points are identified accurately',async()=>{
  const b=await browser();
  setReport(b,{places:{...places,sites:[
    {...road,id:'wood',kind:'habitat',name:'Long Meadow Woods'},
    {...road,id:'terrain',kind:'habitat',terrain_sample:true,name:'Slope near Long Meadow',source:'Local USGS elevation data',geometry:{type:'Point',coordinates:[-72.5,42.37]}},
  ]}});
  const cards=b.get('deep-dive-places-list').children;
  assert.match(textOf(cards[0]),/Habitat area/);
  assert.doesNotMatch(textOf(cards[0]),/Terrain/);
  assert.match(textOf(cards[1]),/Terrain/);
});

test('Clipped geometry measurements disclose that they cover only the inspected area',async()=>{
  const b=await browser();
  setReport(b,{places});
  assert.match(textOf(b.get('deep-dive-places-list')),/road length within inspected area.*830 m/i);
});

test('Unavailable mapping does not claim that selected neighborhoods were inspected',async()=>{
  const b=await browser();
  setReport(b,{places:{...places,status:'unavailable',sites:[],mapped_features:0,assessed_features:0}});
  assert.match(b.get('deep-dive-places-coverage').textContent,/selected.*search/i);
  assert.doesNotMatch(b.get('deep-dive-places-coverage').textContent,/neighborhood.*inspected/i);
});

test('Missing evidence rows are ignored while mapping credit and data freshness remain readable',async()=>{
  const b=await browser();
  setReport(b,{places:{...places,
    searched_areas:[null,...places.searched_areas],
    sites:[{...road,signals:[null,...road.signals]}],
    sources:[null,{name:'OpenStreetMap',url:'https://www.openstreetmap.org/copyright',attribution:'© OpenStreetMap contributors',data_timestamp:'2026-10-09T20:00:00Z'}],
  }});
  assert.equal(b.get('deep-dive-places-list').children.length,1);
  assert.match(textOf(b.get('deep-dive-places-evidence')),/© OpenStreetMap contributors/);
  assert.match(textOf(b.get('deep-dive-places-evidence')),/2026-10-09T20:00:00Z/);
});
