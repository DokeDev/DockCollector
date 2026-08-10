import asyncio
import json
import os
import sys
import threading
from pathlib import Path

from .runner import TargetRunner


LOGIN_TOOL_SCRIPT = r"""
(() => {
  if (window.top !== window) return;
  const mount=()=>{
  if (window.__collectorLoginTool || !document.documentElement) return;
  window.__collectorLoginTool = true;
  const host=document.createElement('div');
  Object.assign(host.style,{position:'fixed',right:'14px',top:'14px',zIndex:'2147483647'});
  const root=host.attachShadow({mode:'open'});
  root.innerHTML=`<style>*{box-sizing:border-box}button,textarea{font:13px -apple-system,BlinkMacSystemFont,sans-serif}.bar{width:340px;background:#10221b;color:#fff;border-radius:12px;box-shadow:0 10px 35px #0005;overflow:hidden}.head{display:flex;gap:8px;align-items:center;padding:10px;cursor:move;user-select:none}.head b{margin-right:auto}.head button{border:0;border-radius:7px;padding:8px 10px;cursor:pointer}.cookies{background:#fff;color:#15211b;padding:12px;display:none}.cookies.open{display:block}textarea{width:100%;height:150px;border:1px solid #ccd7d1;border-radius:7px;padding:8px;resize:vertical}.row{display:flex;gap:7px;margin-top:8px}.row button{flex:1;border:1px solid #ccd7d1;background:#fff;border-radius:7px;padding:8px;cursor:pointer}.row .primary,.finish{background:#137657;color:#fff;border-color:#137657}.status{font-size:12px;margin-top:8px;color:#52635a;word-break:break-all}</style><div class="bar"><div class="head"><b>预登录工具 · 可拖动</b><button class="cookie">Cookie 登录</button><button class="finish">完成登录</button></div><div class="cookies"><textarea placeholder="粘贴 Cookie JSON 数组或 Netscape Cookie 文本"></textarea><div class="row"><button class="primary import">导入 Cookie</button><button class="clear">清空输入</button></div><div class="status">Cookie 仅保存到当前目标的浏览器数据目录。</div></div></div>`;
  document.documentElement.appendChild(host);
  const panel=root.querySelector('.cookies'),status=root.querySelector('.status'),textarea=root.querySelector('textarea');
  const head=root.querySelector('.head');let drag=null;
  head.addEventListener('pointerdown',event=>{if(event.target.closest('button'))return;const rect=host.getBoundingClientRect();drag={dx:event.clientX-rect.left,dy:event.clientY-rect.top};host.style.right='auto';head.setPointerCapture(event.pointerId);event.preventDefault()});
  head.addEventListener('pointermove',event=>{if(!drag)return;let left=Math.max(0,Math.min(innerWidth-host.offsetWidth,event.clientX-drag.dx));let top=Math.max(0,Math.min(innerHeight-host.offsetHeight,event.clientY-drag.dy));host.style.left=left+'px';host.style.top=top+'px'});
  head.addEventListener('pointerup',event=>{drag=null;try{head.releasePointerCapture(event.pointerId)}catch(_){}});
  root.querySelector('.cookie').onclick=()=>panel.classList.toggle('open');
  root.querySelector('.clear').onclick=()=>{textarea.value='';status.textContent='已清空输入'};
  root.querySelector('.import').onclick=async()=>{if(!textarea.value.trim()){status.textContent='请先粘贴 Cookie';return}status.textContent='正在导入…';try{const result=await window.__collectorImportCookies(textarea.value);status.textContent=result.message;if(result.ok)location.reload()}catch(error){status.textContent='导入失败：'+error}};
  root.querySelector('.finish').onclick=async()=>{root.querySelector('.finish').textContent='正在保存…';await window.__collectorFinishLogin()};
  };
  if(document.documentElement)mount();else document.addEventListener('DOMContentLoaded',mount,{once:true});
})();
"""


