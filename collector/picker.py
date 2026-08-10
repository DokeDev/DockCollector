import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from .rules import account_profile_id
from .runner import TargetRunner


PICKER_SCRIPT = r"""
(() => {
  if (window.__collectorPickerInstalled) return;
  window.__collectorPickerInstalled = true;
  const box = document.createElement('div');
  Object.assign(box.style,{position:'fixed',pointerEvents:'none',zIndex:'2147483647',border:'2px solid #18a875',background:'rgba(24,168,117,.10)',display:'none'});
  const tip = document.createElement('div');
  Object.assign(tip.style,{position:'fixed',pointerEvents:'none',zIndex:'2147483647',background:'#10221b',color:'#fff',padding:'6px 9px',borderRadius:'6px',font:'12px sans-serif',display:'none',maxWidth:'420px'});
  const panel = document.createElement('div'); panel.className='collector-rule-panel';
  panel.setAttribute('popover','manual');
  Object.assign(panel.style,{position:'fixed',inset:'0 0 0 auto',margin:'0',zIndex:'2147483647',width:'min(480px,100dvw)',height:'100dvh',maxWidth:'100dvw',maxHeight:'100dvh',overflow:'hidden',boxSizing:'border-box',background:'#fff',color:'#15211b',padding:'16px',border:'0',borderLeft:'1px solid #b9c9c0',borderRadius:'0',boxShadow:'-12px 0 40px rgba(0,0,0,.25)',font:'13px -apple-system,BlinkMacSystemFont,sans-serif',display:'none',flexDirection:'column'});
  panel.innerHTML='<div style="flex:none;display:flex;justify-content:space-between;gap:8px;align-items:center;margin-bottom:8px"><b style="font-size:15px">批量采集规则</b><div style="display:flex;gap:6px;align-items:center"><iframe class="collector-save-frame" title="保存全部规则" style="width:104px;height:38px;border:0;display:block"></iframe><button class="collector-hide" style="padding:4px 8px;border:1px solid #ccd7d1;background:#fff;border-radius:7px;font-size:18px">×</button></div></div><div style="flex:none;color:#64736b;margin-bottom:12px">继续点击页面元素可加入多项；为每项填写字段名后点击顶部“保存全部”。</div><div class="collector-save-status" style="flex:none;min-height:18px;color:#137657;margin:0 0 8px"></div><div class="collector-items" style="flex:1;min-height:0;overflow-y:auto;padding-right:4px"></div><div style="flex:none;background:#fff;padding-top:10px;border-top:1px solid #e4e9e6"><button class="collector-clear" style="width:100%;padding:8px 12px;border:1px solid #ccd7d1;background:#fff;border-radius:7px">清空选择</button></div>';
  document.documentElement.append(box,tip,panel);
  const saveFrame=panel.querySelector('.collector-save-frame');
  saveFrame.srcdoc='<style>*{box-sizing:border-box}body{margin:0}button{width:100%;height:38px;border:0;border-radius:7px;background:#137657;color:white;font:13px -apple-system,BlinkMacSystemFont,sans-serif;cursor:pointer}</style><button>保存全部</button>';
  saveFrame.addEventListener('load',()=>saveFrame.contentDocument.querySelector('button').addEventListener('click',()=>window.__collectorSaveAll()));
  const chosen=[];
  const showPanel=()=>{panel.style.display='flex';try{if(panel.matches(':popover-open'))return;panel.showPopover();}catch(_){panel.style.display='flex';}};
  const hidePanel=()=>{try{if(panel.matches(':popover-open'))panel.hidePopover();}catch(_){}panel.style.display='none';};
  function selector(el){
    if(el.id) return '#'+CSS.escape(el.id);
    const parts=[];
    while(el && el.nodeType===1 && el!==document.documentElement){
      let p=el.tagName.toLowerCase();
      const classes=[...el.classList].filter(x=>!x.startsWith('collector-')).slice(0,2);
      if(classes.length) p+='.'+classes.map(CSS.escape).join('.');
      const parent=el.parentElement;
      if(parent && parent.querySelectorAll(':scope > '+p).length>1) p+=`:nth-of-type(${[...parent.children].filter(x=>x.tagName===el.tagName).indexOf(el)+1})`;
      parts.unshift(p); if(document.querySelectorAll(parts.join(' > ')).length===1) break; el=parent;
    }
    return parts.join(' > ');
  }
  const insidePanel=el=>panel===el||panel.contains(el);
  const safe=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function postBody(el){
    if(!el.matches?.("td[id^='postmessage_']"))return null;
    const fields=[...el.querySelectorAll('.showhide')];
    if(!fields.length)return null;
    const range=document.createRange();range.setStartAfter(fields[fields.length-1]);range.setEnd(el,el.childNodes.length);
    const text=range.toString().trim().replace(/\s+/g,' '),rect=range.getBoundingClientRect();
    return text&&rect.width&&rect.height?{text,rect}:null;
  }
  const inRect=(e,r)=>e.clientX>=r.left&&e.clientX<=r.right&&e.clientY>=r.top&&e.clientY<=r.bottom;
  function renderItems(){
    const root=panel.querySelector('.collector-items'); root.innerHTML='';
    chosen.forEach((item,index)=>{const row=document.createElement('div');Object.assign(row.style,{padding:'10px',border:'1px solid #dfe6e1',borderRadius:'8px',marginBottom:'8px'});row.innerHTML=`<div style="display:flex;justify-content:space-between;gap:8px"><b style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${safe(item.text||'(无文本)')}</b><button data-remove="${index}" style="border:0;background:transparent;color:#b94040">移除</button></div><code style="display:block;color:#137657;word-break:break-all;margin:6px 0">${safe(item.selector)}</code><div style="display:grid;grid-template-columns:minmax(0,1fr) 154px;gap:7px"><input data-name="${index}" value="${safe(item.name||'')}" placeholder="字段名称，例如：正文" style="width:100%;box-sizing:border-box;padding:9px;border:1px solid #ccd7d1;border-radius:7px"><select data-kind="${index}" style="width:100%;padding:9px;border:1px solid #ccd7d1;border-radius:7px;background:#fff"><option value="css" ${item.kind!=='body_after_fields'?'selected':''}>元素全部内容</option><option value="body_after_fields" ${item.kind==='body_after_fields'?'selected':''}>正文（排除字段）</option></select></div>${item.kind==='body_after_fields'?'<div style="margin-top:6px;color:#b06716">已识别帖子正文区域，保存时会排除上方结构化字段。</div>':''}`;root.append(row);});
    root.querySelectorAll('[data-remove]').forEach(btn=>btn.onclick=e=>{e.preventDefault();e.stopPropagation();chosen.splice(Number(btn.dataset.remove),1);renderItems();if(!chosen.length)hidePanel();});
    root.querySelectorAll('[data-kind]').forEach(select=>select.onchange=e=>{e.preventDefault();e.stopPropagation();chosen[Number(select.dataset.kind)].kind=select.value;renderItems();});
  }
  document.addEventListener('mousemove',e=>{const el=e.target;if(el===box||el===tip||insidePanel(el))return;const body=postBody(el),isBody=body&&inRect(e,body.rect),r=isBody?body.rect:el.getBoundingClientRect();Object.assign(box.style,{display:'block',left:r.left+'px',top:r.top+'px',width:r.width+'px',height:r.height+'px'});tip.style.display='block';tip.style.left=Math.max(4,Math.min(innerWidth-430,r.left))+'px';tip.style.top=Math.max(4,r.top-34)+'px';tip.textContent=isBody?'帖子正文（排除上方字段） · 点击选择':selector(el)+' · 点击选择';},true);
  document.addEventListener('click',e=>{if(e.target===box||e.target===tip||insidePanel(e.target))return;e.preventDefault();e.stopImmediatePropagation();const el=e.target,body=postBody(el),post=body&&inRect(e,body.rect)?el:null;const item={selector:post?"td.t_f[id^='postmessage_']":selector(el),text:(post?body.text:(el.innerText||el.textContent||'').trim().replace(/\s+/g,' ')).slice(0,500),tag:el.tagName.toLowerCase(),attribute:'',kind:post?'body_after_fields':'css'};if(!chosen.some(x=>x.selector===item.selector))chosen.push(item);renderItems();panel.querySelector('.collector-save-status').textContent=post?'已选择帖子正文；上方结构化字段不会包含在内':`已选择 ${chosen.length} 项，可继续点击页面元素`;showPanel();},true);
  panel.querySelector('.collector-hide').onclick=e=>{e.preventDefault();e.stopPropagation();hidePanel();};
  panel.querySelector('.collector-clear').onclick=e=>{e.preventDefault();e.stopPropagation();chosen.splice(0);renderItems();hidePanel();};
  window.__collectorSaveAll=async()=>{const status=panel.querySelector('.collector-save-status');panel.querySelectorAll('[data-name]').forEach(input=>chosen[Number(input.dataset.name)].name=input.value.trim());if(!chosen.length||chosen.some(x=>!x.name)){status.style.color='#b94040';status.textContent='请为每个选中元素填写字段名称';return;}status.style.color='#64736b';status.textContent='正在保存全部规则…';try{const result=await window.__collectorPick({items:chosen,save:true});status.style.color='#137657';status.textContent=result?.message||`已保存 ${chosen.length} 条规则`;chosen.splice(0);renderItems();setTimeout(()=>hidePanel(),1400);}catch(err){status.style.color='#b94040';status.textContent='保存失败：'+err;}};
})();
"""

