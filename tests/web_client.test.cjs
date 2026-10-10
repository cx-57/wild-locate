const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

async function browser({missingIds=[]}={}) {
  const nodes = new Map();
  function element(tagName='div') {
    let value='';
    return {
      tagName:tagName.toUpperCase(),
      get value(){return value;}, set value(next){value=String(next);},
      textContent:'', hidden:false, disabled:false, open:false,
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
    if(missingIds.includes(id)) return null;
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
      querySelectorAll:()=>[],
      body:element(),
    },
    window:{confirm:()=>true,scrollTo(){},print(){}},
    Map,
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
    clearTimeout(){},
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

function textOf(node) {
  return [node.textContent,...node.children.map(textOf)].join(' ');
}

const deepDiveReport = {
  analysis_type:'deep_dive',
  species:'Bobcat',
  region:'MA',
  latitude:42.37,
  longitude:-72.28,
  radius_km:25,
  model:'Random Forest',
  sample_points:81,
  unavailable_points:0,
  overview:{
    evaluated_points:81,
    mean_percentile:64,
    high_suitability_points:45,
    strongest_point:{latitude:42.4,longitude:-72.3,score:.9,percentile:95,category:'Very High'},
    strongest_sector:{name:'Northwest',mean_percentile:77,points:10},
  },
  habitat:{
    strengths:[{feature:'forest_fraction_1000m',mean_effect:.08,affected_points:55,points_evaluated:81}],
    constraints:[{feature:'mean_impervious_1000m',mean_effect:-.05,affected_points:42,points_evaluated:81}],
  },
  pressures:[
    {domain:'Development',mean_negative_effect:.04,affected_points:42,affected_percentage:51.85,features:['mean_impervious_1000m']}
  ],
  points:[
    {latitude:42.4,longitude:-72.3,status:'ok',score:.9,percentile:95,category:'Very High'},
    {latitude:42.2,longitude:-72.1,status:'ok',score:.2,percentile:18,category:'Very Low'},
  ],
};

test('Deep Dive opens a separate in-app workspace and preserves the habitat result',async()=>{
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
  assert.match(b.get('deep-dive-title').textContent,/specific places/i);
  assert.match(b.get('deep-dive-summary').textContent,/mapped roads|mapped features/i);
  assert.equal(b.get('deep-dive-loading').hidden,true);

  b.get('deep-dive-back').click();
  assert.equal(b.get('explore-page').hidden,false);
  assert.equal(b.get('deep-dive-page').hidden,true);
  assert.equal(b.run('result'),b.context.assessment);
});

test('Point analyses expand to a 10 km Deep Dive area',async()=>{
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

test('Scenario percentages render their exact outcomes, including a decline',async()=>{
  const b=await browser();
  b.get('scenario-level').value='0.25';
  b.context.assessment={...complete.result,insights:{influences:[],scenarios:[
    {title:'Reduce impervious surface by 25%',fraction:.25,percentile:40,delta:-.05,changes:[{feature:'mean_impervious_250m',before:40,after:30}]},
    {title:'Reduce impervious surface by 50%',fraction:.5,percentile:70,delta:.2,changes:[]},
  ]}};
  b.run('showResult(assessment)');
  assert.equal(b.get('scenario-cards').children.length,1);
  assert.match(textOf(b.get('scenario-cards')),/40th percentile/);
  assert.match(textOf(b.get('scenario-cards')),/-10 percentile points/);
  assert.match(textOf(b.get('scenario-cards')),/40.0% → 30.0%/);
  b.get('scenario-level').value='0.5';
  b.get('scenario-level').handlers.change();
  assert.match(textOf(b.get('scenario-cards')),/70th percentile/);
});

test('Survey shortlist excludes missing samples and sorts by within-model rank',async()=>{
  const b=await browser();
  b.context.assessment={...complete.result,analysis_type:'regional',radius_km:10,grid_spacing_km:2,evaluated_points:2,points:[
    {latitude:42,longitude:-72,status:'unavailable',percentile:100},
    {latitude:42.1,longitude:-72,status:'ok',percentile:30,score:.2,category:'Low'},
    {latitude:42.2,longitude:-72,status:'ok',percentile:90,score:.8,category:'Very High'},
  ]};
  b.run('showResult(assessment)');
  assert.equal(b.get('survey-list').children.length,2);
  assert.match(textOf(b.get('survey-list').children[0]),/90th/);
  b.context.assessment.points=[];
  b.run('showResult(assessment)');
  assert.equal(b.get('survey-panel').hidden,true);
});

test('Recorded example preserves its origin and does not sign into an account',async()=>{
  const b=await browser();
  b.context.fetch=async()=>response({recorded_at:'2026-10-10T00:00:00Z',point:{...complete.result,region:'MA'}});
  await b.get('try-example').click();
  assert.match(b.get('assessment-origin').textContent,/Recorded Amherst example/);
  assert.equal(b.run('regional'),false);
  assert.equal(b.get('result').hidden,false);
});

test('Deep Dive cancellation suppresses a completion response already in flight',async()=>{
  const b=await browser();
  let finishPoll,finishCancel;
  b.context.fetch=path=>new Promise(resolve=>{
    if(path.endsWith('/cancel')) finishCancel=resolve;
    else finishPoll=resolve;
  });
  b.run("deepDiveJobId='deep-race';setDeepDiveBusy(true)");
  b.get('deep-dive-content').hidden=true;
  const polling=b.run("pollDeepDive('deep-race',deepDiveRevision)");
  const cancelling=b.get('deep-dive-cancel').click();
  finishPoll(response({status:'complete',result:deepDiveReport}));
  await polling;
  finishCancel(response({status:'complete'}));
  await cancelling;
  assert.equal(b.get('deep-dive-content').hidden,true);
  assert.equal(b.run('deepDiveBusy'),false);
  assert.match(b.get('deep-dive-error').textContent,/cancelled/i);
});

test('A late Deep Dive start cannot restore a job after leaving the account',async()=>{
  const b=await browser();
  b.context.assessment={...complete.result,region:'MA'};
  b.run('showResult(assessment)');
  let finishStart;
  b.context.fetch=path=>{
    if(path==='/api/deep-dives') return new Promise(resolve=>{finishStart=resolve;});
    if(path==='/api/auth/logout') return Promise.resolve(response({token:'new',authenticated:false,regions:[]}));
    throw Error('A stale job must not be polled: '+path);
  };
  const starting=b.get('deep-dive-launch').click();
  await b.get('sign-out').click();
  finishStart(response({id:'stale-deep',status:'running'}));
  await starting;
  assert.equal(b.run('deepDiveJobId'),null);
  assert.equal(b.run('deepDiveBusy'),false);
});

test('Deep Dive discloses incomplete spatial coverage and keeps validation accessible',async()=>{
  const b=await browser();
  b.context.report={...deepDiveReport,
    unavailable_points:21,
    overview:{...deepDiveReport.overview,evaluated_points:60,total_points:81},
    evidence:{presence_count:405,background_count:810,folds:5,spatial_block_km:10,roc_auc:.82,pr_auc:.71,
      sources:['iNaturalist research-grade observations']},
  };
  b.run('renderDeepDive(report)');
  assert.match(b.get('deep-dive-coverage-note').textContent,/60.*81/);
  assert.match(b.get('deep-dive-coverage-note').textContent,/partial|incomplete|limited|selected/i);
  assert.match(textOf(b.get('deep-dive-evidence-stats')),/405/);
  assert.match(b.get('deep-dive-evidence-sources').textContent,/iNaturalist/);
});

test('Deep Dive no longer presents directional charts or generic pressure summaries',async()=>{
  const html=fs.readFileSync('wildlocate/web/index.html','utf8');
  const page=html.slice(html.indexOf('<main id="deep-dive-page"'),html.indexOf('<main id="species-page"'));
  assert.match(page,/deep-dive-places-list/);
  assert.match(page,/deep-dive-map/);
  assert.doesNotMatch(page,/deep-dive-distribution|deep-dive-sectors|deep-dive-strengths|deep-dive-pressures|deep-dive-survey-list/);
  const mapPosition=page.indexOf('id="deep-dive-map"');
  const contentPosition=page.indexOf('id="deep-dive-content"');
  assert.ok(mapPosition < contentPosition,'The map must be visible while the report is loading');
});

test('Recorded examples label the Deep Dive transition as a live analysis',async()=>{
  const b=await browser();
  b.context.assessment={...complete.result,region:'MA',recorded_at:'2026-10-10T00:00:00Z'};
  b.run('showResult(assessment)');
  b.context.fetch=async()=>response({id:'live-deep',status:'running'});
  await b.get('deep-dive-launch').click();
  assert.equal(b.get('deep-dive-demo-note').hidden,false);
  assert.match(b.get('deep-dive-demo-note').textContent,/live.*local.*data/i);
});

test('Deep Dive JSON exports the landscape report instead of the original point assessment',async()=>{
  const b=await browser();
  b.context.assessment={...complete.result,region:'MA'};
  b.run('showResult(assessment)');
  b.context.report=deepDiveReport;
  b.run('renderDeepDive(report)');
  let exported;
  b.context.Blob=function(parts){exported=JSON.parse(parts[0]);};
  b.get('deep-dive-export').click();
  assert.equal(exported.analysis_type,'deep_dive');
  assert.equal(exported.radius_km,25);
  assert.equal(exported.overview.evaluated_points,81);
  assert.match(exported.note,/relative/i);
});

test('Failed cancellation resumes the current Deep Dive without accepting an old poll',async()=>{
  const b=await browser();
  let finishOldPoll;
  b.context.fetch=path=>{
    if(path.endsWith('/cancel')) return Promise.resolve({ok:false,status:503,json:async()=>({error:'Temporarily unavailable'})});
    return new Promise(resolve=>{finishOldPoll=resolve;});
  };
  b.run("deepDiveJobId='deep-retry';setDeepDiveBusy(true)");
  b.get('deep-dive-content').hidden=true;
  const polling=b.run("pollDeepDive('deep-retry',deepDiveRevision)");
  const oldFinish=finishOldPoll;
  await b.get('deep-dive-cancel').click();
  oldFinish(response({status:'complete',result:deepDiveReport}));
  await polling;
  assert.equal(b.run('deepDiveBusy'),true);
  assert.equal(b.get('deep-dive-content').hidden,true);
  assert.equal(b.get('deep-dive-cancel').disabled,false);
  assert.match(b.get('deep-dive-error').textContent,/could not cancel/i);
});

test('The remaining example works after removing the redundant area-example control',async()=>{
  const b=await browser({missingIds:['try-area-example']});
  b.context.fetch=async()=>response({recorded_at:'2026-10-10T00:00:00Z',point:{...complete.result,region:'MA'}});
  await b.get('try-example').click();
  assert.equal(b.get('result').hidden,false);
  assert.match(b.get('result-title').textContent,/Bobcat/);
  b.run('setBusy(true)');
  assert.equal(b.get('analyze').disabled,true);
  b.run('setBusy(false)');
  assert.equal(b.get('analyze').disabled,false);
});

test('A Deep Dive without habitat samples can still show a mapped-place view',async()=>{
  const b=await browser();
  b.context.report={...deepDiveReport,
    overview:{evaluated_points:0,total_points:81,unavailable_points:81},
    points:[],places:{status:'partial',mapped_features:2,assessed_features:0,
      sites:[{id:'road-1',kind:'road',name:'Mapped road',latitude:42.37,longitude:-72.28,
        signals:[],assessment_status:'unavailable',field_checks:[]}]}};
  b.run('renderDeepDive(report)');
  assert.match(b.get('deep-dive-coverage-note').textContent,/0 of 81/);
  assert.match(b.get('deep-dive-summary').textContent,/2 mapped features/);
  assert.match(textOf(b.get('deep-dive-places-list')),/Mapped road/);
  assert.match(textOf(b.get('deep-dive-places-list')),/unavailable/i);
});

test('Evidence labels background counts accurately in both reports',async()=>{
  const b=await browser();
  b.context.report={...deepDiveReport,evidence:{presence_count:405,background_count:810}};
  b.run('renderDeepDive(report);renderEvidence(report)');
  for(const id of ['deep-dive-evidence-stats','evidence-stats']) {
    assert.match(textOf(b.get(id)),/Background locations.*810/);
    assert.doesNotMatch(textOf(b.get(id)),/Comparison locations/);
  }
});

test('Printing fits the complete Deep Dive landscape and restores the map view afterward',async()=>{
  const b=await browser();
  b.context.calls=[];
  b.context.reportBounds={north:43,south:42};
  b.run(`
    showSection('deep-dive');
    deepDiveBounds=reportBounds;
    deepDiveMap={
      getCenter:()=>({lat:42.3,lng:-72.5}),getZoom:()=>13,
      invalidateSize:()=>calls.push('resize'),
      fitBounds:bounds=>calls.push(bounds),
      setView:(center,zoom)=>calls.push([center.lat,center.lng,zoom]),
    };
    preparePrintMaps();
  `);
  assert.equal(b.context.calls[0],'resize');
  assert.equal(b.context.calls[1],b.context.reportBounds);
  b.run('restorePrintMaps()');
  assert.deepEqual(Array.from(b.context.calls.at(-1)),[42.3,-72.5,13]);
});

test('Print waits for map tiles before opening the dialog and releases the button afterward',async()=>{
  const b=await browser();
  b.context.report=deepDiveReport;
  b.context.printed=0;
  b.context.tileLoads=[];
  b.run(`
    showSection('deep-dive');renderDeepDive(report);
    window.print=()=>printed++;
    deepDiveMap={getCenter:()=>({lat:42.3,lng:-72.5}),getZoom:()=>10,
      invalidateSize(){},fitBounds(){},setView(){},
      eachLayer:callback=>callback({isLoading:()=>true,once:(event,done)=>tileLoads.push(done),off(){}}),
    };
  `);
  const printing=b.get('deep-dive-print').click();
  assert.equal(b.context.printed,0,'The dialog must wait while map tiles are loading');
  assert.equal(b.get('deep-dive-print').disabled,true);
  b.context.tileLoads[0]();
  await printing;
  assert.equal(b.context.printed,1);
  assert.equal(b.get('deep-dive-print').disabled,false);
});

test('Technical validation scores have expandable explanations instead of leading the report',async()=>{
  const b=await browser();
  b.context.report={...deepDiveReport,evidence:{presence_count:405,background_count:810,folds:5,spatial_block_km:10,
    roc_auc:.82,pr_auc:.71,pr_reference:1/3,sources:['iNaturalist observations']}};
  b.run('renderDeepDive(report)');
  const stats=b.get('deep-dive-evidence-stats');
  const visible=stats.children.filter(child=>child.tagName!=='DETAILS').map(textOf).join(' ');
  assert.doesNotMatch(visible,/ROC-AUC|PR-AUC/);
  const help=stats.children.find(child=>child.tagName==='DETAILS');
  assert.ok(help,'Validation scores must be available in expandable help');
  assert.match(textOf(help),/What is ROC-AUC/);
  assert.match(textOf(help),/0\.820/);
  assert.match(textOf(help),/background/i);
});
