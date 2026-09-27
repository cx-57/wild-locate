const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

async function browser() {
  const nodes = new Map();
  function element() {
    return {
      value:'', textContent:'', hidden:false, disabled:false, open:false,
      className:'', style:{},
      children:[], handlers:{}, firstChild:{textContent:''},
      classList:{toggle(){}},
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

const deepDiveReport = {
  analysis_type:'deep_dive',
  species:'Bobcat',
  region:'MA',
  latitude:42.37,
  longitude:-72.28,
  radius_km:25,
  grid_spacing_km:5,
  model:'Random Forest',
  training_observations:405,
  sample_points:81,
  unavailable_points:0,
  overview:{
    evaluated_points:81,
    mean_percentile:64,
    median_percentile:66,
    high_suitability_points:45,
    very_high_suitability_points:18,
    strongest_point:{latitude:42.4,longitude:-72.3,score:.9,percentile:95,category:'Very High'},
    weakest_point:{latitude:42.2,longitude:-72.1,score:.2,percentile:18,category:'Very Low'},
    strongest_sector:{name:'Northwest',mean_percentile:77,points:10},
  },
  habitat:{
    strengths:[{feature:'forest_fraction_1000m',mean_effect:.08,affected_points:55,points_evaluated:81,median_value:.62}],
    constraints:[{feature:'mean_impervious_1000m',mean_effect:-.05,affected_points:42,points_evaluated:81,median_value:20}],
    contrasts:[{feature:'forest_fraction_1000m',high_habitat_median:.75,low_habitat_median:.3,difference:.45,standardized_difference:1.2}],
  },
  pressures:[{domain:'Development',mean_negative_effect:.04,affected_points:42,affected_percentage:51.85,features:['mean_impervious_1000m']}],
  protection:{
    status:'available',
    high_suitability_samples:2,
    checked_samples:2,
    failed_queries:0,
    intersecting_padus:1,
    biodiversity_managed:1,
    not_intersecting_padus:1,
    samples:[
      {latitude:42.4,longitude:-72.3,percentile:95,category:'Very High',within_padus:true,biodiversity_managed:true,areas:[{name:'Example Refuge'}]},
      {latitude:42.3,longitude:-72.2,percentile:70,category:'High',within_padus:false,biodiversity_managed:false,areas:[]},
    ],
  },
  scenarios:[{latitude:42.3,longitude:-72.2,current_percentile:50,projected_percentile:68,percentile_delta:18,description:'Reduce impervious surface by 50%',score_delta:.1,changes:[]}],
  points:[
    {latitude:42.4,longitude:-72.3,status:'ok',score:.9,percentile:95,category:'Very High',top_influences:[]},
    {latitude:42.2,longitude:-72.1,status:'ok',score:.2,percentile:18,category:'Very Low',top_influences:[]},
  ],
  data_scope:{
    connected:['land cover','road context','USGS PAD-US 4.1 protected-area context'],
    not_connected_yet:['historical land-cover change'],
  },
  limitations:['sampled locations only'],
};

test('Conservation Deep Dive opens a separate in-app workspace and preserves the habitat result',async()=>{
  const b=await browser();
  b.context.assessment={
    ...complete.result,
    analysis_type:'regional',
    region:'MA',
    radius_km:25,
    grid_spacing_km:5,
    evaluated_points:1,
    unavailable_points:0,
    mean_score:.8,
    points:[{latitude:42.37,longitude:-72.28,status:'ok',score:.8,percentile:80,category:'Very High'}],
  };
  b.run('showResult(assessment)');

  b.context.fetch=async(path,options)=>{
    if(path==='/api/deep-dives') {
      const payload=JSON.parse(options.body);
      assert.equal(payload.species,'Bobcat');
      assert.equal(payload.radius_km,25);
      return response({id:'deep-1',status:'running'});
    }
    if(path==='/api/deep-dives/deep-1') return response({status:'complete',result:deepDiveReport});
    throw Error('Unexpected request: '+path);
  };

  await b.get('deep-dive-launch').click();
  await new Promise(resolve=>setImmediate(resolve));

  assert.equal(b.get('explore-page').hidden,true);
  assert.equal(b.get('deep-dive-page').hidden,false);
  assert.equal(b.get('deep-dive-content').hidden,false);
  assert.match(b.get('deep-dive-title').textContent,/Bobcat/);
  assert.match(textOf(b.get('deep-dive-strengths')),/forest fraction 1 km/i);
  assert.match(textOf(b.get('deep-dive-pressures')),/Development/);
  assert.match(textOf(b.get('deep-dive-protection')),/Example Refuge/);
  assert.match(textOf(b.get('deep-dive-scenarios')),/18 percentile points/);

  b.get('deep-dive-back').click();
  assert.equal(b.get('explore-page').hidden,false);
  assert.equal(b.get('deep-dive-page').hidden,true);
  assert.equal(b.run('result'),b.context.assessment);
});

test('Point analyses expand to a 10 km Deep Dive research area',async()=>{
  const b=await browser();
  b.context.assessment={...complete.result,region:'MA'};
  b.run('showResult(assessment)');
  let request;
  b.context.fetch=async(path,options)=>{
    if(path==='/api/deep-dives') {
      request=JSON.parse(options.body);
      return response({id:'deep-point',status:'running'});
    }
    if(path==='/api/deep-dives/deep-point') return response({status:'complete',result:{...deepDiveReport,radius_km:10}});
    throw Error('Unexpected request: '+path);
  };
  await b.get('deep-dive-launch').click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(request.radius_km,10);
  assert.match(b.get('deep-dive-meta').textContent,/10 km/);
});

test('Ask Wild-Locate is grounded in the completed Deep Dive job',async()=>{
  const b=await browser();
  b.context.report=deepDiveReport;
  b.run("deepDiveJobId='deep-chat';deepDiveReport=report;renderDeepDive(report)");
  b.context.fetch=async(path,options)=>{
    assert.equal(path,'/api/deep-dives/deep-chat/ask');
    const payload=JSON.parse(options.body);
    assert.match(payload.question,/pressure/i);
    return response({answer:'Development is the strongest modeled pressure signal in this analysis.'});
  };
  await b.run("askDeepDive('What is the biggest pressure?')");
  assert.match(textOf(b.get('deep-dive-chat-log')),/Development is the strongest modeled pressure/i);
});