STOP_PICKER_SCRIPT = r"""
(() => {
  if(window.__collectorStopPicker)return;window.__collectorStopPicker=true;
  const selector=el=>{if(el.id)return '#'+CSS.escape(el.id);let a=[];while(el&&el.nodeType===1&&el!==document.documentElement){let s=el.tagName.toLowerCase(),c=[...el.classList].filter(x=>!x.startsWith('collector-')).slice(0,2);if(c.length)s+='.'+c.map(CSS.escape).join('.');let p=el.parentElement;if(p&&p.querySelectorAll(':scope > '+s).length>1)s+=`:nth-of-type(${[...p.children].filter(x=>x.tagName===el.tagName).indexOf(el)+1})`;a.unshift(s);if(document.querySelectorAll(a.join(' > ')).length===1)break;el=p}return a.join(' > ')};
  const box=document.createElement('div'),tip=document.createElement('div');Object.assign(box.style,{position:'fixed',pointerEvents:'none',zIndex:'2147483647',border:'2px solid #e25b45',background:'#e25b4520'});Object.assign(tip.style,{position:'fixed',pointerEvents:'none',zIndex:'2147483647',background:'#7b271b',color:'#fff',padding:'7px 10px',borderRadius:'7px',font:'13px sans-serif'});document.documentElement.append(box,tip);
  document.addEventListener('mousemove',e=>{let r=e.target.getBoundingClientRect();Object.assign(box.style,{left:r.left+'px',top:r.top+'px',width:r.width+'px',height:r.height+'px'});tip.style.left=Math.max(4,r.left)+'px';tip.style.top=Math.max(4,r.top-36)+'px';tip.textContent='点击设为停止判断元素：'+selector(e.target)},true);
  document.addEventListener('click',async e=>{e.preventDefault();e.stopImmediatePropagation();let value={selector:selector(e.target),text:(e.target.innerText||e.target.textContent||'').trim().replace(/\s+/g,' ').slice(0,300)};tip.textContent='已选择，正在返回…';await window.__collectorStopPick(value);setTimeout(()=>window.close(),500)},true);
})();
"""

