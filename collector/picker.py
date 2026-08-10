import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path


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
  function renderItems(){
    const root=panel.querySelector('.collector-items'); root.innerHTML='';
    chosen.forEach((item,index)=>{const row=document.createElement('div');Object.assign(row.style,{padding:'10px',border:'1px solid #dfe6e1',borderRadius:'8px',marginBottom:'8px'});row.innerHTML=`<div style="display:flex;justify-content:space-between;gap:8px"><b style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${safe(item.text||'(无文本)')}</b><button data-remove="${index}" style="border:0;background:transparent;color:#b94040">移除</button></div><code style="display:block;color:#137657;word-break:break-all;margin:6px 0">${safe(item.selector)}</code><input data-name="${index}" value="${safe(item.name||'')}" placeholder="字段名称，例如：详细地址" style="width:100%;box-sizing:border-box;padding:9px;border:1px solid #ccd7d1;border-radius:7px">`;root.append(row);});
    root.querySelectorAll('[data-remove]').forEach(btn=>btn.onclick=e=>{e.preventDefault();e.stopPropagation();chosen.splice(Number(btn.dataset.remove),1);renderItems();if(!chosen.length)hidePanel();});
  }
  document.addEventListener('mousemove',e=>{const el=e.target;if(el===box||el===tip||insidePanel(el))return;const r=el.getBoundingClientRect();Object.assign(box.style,{display:'block',left:r.left+'px',top:r.top+'px',width:r.width+'px',height:r.height+'px'});tip.style.display='block';tip.style.left=Math.max(4,Math.min(innerWidth-430,r.left))+'px';tip.style.top=Math.max(4,r.top-34)+'px';tip.textContent=selector(el)+' · 点击选择';},true);
  document.addEventListener('click',e=>{if(e.target===box||e.target===tip||insidePanel(e.target))return;e.preventDefault();e.stopImmediatePropagation();const el=e.target;const item={selector:selector(el),text:(el.innerText||el.textContent||'').trim().replace(/\s+/g,' ').slice(0,500),tag:el.tagName.toLowerCase(),attribute:''};if(!chosen.some(x=>x.selector===item.selector))chosen.push(item);renderItems();panel.querySelector('.collector-save-status').textContent=`已选择 ${chosen.length} 项，可继续点击页面元素`;showPanel();},true);
  panel.querySelector('.collector-hide').onclick=e=>{e.preventDefault();e.stopPropagation();hidePanel();};
  panel.querySelector('.collector-clear').onclick=e=>{e.preventDefault();e.stopPropagation();chosen.splice(0);renderItems();hidePanel();};
  window.__collectorSaveAll=async()=>{const status=panel.querySelector('.collector-save-status');panel.querySelectorAll('[data-name]').forEach(input=>chosen[Number(input.dataset.name)].name=input.value.trim());if(!chosen.length||chosen.some(x=>!x.name)){status.style.color='#b94040';status.textContent='请为每个选中元素填写字段名称';return;}status.style.color='#64736b';status.textContent='正在保存全部规则…';try{const result=await window.__collectorPick({items:chosen,save:true});status.style.color='#137657';status.textContent=result?.message||`已保存 ${chosen.length} 条规则`;chosen.splice(0);renderItems();setTimeout(()=>hidePanel(),1400);}catch(err){status.style.color='#b94040';status.textContent='保存失败：'+err;}};
})();
"""

LIST_PICKER_SCRIPT = r"""
(() => {
  if(window.__collectorListPicker)return;window.__collectorListPicker=true;
  const selector=el=>{if(el.id)return '#'+CSS.escape(el.id);let a=[];while(el&&el.nodeType===1&&el!==document.documentElement){let s=el.tagName.toLowerCase(),c=[...el.classList].slice(0,2);if(c.length)s+='.'+c.map(CSS.escape).join('.');let p=el.parentElement;if(p&&p.querySelectorAll(':scope > '+s).length>1)s+=`:nth-of-type(${[...p.children].filter(x=>x.tagName===el.tagName).indexOf(el)+1})`;a.unshift(s);if(document.querySelectorAll(a.join(' > ')).length===1)break;el=p}return a.join(' > ')};
  const simple=el=>{if(el.id)return '#'+CSS.escape(el.id);let s=el.tagName.toLowerCase(),c=[...el.classList].slice(0,3);return s+(c.length?'.'+c.map(CSS.escape).join('.'):'')};
  const repeated=el=>{for(let node=el,depth=0;node&&node!==document.body&&depth<8;node=node.parentElement,depth++){if(node.id&&node.id.startsWith('normalthread_'))return "tbody[id^='normalthread_']";let classes=[...node.classList].filter(x=>!/^active|selected|hover$/.test(x)).slice(0,3),candidates=[];if(classes.length)candidates.push(node.tagName.toLowerCase()+'.'+classes.map(CSS.escape).join('.'),'.'+classes.map(CSS.escape).join('.'));if(['TR','LI','ARTICLE'].includes(node.tagName))candidates.push(node.tagName.toLowerCase());for(let candidate of candidates){let count=0;try{count=document.querySelectorAll(candidate).length}catch(_){}if(count>=2&&count<=500)return candidate}}return selector(el)};
  let step='row',rules={};const panel=document.createElement('div');Object.assign(panel.style,{position:'fixed',right:'12px',top:'12px',zIndex:'2147483647',width:'390px',background:'#10221b',color:'#fff',padding:'14px',borderRadius:'12px',boxShadow:'0 10px 35px #0006',font:'13px -apple-system,sans-serif'});panel.innerHTML='<b style="font-size:15px">列表规则识别器</b><div class="lp-help" style="margin:9px 0;color:#b8ccc2">第1步：点击任意一个完整列表项的内部</div><div class="lp-data" style="background:#fff;color:#173027;padding:9px;border-radius:8px;word-break:break-all;line-height:1.6"></div><div style="display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-top:8px"><button class="lp-row">重选列表项</button><button class="lp-link">重选详情入口</button><button class="lp-next">重选翻页</button></div><button class="lp-save" disabled style="width:100%;margin-top:10px;padding:9px;border:0;border-radius:7px;background:#137657;color:#fff">确认并保存三个规则</button>';document.documentElement.append(panel);
  const help=panel.querySelector('.lp-help'),data=panel.querySelector('.lp-data'),save=panel.querySelector('.lp-save');const render=()=>{data.innerHTML=`列表项容器：${rules.row_selector||'未选择'}<br>详情入口：${rules.link_selector||'未选择'}<br>下一页/加载更多：${rules.next_selector||'未选择'}`;save.disabled=!(rules.row_selector&&rules.link_selector&&rules.next_selector)};render();
  panel.querySelector('.lp-row').onclick=()=>{step='row';help.textContent='请重新点击任意一个完整列表项的内部'};panel.querySelector('.lp-link').onclick=()=>{step='link';help.textContent='请重新点击列表项中的详情链接'};panel.querySelector('.lp-next').onclick=()=>{step='next';help.textContent='请重新点击“下一页”或“加载更多”链接/按钮'};
  document.addEventListener('click',e=>{if(panel.contains(e.target))return;e.preventDefault();e.stopImmediatePropagation();if(step==='row'){let discuz=e.target.closest("tbody[id^='normalthread_']");rules.row_selector=discuz?'tbody[id^="normalthread_"]':repeated(e.target);step='link';help.textContent=`已识别 ${document.querySelectorAll(rules.row_selector).length} 个重复列表项：${rules.row_selector}。第2步：点击其中的详情入口`}else if(step==='link'){let link=e.target.closest('a[href]');if(!link){help.textContent='这里不是链接，请点击列表项中可进入详情的标题或按钮';return}rules.link_selector=simple(link);step='next';help.textContent='第3步：点击“下一页”或“加载更多”'}else if(step==='next'){let next=e.target.closest('a[href],button');if(!next){help.textContent='这里不是翻页链接/按钮，请点击真正的“下一页”或“加载更多”';return}rules.next_selector=selector(next);step='done';help.textContent=`识别完成：匹配 ${document.querySelectorAll(rules.row_selector).length} 个列表项，请确认保存`}render()},true);
  save.onclick=async()=>{save.disabled=true;save.textContent='正在保存…';let result=await window.__collectorListPick({...rules,save:true});save.textContent=result.message||'已保存';setTimeout(()=>window.close(),1000)};
})();
"""

AUTO_LIST_SCRIPT = r"""
() => {
  const css=s=>CSS.escape(s), simple=el=>{let c=[...el.classList].filter(Boolean).slice(0,2);return el.tagName.toLowerCase()+(c.length?'.'+c.map(css).join('.'):'')};
  let rowSelector='';
  if(document.querySelectorAll("tbody[id^='normalthread_']").length>=2)rowSelector='tbody[id^="normalthread_"]';
  if(!rowSelector){let groups=new Map();document.querySelectorAll('article,tr,li,div').forEach(el=>{let key=simple(el);if(key==='div'||key==='li')return;let a=groups.get(key)||[];a.push(el);groups.set(key,a)});let best={score:-1,key:''};for(let [key,els] of groups){if(els.length<2||els.length>300)continue;let sample=els.slice(0,30),links=sample.filter(x=>x.querySelector('a[href]')).length,text=sample.reduce((n,x)=>n+(x.innerText||'').trim().length,0)/sample.length,time=sample.filter(x=>/昨天|小时前|分钟前|天前|\d{4}[-/]\d{1,2}/.test(x.innerText||'')).length;let score=els.length+links*4+time*5+Math.min(text,300)/30;if(text<15)score-=30;if(score>best.score)best={score,key}}rowSelector=best.key}
  if(!rowSelector)return {ok:false,message:'没有找到可靠的重复列表项容器'};
  let rows=[...document.querySelectorAll(rowSelector)],linkScores=new Map();rows.slice(0,50).forEach(row=>row.querySelectorAll('a[href]').forEach(a=>{let href=a.getAttribute('href')||'',text=(a.innerText||'').trim();if(!href||href.startsWith('javascript:')||href==='#'||text.length<2)return;let key=simple(a),score=(linkScores.get(key)||0)+1+Math.min(text.length,40)/20;if(a.classList.contains('xst'))score+=20;linkScores.set(key,score)}));let linkSelector=[...linkScores].sort((a,b)=>b[1]-a[1])[0]?.[0]||'a[href]';
  let next=document.querySelector('a[rel="next"],a.nxt');if(!next)next=[...document.querySelectorAll('a[href],button')].find(x=>/^(下一页|下页|加载更多|更多)$/.test((x.innerText||'').trim()));let nextSelector=next?(next.matches('a.nxt')?'a.nxt':simple(next)):'';
  let previews=rows.slice(0,5).map(row=>{let a=row.querySelector(linkSelector);return {text:(a?.innerText||row.innerText||'').trim().replace(/\s+/g,' ').slice(0,80),href:a?.href||''}});
  return {ok:true,row_selector:rowSelector,link_selector:linkSelector,next_selector:nextSelector,row_count:rows.length,previews};
}
"""


class PickerManager:
    def __init__(self, root, store):
        self.root, self.store = root, store
        self.sessions = {}

    def start(self, target_id, source, mode="detail"):
        if target_id in self.sessions and self.sessions[target_id].get("state") == "running":
            return self.sessions[target_id]
        state = {"state":"starting","source":source,"mode":mode,"selected":None,"message":"正在打开规则拾取器","stop":False}
        self.sessions[target_id] = state
        threading.Thread(target=lambda: asyncio.run(self._run(target_id, source, mode, state)), daemon=True).start()
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

    async def _run(self, target_id, source, mode, state):
        if sys.platform != "win32":
            sys.path.insert(0, str(self.root / ".vendor"))
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(self.root / ".browsers"))
        from playwright.async_api import async_playwright
        target = self.store.target(target_id); rule = target["rule"]
        # The picker has its own profile so it cannot be blocked by a running
        # collector context or a stale singleton lock in the collection profile.
        profile = self.root / rule["folder"] / "规则拾取器数据"
        profile.mkdir(parents=True, exist_ok=True)
        try:
            async with async_playwright() as pw:
                state["message"] = "正在启动内置 Chromium"
                browser = None
                if mode == "list_auto":
                    browser = await asyncio.wait_for(
                        pw.chromium.launch(headless=True, args=["--password-store=basic", "--use-mock-keychain"]),
                        timeout=15)
                    ctx = await browser.new_context(no_viewport=True)
                else:
                    try:
                        ctx = await asyncio.wait_for(
                            pw.chromium.launch_persistent_context(
                                str(profile), headless=False, no_viewport=True,
                                args=["--new-window", "--window-position=30,50", "--window-size=1100,700",
                                      "--disable-background-mode"]),
                            timeout=15)
                    except asyncio.TimeoutError:
                        state["message"] = "资料目录启动超时，正在使用临时窗口重试"
                        browser = await asyncio.wait_for(
                            pw.chromium.launch(headless=False, args=["--new-window", "--window-position=30,50",
                                                                    "--window-size=1100,700",
                                                                    "--disable-background-mode"]), timeout=15)
                        ctx = await browser.new_context(no_viewport=True)
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
                            field = {"name": name, "kind": "css", "selector": item["selector"]}
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
                    for key in ("row_selector", "link_selector", "next_selector"):
                        latest["rule"]["list"][key] = value[key]
                    self.store.save_target(target_id, latest)
                    value["saved"] = True; state["selected"] = value
                    state["message"] = "列表规则已保存"
                    return {"ok": True, "message": state["message"]}
                await page.expose_binding("__collectorListPick", list_picked)
                async def install_picker():
                    try:
                        if mode == "list_auto": return
                        await page.evaluate(LIST_PICKER_SCRIPT if mode == "list" else PICKER_SCRIPT)
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
