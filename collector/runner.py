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
from .challenge_signals import CHALLENGE_SELECTORS, CHALLENGE_TEXTS, LOGIN_TEXTS
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
        self.condition_completed = False
        self.status = {"state": "starting", "board": "", "url": "", "pages": 0,
                       "opened": 0, "saved": 0, "skipped": 0, "errors": 0,
                       "captcha": 0, "message": "正在启动"}
        self.thread = None
        self.detail_baseline = []
        self.loop = None
        self.active_context = None
        self.active_browser = None
        self.active_page = None
        self.manual_intervention = False
        self.failure_message = ""
        self.consecutive_captcha_failures = 0

    def start(self):
        self.thread = threading.Thread(target=lambda: asyncio.run(self.run()), daemon=True)
        self.thread.start()

    def pause(self, message="已暂停"):
        self.pause_flag.clear(); self.status.update(state="paused", message=message)

    def resume(self):
        if self.manual_intervention:
            self.status.update(state="paused", message="等待手动完成验证码")
            if self.loop and self.loop.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(self._restore_manual_page(), self.loop)
                except Exception:
                    pass
            return
        self.pause_flag.set(); self.status.update(state="running", message="继续采集")

    async def _restore_manual_page(self):
        page = self.active_page
        if page is None or page.is_closed():
            self.failure_message = "验证浏览器已关闭，请重新开始任务"
            self.stop_flag.set(); self.pause_flag.set()
            self.status.update(state="error", message=self.failure_message)
            return
        await self.set_operation_lock(page, False)
        try:
            pages = [candidate for candidate in page.context.pages if not candidate.is_closed()]
            # 验证组件可能打开新标签页/弹窗，优先唤醒最后创建的页面。
            manual_page = pages[-1] if pages else page
            await self.set_window_state(manual_page, "normal")
            await manual_page.bring_to_front()
            self.status.update(state="paused", message="验证窗口已唤醒，请在独立浏览器中完成验证码")
        except Exception:
            pass

    def stop(self):
        self.stop_flag.set(); self.pause_flag.set(); self.status.update(state="stopping", message="正在停止")
        # 主动关闭当前 Chromium，不等待页面超时或访问间隔结束。
        if self.loop and self.loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self._close_active_browser(), self.loop)
            except Exception:
                pass

    async def _close_active_browser(self):
        context, browser = self.active_context, self.active_browser
        self.active_context = self.active_browser = None
        self.active_page = None
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass

    async def wait_gate(self):
        while not self.pause_flag.is_set() and not self.stop_flag.is_set():
            await asyncio.sleep(1)

    @staticmethod
    def match_values(actual, item, present=True):
        """按 OR/AND 组合多个判断值，并兼容旧版单 value 规则。"""
        operator = item.get("operator", "contains")
        if operator == "exists": return present
        if operator == "missing": return not present
        if operator == "empty": return present and not actual.strip()
        values = item.get("values")
        if not isinstance(values, list): values = [item.get("value", "")]
        values = [str(value) for value in values if str(value)]
        if not values: return False
        checks = {"contains": lambda value: value in actual,
                  "equals": lambda value: actual == value,
                  "not_contains": lambda value: value not in actual}
        check = checks.get(operator)
        if not check: return False
        hits = [check(value) for value in values]
        return all(hits) if item.get("logic", "or").lower() == "and" else any(hits)

    async def matched_stop_rule(self, page, data, rule, phase):
        for item in rule.get("stop", {}).get("rules", []):
            if not item.get("enabled", True) or item.get("phase", "detail") != phase:
                continue
            kind, present, actual = item.get("kind", "field"), True, ""
            if kind == "field":
                field_name = item.get("field", "")
                present = field_name in (data or {})
                actual = str((data or {}).get(field_name, ""))
            elif kind == "page_text":
                actual = await page.locator("body").inner_text()
            elif kind == "element":
                locator = page.locator(item.get("selector", "")).first
                present = bool(item.get("selector")) and await locator.count() > 0
                actual = (await locator.inner_text()).strip() if present else ""
            matched = self.match_values(actual, item, present)
            if matched:
                return item
        return None

    def stop_by_rule(self, item):
        scope = item.get("scope", "board")
        label = item.get("name") or item.get("field") or item.get("selector") or "停止条件"
        self.status["message"] = f"命中停止条件：{label}"
        if scope == "target":
            self.condition_completed = True
            self.stop_flag.set()
        return True

    async def detail_structure_present(self, page, rule):
        fields = rule.get("fields", [])
        if not fields:
            return True
        body_text = None
        for field in fields:
            kind = field.get("kind", "css")
            selector = (field.get("selector") if kind in {"css", "body_after_fields"}
                        else field.get("root") if kind == "discuz_showhide" else "")
            if selector:
                try:
                    if await page.locator(selector).count():
                        return True
                except Exception:
                    pass
            label = field.get("label")
            if label:
                body_text = body_text if body_text is not None else await page.locator("body").inner_text()
                if label in body_text:
                    return True
        return False

    async def ensure_detail_structure(self, page, rule):
        assessment = await self.assess_detail_page(page, rule)
        if assessment["classification"] == "normal":
            self.detail_baseline.append(assessment["fingerprint"])
            self.detail_baseline = self.detail_baseline[-20:]
            return True
        # 动态验证组件可能稍晚出现；稳定后复核一次。
        await page.wait_for_timeout(1200)
        assessment = await self.assess_detail_page(page, rule)
        if assessment["classification"] not in {"challenge", "login"}:
            reasons = "、".join(assessment["reasons"])
            self.store.event(self.target_id, "warning",
                             f"详情页异常（评分 {assessment['score']}），已跳过且未保存：{reasons}；{page.url}")
            self.status["skipped"] += 1
            return False
        if rule.get("browser", {}).get("mode") == "silent":
            self.stop_flag.set()
            self.status["message"] = "验证页未恢复，静默模式无法人工处理"
            return False
        await self.set_operation_lock(page, False)
        message = ("检测到登录状态失效，请在浏览器中重新登录" if assessment["classification"] == "login"
                   else "验证后详情结构仍未恢复，请在浏览器中完成人机验证")
        self.pause(message)
        while not self.stop_flag.is_set():
            await asyncio.sleep(2)
            if await self.detail_structure_present(page, rule):
                await self.set_operation_lock(page, True)
                self.resume()
                return True
        return False

    async def assess_detail_page(self, page, rule):
        """用正常结构、历史正常页和验证信号进行异常评分。"""
        try:
            body = await page.locator("body").inner_text(timeout=5000)
            title = await page.title()
        except Exception:
            body, title = "", ""
        folded = body.casefold()
        custom_texts = tuple(rule.get("captcha", {}).get("texts", []))
        challenge_texts = list(dict.fromkeys(
            x for x in (*CHALLENGE_TEXTS, *custom_texts) if x and x.casefold() in folded))
        selectors = tuple(dict.fromkeys((*CHALLENGE_SELECTORS,
                                         *rule.get("captcha", {}).get("selectors", []))))
        challenge_nodes = await self._visible_selector_count(page, selectors)
        normal_selectors = self._normal_page_selectors(rule)
        normal_count = await self._visible_selector_count(page, normal_selectors)
        structure = await self.detail_structure_present(page, rule)
        length = len(body.strip())
        score, reasons = 0, []
        if not structure:
            score += 5; reasons.append("目标字段结构不存在")
        if normal_selectors and not normal_count:
            score += 3; reasons.append("正常页面选择器全部缺失")
        if challenge_nodes:
            score += 6; reasons.append(f"发现验证组件 {challenge_nodes} 项")
        if challenge_texts:
            score += min(6, 2 + len(challenge_texts)); reasons.append("验证文案：" + "、".join(challenge_texts[:3]))
        login_texts = [x for x in LOGIN_TEXTS if x.casefold() in folded]
        if login_texts:
            score += 5; reasons.append("登录提示：" + "、".join(login_texts[:2]))
        if self.detail_baseline:
            lengths = sorted(x["body_length"] for x in self.detail_baseline if x["body_length"])
            median = lengths[len(lengths) // 2] if lengths else 0
            if median and length < median * .35:
                score += 3; reasons.append(f"正文长度仅为正常页约 {round(length / median * 100)}%")
            usual = max(x["normal_count"] for x in self.detail_baseline)
            if usual and normal_count < usual * .4:
                score += 3; reasons.append("正常结构命中数显著下降")
        fingerprint = {"body_length": length, "title_length": len(title),
                       "normal_count": normal_count, "url": page.url}
        if login_texts and not structure:
            classification = "login"
        elif (challenge_nodes or challenge_texts) and not structure and score >= 7:
            classification = "challenge"
        elif structure:
            classification = "normal"
        else:
            classification = "anomaly"
        return {"score": score, "classification": classification,
                "reasons": reasons or ["页面结构正常"], "fingerprint": fingerprint}

    async def captcha_present(self, page, cfg):
        # 部分站点在 DOMContentLoaded 之后才用脚本写入验证区域。
        await page.wait_for_timeout(800)
        body = await page.locator("body").inner_text(timeout=5000)
        if any(x and x.casefold() in body.casefold() for x in (*CHALLENGE_TEXTS, *cfg.get("texts", []))):
            return True
        for sel in (*CHALLENGE_SELECTORS, *cfg.get("selectors", [])):
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
                self.consecutive_captcha_failures = 0
                await self.set_operation_lock(page, True)
                self.status.update(message="验证码已自动处理")
                return
        if browser_mode == "auto": await self.set_window_state(page, "normal")
        await self.set_operation_lock(page, False)
        message = ("检测到验证码；后台静默模式无法显示页面，请停止后切换运行模式"
                   if browser_mode == "silent" else "检测到验证码，请在浏览器中手动完成")
        self.pause(message)
        self.manual_intervention = True
        self.store.event(self.target_id, "warning", f"验证码暂停：{page.url}")
        # 自动打码失败后必须先给人工处理机会，不能按验证码检测总次数停止。
        # 人工提交错误通常会刷新验证码图片，以图片变化记录一次“未通过”。
        manual_image_hash = await self._captcha_image_hash(page)
        while not self.stop_flag.is_set():
            await asyncio.sleep(2)
            if page.is_closed():
                self.manual_intervention = False
                self.failure_message = "验证浏览器已关闭，请重新开始任务"
                self.stop_flag.set(); self.pause_flag.set()
                self.status.update(state="error", message=self.failure_message)
                self.store.event(self.target_id, "error", self.failure_message)
                return
            try:
                # 验证组件可能在暂停后才创建或重载 iframe。
                await self.set_operation_lock(page, False)
                verified, _, _ = await self._captcha_verification(page, rule, before)
            except Exception:
                verified = False  # 页面导航中，等待页面稳定后再判断
            if verified:
                self.manual_intervention = False
                self.consecutive_captcha_failures = 0
                await self.set_operation_lock(page, True)
                if browser_mode == "auto": await self.set_window_state(page, "minimized")
                self.resume(); return
            current_hash = await self._captcha_image_hash(page)
            if manual_image_hash and current_hash and current_hash != manual_image_hash:
                manual_image_hash = current_hash
                self.consecutive_captcha_failures += 1
                limit = max(1, int(rule["limits"].get("max_captcha", 3)))
                self.store.event(
                    self.target_id, "warning",
                    f"验证码未通过（连续 {self.consecutive_captcha_failures}/{limit} 次）")
                if self.consecutive_captcha_failures >= limit:
                    self.manual_intervention = False
                    self.stop_flag.set(); self.pause_flag.set()
                    self.status["message"] = f"连续 {limit} 次验证码未通过，任务已停止"
                    return

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
        img = page.locator(
            "img[src*='seccode']:visible, img[src*='captcha']:visible, "
            "img[id*='seccode']:visible, img[id*='captcha']:visible").first
        if not await img.count():
            self.store.event(self.target_id, "warning", "自动打码未找到可见验证码图片")
            return False
        inp = await self._captcha_input(page, img)
        if inp is None:
            self.store.event(self.target_id, "warning", "验证码图片已找到，但未找到可见且可编辑的输入框")
            return False
        await page.wait_for_timeout(300)
        img_bytes = await img.screenshot()
        text = await asyncio.to_thread(solver.solve_best, img_bytes)
        if not text:
            self.store.event(self.target_id, "warning", "验证码图片已获取，但 OCR 未识别出内容")
            return False
        await inp.fill(text, timeout=5000)
        form = inp.locator("xpath=ancestor::form").first
        if await form.count():
            submit = form.locator("input[type=submit]:visible, button[type=submit]:visible").last
            if await submit.count():
                await submit.click()
                return True
        # 某些验证框不是独立 form，提交按钮位于同一弹层或父容器中。
        container = inp.locator("xpath=ancestor::*[self::div or self::td][1]")
        submit = container.locator(
            "button:visible, input[type=submit]:visible, a:visible"
        ).filter(has_text="提交").first
        if await submit.count():
            await submit.click()
            return True
        await inp.press("Enter")
        return True

    async def _captcha_input(self, page, image):
        """返回可见、可编辑的验证码输入框，排除 seccodehash 等隐藏状态字段。"""
        selectors = (
            "input[name*='seccode']:not([type=hidden]):visible",
            "input[id*='seccode']:not([type=hidden]):visible",
            "input[name*='captcha']:not([type=hidden]):visible",
            "input[id*='captcha']:not([type=hidden]):visible",
            "input[name*='verify']:not([type=hidden]):visible",
            "input[id*='verify']:not([type=hidden]):visible",
        )
        for selector in selectors:
            locator = page.locator(selector)
            for index in range(await locator.count()):
                candidate = locator.nth(index)
                try:
                    if await candidate.is_editable():
                        return candidate
                except Exception:
                    pass
        # 兼容无 name/id 的输入框：只在验证码图片所属表单内查找，避免选中登录或搜索框。
        form = image.locator("xpath=ancestor::form").first
        if await form.count():
            locator = form.locator("input[type=text]:visible, input:not([type]):visible")
            for index in range(await locator.count()):
                candidate = locator.nth(index)
                try:
                    if await candidate.is_editable():
                        return candidate
                except Exception:
                    pass
        return None

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
        inp = page.locator(
            "input[name*='seccode']:not([type=hidden]):visible, "
            "input[name*='captcha']:not([type=hidden]):visible, "
            "input[name*='verify']:not([type=hidden]):visible").first
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
        # 验证码可能位于 iframe、新标签页或弹窗。人工接管时必须同步
        # 当前独立浏览器上下文中的全部页面与框架，不能只解锁采集页。
        try:
            pages = list(page.context.pages)
        except Exception:
            pages = [page]
        for candidate in pages:
            if candidate.is_closed():
                continue
            for frame in candidate.frames:
                try:
                    await frame.evaluate(
                        "locked => { window.__collectorOperationLocked = locked; }", locked)
                except Exception:
                    # 导航期间页面/框架可能被替换，人工等待循环会再次同步。
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
        self.loop = asyncio.get_running_loop()
        if sys.platform != "win32" and os.environ.get("DOCK_USE_VENDOR", "1") != "0":
            sys.path.insert(0, str(self.root / ".vendor"))
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(self.root / ".browsers"))
        from playwright.async_api import async_playwright
        target = self.store.target(self.target_id); rule = target["rule"]
        checkpoint = self.store.checkpoint(self.target_id)
        resume_board_id = checkpoint.get("board_id") if checkpoint else None
        resume_reached = not bool(resume_board_id)
        profiles_root = self.root / rule["folder"] / "浏览器数据"
        profiles_root.mkdir(exist_ok=True)
        started = time.monotonic()
        try:
            async with async_playwright() as pw:
                self.status.update(state="running", message="正在采集")
                browser = context = page = session_key = None
                try:
                    for board in rule.get("boards", []):
                        if self.stop_flag.is_set(): break
                        if not board.get("enabled", True): continue
                        if not resume_reached:
                            if board.get("id") != resume_board_id:
                                continue
                            resume_reached = True
                        proxy_arg = await self.resolve_proxy(board, rule.get("proxy", {}))
                        profile = profiles_root / account_profile_id(rule, board)
                        profile.mkdir(parents=True, exist_ok=True)
                        # 同一账号目录且代理完全一致时复用浏览器；代理变化时必须重启，防止出口串用。
                        next_key = (str(profile.resolve()), json.dumps(proxy_arg, sort_keys=True))
                        if context is None or next_key != session_key:
                            if context is not None:
                                await context.close()
                                if browser is not None:
                                    await browser.close()
                                browser = context = page = session_key = None
                            browser_mode = rule.get("browser", {}).get("mode", "visible")
                            if browser_mode == "silent":
                                # 不打开持久化账号目录，避免 macOS 后台进程读取
                                # Chromium Safe Storage 并弹出系统钥匙串授权框。
                                browser = await pw.chromium.launch(
                                    headless=True, proxy=proxy_arg,
                                    args=["--password-store=basic", "--use-mock-keychain"])
                                context = await browser.new_context(
                                    viewport={"width": 1280, "height": 850})
                            else:
                                context = await pw.chromium.launch_persistent_context(
                                    str(profile), headless=False, proxy=proxy_arg,
                                    viewport={"width": 1280, "height": 850})
                            self.active_context, self.active_browser = context, browser
                            snapshot = profile / "登录状态.json"
                            if snapshot.exists():
                                try:
                                    saved = json.loads(snapshot.read_text(encoding="utf-8"))
                                    cookies = saved.get("cookies", [])
                                    if cookies: await context.add_cookies(cookies)
                                except Exception as exc:
                                    self.store.event(self.target_id, "warning", f"登录状态载入失败：{exc}")
                            page = context.pages[0] if context.pages else await context.new_page()
                            self.active_page = page
                            await self.install_operation_lock(context, page)
                            if browser_mode == "auto": await self.set_window_state(page, "minimized")
                            session_key = next_key
                        else:
                            self.status["message"] = f"复用统一账号浏览器：{board.get('name', '未命名来源')}"
                        board_checkpoint = checkpoint if checkpoint and board.get("id") == resume_board_id else None
                        if board_checkpoint:
                            self.status["message"] = (f"从断点继续：{board.get('name', '未命名')} · "
                                                      f"第 {board_checkpoint.get('page_no', 1)} 页")
                        await self.collect_board(page, board, rule, started, board_checkpoint)
                        checkpoint = None
                finally:
                    await self._close_active_browser()
            if self.failure_message:
                self.status.update(state="error", message=self.failure_message)
            elif self.condition_completed:
                self.store.clear_checkpoint(self.target_id)
                self.status.update(state="completed")
            elif self.stop_flag.is_set():
                self.status.update(state="stopped", message="已停止，断点已保存")
            else:
                self.store.clear_checkpoint(self.target_id)
                self.status.update(state="completed", message="采集完成")
        except Exception as exc:
            if self.stop_flag.is_set():
                self.status.update(state="stopped", message="已停止，浏览器进程已结束")
            else:
                self.status.update(state="error", message=str(exc), errors=self.status["errors"] + 1)
                self.store.event(self.target_id, "error", str(exc))
        finally:
            self.loop = None
            self.active_context = self.active_browser = None
            self.active_page = None
            self.manual_intervention = False

    async def collect_board(self, page, board, rule, started, checkpoint=None):
        start_page = max(1, int((checkpoint or {}).get("page_no", 1)))
        url, empty = (checkpoint or {}).get("url") or board["url"], 0
        limits, freq = rule["limits"], rule["frequency"]
        pagination_mode = rule.get("list", {}).get("pagination_mode", "next")
        if checkpoint and pagination_mode == "scroll":
            # 瀑布流无法直接打开某一批；重新加载并滚动到保存的批次，
            # 期间已保存详情仍由 URL 去重。
            url = board["url"]
            await page.goto(url, wait_until="domcontentloaded",
                            timeout=int(freq["timeout_seconds"] * 1000))
            await self.handle_captcha(page, rule); await self.wait_gate()
            for _ in range(1, start_page):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(float(freq["list_seconds"]))
        first_iteration = True
        for page_no in range(start_page, int(limits["max_list_pages"]) + 1):
            if self.stop_flag.is_set(): return
            if (time.monotonic() - started) / 60 >= float(limits["max_minutes"]):
                self.stop_flag.set(); self.status["message"] = "达到最长运行时间"; return
            await self.wait_gate()
            self.store.save_checkpoint(self.target_id, {
                "board_id": board.get("id"), "board_name": board.get("name", ""),
                "page_no": page_no, "url": url, "pagination_mode": pagination_mode})
            self.status.update(board=board["name"], url=url, message=f"读取列表第 {page_no} 页")
            if pagination_mode != "scroll" or (first_iteration and not checkpoint):
                await page.goto(url, wait_until="domcontentloaded", timeout=int(freq["timeout_seconds"] * 1000))
                await self.handle_captcha(page, rule); await self.wait_gate()
            first_iteration = False
            page_text = await page.locator("body").inner_text()
            stop_rule = await self.matched_stop_rule(page, None, rule, "list")
            if stop_rule:
                self.stop_by_rule(stop_rule); return
            self.status["pages"] += 1
            rows = page.locator(rule["list"]["row_selector"])
            matched = []
            for i in range(await rows.count()):
                row = rows.nth(i); text = await row.inner_text()
                excluded = False
                for condition in rule["list"].get("exclude_rules", []):
                    if not condition.get("enabled", True):
                        continue
                    selector = str(condition.get("selector", "")).strip()
                    node = row.locator(selector).first if selector else row
                    try:
                        present = await node.count() > 0
                        actual = (await node.inner_text()).strip() if present else ""
                    except Exception:
                        present, actual = False, ""
                    hit = self.match_values(actual, condition, present)
                    if hit:
                        excluded = True
                        break
                # 兼容尚未经过配置迁移的运行中旧规则。
                if excluded or any(value and value in text for value in rule["list"].get("exclude_texts", [])):
                    self.status["skipped"] += 1; continue
                required_values = rule["list"].get("required_texts")
                if not isinstance(required_values, list):
                    required_values = [rule["list"].get("required_text", "")]
                required_values = [str(value) for value in required_values if str(value)]
                required_logic = rule["list"].get("required_logic", "or").lower()
                time_selector = str(rule["list"].get("time_selector", "")).strip()
                if time_selector:
                    time_node = row.locator(time_selector).first
                    if not await time_node.count():
                        self.status["skipped"] += 1; continue
                    list_time = (await time_node.inner_text()).strip()
                else:
                    # 兼容尚未设置时间元素的旧规则；只保存包含值所在的文本行。
                    list_time = next((line.strip() for line in text.splitlines()
                                      if any(value in line for value in required_values)), "")
                time_hits = [value in list_time for value in required_values]
                time_matched = all(time_hits) if required_logic == "and" else any(time_hits)
                if required_values and not time_matched:
                    self.status["skipped"] += 1; continue
                link = row.locator(rule["list"]["link_selector"]).first
                if not await link.count(): continue
                matched.append((await link.inner_text(), urljoin(page.url, await link.get_attribute("href")),
                                list_time))
            empty = empty + 1 if not matched else 0
            for title, detail_url, list_time in matched:
                if self.stop_flag.is_set() or self.status["opened"] >= int(limits["max_details"]): return
                if self.store.seen(self.target_id, detail_url):
                    self.status["skipped"] += 1; continue
                await asyncio.sleep(float(freq["detail_seconds"])); await self.wait_gate()
                self.status.update(url=detail_url, message=f"采集：{title}")
                await page.goto(detail_url, wait_until="domcontentloaded", timeout=int(freq["timeout_seconds"] * 1000))
                await self.handle_captcha(page, rule); await self.wait_gate()
                if not await self.ensure_detail_structure(page, rule):
                    if self.stop_flag.is_set(): return
                    await page.go_back(wait_until="domcontentloaded")
                    continue
                self.status["opened"] += 1
                html = await page.content(); data = parse_detail_html(html, rule, detail_url)
                excluded = False
                for field in rule.get("fields", []):
                    values = field.get("exclude_contains", [])
                    if isinstance(values, str): values = [values]
                    if any(value and value in str(data.get(field.get("name", ""), "")) for value in values):
                        excluded = True; break
                if excluded:
                    self.status["skipped"] += 1
                    await page.go_back(wait_until="domcontentloaded")
                    continue
                if self.store.add_result(self.target_id, board["name"], detail_url, title, list_time, data):
                    self.status["saved"] += 1
                stop_rule = await self.matched_stop_rule(page, data, rule, "detail")
                if stop_rule:
                    self.stop_by_rule(stop_rule); return
                await page.go_back(wait_until="domcontentloaded")
            if empty >= int(limits["empty_pages"]): return
            if pagination_mode == "scroll":
                before = await page.locator(rule["list"]["row_selector"]).count()
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(float(freq["list_seconds"]))
                after = await page.locator(rule["list"]["row_selector"]).count()
                if after <= before: return
                continue
            nxt = page.locator(rule["list"]["next_selector"]).first
            if not await nxt.count(): return
            url = urljoin(page.url, await nxt.get_attribute("href"))
            await asyncio.sleep(float(freq["list_seconds"]))