LIST_PICKER_SCRIPT = r"""
(() => {
  if(window.__collectorListPicker)return;window.__collectorListPicker=true;
  const selector=el=>{if(el.id)return '#'+CSS.escape(el.id);let a=[];while(el&&el.nodeType===1&&el!==document.documentElement){let s=el.tagName.toLowerCase(),c=[...el.classList].slice(0,2);if(c.length)s+='.'+c.map(CSS.escape).join('.');let p=el.parentElement;if(p&&p.querySelectorAll(':scope > '+s).length>1)s+=`:nth-of-type(${[...p.children].filter(x=>x.tagName===el.tagName).indexOf(el)+1})`;a.unshift(s);if(document.querySelectorAll(a.join(' > ')).length===1)break;el=p}return a.join(' > ')};
  const simple=el=>{if(el.id)return '#'+CSS.escape(el.id);let s=el.tagName.toLowerCase(),c=[...el.classList].slice(0,3);return s+(c.length?'.'+c.map(CSS.escape).join('.'):'')};
  const relative=(el,row)=>{let s=simple(el);if(row.querySelectorAll(s).length===1)return s;let a=[];for(let n=el;n&&n!==row;n=n.parentElement){let p=simple(n),parent=n.parentElement;if(parent&&parent.querySelectorAll(':scope > '+p).length>1)p+=`:nth-of-type(${[...parent.children].filter(x=>x.tagName===n.tagName).indexOf(n)+1})`;a.unshift(p);if(row.querySelectorAll(a.join(' > ')).length===1)break}return a.join(' > ')};
  const repeated=el=>{for(let node=el,depth=0;node&&node!==document.body&&depth<8;node=node.parentElement,depth++){if(node.id&&node.id.startsWith('normalthread_'))return "tbody[id^='normalthread_']";let classes=[...node.classList].filter(x=>!/^active|selected|hover$/.test(x)).slice(0,3),candidates=[];if(classes.length)candidates.push(node.tagName.toLowerCase()+'.'+classes.map(CSS.escape).join('.'),'.'+classes.map(CSS.escape).join('.'));if(['TR','LI','ARTICLE'].includes(node.tagName))candidates.push(node.tagName.toLowerCase());for(let candidate of candidates){let count=0;try{count=document.querySelectorAll(candidate).length}catch(_){}if(count>=2&&count<=500)return candidate}}return selector(el)};
  let rules={...(window.__collectorExistingListRules||{})},hasSaved=!!(rules.row_selector||rules.link_selector||rules.time_selector||rules.next_selector),step=hasSaved?'done':'row';const panel=document.createElement('div');Object.assign(panel.style,{position:'fixed',right:'12px',top:'12px',zIndex:'2147483647',width:'410px',background:'#10221b',color:'#fff',padding:'14px',borderRadius:'12px',boxShadow:'0 10px 35px #0006',font:'13px -apple-system,sans-serif'});panel.innerHTML=`<b style="font-size:15px">列表规则修正器</b><div class="lp-help" style="margin:9px 0;color:#b8ccc2">${hasSaved?'已载入当前保存的规则；点击下方对应按钮，只重选需要修正的项目。':'尚无完整规则，请从列表项开始依次选择。'}</div><div class="lp-data" style="background:#fff;color:#173027;padding:9px;border-radius:8px;word-break:break-all;line-height:1.6"></div><div style="display:grid;grid-template-columns:repeat(4,1fr);gap:5px;margin-top:8px"><button class="lp-row">修正列表项</button><button class="lp-link">修正详情入口</button><button class="lp-time">修正列表时间</button><button class="lp-next">修正翻页</button></div><button class="lp-save" disabled style="width:100%;margin-top:10px;padding:9px;border:0;border-radius:7px;background:#137657;color:#fff">保存修正后的规则</button>`;document.documentElement.append(panel);
  const help=panel.querySelector('.lp-help'),data=panel.querySelector('.lp-data'),save=panel.querySelector('.lp-save');const render=()=>{data.innerHTML=`列表项容器：${rules.row_selector||'未选择'}<br>详情入口：${rules.link_selector||'未选择'}<br>列表时间：${rules.time_selector||'未选择'}${rules.time_preview?'（示例：'+rules.time_preview+'）':''}<br>翻页方式：${rules.pagination_mode==='scroll'?'滚动到底加载':'下一页按钮'}<br>下一页/加载更多：${rules.next_selector||'滚动模式无需选择'}`;save.disabled=!(rules.row_selector&&rules.link_selector&&rules.time_selector&&(rules.pagination_mode==='scroll'||rules.next_selector))};render();
  panel.querySelector('.lp-row').onclick=()=>{step='row';help.textContent='请重新点击任意一个完整列表项的内部'};panel.querySelector('.lp-link').onclick=()=>{step='link';help.textContent='请重新点击列表项中的详情链接'};panel.querySelector('.lp-time').onclick=()=>{step='time';help.textContent='请点击列表项中代表发布时间的文字'};panel.querySelector('.lp-next').onclick=()=>{step='next';help.textContent='请重新点击“下一页”或“加载更多”链接/按钮'};
  document.addEventListener('click',e=>{if(panel.contains(e.target))return;e.preventDefault();e.stopImmediatePropagation();if(step==='row'){let discuz=e.target.closest("tbody[id^='normalthread_']");rules.row_selector=discuz?'tbody[id^="normalthread_"]':repeated(e.target);step='link';help.textContent=`已识别 ${document.querySelectorAll(rules.row_selector).length} 个重复列表项。第2步：点击其中的详情入口`}else if(step==='link'){let link=e.target.closest('a[href]');if(!link){help.textContent='这里不是链接，请点击列表项中可进入详情的标题或按钮';return}let row=link.closest(rules.row_selector);rules.link_selector=row?relative(link,row):simple(link);step='time';help.textContent='第3步：点击同一列表项中真正代表发布时间的文字'}else if(step==='time'){let row=e.target.closest(rules.row_selector);if(!row){help.textContent='请在已识别的列表项内部选择时间';return}rules.time_selector=relative(e.target,row);rules.time_preview=(e.target.innerText||e.target.textContent||'').trim().replace(/\s+/g,' ');step=rules.pagination_mode==='scroll'?'done':'next';help.textContent=rules.pagination_mode==='scroll'?'滚动加载规则已完成，请保存':'第4步：点击“下一页”或“加载更多”'}else if(step==='next'){let next=e.target.closest('a[href],button');if(!next){help.textContent='这里不是翻页链接/按钮，请点击真正的“下一页”或“加载更多”';return}rules.next_selector=selector(next);rules.pagination_mode='next';step='done';help.textContent=`识别完成：匹配 ${document.querySelectorAll(rules.row_selector).length} 个列表项，请确认保存`}render()},true);
  save.onclick=async()=>{save.disabled=true;save.textContent='正在保存…';let result=await window.__collectorListPick({...rules,save:true});save.textContent=result.message||'已保存';setTimeout(()=>window.close(),1000)};
})();
"""

