import asyncio
import hashlib
import os
import csv
import json
import random
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .captcha_solver import get_captcha_solver, png_width
from .rules import account_profile_id, parse_detail_html


OPERATION_LOCK_SCRIPT = r"""
(() => {
  if (window.__collectorOperationLockInstalled) return;
  window.__collectorOperationLockInstalled = true;
  window.__collectorOperationLocked = true;
  const blocked = ['click','dblclick','auxclick','contextmenu','mousedown','mouseup',
    'pointerdown','pointerup','pointermove','touchstart','touchmove','touchend',
    'wheel','keydown','keypress','keyup','dragstart','drop','beforeinput','input'];
  const guard = event => {
    if (!window.__collectorOperationLocked || !event.isTrusted) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  };
  blocked.forEach(name => window.addEventListener(name, guard, {capture:true, passive:false}));
})();
"""


def _slide_trace(total):
    """生成模拟人类的滑块拖动轨迹：先加速后减速，带少量过冲回拉。"""
    trace, current, target = [], 0.0, total + min(total * 0.02, 5)
    while current < target:
        step = random.uniform(10, 32)
        current = min(current + step, target)
        trace.append(round(current, 1))
    if trace and trace[-1] != round(total, 1):
        trace.append(round(total, 1))
    return trace


class TargetRunner:
    def __init__(self, root, store, target_id):
        self.root, self.store, self.target_id = root, store, target_id
        self.stop_flag = threading.Event()
        self.pause_flag = threading.Event()
        self.pause_flag.set()
        self.status = {"state": "starting", "board": "", "url": "", "pages": 0,
                       "opened": 0, "saved": 0, "skipped": 0, "errors": 0,
                       "captcha": 0, "message": "正在启动"}
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=lambda: asyncio.run(self.run()), daemon=True)
        self.thread.start()

    def pause(self, message="已暂停"):
        self.pause_flag.clear(); self.status.update(state="paused", message=message)

    def resume(self):
        self.pause_flag.set(); self.status.update(state="running", message="继续采集")

    def stop(self):
        self.stop_flag.set(); self.pause_flag.set(); self.status.update(state="stopping", message="正在停止")

    async def wait_gate(self):
        while not self.pause_flag.is_set() and not self.stop_flag.is_set():
            await asyncio.sleep(1)

    async def captcha_present(self, page, cfg):
        body = await page.locator("body").inner_text(timeout=5000)
        if any(x and x in body for x in cfg.get("texts", [])):
            return True
        for sel in cfg.get("selectors", []):
            try:
                if await page.locator(sel).count(): return True
            except Exception: pass
        return False

    async def handle_captcha(self, page, rule):
        cfg = rule["captcha"]
        if not await self.captcha_present(page, cfg): return
        before = await self._captcha_snapshot(page, rule)
        self.status["captcha"] += 1
        browser_mode = rule.get("browser", {}).get("mode", "visible")
        if bool(cfg.get("auto", True)):
            await self.set_operation_lock(page, False)
            if await self._auto_solve(page, cfg, rule, before):
                await self.set_operation_lock(page, True)
                self.status.update(message="验证码已自动处理")
                return
        if browser_mode == "auto": await self.set_window_state(page, "normal")
        await self.set_operation_lock(page, False)
        message = ("检测到验证码；后台静默模式无法显示页面，请停止后切换运行模式"
                   if browser_mode == "silent" else "检测到验证码，请在浏览器中手动完成")
        self.pause(message)
        self.store.event(self.target_id, "warning", f"验证码暂停：{page.url}")
        if self.status["captcha"] >= int(rule["limits"].get("max_captcha", 3)):
            self.stop_flag.set(); self.pause_flag.set(); self.status["message"] = "达到验证码次数上限"; return
        while not self.stop_flag.is_set():
            await asyncio.sleep(2)
            try:
                verified, _, _ = await self._captcha_verification(page, rule, before)
            except Exception:
                verified = False  # 页面导航中，等待页面稳定后再判断
            if verified:
                await self.set_operation_lock(page, True)
                if browser_mode == "auto": await self.set_window_state(page, "minimized")
                self.resume(); return

    # ---------- ddddocr 自动打码 ----------

    async def _auto_solve(self, page, cfg, rule=None, before=None):
        """尝试用 ddddocr 自动完成验证码，成功返回 True，失败回退人工。"""
        try:
            solver = await asyncio.to_thread(get_captcha_solver)
            if not solver.available:
                self.store.event(self.target_id, "warning", f"自动打码不可用：{solver.error}")
                return False
            try:
                max_tries = max(1, int(cfg.get("max_auto_tries", 3)))
            except (TypeError, ValueError):
                max_tries = 3
            for attempt in range(1, max_tries + 1):
                if self.stop_flag.is_set(): return False
                self.status.update(message=f"正在自动识别验证码（{attempt}/{max_tries}）")
                try:
                    handled = await self._auto_solve_once(page, cfg, solver)
                except Exception as exc:
                    handled = False
                    self.store.event(self.target_id, "warning", f"自动打码异常：{exc}")
                if handled:
                    await asyncio.sleep(2)
                    verified, score, reasons = await self._captcha_verification(
                        page, rule or {"captcha": cfg}, before or {})
                    if verified:
                        detail = "、".join(reasons) or "验证组件消失"
                        self.store.event(self.target_id, "info",
                                         f"验证码自动打码成功（尝试 {attempt} 次，可信度 {score}：{detail}）")
                        return True
                await self._refresh_captcha(page, cfg)
                await asyncio.sleep(1)
            self.store.event(self.target_id, "warning", f"自动打码失败（{max_tries} 次），回退人工处理")
        except Exception as exc:
            self.store.event(self.target_id, "warning", f"自动打码加载失败：{exc}")
        return False

    async def _captcha_snapshot(self, page, rule):
        """记录提交前页面状态，供跨站点验证结果判定使用。"""
        cfg = rule.get("captcha", {})
        try:
            body = await page.locator("body").inner_text(timeout=5000)
        except Exception:
            body = ""
        return {
            "url": page.url,
            "body_length": len(body.strip()),
            "image_hash": await self._captcha_image_hash(page),
            "normal_selectors": self._normal_page_selectors(rule),
            "captcha_present": await self.captcha_present(page, cfg),
        }

    @staticmethod
    def _normal_page_selectors(rule):
        """复用采集规则形成正常页面特征，无需为每个网站重复填写成功文案。"""
        selectors = []
        row = rule.get("list", {}).get("row_selector", "").strip()
        if row:
            selectors.append(row)
        for field in rule.get("fields", []):
            selector = (field.get("selector") or "").strip()
            if selector and selector not in selectors:
                selectors.append(selector)
        return selectors[:20]

    async def _visible_selector_count(self, page, selectors):
        count = 0
        for selector in selectors:
            try:
                locator = page.locator(selector)
                if await locator.count() and await locator.first.is_visible():
                    count += 1
            except Exception:
                pass
        return count

    async def _captcha_image_hash(self, page):
        image = page.locator("img[src*='seccode'], img[src*='captcha'], img[id*='seccode']").first
        try:
            if await image.count() and await image.is_visible():
                return hashlib.sha256(await image.screenshot()).hexdigest()
        except Exception:
            pass
        return ""

    async def _captcha_verification(self, page, rule, before):
        """综合页面结构变化判断是否放行，返回 (成功, 可信度, 原因)。"""
        cfg = rule.get("captcha", {})
        present = await self.captcha_present(page, cfg)
        selectors = before.get("normal_selectors") or self._normal_page_selectors(rule)
        normal_count = await self._visible_selector_count(page, selectors)
        try:
            body = await page.locator("body").inner_text(timeout=5000)
        except Exception:
            body = ""

        score, reasons = 0, []
        if not present:
            score += 45; reasons.append("验证组件已消失")
        else:
            score -= 50
        if normal_count:
            score += 45; reasons.append(f"正常页面结构已恢复 {normal_count} 项")
        if before.get("url") and page.url != before["url"]:
            score += 15; reasons.append("页面地址已变化")
        old_length = int(before.get("body_length") or 0)
        if old_length and len(body.strip()) >= max(old_length + 200, int(old_length * 1.25)):
            score += 10; reasons.append("主要内容已恢复")

        # 有正常页面规则时要求结构恢复；没有可用规则时兼容原来的“组件消失”判定。
        if present:
            return False, score, reasons
        if selectors:
            return normal_count > 0 or score >= 70, score, reasons
        return True, score, reasons

    async def _auto_solve_once(self, page, cfg, solver):
        """单次自动打码：滑块优先，其次字符验证码。返回是否已操作。"""
        if await self._solve_slide_captcha(page, cfg, solver):
            return True
        if await self._solve_char_captcha(page, cfg, solver):
            return True
        return False

    async def _solve_slide_captcha(self, page, cfg, solver):
        """极验滑块：对比缺口图与完整背景图，计算位移后拖动滑块。"""
        gap = page.locator(".geetest_canvas canvas, .geetest_canvas_bg canvas").first
        full = page.locator(".geetest_canvas_bg canvas").first
        if not await gap.count() or not await full.count(): return False
        await page.wait_for_timeout(500)
        gap_bytes = await gap.screenshot()
        full_bytes = await full.screenshot()
        distance = await asyncio.to_thread(solver.solve_slide, gap_bytes, full_bytes)
        if not distance: return False
        slider = page.locator(".geetest_slider_button").first
        if not await slider.count():
            slider = page.locator(".geetest_slider").first
        if not await slider.count(): return False
        box = await slider.bounding_box()
        gap_box = await gap.bounding_box()
        if not box or not gap_box: return False
        img_w = png_width(gap_bytes)
        scale = gap_box["width"] / img_w if img_w else 1.0
        # 缺口中心换算到页面坐标，再减去滑块按钮初始中心，才是实际拖动距离
        distance_css = distance * scale
        target_center = gap_box["x"] + distance_css
        slider_center = box["x"] + box["width"] / 2
        drag = max(0.0, target_center - slider_center)
        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2
        await page.mouse.move(start_x, start_y)
        await page.mouse.down()
        try:
            await page.wait_for_timeout(random.randint(120, 300))
            for pos in _slide_trace(drag):
                await page.mouse.move(start_x + pos, start_y + random.uniform(-1.5, 1.5))
                await page.wait_for_timeout(random.randint(10, 35))
        finally:
            await page.mouse.up()
        return True

    async def _solve_char_captcha(self, page, cfg, solver):
        """Discuz 类字符验证码：识别 seccode 图片并填入输入框，随后提交表单。"""
        img = page.locator("img[src*='seccode'], img[src*='captcha'], img[id*='seccode']").first
        if not await img.count(): return False
        inp = page.locator("input[name*='seccode'], input[name*='captcha'], input[id*='seccode']").first
        if not await inp.count(): return False
        await page.wait_for_timeout(300)
        img_bytes = await img.screenshot()
        text = await asyncio.to_thread(solver.solve_best, img_bytes)
        if not text: return False
        await inp.fill(text)
        form = inp.locator("xpath=ancestor::form").first
        if await form.count():
            submit = form.locator("input[type=submit], button[type=submit]").last
            if await submit.count():
                await submit.click()
                return True
        await inp.press("Enter")
        return True

    async def _refresh_captcha(self, page, cfg):
        """刷新验证码图片并清空输入框，供下一轮自动打码使用。"""
        img = page.locator("img[src*='seccode']").first
        if await img.count():
            try:
                await img.click(timeout=3000)
            except Exception:
                try:
                    await img.evaluate("""el => {
                        const sep = el.src.includes('?') ? '&' : '?';
                        el.src = el.src.replace(/[?&]update=\\d+/, '') + sep + 'update=' + Date.now();
                    }""")
                except Exception:
                    pass
        inp = page.locator("input[name*='seccode'], input[name*='captcha']").first
        if await inp.count():
            try:
                await inp.fill("")
            except Exception:
                pass
        await page.wait_for_timeout(800)

    async def install_operation_lock(self, context, page):
        await context.add_init_script(script=OPERATION_LOCK_SCRIPT)
        await page.evaluate(OPERATION_LOCK_SCRIPT)

    async def set_operation_lock(self, page, locked):
        try:
            await page.evaluate("locked => { window.__collectorOperationLocked = locked; }", locked)
        except Exception:
            pass

    async def set_window_state(self, page, state):
        try:
            session = await page.context.new_cdp_session(page)
            info = await session.send("Browser.getWindowForTarget")
            await session.send("Browser.setWindowBounds", {
                "windowId": info["windowId"], "bounds": {"windowState": state}})
            await session.detach()
        except Exception:
            pass

    async def resolve_proxy(self, board, config):
        if board.get("proxy"):
            return {"server": board["proxy"]}
        mode = config.get("mode", "fixed" if config.get("server") else "direct")
        if mode == "direct": return None
        if mode == "fixed":
            return {k: config[k] for k in ("server", "username", "password") if config.get(k)}
        if mode != "api" or not config.get("api_url"):
            raise ValueError("API 代理模式缺少 API 地址")
        retries = max(1, int(config.get("api_retries", 2)) + 1)
        error = None
        for attempt in range(retries):
            try:
                self.status["message"] = f"正在从代理 API 获取地址（{attempt + 1}/{retries}）"
                server = await asyncio.to_thread(self._fetch_proxy_api, config)
                result = {"server": server}
                for key in ("username", "password"):
                    if config.get(key): result[key] = config[key]
                return result
            except Exception as exc:
                error = exc
                if attempt + 1 < retries: await asyncio.sleep(2)
        raise RuntimeError(f"代理 API 获取失败：{error}")

    @staticmethod
    def _fetch_proxy_api(config):
        method = config.get("api_method", "GET").upper()
        body = config.get("api_body", "").encode() if method == "POST" and config.get("api_body") else None
        headers = {"User-Agent": "DockCollector/1.0"}
        if config.get("api_header_name"):
            headers[config["api_header_name"]] = config.get("api_header_value", "")
        request = Request(config["api_url"], data=body, headers=headers, method=method)
        with urlopen(request, timeout=20) as response:
            text = response.read().decode("utf-8", errors="replace").strip()
        path = config.get("api_json_path", "").strip()
        value = text
        if path:
            value = json.loads(text)
            for part in path.split("."):
                value = value[int(part)] if isinstance(value, list) else value[part]
        elif text[:1] in "[{":
            parsed = json.loads(text)
            if isinstance(parsed, list): value = parsed[0]
            elif isinstance(parsed, dict): value = parsed.get("proxy") or parsed.get("data") or parsed
        if isinstance(value, list): value = value[0]
        if isinstance(value, dict):
            host = value.get("ip") or value.get("host") or value.get("server")
            value = f"{host}:{value['port']}" if host and value.get("port") else host
        value = str(value or "").strip().strip('"\'')
        if not value: raise ValueError("API 返回内容中没有代理地址")
        if "://" not in value: value = f"{config.get('api_scheme', 'http')}://{value}"
        return value

    async def run(self):
        if sys.platform != "win32" and os.environ.get("DOCK_USE_VENDOR", "1") != "0":
            sys.path.insert(0, str(self.root / ".vendor"))
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(self.root / ".browsers"))
        from playwright.async_api import async_playwright
        target = self.store.target(self.target_id); rule = target["rule"]
        profiles_root = self.root / rule["folder"] / "浏览器数据"
        profiles_root.mkdir(exist_ok=True)
        started = time.monotonic()
        try:
            async with async_playwright() as pw:
                self.status.update(state="running", message="正在采集")
                context = page = session_key = None
                try:
                    for board in rule.get("boards", []):
                        if self.stop_flag.is_set(): break
                        if not board.get("enabled", True): continue
                        proxy_arg = await self.resolve_proxy(board, rule.get("proxy", {}))
                        profile = profiles_root / account_profile_id(rule, board)
                        profile.mkdir(parents=True, exist_ok=True)
                        # 同一账号目录且代理完全一致时复用浏览器；代理变化时必须重启，防止出口串用。
                        next_key = (str(profile.resolve()), json.dumps(proxy_arg, sort_keys=True))
                        if context is None or next_key != session_key:
                            if context is not None:
                                await context.close()
                                context = page = session_key = None
                            browser_mode = rule.get("browser", {}).get("mode", "visible")
                            context = await pw.chromium.launch_persistent_context(
                                str(profile), headless=browser_mode == "silent",
                                proxy=proxy_arg, viewport={"width": 1280, "height": 850})
                            snapshot = profile / "登录状态.json"
                            if snapshot.exists():
                                try:
                                    saved = json.loads(snapshot.read_text(encoding="utf-8"))
                                    cookies = saved.get("cookies", [])
                                    if cookies: await context.add_cookies(cookies)
                                except Exception as exc:
                                    self.store.event(self.target_id, "warning", f"登录状态载入失败：{exc}")
                            page = context.pages[0] if context.pages else await context.new_page()
                            await self.install_operation_lock(context, page)
                            if browser_mode == "auto": await self.set_window_state(page, "minimized")
                            session_key = next_key
                        else:
                            self.status["message"] = f"复用统一账号浏览器：{board.get('name', '未命名来源')}"
                        await self.collect_board(page, board, rule, started)
                finally:
                    if context is not None:
                        await context.close()
            self.status.update(state="stopped" if self.stop_flag.is_set() else "completed",
                               message="已停止" if self.stop_flag.is_set() else "采集完成")
        except Exception as exc:
            self.status.update(state="error", message=str(exc), errors=self.status["errors"] + 1)
            self.store.event(self.target_id, "error", str(exc))

    async def collect_board(self, page, board, rule, started):
        url, empty = board["url"], 0
        limits, freq = rule["limits"], rule["frequency"]
        for page_no in range(1, int(limits["max_list_pages"]) + 1):
            if self.stop_flag.is_set(): return
            if (time.monotonic() - started) / 60 >= float(limits["max_minutes"]):
                self.stop_flag.set(); self.status["message"] = "达到最长运行时间"; return
            await self.wait_gate()
            self.status.update(board=board["name"], url=url, message=f"读取列表第 {page_no} 页")
            await page.goto(url, wait_until="domcontentloaded", timeout=int(freq["timeout_seconds"] * 1000))
            await self.handle_captcha(page, rule); await self.wait_gate()
            page_text = await page.locator("body").inner_text()
            if any(x in page_text for x in rule.get("stop", {}).get("page_contains", [])):
                self.status["message"] = "命中列表页结束条件"; return
            self.status["pages"] += 1
            rows = page.locator(rule["list"]["row_selector"])
            matched = []
            for i in range(await rows.count()):
                row = rows.nth(i); text = await row.inner_text()
                if rule["list"]["required_text"] not in text:
                    self.status["skipped"] += 1; continue
                link = row.locator(rule["list"]["link_selector"]).first
                if not await link.count(): continue
                matched.append((await link.inner_text(), urljoin(page.url, await link.get_attribute("href")),
                                next((x for x in text.split() if "昨天" in x), "昨天")))
            empty = empty + 1 if not matched else 0
            for title, detail_url, list_time in matched:
                if self.stop_flag.is_set() or self.status["opened"] >= int(limits["max_details"]): return
                if self.store.seen(self.target_id, detail_url):
                    self.status["skipped"] += 1; continue
                await asyncio.sleep(float(freq["detail_seconds"])); await self.wait_gate()
                self.status.update(url=detail_url, message=f"采集：{title}")
                await page.goto(detail_url, wait_until="domcontentloaded", timeout=int(freq["timeout_seconds"] * 1000))
                await self.handle_captcha(page, rule); await self.wait_gate()
                self.status["opened"] += 1
                html = await page.content(); data = parse_detail_html(html, rule, detail_url)
                if self.store.add_result(self.target_id, board["name"], detail_url, title, list_time, data):
                    self.status["saved"] += 1
                stop = rule.get("stop", {})
                if stop.get("detail_contains") and any(x in html for x in stop["detail_contains"]): return
                if stop.get("field_name") and stop.get("field_contains") in data.get(stop["field_name"], ""): return
                await page.go_back(wait_until="domcontentloaded")
            if empty >= int(limits["empty_pages"]): return
            nxt = page.locator(rule["list"]["next_selector"]).first
            if not await nxt.count(): return
            url = urljoin(page.url, await nxt.get_attribute("href"))
            await asyncio.sleep(float(freq["list_seconds"]))


class RunnerManager:
    def __init__(self, root, store): self.root, self.store, self.runners = root, store, {}
    def start(self, target_id):
        old = self.runners.get(target_id)
        if old and old.status["state"] not in ("completed", "stopped", "error"): return old.status
        runner = TargetRunner(self.root, self.store, target_id); self.runners[target_id] = runner; runner.start()
        return runner.status
    def action(self, target_id, action):
        r = self.runners.get(target_id)
        if not r: return {"state": "idle", "message": "任务尚未启动"}
        getattr(r, action)(); return r.status
    def status(self, target_id):
        r = self.runners.get(target_id); return r.status if r else {"state": "idle", "message": "尚未运行"}