class RunnerManager:
    def __init__(self, root, store):
        self.root, self.store, self.runners = root, store, {}
    def start(self, target_id, restart=False):
        old = self.runners.get(target_id)
        self._finalize_dead_runner(old)
        if old and old.status["state"] not in ("completed", "stopped", "error"): return old.status
        if restart: self.store.clear_checkpoint(target_id)
        runner = TargetRunner(self.root, self.store, target_id); self.runners[target_id] = runner; runner.start()
        return self.status(target_id)
    def action(self, target_id, action):
        r = self.runners.get(target_id)
        if not r: return {"state": "idle", "message": "任务尚未启动"}
        self._finalize_dead_runner(r)
        getattr(r, action)(); return r.status
    @staticmethod
    def _finalize_dead_runner(runner):
        """浏览器已关闭且采集线程已退出时，修复遗留的 stopping 状态。"""
        if (runner and runner.status.get("state") == "stopping" and runner.thread
                and not runner.thread.is_alive()):
            runner.status.update(state="stopped", message="已停止，断点已保存")
    def status(self, target_id):
        r = self.runners.get(target_id)
        self._finalize_dead_runner(r)
        status = dict(r.status) if r else {"state": "idle", "message": "尚未运行"}
        checkpoint = self.store.checkpoint(target_id)
        status["has_checkpoint"] = bool(checkpoint)
        if checkpoint: status["checkpoint"] = checkpoint
        return status