AUTO_LIST_SCRIPT = r"""
() => {
  const css=s=>CSS.escape(s), simple=el=>{let c=[...el.classList].filter(Boolean).slice(0,2);return el.tagName.toLowerCase()+(c.length?'.'+c.map(css).join('.'):'')},relative=(el,row)=>{let s=simple(el);if(row.querySelectorAll(s).length===1)return s;let a=[];for(let n=el;n&&n!==row;n=n.parentElement){let p=simple(n),parent=n.parentElement;if(parent&&parent.querySelectorAll(':scope > '+p).length>1)p+=`:nth-of-type(${[...parent.children].filter(x=>x.tagName===n.tagName).indexOf(n)+1})`;a.unshift(p);if(row.querySelectorAll(a.join(' > ')).length===1)break}return a.join(' > ')};
  let rowSelector='';
  if(document.querySelectorAll("tbody[id^='normalthread_']").length>=2)rowSelector='tbody[id^="normalthread_"]';
  if(!rowSelector){let groups=new Map();document.querySelectorAll('article,tr,li,div').forEach(el=>{let key=simple(el);if(key==='div'||key==='li')return;let a=groups.get(key)||[];a.push(el);groups.set(key,a)});let best={score:-1,key:''};for(let [key,els] of groups){if(els.length<2||els.length>300)continue;let sample=els.slice(0,30),links=sample.filter(x=>x.querySelector('a[href]')).length,text=sample.reduce((n,x)=>n+(x.innerText||'').trim().length,0)/sample.length,time=sample.filter(x=>/昨天|小时前|分钟前|天前|\d{4}[-/]\d{1,2}/.test(x.innerText||'')).length;let score=els.length+links*4+time*5+Math.min(text,300)/30;if(text<15)score-=30;if(score>best.score)best={score,key}}rowSelector=best.key}
  if(!rowSelector)return {ok:false,message:'没有找到可靠的重复列表项容器'};
  let rows=[...document.querySelectorAll(rowSelector)],linkScores=new Map();rows.slice(0,50).forEach(row=>row.querySelectorAll('a[href]').forEach(a=>{let href=a.getAttribute('href')||'',text=(a.innerText||'').trim();if(!href||href.startsWith('javascript:')||href==='#'||text.length<2)return;let key=simple(a),score=(linkScores.get(key)||0)+1+Math.min(text.length,40)/20;if(a.classList.contains('xst'))score+=20;linkScores.set(key,score)}));let linkSelector=[...linkScores].sort((a,b)=>b[1]-a[1])[0]?.[0]||'a[href]';
  const timeRe=/(?:昨天|前天)(?:\s*\d{1,2}:\d{2})?|\d+\s*(?:秒|分钟|小时|天|周|个月|月|年)前|\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2})?/;let timeScores=new Map();rows.slice(0,50).forEach(row=>row.querySelectorAll('*').forEach(el=>{if(el.children.length)return;let text=(el.innerText||el.textContent||'').trim().replace(/\s+/g,' ');if(!text||text.length>40||!timeRe.test(text))return;let key=relative(el,row),old=timeScores.get(key)||{score:0,values:[]};old.score+=10-Math.min(text.length,30)/10;old.values.push(text);timeScores.set(key,old)}));let timeEntry=[...timeScores].sort((a,b)=>b[1].score-a[1].score)[0],timeSelector=timeEntry?.[0]||'',timeValues=timeEntry?.[1].values.slice(0,5)||[];
  let next=document.querySelector('a[rel="next"],a.nxt');if(!next)next=[...document.querySelectorAll('a[href],button')].find(x=>/^(下一页|下页|加载更多|更多)$/.test((x.innerText||'').trim()));let nextSelector=next?(next.matches('a.nxt')?'a.nxt':simple(next)):'';
  let previews=rows.slice(0,5).map(row=>{let a=row.querySelector(linkSelector),t=timeSelector?row.querySelector(timeSelector):null;return {text:(a?.innerText||row.innerText||'').trim().replace(/\s+/g,' ').slice(0,80),time:(t?.innerText||t?.textContent||'').trim().replace(/\s+/g,' '),href:a?.href||''}});
  return {ok:true,row_selector:rowSelector,link_selector:linkSelector,time_selector:timeSelector,time_values:timeValues,next_selector:nextSelector,pagination_mode:nextSelector?'next':'scroll',row_count:rows.length,previews};
}
"""


