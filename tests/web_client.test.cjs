const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

async function browser() {
  const nodes = new Map();
  function element() {
    let value='';
    return {
      get value(){return value;}, set value(next){value=String(next);},
      textContent:'', hidden:false, disabled:false, open:false,
      children:[], handlers:{}, firstChild:{textContent:''},
      classList:{toggle(){}},
      style:{},
      addEventListener(name, fn){this.handlers[name]=fn;},
      setAttribute(name, value){this[name]=value;},
      append(...children){this.children.push(...children);},
      replaceChildren(...children){
        this.children=children;
        if(children[0]?.value) this.value=children[0].value;
      },
      click(){return this.handlers.click?.();},
      focus(){},
      scrollIntoView(){},
      showModal(){this.open=true;},
      close(){this.open=false;},
    };
  }
  const get = id => {
    if(!nodes.has(id)) nodes.set(id,element());
    return nodes.get(id);
  };

  get('latitude').value='42.37';
  get('longitude').value='-72.28';
  get('radius').value='25';

  const context=vm.createContext({
    document:{
      getElementById:get,
      createElement:element,
      querySelector:()=>element(),
      body:element(),
    },
    window:{confirm:()=>true,scrollTo(){}},
    console, Date, Number, Object, String, Math, JSON, Error,
    encodeURIComponent,
    Option:function(name,value){this.value=value;this.textContent=name;},
    fetch:async()=>({
      ok:true,
      status:200,
      json:async()=>({
        token:'token',
        authenticated:true,
        username:'tester',
        regions:[{
          code:'MA',
          name:'Massachusetts',
          center:[42.37,-72.28],
          species:['Bobcat'],
        }],
      }),
    }),
    setTimeout:()=>0,
    setInterval:()=>0,
    clearInterval(){},
    Blob:function(){},
    URL:{createObjectURL:()=> 'blob:test', revokeObjectURL(){}},
  });

  vm.runInContext(fs.readFileSync('wildlocate/web/app.js','utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  return {context,get,run:code=>vm.runInContext(code,context)};
}

const complete={
  status:'complete',
  result:{
    species:'Bobcat',
    score:.5,
    percentile:50,
    category:'Moderate',
    model:'Test',
    training_observations:25,
    latitude:42.37,
    longitude:-72.28,
    features:{},
  },
};
const response=data=>({ok:true,status:200,json:async()=>data});

test('Cancel suppresses a completion response already in flight',async()=>{
  const b=await browser();
  let completePoll,completeCancel;
  b.context.fetch=path=>new Promise(resolve=>{
    if(path.endsWith('/cancel')) completeCancel=resolve;
    else completePoll=resolve;
  });
  b.run("clearResult();activeJob='job';setBusy(true)");
  const polling=b.run("poll('job',revision)");
  const cancelling=b.get('cancel').handlers.click();
  completePoll(response(complete));
  await polling;
  completeCancel(response({status:'complete'}));
  await cancelling;
  assert.equal(b.get('result').hidden,true);
  assert.equal(b.get('inputs').disabled,false);
});

test('A missing job releases controls instead of polling forever',async()=>{
  const b=await browser();
  b.context.fetch=async()=>({
    ok:false,
    status:404,
    json:async()=>({error:'Analysis not found.'}),
  });
  b.run("clearResult();activeJob='missing';setBusy(true)");
  await b.run("poll('missing',revision)");
  assert.equal(b.get('inputs').disabled,false);
  assert.equal(b.get('cancel').hidden,true);
  assert.match(b.get('error').textContent,/not found/i);
});

test('Partial species search renders state-valid suggestions before training',async()=>{
  const b=await browser();
  b.get('training-query').value='alligator';
  b.run("managerRegion='FL'");

  const suggestions={
    region:'FL',
    region_name:'Florida',
    suggestions:[
      {
        taxon_id:1,
        common_name:'American Alligator',
        scientific_name:'Alligator mississippiensis',
        iconic_taxon_name:'Reptilia',
        observation_count:1500,
      },
      {
        taxon_id:2,
        common_name:'Example Alligator',
        scientific_name:'Alligator example',
        iconic_taxon_name:'Reptilia',
        observation_count:25,
      },
    ],
  };

  b.context.fetch=async(path,options)=>{
    if(path==='/api/species/suggestions') return response(suggestions);
    if(path==='/api/training/start') {
      const body=JSON.parse(options.body);
      assert.equal(body.query,'American Alligator');
      return response({
        id:'training-1',
        status:'resolved',
        message:'Species found.',
        logs:[],
        result:{
          common_name:'American Alligator',
          scientific_name:'Alligator mississippiensis',
          iconic_taxon_name:'Reptilia',
        },
      });
    }
    if(path==='/api/training/training-1') {
      return response({status:'resolved',logs:[],result:{
        common_name:'American Alligator',
        scientific_name:'Alligator mississippiensis',
        iconic_taxon_name:'Reptilia',
      }});
    }
    throw Error('Unexpected request: '+path);
  };

  await b.get('training-form').handlers.submit({preventDefault(){}});
  assert.equal(b.get('training-suggestions').hidden,false);
  assert.equal(b.get('training-suggestions').children.length,2);
  assert.equal(
    b.get('training-suggestions').children[0].children[0].textContent,
    'American Alligator'
  );

  await b.get('training-suggestions').children[0].click();
  assert.equal(b.get('training-query').value,'American Alligator');
  assert.equal(b.get('training-match').hidden,false);
  assert.match(b.get('training-match').textContent,/American Alligator/);
});

const areaResult = {
  ...complete.result, analysis_type:'regional', radius_km:10, grid_spacing_km:2,
  evaluated_points:1, unavailable_points:1, mean_score:.8,
  points:[
    {latitude:42.4,longitude:-72.2,status:'unavailable'},
    {latitude:42.37,longitude:-72.28,status:'ok',score:.8,percentile:80,category:'Very High',
     insights:{influences:[{feature:'forest_fraction_250m',current:.5,reference:.2,effect:.1}]},
     restoration:{current_percentile:80,projected_percentile:95,percentile_delta:15,
       description:'Replace 50% of developed cover with forest',
       changes:[{feature:'forest_fraction_250m',before:.5,after:.6}]}}
  ],
  conservation:{
    evaluated_points:1,
    habitat_distribution:{'Very High':{count:1,percentage:100}},
    protection_candidates:[{point_index:1,latitude:42.37,longitude:-72.28,percentile:80,category:'Very High'}],
    restoration_candidates:[{point_index:1,latitude:42.37,longitude:-72.28,percentile:80,
      restoration:{current_percentile:80,projected_percentile:95,percentile_delta:15,
        description:'Replace 50% of developed cover with forest'}}],
  },
};

function textOf(node) {
  return [node.textContent,...node.children.map(textOf)].join(' ');
}

function attachMap(b) {
  const markers=[];
  const views=[];
  const layer=()=>({
    handlers:{},options:{},opened:false,
    addTo(){return this;},bindPopup(content){this.popup=content;return this;},
    on(event,fn){this.handlers[event]=fn;return this;},
    setStyle(style){Object.assign(this.options,style);return this;},
    setRadius(radius){this.options.radius=radius;return this;},
    openPopup(){this.opened=true;return this;},
    getBounds(){return [];},
  });
  b.context.L={circle:layer,marker:layer,divIcon:()=>({}),
    circleMarker(position,options){const m=layer();m.position=position;m.options=options;markers.push(m);return m;}};
  b.context.testMap={setView(...args){views.push(args);},getZoom:()=>8,fitBounds(){}};
  b.context.testOverlay={clearLayers(){}};
  b.run('map=testMap;overlay=testOverlay');
  return {markers,views};
}

test('Conservation cards select existing map points without changing the analysis center',async()=>{
  const b=await browser();
  const {markers,views}=attachMap(b);
  b.context.assessment=areaResult;
  b.run('showResult(assessment)');
  assert.equal(b.get('conservation').hidden,false);
  assert.match(textOf(b.get('habitat-distribution')),/Very High.*1.*100/);
  const cards=b.get('restoration-candidates').children;
  assert.equal(cards.length,1);
  cards[0].click();
  assert.equal(markers.length,2,'selection reuses markers');
  assert.equal(markers[1].opened,true);
  assert.equal(markers[0].opened,false);
  assert.equal(views.length,1);
  assert.equal(b.run('result'),areaResult);
  assert.equal(b.get('latitude').value,'42.37');
  assert.match(textOf(b.get('selected-point')),/80.*95/);
  assert.match(textOf(b.get('selected-point')),/forest fraction 250m/);
  assert.match(textOf(b.get('selected-point')),/model-based scenario/i);
  markers[0].handlers.click();
  assert.match(textOf(b.get('selected-point')),/unavailable/i);
  b.run('clearResult()');
  assert.equal(b.get('conservation').hidden,true);
  assert.equal(b.get('selected-point').hidden,true);
});

test('Empty conservation rankings explain missing candidates and point mode hides screening',async()=>{
  const b=await browser();
  b.context.assessment={...areaResult,points:[],evaluated_points:0,conservation:{
    evaluated_points:0,habitat_distribution:{},protection_candidates:[],restoration_candidates:[]}};
  b.run('showResult(assessment)');
  assert.match(textOf(b.get('protection-candidates')),/no.*high/i);
  assert.match(textOf(b.get('restoration-candidates')),/no.*positive/i);
  b.context.assessment=complete.result;
  b.run('showResult(assessment)');
  assert.equal(b.get('conservation').hidden,true);
  assert.equal(b.get('selected-point').hidden,true);
});

test('JSON export preserves conservation and scenario data with explicit limitations',async()=>{
  const b=await browser();
  let exported;
  b.context.Blob=function(parts){exported=JSON.parse(parts.join(''));};
  b.context.assessment=areaResult;
  b.run('showResult(assessment)');
  b.get('export').click();
  assert.deepEqual(exported.conservation,areaResult.conservation);
  assert.deepEqual(exported.points,areaResult.points);
  for(const phrase of ['81 points','not continuous habitat coverage','not causal predictions']) {
    assert.ok(exported.note.includes(phrase));
  }
});
