/* ============================================================
   全国悬空端点普查

   配套 v0.14.0 的断口搭桥修复。diagnose_gaps.py 只查「同一条线路内部」的
   段间缺口，查不出**跨线路**的断口（鸡西→穆棱那类），这个脚本补上这块盲区。

   用法：node tools/scan_dangling.js
   依赖：从 railway-demo/ 目录的父级运行 vm 沙箱（与 /tmp/rw_smoke.js 同构），
        实际上直接 node 跑即可，脚本自带 mock。
   ============================================================ */
/* 全国断口普查：所有悬空端点的最近主干距离 + 断口成因分类 */
const fs=require('fs'),vm=require('vm'),path=require('path');
const DIR='/Users/chunyu/WorkBuddy/2026-09-17-18-09-43/railway-demo';
const html=fs.readFileSync(path.join(DIR,'index.html'),'utf8');
const main=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]).find(b=>b.includes('APP_VERSION'));
function el(){const a={};return{style:{},classList:{add(){},remove(){},toggle(){},contains(){return false}},innerHTML:'',value:'',textContent:'',checked:false,children:[],appendChild(c){this.children.push(c);return c},setAttribute(k,v){a[k]=String(v)},getAttribute(k){return k in a?a[k]:null},addEventListener(){},onclick:null,querySelector(){return el()},querySelectorAll(){return[]},closest(){return null}};}
const c={};const document={getElementById(id){return c[id]||(c[id]=el())},createElement(){return el()},querySelectorAll(){return[]},addEventListener(){},body:el()};
const bs={getNorthEast(){return{getLat:()=>45}},getSouthWest(){return{getLat:()=>40}}};
const TMap={LatLng:function(x,y){this.x=x},LatLngBounds:function(){this.extend=()=>{}},PolylineStyle:function(o){this.o=o},MarkerStyle:function(o){this.o=o},LabelStyle:function(o){this.o=o},MultiPolyline:function(o){this.o=o;this.setGeometries=()=>{};this.setMap=()=>{}},MultiMarker:function(o){this.setGeometries=()=>{}},MultiLabel:function(o){this.setGeometries=()=>{}},Map:function(){this.setCenter=()=>{};this.setZoom=()=>{};this.setBaseMap=()=>{};this.setMapStyleId=()=>{};this.getZoom=()=>4.6;this.getBounds=()=>bs;this.fitBounds=()=>{}}};
const st={};const localStorage={getItem:k=>k in st?st[k]:null,setItem:(k,v)=>{st[k]=String(v)},removeItem:k=>{delete st[k]}};
const window={addEventListener(){},HSLINES:null,CONVLINES:null,LINES_REAL:null,STATIONS_REAL:null,STATIONS_ALL:null,STATIONS_ALIASES:null,RW_PATCHES:[],RW_GAPS:{gaps:[]},RW_LINE_PY:null,RW_LINE_ALIAS:null};
const sandbox={window,document,TMap,localStorage,console,setTimeout:()=>0,clearTimeout:()=>{},requestAnimationFrame:()=>0,cancelAnimationFrame:()=>{},Math,JSON,Object,Array,String,Number,Boolean,Date,parseFloat,parseInt,isNaN,RegExp,Error,Set,Map};
sandbox.globalThis=sandbox;vm.createContext(sandbox);
for(const f of ['lines_real.js','highspeed_lines.js','conventional_lines.js','stations_real.js','stations_all.js','aliases.js','patches.js','gaps.js','line_py.js','line_alias.js'])
  vm.runInContext(fs.readFileSync(path.join(DIR,f),'utf8'),sandbox,{filename:f});
vm.runInContext(main+'\n;window.__t={Net,Stations};',sandbox);
const {Net,Stations}=window.__t; const g=Net.build();
function kmv(a,b){return Math.hypot((b[0]-a[0])*110.574,(b[1]-a[1])*111.32*Math.cos((a[0]+b[0])/2*Math.PI/180));}
function L(i){const s=new Set();(g.adjL[i]||[]).forEach(x=>{if(x>=0)s.add(g.lineNames[x]);});return [...s];}
function KIND(i){return g.kinds[(g.adjK[i]||[])[0]];}

