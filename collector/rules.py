import re
from copy import deepcopy
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup


FIELDS = ["地区", "标题", "详细地址", "服务项目", "年龄容貌", "消费", "联系方式", "正文"]


def account_profile_id(rule, board):
    """返回数据列表实际使用的账号配置目录名。"""
    mode = rule.get("account", {}).get("mode", "independent")
    if mode == "shared":
        return "_shared"
    if mode == "mixed" and board.get("account_mode", "shared") != "independent":
        return "_shared"
    return board["id"]


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def parse_detail_html(html, rule, url=""):
    soup = BeautifulSoup(html, "html.parser")
    result = {}
    for field in rule.get("fields", []):
        value = ""
        kind = field.get("kind", "css")
        if kind == "css":
            node = soup.select_one(field.get("selector", ""))
            if node:
                value = node.get(field.get("attribute")) if field.get("attribute") else node.get_text(" ")
        elif kind == "label_next":
            label = field.get("label", "")
            for node in soup.find_all(["th", "td", "div", "span"]):
                if clean(node.get_text(" ")).rstrip(":：") == label.rstrip(":："):
                    nxt = node.find_next_sibling(field.get("next_tag")) or node.find_next_sibling()
                    if nxt:
                        value = nxt.get_text(" ")
                    break
        elif kind == "discuz_showhide":
            root = soup.select_one(field.get("root", "td.t_f[id^='postmessage_']"))
            if root:
                text = str(root)
                label = re.escape(field.get("label", ""))
                match = re.search(label + r"[：:]\s*<div[^>]*class=[\"']showhide[\"'][^>]*>.*?</h4>(.*?)</div>", text, re.S | re.I)
                if match:
                    value = BeautifulSoup(match.group(1), "html.parser").get_text(" ")
        elif kind == "breadcrumb":
            nodes = soup.select(field.get("selector", ".z a"))
            index = field.get("index", -1)
            if nodes and -len(nodes) <= index < len(nodes):
                value = nodes[index].get_text(" ")
        elif kind == "body_after_fields":
            root = soup.select_one(field.get("selector", "td.t_f[id^='postmessage_']"))
            if root:
                clone = BeautifulSoup(str(root), "html.parser")
                for n in clone.select(".showhide, h4"):
                    n.decompose()
                text = clone.get_text(" ")
                text = re.sub(r"(?:体验日期|场所地点|服务项目|年龄容貌|环境评分|总体消费|联系方式|联络攻略)[：:]", " ", text)
                value = text
        value = clean(value)
        for pattern in field.get("remove", []):
            value = clean(re.sub(pattern, " ", value))
        name = field["name"]
        if name in result:
            number = 2
            while f"{name}_{number}" in result: number += 1
            name = f"{name}_{number}"
        result[name] = value
    result["原始链接"] = url
    return result


def discover_boards(root: Path, folder):
    path = root / folder / "目录.txt"
    if not path.exists():
        return []
    return [{"name": f"版块{i+1}", "url": x.strip(), "enabled": True, "proxy": ""}
            for i, x in enumerate(path.read_text(encoding="utf-8").splitlines()) if x.strip()]


def default_rule(target_id, folder, domain, adapter):
    return {
        "folder": folder,
        "domain": domain,
        "adapter": adapter,
        "boards": [],
        "list": {
            "row_selector": "#threadlisttableid tbody[id^='normalthread_'], #threadlisttableid tbody",
            "link_selector": "a.xst, a.s.xst, a[href*='thread-']",
            "time_selector": "",
            "required_text": "昨天",
            "required_texts": ["昨天"],
            "required_logic": "or",
            "exclude_texts": [],
            "exclude_rules": [],
            "next_selector": "a.nxt",
            "pagination_mode": "next",
        },
        "frequency": {"list_seconds": 15, "detail_seconds": 30, "timeout_seconds": 45,
                      "retry_limit": 2, "backoff_seconds": 300},
        "limits": {"max_list_pages": 10, "max_details": 20, "max_minutes": 60,
                   "max_captcha": 3, "max_errors": 5, "empty_pages": 1},
        "stop": {"rules": []},
        # texts 只保存目标专用补充文案；通用文案由 challenge_signals 内置提供。
        "captcha": {"texts": [],
                    "selectors": ["img[src*='seccode']", "input[name*='seccode']", ".geetest_panel"],
                    "auto": True, "max_auto_tries": 3},
        "browser": {"mode": "visible"},
        "account": {"mode": "shared"},
        "proxy": {"mode": "direct", "server": "", "username": "", "password": "",
                  "api_url": "", "api_method": "GET", "api_body": "", "api_json_path": "",
                  "api_header_name": "", "api_header_value": "", "api_scheme": "http",
                  "api_retries": 2},
        "fields": [],
    }