class PickerManager:
    def __init__(self, root, store):
        self.root, self.store = root, store
        self.sessions = {}

    def start(self, target_id, source, mode="detail", board_id=""):
        if target_id in self.sessions and self.sessions[target_id].get("state") == "running":
            return self.sessions[target_id]
        state = {"state":"starting","source":source,"mode":mode,"board_id":board_id,
                 "selected":None,"message":"正在打开规则拾取器","stop":False}
        self.sessions[target_id] = state
        threading.Thread(target=lambda: asyncio.run(self._run(target_id, source, mode, board_id, state)), daemon=True).start()
        return state

    def status(self, target_id):
        return self.sessions.get(target_id, {"state":"idle","selected":None,"message":"尚未打开"})

    def stop(self, target_id):
        state = self.sessions.get(target_id)
        if state: state["stop"] = True; state["message"] = "正在关闭"
        return state or {"state":"idle"}

    def activate(self, target_id):
        state = self.status(target_id)
        try:
            subprocess.run(
                ["osascript", "-e", 'tell application "Chromium" to activate'],
                check=False, timeout=5, capture_output=True)
            state["message"] = "已请求将 Chromium 窗口置于前台"
        except Exception as exc:
            state["message"] = f"窗口激活失败：{exc}"
        return state

    async def _run(self, target_id, source, mode, board_id, state):
        if sys.platform != "win32" and os.environ.get("DOCK_USE_VENDOR", "1") != "0":
            sys.path.insert(0, str(self.root / ".vendor"))
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(self.root / ".browsers"))
        from playwright.async_api import async_playwright
        target = self.store.target(target_id); rule = target["rule"]
        boards = rule.get("boards", [])
        board = next((x for x in boards if x.get("id") == board_id), None)
        if board_id == "_shared" and boards:
            board = next((x for x in boards if x.get("enabled", True)), boards[0])
        if board_id and board_id != "__picker__" and not board:
            state.update(state="error", message="选择的登录账号来源不存在"); return
        profile_id = ("_shared" if board_id == "_shared" else
                      account_profile_id(rule, board) if board else "")
        guest_profile = None
        if profile_id:
            profile = self.root / rule["folder"] / "浏览器数据" / profile_id
        else:
            guest_profile = tempfile.TemporaryDirectory(prefix="dock-picker-guest-")
            profile = Path(guest_profile.name)
        profile.mkdir(parents=True, exist_ok=True)
        proxy = await TargetRunner(self.root, self.store, target_id).resolve_proxy(
            board or {}, rule.get("proxy", {})) if board else None
        ctx = browser = None
        try:
            async with async_playwright() as pw:
                state["message"] = "正在启动内置 Chromium"
                if mode == "list_auto":
                    browser = await asyncio.wait_for(
                        pw.chromium.launch(headless=True, proxy=proxy,
                                           args=["--password-store=basic", "--use-mock-keychain"]),
                        timeout=15)
                    ctx = await browser.new_context(no_viewport=True)
                else:
                    try:
                        ctx = await asyncio.wait_for(
                            pw.chromium.launch_persistent_context(
                                str(profile), headless=False, no_viewport=True,
                                proxy=proxy,
                                args=["--new-window", "--window-position=30,50", "--window-size=1100,700",
                                      "--disable-background-mode"]),
                            timeout=15)
                    except asyncio.TimeoutError:
                        state["message"] = "账号目录启动超时；请先关闭预登录或采集浏览器后重试"
                        if profile_id:
                            raise RuntimeError(state["message"])
                        browser = await asyncio.wait_for(
                            pw.chromium.launch(headless=False, args=["--new-window", "--window-position=30,50",
                                                                    "--window-size=1100,700",
                                                                    "--disable-background-mode"]), timeout=15)
                        ctx = await browser.new_context(no_viewport=True)
                snapshot = profile / "登录状态.json"
                if snapshot.exists():
                    saved = json.loads(snapshot.read_text(encoding="utf-8"))
                    if saved.get("cookies"):
                        await ctx.add_cookies(saved["cookies"])
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                async def picked(_source, value):
                    items = value.get("items") or ([value] if value.get("name") else [])
                    if value.get("save") and items:
                        latest = self.store.target(target_id)
                        existing = latest["rule"].setdefault("fields", [])
                        used_names = {x.get("name") for x in existing}
                        saved = []
                        for item in items:
                            base = item["name"].strip() or "字段"
                            name, number = base, 2
                            while name in used_names:
                                name = f"{base}_{number}"; number += 1
                            used_names.add(name)
                            selector = item["selector"]
                            kind = item.get("kind", "css")
                            if (kind == "body_after_fields" or
                                    (re.fullmatch(r"#postmessage_\d+", selector) and base in {"内容", "正文", "帖子正文"})):
                                kind = "body_after_fields"
                                selector = "td.t_f[id^='postmessage_']"
                            field = {"name": name, "kind": kind, "selector": selector}
                            if item.get("attribute"): field["attribute"] = item["attribute"]
                            duplicate = next((x for x in existing if x.get("name") == field["name"] and x.get("selector") == field["selector"]), None)
                            if not duplicate: existing.append(field)
                            saved.append(field)
                        self.store.save_target(target_id, latest)
                        value["saved"] = True
                        value["saved_fields"] = saved
                        state["selected"] = value
                        state["message"] = f"已保存 {len(saved)} 条字段规则"
                        return {"ok": True, "message": state["message"]}
                    state["selected"] = value
                    state["message"] = "已选择元素，等待确认保存"
                    return {"ok": True}
                await page.expose_binding("__collectorPick", picked)
                async def list_picked(_source, value):
                    latest = self.store.target(target_id)
                    for key in ("row_selector", "link_selector", "time_selector", "next_selector", "pagination_mode"):
                        latest["rule"]["list"][key] = value[key]
                    self.store.save_target(target_id, latest)
                    value["saved"] = True; state["selected"] = value
                    state["message"] = "列表规则已保存"
                    return {"ok": True, "message": state["message"]}
                await page.expose_binding("__collectorListPick", list_picked)
                async def stop_picked(_source, value):
                    value["stop_rule"] = True
                    state["selected"] = value
                    state["message"] = "已选择停止条件元素"
                    return {"ok": True}
                await page.expose_binding("__collectorStopPick", stop_picked)
                async def install_picker():
                    try:
                        if mode == "list_auto": return
                        if mode == "list":
                            listing = self.store.target(target_id)["rule"].get("list", {})
                            await page.evaluate(
                                "rules => { window.__collectorExistingListRules = rules; }",
                                {key: listing.get(key, "") for key in
                                ("row_selector", "link_selector", "time_selector", "next_selector", "pagination_mode")})
                            await page.evaluate(LIST_PICKER_SCRIPT)
                        elif mode == "stop":
                            await page.evaluate(STOP_PICKER_SCRIPT)
                        else:
                            await page.evaluate(PICKER_SCRIPT)
                        state["panel_ready"] = True
                    except Exception as exc:
                        state["panel_ready"] = False
                        state["message"] = f"规则面板注入失败：{exc}"
                page.on("domcontentloaded", lambda: asyncio.create_task(install_picker()))
                page.on("pageerror", lambda exc: state.update(message=f"页面脚本错误：{exc}"))
                if source.startswith("sample:"):
                    name = Path(source.removeprefix("sample:")).name
                    sample = self.root / rule["folder"] / name
                    if not sample.exists():
                        raise FileNotFoundError(f"没有找到样例 {name}，请选择“目标网页地址”并填写链接")
                    destination = sample.resolve().as_uri()
                else:
                    if not source.startswith(("http://", "https://")):
                        raise ValueError("请填写以 http:// 或 https:// 开头的目标网页地址")
                    destination = source
                await page.goto(destination, wait_until="domcontentloaded")
                if mode == "list_auto":
                    result = await page.evaluate(AUTO_LIST_SCRIPT)
                    state["selected"] = result
                    state.update(state="completed", message="自动识别完成" if result.get("ok") else result.get("message", "自动识别失败"))
                    await ctx.close()
                    if browser: await browser.close()
                    return
                await install_picker()
                await page.bring_to_front()
                self.activate(target_id)
                state.update(state="running", message="依次选择帖子行、详情链接和下一页" if mode == "list" else "在浏览器页面中点击要保存的内容")
                while not state["stop"] and not page.is_closed(): await asyncio.sleep(.5)
                await ctx.close()
                if browser: await browser.close()
                state.update(state="closed", message="规则拾取器已关闭")
        except Exception as exc:
            state.update(state="error", message=str(exc))
        finally:
            if ctx:
                try: await ctx.close()
                except Exception: pass
            if browser:
                try: await browser.close()
                except Exception: pass
            if guest_profile:
                guest_profile.cleanup()