const n=g.pos.length;
const deg=new Int32Array(n); for(let i=0;i<n;i++) deg[i]=(g.adj[i]||[]).length;
// 搭桥后度数已变；原始悬空端点 = 现在度数==2 且其一条边是新搭的
// 更简单：重新建一个「不搭桥」的图来数原始悬空端点
const CELL=0.02; const grid=new Map();
for(let i=0;i<n;i++){
  const k=Math.floor(g.pos[i][0]/CELL)+','+Math.floor(g.pos[i][1]/CELL);
  let a=grid.get(k); if(!a){a=[];grid.set(k,a);} a.push(i);
}
function nearestMain(i, maxKm){
  let best=-1,bd=maxKm;
  const cy=Math.floor(g.pos[i][0]/CELL), cx=Math.floor(g.pos[i][1]/CELL);
  const R=Math.ceil(maxKm/2.2)+1;
  for(let dy=-R;dy<=R;dy++)for(let dx=-R;dx<=R;dx++){
    const b=grid.get((cy+dy)+','+(cx+dx)); if(!b)continue;
    for(const j of b){
      if(j===i||deg[j]<3)continue;
      if(deg[i]<3 && g.adj[i].indexOf(j)>=0) continue;
      const d=kmv(g.pos[i],g.pos[j]); if(d<bd){bd=d;best=j;}
    }
  }
  return {j:best,d:bd};
}
// 找出所有"真悬空端点"：当前度数==2（原始1+1桥）且邻边一条很矮（桥）—— 改用原始数据重建
console.log('=== 重建原始图（不搭桥）以准确统计悬空端点 ===');
// 用 Net.build 的内部逻辑重跑一次：临时把 BRIDGE_DANGLING 调大再 build
// 更简单：直接在 raw 图上重建
const pos=[],index=new Map(),adj=[],adjW=[],adjK=[],adjL=[];
const kinds=['highspeed','conventional','narrow']; const lineNames=[],lineIdx=new Map();
const lineId=nm=>{let i=lineIdx.get(nm);if(i===undefined){i=lineNames.length;lineNames.push(nm);lineIdx.set(nm,i);}return i;};
const GRID=0.01, MAX_EDGE=0.5, NET_SPLIT_KM=2, RAD=Math.PI/180;
const kmOf=(a,b)=>Math.hypot((b[0]-a[0])*110.574,(b[1]-a[1])*111.320*Math.cos((a[0]+b[0])/2*RAD));
const getIdx=p=>{const k=Math.round(p[0]/GRID)+','+Math.round(p[1]/GRID);let i=index.get(k);
  if(i===undefined){i=pos.length;index.set(k,i);pos.push(p);adj.push([]);adjW.push([]);adjK.push([]);adjL.push([]);}return i;};
const addPoly=(coords,kind,name)=>{
  if(!coords||coords.length<2)return; const k=Math.max(0,kinds.indexOf(kind)); const ln=lineId(name);
  for(let i=0;i<coords.length-1;i++){
    const p0=coords[i],p1=coords[i+1];
    if(Math.hypot(p1[0]-p0[0],p1[1]-p0[1])>MAX_EDGE)continue;
    const parts=Math.max(1,Math.ceil(kmOf(p0,p1)/NET_SPLIT_KM)); let prev=p0;
    for(let s=1;s<=parts;s++){
      const next=s===parts?p1:[p0[0]+(p1[0]-p0[0])*s/parts,p0[1]+(p1[1]-p0[1])*s/parts];
      const a=getIdx(prev),b=getIdx(next);
      if(a!==b){const w=kmOf(prev,next);adj[a].push(b);adjW[a].push(w);adjK[a].push(k);adjL[a].push(ln);
                adj[b].push(a);adjW[b].push(w);adjK[b].push(k);adjL[b].push(ln);}
      prev=next;
    }
  }
};
[['hs',window.HSLINES],['cv',window.CONVLINES],['rl',window.LINES_REAL]].forEach(([tag,S])=>{
  if(!S||!S.lines)return;
  Object.keys(S.lines).forEach(nm=>(S.lines[nm].polylines||[]).forEach(pl=>addPoly(pl,S.lines[nm].kind,nm)));
});
console.log('原始图 '+pos.length+' 顶点');
const d0=new Int32Array(pos.length); for(let i=0;i<pos.length;i++) d0[i]=adj[i].length;
const dangle=[]; for(let i=0;i<pos.length;i++) if(d0[i]===1) dangle.push(i);
console.log('悬空端点（度=1）: '+dangle.length+' 个');

