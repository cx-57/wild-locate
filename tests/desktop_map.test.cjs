const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

test('Desktop map selection highlights existing points and uses the result bridge',()=>{
  const points=[];
  const selected=[];
  const locations=[];
  const views=[];
  const map={setView(position,zoom){views.push({position,zoom});return this;},
    on(){},removeLayer(){},fitBounds(){},panTo(){},getZoom:()=>8,invalidateSize(){}};
  function layer(position,style={}) {
    return {position,style,handlers:{},opened:false,
      addTo(){return this;},on(event,fn){this.handlers[event]=fn;return this;},
      bindPopup(){return this;},openPopup(){this.opened=true;return this;},
      setStyle(next){Object.assign(this.style,next);return this;},
      getLatLng(){return position;},getBounds(){return [];},clearLayers(){}};
  }
  const window={addEventListener(event,fn){this.start=fn;}};
  const context=vm.createContext({
    window,
    document:{getElementById:()=>({addEventListener(){}}),createElement:()=>({}),body:{setAttribute(){}}},
    L:{map:()=>map,divIcon:()=>({}),layerGroup:()=>layer(),tileLayer:()=>layer(),
      latLng:(lat,lng)=>({lat,lng}),marker:layer,circle:layer,
      circleMarker(position,style){const marker=layer(position,style);points.push(marker);return marker;}},
    qt:{webChannelTransport:{}},
    QWebChannel:function(_,ready){ready({objects:{locationBridge:{
      mapReady(){},tilesAvailable(){},selectResultPoint:index=>selected.push(index),
      selectLocation:(...args)=>locations.push(args),
    }}});},
    ResizeObserver:function(){this.observe=()=>{};},
  });
  const html=fs.readFileSync('wildlocate/gui/map.html','utf8');
  vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
  window.start();
  window.setLocationState({latitude:42,longitude:-72,enabled:true,radiusKm:10,
    areaPoints:[{latitude:42.1,longitude:-72,status:'unavailable'},
      {latitude:42,longitude:-72,status:'ok',percentile:80,score:.8,category:'Very High'}]});
  window.highlightResultPoint(1);
  assert.equal(points.length,2);
  assert.equal(points[1].style.radius,8);
  assert.equal(points[1].opened,true);
  assert.equal(views.at(-1).zoom,12);
  points[0].handlers.click();
  assert.deepEqual(selected,[0]);
  assert.deepEqual(locations,[],'result selection never moves the analysis center');
  assert.equal(points[1].style.radius,3.5);
  assert.equal(points[0].style.radius,8);
  window.setLocationState({latitude:42,longitude:-72,enabled:true,radiusKm:10,areaPoints:[]});
  const previousViews=views.length;
  window.highlightResultPoint(1);
  assert.equal(views.length,previousViews,'cleared results cannot select stale points');
});
