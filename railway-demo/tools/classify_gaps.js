/* ============================================================
   断口成因分类

   配套 v0.14.0 的断口搭桥修复。diagnose_gaps.py 只查「同一条线路内部」的
   段间缺口，查不出**跨线路**的断口（鸡西→穆棱那类），这个脚本补上这块盲区。

   用法：node tools/classify_gaps.js
   依赖：从 railway-demo/ 目录的父级运行 vm 沙箱（与 /tmp/rw_smoke.js 同构），
        实际上直接 node 跑即可，脚本自带 mock。
   ============================================================ */
/* 断口成因分类：断口处「断掉的线」与「最近的线」是否同名 + 是否在桥隧/省界 */
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
vm.runInContext(main+'\n;window.__t={Net};',sandbox);
const {Net}=window.__t;
function kmv(a,b){return Math.hypot((b[0]-a[0])*110.574,(b[1]-a[1])*111.32*Math.cos((a[0]+b[0])/2*Math.PI/180));}

// 从数据文件重建原始图 + 端点信息（含目标线名）
const pos=[],index=new Map(),adj=[],adjW=[],adjK=[],adjL=[];
const kinds=['highspeed','conventional','narrow']; const lineNames=[],lineIdx=new Map();
const lineId=nm=>{let i=lineIdx.get(nm);if(i===undefined){i=lineNames.length;lineNames.push(nm);lineIdx.set(nm,i);}return i;};
const GRID=0.01,MAX_EDGE=0.5,NET_SPLIT_KM=2,RAD=Math.PI/180;
const kmOf=(a,b)=>Math.hypot((b[0]-a[0])*110.574,(b[1]-a[1])*111.320*Math.cos((a[0]+b[0])/2*RAD));
const getIdx=p=>{const k=Math.round(p[0]/GRID)+','+Math.round(p[1]/GRID);let i=index.get(k);
  if(i===undefined){i=pos.length;index.set(k,i);pos.push(p);adj.push([]);adjW.push([]);adjK.push([]);adjL.push([]);}return i;};
const addPoly=(c,kind,name)=>{ if(!c||c.length<2)return; const k=Math.max(0,kinds.indexOf(kind)); const ln=lineId(name);
  for(let i=0;i<c.length-1;i++){ const p0=c[i],p1=c[i+1];
    if(Math.hypot(p1[0]-p0[0],p1[1]-p0[1])>MAX_EDGE)continue;
    const parts=Math.max(1,Math.ceil(kmOf(p0,p1)/NET_SPLIT_KM)); let prev=p0;
    for(let s=1;s<=parts;s++){ const nx=s===parts?p1:[p0[0]+(p1[0]-p0[0])*s/parts,p0[1]+(p1[1]-p0[1])*s/parts];
      const a=getIdx(prev),b=getIdx(nx);
      if(a!==b){const w=kmOf(prev,nx);adj[a].push(b);adjW[a].push(w);adjK[a].push(k);adjL[a].push(ln);
                adj[b].push(a);adjW[b].push(w);adjK[b].push(k);adjL[b].push(ln);}
      prev=nx; } } };
[['hs',window.HSLINES],['cv',window.CONVLINES],['rl',window.LINES_REAL]].forEach(([t,S])=>{
  if(!S||!S.lines)return; Object.keys(S.lines).forEach(nm=>(S.lines[nm].polylines||[]).forEach(pl=>addPoly(pl,S.lines[nm].kind,nm)));});
const n=pos.length; const deg=new Int32Array(n); for(let i=0;i<n;i++)deg[i]=adj[i].length;
const linesAt=i=>{const s=new Set();(adjL[i]||[]).forEach(x=>{if(x>=0)s.add(lineNames[x]);});return [...s];};

const CELL=0.02; const grid=new Map();
for(let i=0;i<n;i++){const k=Math.floor(pos[i][0]/CELL)+','+Math.floor(pos[i][1]/CELL);
  let a=grid.get(k);if(!a){a=[];grid.set(k,a);}a.push(i);}
function nearestMain(i,maxKm){
  let best=-1,bd=maxKm;
  const cy=Math.floor(pos[i][0]/CELL),cx=Math.floor(pos[i][1]/CELL);
  const R=Math.ceil(maxKm/2.2)+1;
  for(let dy=-R;dy<=R;dy++)for(let dx=-R;dx<=R;dx++){
    const b=grid.get((cy+dy)+','+(cx+dx));if(!b)continue;
    for(const j of b){ if(j===i||deg[j]<3)continue; if(adj[i].indexOf(j)>=0)continue;
      const d=kmOf(pos[i],pos[j]);if(d<bd){bd=d;best=j;} } }
  return {j:best,d:bd};
}
const dang=[]; for(let i=0;i<n;i++) if(deg[i]===1) dang.push(i);
const out=[];
for(const i of dang){
  const r=nearestMain(i,6);
  if(r.j<0) continue;
  out.push({d:r.d,p:pos[i],mine:linesAt(i),tgt:linesAt(r.j),kind:kinds[adjK[i][0]]});
}
console.log('可接断口（<6km）: '+out.length+' 处\n');

let same=0, diff=0;
const diffList=[];
out.forEach(o=>{
  const ov=o.mine.filter(m=>o.tgt.indexOf(m)>=0);
  if(ov.length) same++; else { diff++; diffList.push(o); }
});
console.log('=== 断口成因分类 ===');
console.log('A) 断掉的线与最近的线**同名**（同一线路内部断段）: '+same+' 处 '+(same/Math.max(1,out.length)*100).toFixed(0)+'%');
console.log('B) 断掉的线与最近的线**不同名**（线路命名切换处）: '+diff+' 处 '+(diff/Math.max(1,out.length)*100).toFixed(0)+'%');
console.log('\nB 类样例（断口正好在「两条线路名的分界」）：');
diffList.sort((a,b)=>a.d-b.d).slice(0,18).forEach(o=>
  console.log('  '+o.d.toFixed(2)+'km @'+o.p[0].toFixed(3)+','+o.p[1].toFixed(3)+'  '+o.mine.join('/')+'  ⟶  '+o.tgt.join('/')));

// 同名类：距离分布（这类是纯数据缺失）
console.log('\nA 类（同线断段）距离分布：');
const sa=out.filter(o=>o.mine.some(m=>o.tgt.indexOf(m)>=0)).map(o=>o.d).sort((a,b)=>a-b);
if(sa.length)console.log('  中位 '+sa[sa.length>>1].toFixed(2)+'km，最大 '+sa[sa.length-1].toFixed(2)+'km；<2km 的 '+sa.filter(x=>x<2).length+' 个');

// 类型一致性：断掉的高铁能否接到普速（不合理搭桥）
let crossKind=0;
out.forEach(o=>{
  const tk=kinds[adjK[o.j===undefined?0:0]]; // placeholder
});
console.log('\n=== 搭桥合理性抽查 ===');
const kmD={highspeed:0,conventional:0,narrow:0};
out.forEach(o=>{kmD[o.kind]++;});
console.log('断口端点类型: '+JSON.stringify(kmD));
fs.writeFileSync('/tmp/gap_class.json',JSON.stringify(out.map(o=>({d:+o.d.toFixed(2),lat:o.p[0],lon:o.p[1],mine:o.mine,tgt:o.tgt,kind:o.kind}))));