// 每个悬空端点的最近主干距离
const grid2=new Map();
for(let i=0;i<pos.length;i++){
  const k=Math.floor(pos[i][0]/CELL)+','+Math.floor(pos[i][1]/CELL);
  let a=grid2.get(k); if(!a){a=[];grid2.set(k,a);} a.push(i);
}
function nm2(i,maxKm){
  let best=-1,bd=maxKm;
  const cy=Math.floor(pos[i][0]/CELL), cx=Math.floor(pos[i][1]/CELL);
  const R=Math.ceil(maxKm/2.2)+1;
  for(let dy=-R;dy<=R;dy++)for(let dx=-R;dx<=R;dx++){
    const b=grid2.get((cy+dy)+','+(cx+dx)); if(!b)continue;
    for(const j of b){
      if(j===i||d0[j]<3)continue;
      if(adj[i].indexOf(j)>=0)continue;
      const d=kmOf(pos[i],pos[j]); if(d<bd){bd=d;best=j;}
    }
  }
  return {j:best,d:bd};
}
const rows=[];
for(const i of dangle){
  const r=nm2(i,60);
  const ln=new Set(); adjL[i].forEach(x=>{if(x>=0)ln.add(lineNames[x]);});
  rows.push({i,d:r.d,j:r.j,lines:[...ln],kind:kinds[adjK[i][0]],p:pos[i]});
}
rows.sort((a,b)=>a.d-b.d);
console.log('\n最近主干距离分布：');
const buckets=[[0,1],[1,2],[2,3],[3,6],[6,10],[10,20],[20,40],[40,60]];
buckets.forEach(([a,b])=>{
  const c=rows.filter(r=>r.d>=a&&r.d<b).length;
  console.log('  '+a+'~'+b+' km: '+String(c).padStart(3)+' 个');
});
const noNear=rows.filter(r=>r.d>=60);
console.log('\n60km 内没有任何主干节点的悬空端点: '+noNear.length+' 个（这些是铁路尽头/孤线，属正常）');
console.log('其中线路名分布（前 15）:');
const cnt={}; rows.filter(r=>r.d<6).forEach(r=>r.lines.forEach(l=>cnt[l]=(cnt[l]||0)+1));
Object.entries(cnt).sort((a,b)=>b[1]-a[1]).slice(0,15).forEach(([k,v])=>console.log('  '+k+': '+v));

console.log('\n=== 断口最严重的 25 处（最近主干 1~10km，说明数据缺失）===');
rows.filter(r=>r.d>=1&&r.d<10).slice(0,25).forEach(r=>{
  const tgt=r.j>=0?[...new Set((()=>{const s=new Set();adjL[r.j].forEach(x=>{if(x>=0)s.add(lineNames[x]);});return s;})())]:[];
  console.log('  '+r.d.toFixed(2)+'km @ '+r.p[0].toFixed(3)+','+r.p[1].toFixed(3)+'  断掉的线: '+r.lines.join('/')+'  → 最近的线: '+tgt.join('/'));
});
fs.writeFileSync('/tmp/dangle_all.json', JSON.stringify(rows.map(r=>({d:+r.d.toFixed(2),lat:+r.p[0].toFixed(4),lon:+r.p[1].toFixed(4),lines:r.lines,kind:r.kind}))));
console.log('\n明细已写 /tmp/dangle_all.json');