class LoginManager:
    def __init__(self, root, store, runner_manager):
        self.root, self.store, self.runner_manager = root, store, runner_manager
        self.sessions = {}

    def start(self, target_id, board_id):
        running = self.runner_manager.status(target_id).get("state")
        if running in ("starting", "running", "paused", "stopping"):
            return {"state": "error", "message": "请先停止当前采集任务"}
        target = self.store.target(target_id)
        board = next((x for x in target["rule"].get("boards", []) if x.get("id") == board_id), None) if target else None
        if not board:
            return {"state": "error", "message": "请选择有效的页面来源"}
        key = (target_id, board_id)
        old = self.sessions.get(key)
        if old and old.get("state") in ("starting", "running"):
            return old
        state = {"state": "starting", "message": "正在打开预登录浏览器", "stop": False}
        state["board_id"] = board_id
        self.sessions[key] = state
        threading.Thread(target=lambda: asyncio.run(self._run(target_id, board_id, state)), daemon=True).start()
        return state

    def stop(self, target_id, board_id=""):
        state = self.sessions.get((target_id, board_id))
        if not state and not board_id:
            states = [v for (tid, _), v in self.sessions.items() if tid == target_id]
            state = states[0] if states else None
        if state:
            state["stop"] = True
            state["message"] = "正在保存登录状态并关闭浏览器"
        return state or {"state": "idle", "message": "预登录浏览器未打开"}

    def status(self, target_id, board_id=""):
        if board_id: return self.sessions.get((target_id, board_id), {"state": "idle", "message": "尚未预登录"})
        active = [v for (tid, _), v in self.sessions.items() if tid == target_id and v.get("state") in ("starting", "running")]
        return active[0] if active else {"state": "idle", "message": "尚未预登录"}

    async def _run(self, target_id, board_id, state):
        if sys.platform != "win32":
            sys.path.insert(0, str(self.root / ".vendor"))
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(self.root / ".browsers"))
        from playwright.async_api import async_playwright
        target = self.store.target(target_id)
        if not target:
            state.update(state="error", message="目标不存在"); return
        rule = target["rule"]
        board = next((x for x in rule.get("boards", []) if x.get("id") == board_id), None)
        if not board:
            state.update(state="error", message="页面来源不存在"); return
        profile = self.root / rule["folder"] / "浏览器数据" / board_id
        profile.mkdir(parents=True, exist_ok=True)
        context = None
        try:
            resolver = TargetRunner(self.root, self.store, target_id)
            proxy = await resolver.resolve_proxy(board, rule.get("proxy", {}))
            async with async_playwright() as pw:
                context = await pw.chromium.launch_persistent_context(
                    str(profile), headless=False, proxy=proxy,
                    viewport={"width": 1280, "height": 850})
                async def finish_login(_source):
                    state["stop"] = True
                    state["message"] = "正在保存登录状态并关闭浏览器"
                    return {"ok": True}
                async def import_cookies(_source, raw):
                    try:
                        cookies = self.parse_cookies(raw)
                        await context.add_cookies(cookies)
                        state["message"] = f"已导入 {len(cookies)} 个 Cookie"
                        return {"ok": True, "message": state["message"]}
                    except Exception as exc:
                        return {"ok": False, "message": f"Cookie 格式错误：{exc}"}
                await context.expose_binding("__collectorFinishLogin", finish_login)
                await context.expose_binding("__collectorImportCookies", import_cookies)
                await context.add_init_script(script=LOGIN_TOOL_SCRIPT)
                page = context.pages[0] if context.pages else await context.new_page()
                for existing_page in context.pages:
                    try: await existing_page.evaluate(LOGIN_TOOL_SCRIPT)
                    except Exception: pass
                destination = board.get("url") or ("https://" + rule["domain"] if rule.get("domain") else "about:blank")
                if destination != "about:blank":
                    await page.goto(destination, wait_until="domcontentloaded", timeout=60000)
                state.update(state="running", message="请在浏览器中完成登录，完成后点击“完成登录”")
                while not state["stop"]:
                    if not context.pages: break
                    await asyncio.sleep(1)
                cookies = await context.cookies()
                snapshot = profile / "登录状态.json"
                snapshot.write_text(json.dumps({"cookies": cookies}, ensure_ascii=False), encoding="utf-8")
                snapshot.chmod(0o600)
                await context.close(); context = None
            state.update(state="completed", message=f"登录状态已保存（{len(cookies)} 个 Cookie）")
        except Exception as exc:
            state.update(state="error", message=f"预登录失败：{exc}")
        finally:
            if context:
                try: await context.close()
                except Exception: pass

    @staticmethod
    def parse_cookies(raw):
        raw = raw.strip()
        if raw[:1] in "[{":
            value = json.loads(raw)
            items = value.get("cookies", []) if isinstance(value, dict) else value
            if not isinstance(items, list): raise ValueError("JSON 必须是 Cookie 数组")
            cookies = []
            for item in items:
                cookie = {k: item[k] for k in ("name", "value", "domain", "path", "httpOnly", "secure") if k in item}
                if "expirationDate" in item: cookie["expires"] = float(item["expirationDate"])
                elif "expires" in item and isinstance(item["expires"], (int, float)): cookie["expires"] = float(item["expires"])
                same_site = str(item.get("sameSite", "")).lower()
                if same_site in ("strict", "lax", "none"): cookie["sameSite"] = same_site.title()
                if not cookie.get("domain") and item.get("url"): cookie["url"] = item["url"]
                cookie.setdefault("path", "/")
                cookies.append(cookie)
        else:
            cookies = []
            for line in raw.splitlines():
                line = line.strip()
                if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")): continue
                parts = line.split("\t")
                if len(parts) < 7: raise ValueError("Netscape 每行需要7列")
                domain = parts[0].removeprefix("#HttpOnly_")
                cookie = {"domain": domain, "path": parts[2] or "/", "secure": parts[3].upper() == "TRUE",
                          "name": parts[5], "value": parts[6]}
                if parts[4].isdigit() and int(parts[4]) > 0: cookie["expires"] = float(parts[4])
                if line.startswith("#HttpOnly_"): cookie["httpOnly"] = True
                cookies.append(cookie)
        if not cookies: raise ValueError("没有识别到 Cookie")
        for cookie in cookies:
            if not cookie.get("name") or "value" not in cookie: raise ValueError("Cookie 缺少 name 或 value")
            if not cookie.get("domain") and not cookie.get("url"): raise ValueError(f"Cookie {cookie['name']} 缺少 domain 或 url")
        return cookies
