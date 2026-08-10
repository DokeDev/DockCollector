"""跨站点验证页/访问风控页的内置信号库。

文字只作为风险信号，组件选择器属于更强信号；最终仍需结合目标的正常字段结构，
避免把正文中偶然出现“验证码”等词语误判为验证页。
"""

CHALLENGE_TEXTS = (
    "验证码", "请输入验证码", "输入验证码", "输入图中字符", "看不清换一张", "换一张",
    "人机验证", "人机访问验证", "请完成人机验证", "请确认您是真人", "真人验证",
    "安全验证", "安全检查", "完成验证", "请完成验证", "请先完成验证", "身份验证",
    "拖动滑块", "滑动验证", "向右滑动", "按住滑块", "点击验证", "点此验证",
    "请按顺序点击", "请选择包含", "访问过于频繁", "操作过于频繁", "请求过于频繁",
    "访问受限", "访问异常", "异常流量", "网络环境存在风险", "当日已访问", "风控验证",
    "verify you are human", "verify that you are human", "are you a human",
    "i'm not a robot", "i’m not a robot", "checking your browser", "security check",
    "complete the security check", "complete the captcha", "captcha required",
    "verification required", "unusual traffic", "access denied", "too many requests",
    "rate limit", "press and hold", "slide to verify", "select all images",
)

CHALLENGE_SELECTORS = (
    ".g-recaptcha", "[name='g-recaptcha-response']", "iframe[src*='recaptcha']",
    "script[src*='recaptcha']", ".h-captcha", "[name='h-captcha-response']",
    "iframe[src*='hcaptcha']", "script[src*='hcaptcha']", ".cf-turnstile",
    "[name='cf-turnstile-response']", "iframe[src*='challenges.cloudflare.com']",
    "script[src*='challenges.cloudflare.com/turnstile']", "[class*='geetest']",
    "[id*='geetest']", "script[src*='geetest.com']", "script[src*='geevisit.com']",
    "img[src*='captcha']", "img[src*='seccode']", "img[src*='verify']",
    "input[name*='captcha']", "input[id*='captcha']", "input[name*='seccode']",
)

LOGIN_TEXTS = (
    "请先登录", "登录后查看", "您尚未登录", "账号登录", "重新登录",
    "please log in", "please sign in", "login required", "sign in required",
)
