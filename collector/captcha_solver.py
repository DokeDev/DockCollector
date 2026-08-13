"""ddddocr 自动打码封装。

提供四类验证码的本地离线识别：
- 字符验证码：classification() 返回验证码图片中的文字；
- 算术验证码：识别出算式文本后由后处理计算并返回结果（如 "7+8-？" → "15"）；
- 旋转字符验证码：det 目标检测切出单字符，多角度旋转候选按置信度投票；
- 滑块验证码：slide_comparison() 返回缺口在背景图中的 x 坐标。

ddddocr 与模型均为懒加载（首次调用时才初始化，第一次可能联网下载模型，
耗时数秒到数十秒）。初始化失败时 available=False，采集器自动回退人工处理，
不影响原有流程。
"""
import io
import re
import struct
import threading
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

_lock = threading.Lock()
_solver = None


class CaptchaSolver:
    """ddddocr 实例集合。共享实例串行调用，避免并发识别错乱。"""

    def __init__(self):
        self.ocr = None      # 字符验证码 OCR 实例
        self.slide = None    # 滑块缺口检测实例
        self.error = None
        self._char_lock = threading.Lock()
        self._slide_lock = threading.Lock()
        try:
            import ddddocr
            self.ocr = ddddocr.DdddOcr(show_ad=False)
            self.slide = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
        except Exception as exc:
            self.ocr = self.slide = None
            self.error = f"ddddocr 初始化失败：{exc}"

    @property
    def available(self):
        return self.ocr is not None and self.slide is not None

    def solve_char(self, image_bytes):
        """识别字符验证码图片，返回识别出的文字（可能为空字符串）。"""
        if not self.available:
            raise RuntimeError(self.error or "ddddocr 不可用")
        with self._char_lock:
            text = self.ocr.classification(image_bytes)
        return (text or "").strip()

    @staticmethod
    def image_quality(image_bytes):
        """返回 (是否含有效视觉内容, 宽, 高, 灰度跨度)。"""
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("L")
            extrema = img.getextrema()
            spread = int(extrema[1]) - int(extrema[0])
            histogram = img.histogram()
            total = max(1, sum(histogram))
            dominant = max(histogram) / total
            valid = img.width >= 30 and img.height >= 15 and spread >= 18 and dominant < .985
            return valid, img.width, img.height, spread
        except Exception:
            return False, 0, 0, 0

    def solve_best(self, image_bytes, max_variants=5):
        """只使用同一张图片，在本地尝试多种预处理，不刷新或再次请求网站。"""
        variants = self._preprocess_variants(image_bytes)[:max(1, int(max_variants))]
        outputs = []
        for variant in variants:
            text = self.solve_char(variant)
            if not text: continue
            math = self.compute_math(text)
            if math is not None: return math
            cleaned = re.sub(r"\s+", "", text)
            if re.fullmatch(r"[0-9A-Za-z]+", cleaned): outputs.append(cleaned)
        if outputs:
            # 多个本地版本结果一致时优先；相同票数优先原图/较早方案。
            return max(dict.fromkeys(outputs), key=lambda value: (outputs.count(value), -outputs.index(value)))
        rotated = self.solve_rotated(image_bytes)
        return rotated or ""

    @staticmethod
    def _preprocess_variants(image_bytes):
        """生成原图、放大、对比度、灰度和二值化版本；全程仅在本地内存处理。"""
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        white = Image.new("RGBA", img.size, "white")
        white.alpha_composite(img)
        rgb = white.convert("RGB")
        scale = max(2, min(4, 160 // max(1, rgb.width)))
        enlarged = rgb.resize((rgb.width * scale, rgb.height * scale), Image.Resampling.LANCZOS)
        gray = ImageOps.autocontrast(enlarged.convert("L"))
        contrast = ImageEnhance.Contrast(enlarged).enhance(2.0).filter(ImageFilter.SHARPEN)
        binary = gray.point(lambda value: 255 if value > 155 else 0).convert("RGB")
        sources = [rgb, enlarged, contrast, gray.convert("RGB"), binary]
        result = []
        for source in sources:
            buf = io.BytesIO(); source.save(buf, "PNG"); result.append(buf.getvalue())
        return result

    @staticmethod
    def compute_math(text):
        """从识别文本中提取 '数字 运算符 数字' 并计算，返回结果字符串或 None。"""
        m = re.search(r"(-?\d+)\s*([+\-x×÷])\s*(-?\d+)", text or "")
        if not m:
            return None
        a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
        if op == "+":
            return str(a + b)
        if op == "-":
            return str(a - b)
        if op in "x×":
            return str(a * b)
        if op == "÷" and b:
            return str(a // b)
        return None

    def solve_rotated(self, image_bytes):
        """旋转字符增强：投影切字 → 合并断裂区段 → 单字符多角度候选按置信度投票。
        任何一步失败返回空串，调用方回退原识别结果。"""
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("L")
            w, h = img.size
            px = img.load()
            # 垂直带：行投影去掉上下干扰
            row_peak = max(sum(1 for x in range(w) if px[x, y] < 200) for y in range(h))
            band = [y for y in range(h)
                    if sum(1 for x in range(w) if px[x, y] < 200) > max(2, row_peak * 0.15)]
            if not band:
                return ""
            sub = img.crop((0, band[0], w, band[-1] + 1))
            sw, sh = sub.size
            spx = sub.load()
            cols = [sum(1 for y in range(sh) if spx[x, y] < 200) for x in range(sw)]
            if not cols:
                return ""
            # 列投影 + 自适应阈值切字符区段（字符列深色数明显高于间隙）
            thr = max(6, max(cols) * 0.35)
            segs, x = [], 0
            while x < sw:
                if cols[x] > thr:
                    s = x
                    while x < sw and cols[x] > thr:
                        x += 1
                    if x - s >= 3:
                        segs.append((s, x))
                else:
                    x += 1
            # 合并间隙过小的相邻区段（旋转字符笔划可能断裂）
            merged = []
            for s, e in segs:
                if merged and s - merged[-1][1] < 7:
                    merged[-1] = (merged[-1][0], e)
                else:
                    merged.append((s, e))
            if not merged or len(merged) > 8:  # 切字失败：太少或太多
                return ""
            result = []
            for s, e in merged:
                crop = sub.crop((s, 0, e, sh)).convert("RGB")
                best_ch, best_score = "", -1.0
                for angle in range(-45, 46, 10):
                    cand = crop.rotate(angle, expand=True, fillcolor=(255, 255, 255))
                    buf = io.BytesIO()
                    cand.save(buf, "PNG")
                    with self._char_lock:
                        prob = self.ocr.classification(buf.getvalue(), probability=True)
                    rows = prob.get("probability") or []
                    if not rows:
                        continue
                    text, score = "", 0.0
                    for row in rows:
                        idx = row.index(max(row))
                        text += prob["charsets"][idx]
                        score += row[idx]
                    score /= len(rows)
                    if not re.fullmatch(r"[0-9A-Za-z]+", text):
                        continue  # 过滤中文误识等非法候选
                    if score > best_score:
                        best_score, best_ch = score, text
                result.append(best_ch)
            return "".join(result).strip()
        except Exception:
            return ""

    def solve_slide(self, gap_bytes, background_bytes):
        """计算滑块缺口位置，返回缺口中心 x 坐标（相对 background 图像素）。"""
        if self.slide is None:
            raise RuntimeError(self.error or "ddddocr 不可用")
        with self._slide_lock:
            result = self.slide.slide_comparison(gap_bytes, background_bytes)
        target = result.get("target") or []
        return int(target[0]) if target else 0


def get_captcha_solver():
    global _solver
    with _lock:
        if _solver is None:
            _solver = CaptchaSolver()
        return _solver


def png_width(data):
    """从 PNG 字节流解析宽度；解析失败返回 None（Playwright 截图默认 PNG）。"""
    try:
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return struct.unpack(">I", data[16:20])[0]
    except Exception:
        return None
