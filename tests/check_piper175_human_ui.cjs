const fs=require('fs'),vm=require('vm');
const root='evaluation/piper175/collection/';
const html=fs.readFileSync(root+'web_console.html','utf8');
const vue={console};vm.createContext(vue);vm.runInContext(fs.readFileSync(root+'static/vue.global.prod.js','utf8'),vue);
// Compile the actual template, including mode-specific controls and video links.
vue.Vue.compile(html.split('<body')[1].split('<script')[0].replace(/^[^>]*>/,''), {decodeEntities:s=>s.replace(/&gt;/g,'>').replace(/&lt;/g,'<').replace(/&amp;/g,'&').replace(/&quot;/g,'"')});
let component;
const sandbox={Vue:{createApp:c=>{component=c;return{mount(){}}}},console,URLSearchParams};
vm.createContext(sandbox);
const scripts=[...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(x=>x[1]).filter(x=>x.includes('createApp'));
vm.runInContext(scripts[0],sandbox);
let actions=[];const c={tab:'control',canRecord:true,canDelete:true,session:{task:'demo',mode:'human_video'},humanMode:true,rec:null,act:(...a)=>actions.push(a),savedRows:[],...component.methods};
c.act=(...a)=>actions.push(a);c.toggleRecord();c.rec={phase:'recording'};c.toggleRecord();c.deleteKey();
if(JSON.stringify(actions.map(x=>x[0]))!==JSON.stringify(['start','save','discard']))throw Error('shortcut actions');
console.log('Vue template and start/save/discard actions passed');
