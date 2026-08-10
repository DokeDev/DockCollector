import csv
import copy
import io
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if sys.platform != "win32" and os.environ.get("DOCK_USE_VENDOR", "1") != "0":
    sys.path.insert(0, str(ROOT / ".vendor"))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .rules import account_profile_id, default_rule
from .challenge_signals import CHALLENGE_TEXTS
from .picker import PickerManager
from .login import LoginManager
from .runner import RunnerManager
from .store import Store

app = FastAPI(title="Dock采集器")
store = Store(ROOT / "collector.db")


def normalize_rule_names():
    """JSON object keys must be unique; preserve duplicate rules with suffixes."""
    for target in store.targets():
        used, changed = set(), False
        for field in target["rule"].get("fields", []):
            base = (field.get("name") or "字段").strip()
            name, number = base, 2
            while name in used:
                name = f"{base}_{number}"; number += 1
            if field.get("name") != name: field["name"], changed = name, True
            used.add(name)
        if changed: store.save_target(target["id"], target)


def normalize_picker_sources():
    """Give every target an independently editable list of picker pages."""
    for target in store.targets():
        rule = target["rule"]
        if "picker_sources" in rule:
            continue
        folder = ROOT / rule.get("folder", "")
        sources = [
            {"name": f"本地样例：{path.name}", "source": f"sample:{path.name}"}
            for path in sorted(folder.glob("内容*.html"))
        ]
        rule["picker_sources"] = sources
        store.save_target(target["id"], target)


def normalize_proxy_configs():
    defaults = default_rule("", "", "", "")["proxy"]
    for target in store.targets():
        proxy = target["rule"].setdefault("proxy", {})
        changed = False
        for key, value in defaults.items():
            if key not in proxy: proxy[key], changed = value, True
        if proxy.get("mode") == "direct" and proxy.get("server"):
            proxy["mode"], changed = "fixed", True
        if changed: store.save_target(target["id"], target)


def normalize_browser_configs():
    for target in store.targets():
        if "browser" not in target["rule"]:
            target["rule"]["browser"] = {"mode": "visible"}
            store.save_target(target["id"], target)


def normalize_list_configs():
    for target in store.targets():
        listing = target["rule"].setdefault("list", {})
        changed = False
        for key, value in {"time_selector": "", "exclude_texts": [],
                           "pagination_mode": "next"}.items():
            if key not in listing:
                listing[key], changed = copy.deepcopy(value), True
        if changed:
            store.save_target(target["id"], target)


def normalize_stop_configs():
    for target in store.targets():
        stop = target["rule"].setdefault("stop", {})
        if "rules" in stop:
            valid = [item for item in stop["rules"]
                     if item.get("operator") not in {"contains", "equals", "not_contains"}
                     or str(item.get("value", "")).strip()]
            if valid != stop["rules"]:
                stop["rules"] = valid
                store.save_target(target["id"], target)
            continue
        rules = []
        for value in stop.get("page_contains", []):
            rules.append({"enabled": True, "kind": "page_text", "phase": "list",
                          "operator": "contains", "value": value, "scope": "board"})
        for value in stop.get("detail_contains", []):
            rules.append({"enabled": True, "kind": "page_text", "phase": "detail",
                          "operator": "contains", "value": value, "scope": "board"})
        if stop.get("field_name") and stop.get("field_contains"):
            rules.append({"enabled": True, "kind": "field", "phase": "detail",
                          "field": stop["field_name"], "operator": "contains",
                          "value": stop.get("field_contains", ""), "scope": "board"})
        target["rule"]["stop"] = {"rules": rules}
        store.save_target(target["id"], target)


def normalize_captcha_configs():
    for target in store.targets():
        captcha = target["rule"].setdefault("captcha", {})
        defaults = default_rule("", "", "", "")["captcha"]
        changed = False
        for key, value in defaults.items():
            if key not in captcha: captcha[key], changed = value, True
        # 旧版本曾把内置文案写进每个目标；现在从目标配置中移除，避免界面重复展示。
        custom_texts = [text for text in captcha.get("texts", []) if text not in CHALLENGE_TEXTS]
        if custom_texts != captcha.get("texts", []):
            captcha["texts"], changed = custom_texts, True
        if changed: store.save_target(target["id"], target)


def normalize_board_ids():
    for target in store.targets():
        changed = False
        for board in target["rule"].get("boards", []):
            if not board.get("id"):
                board["id"], changed = "source_" + uuid.uuid4().hex[:12], True
        if changed: store.save_target(target["id"], target)


def normalize_account_configs():
    """旧目标保持来源独立账号；新目标默认使用统一账号。"""
    for target in store.targets():
        rule = target["rule"]
        changed = False
        if "account" not in rule:
            rule["account"], changed = {"mode": "independent"}, True
        for board in rule.get("boards", []):
            if "account_mode" not in board:
                board["account_mode"], changed = "shared", True
        if changed: store.save_target(target["id"], target)


normalize_rule_names(); normalize_picker_sources(); normalize_proxy_configs(); normalize_browser_configs(); normalize_list_configs(); normalize_stop_configs(); normalize_captcha_configs(); normalize_board_ids(); normalize_account_configs(); manager = RunnerManager(ROOT, store); picker = PickerManager(ROOT, store); login = LoginManager(ROOT, store, manager)

@app.get("/api/targets")
def targets():
    return [{k:v for k,v in x.items() if k != "rule_json"} | {"status": manager.status(x["id"])} for x in store.targets()]

@app.post("/api/targets")
def create_target(payload: dict):
    target_id = payload.get("id", "").strip()
    if not target_id or store.target(target_id): raise HTTPException(400, "目标标识为空或已存在")
    rule = default_rule(target_id, payload.get("folder", target_id), payload.get("domain", ""), "generic")
    (ROOT / rule["folder"]).mkdir(parents=True, exist_ok=True)
    store.save_target(target_id, {"name": payload.get("name", target_id), "enabled": True, "rule": rule})
    return {"ok": True, "id": target_id}

@app.get("/api/targets/{target_id}")
def target(target_id: str):
    x = store.target(target_id)
    if not x: raise HTTPException(404)
    x.pop("rule_json", None); x["status"] = manager.status(target_id); return x

@app.put("/api/targets/{target_id}")
def save(target_id: str, payload: dict): store.save_target(target_id, payload); return {"ok": True}

@app.delete("/api/targets/{target_id}")
def delete_target(target_id: str):
    if not store.target(target_id): raise HTTPException(404, "目标不存在")
    manager.action(target_id, "stop"); picker.stop(target_id); login.stop(target_id)
    return {"ok": store.delete_target(target_id), "folder_kept": True}

@app.post("/api/targets/{target_id}/actions/{action}")
def action(target_id: str, action: str):
    if action == "start": return manager.start(target_id)
    if action == "restart-clear": return manager.restart_clear(target_id)
    if action not in ("pause", "resume", "stop"): raise HTTPException(400)
    return manager.action(target_id, action)

@app.post("/api/targets/{target_id}/login/start")
def login_start(target_id: str, payload: dict):
    if not store.target(target_id): raise HTTPException(404, "目标不存在")
    return login.start(target_id, payload.get("board_id", ""))

@app.post("/api/targets/{target_id}/login/stop")
def login_stop(target_id: str, payload: dict): return login.stop(target_id, payload.get("board_id", ""))

@app.get("/api/targets/{target_id}/login/status")
def login_status(target_id: str, board_id: str = ""): return login.status(target_id, board_id)

@app.get("/api/targets/{target_id}/accounts")
def source_accounts(target_id: str):
    target = store.target(target_id)
    if not target: raise HTTPException(404, "目标不存在")
    base = ROOT / target["rule"]["folder"] / "浏览器数据"
    rule = target["rule"]
    sources = []
    for b in rule.get("boards", []):
        profile_id = account_profile_id(rule, b)
        sources.append({"id": b["id"], "name": b.get("name", "未命名"),
                        "account_mode": b.get("account_mode", "shared"),
                        "profile_id": profile_id,
                        "saved": (base / profile_id / "登录状态.json").exists(),
                        "proxy": "独立固定代理" if b.get("proxy") else "继承目标代理"})
    return {"mode": rule.get("account", {}).get("mode", "independent"),
            "shared_saved": (base / "_shared" / "登录状态.json").exists(),
            "sources": sources}

@app.get("/api/targets/{target_id}/status")
def status(target_id: str): return manager.status(target_id)

@app.get("/api/targets/{target_id}/results")
def results(target_id: str, page: int = 1, page_size: int = 20):
    page, page_size = max(1, page), min(100, max(5, page_size))
    total = store.result_count(target_id)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    return {"rows": store.results(target_id, page_size, (page - 1) * page_size),
            "total": total, "page": page, "page_size": page_size, "pages": pages}

@app.delete("/api/targets/{target_id}/results")
def clear_results(target_id: str): return {"ok": True, "deleted": store.clear_results(target_id)}

@app.get("/api/targets/{target_id}/events")
def events(target_id: str): return store.events(target_id)

@app.get("/api/targets/{target_id}/rule.json")
def export_rule(target_id: str):
    target = store.target(target_id)
    if not target: raise HTTPException(404)
    rule = copy.deepcopy(target["rule"])
    for key in ("password", "api_header_value"):
        if rule.get("proxy", {}).get(key): rule["proxy"][key] = "***本机已保存，导出时隐藏***"
    content = json.dumps(rule, ensure_ascii=False, indent=2)
    return Response(content, media_type="application/json; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{target_id}-rule.json"'})

@app.post("/api/targets/{target_id}/picker/start")
def picker_start(target_id: str, payload: dict):
    return picker.start(target_id, payload.get("source", ""), payload.get("mode", "detail"),
                        payload.get("board_id", ""))

@app.get("/api/targets/{target_id}/picker")
def picker_status(target_id: str): return picker.status(target_id)

@app.post("/api/targets/{target_id}/picker/stop")
def picker_stop(target_id: str): return picker.stop(target_id)

@app.post("/api/targets/{target_id}/picker/activate")
def picker_activate(target_id: str): return picker.activate(target_id)

@app.get("/api/targets/{target_id}/export.csv")
def export(target_id: str):
    rows = store.results(target_id); target = store.target(target_id); data_fields = []
    for field in target["rule"].get("fields", []):
        name = field.get("name")
        if name and name not in data_fields: data_fields.append(name)
    for r in rows:
        for key in r["data"]:
            if key not in data_fields and key != "原始链接": data_fields.append(key)
    fields = ["数据列表","列表时间",*data_fields,"链接","采集时间"]
    out = io.StringIO(); w = csv.DictWriter(out, fieldnames=fields); w.writeheader()
    for r in reversed(rows):
        row = {"数据列表":r["board_name"],"列表时间":r["list_time"],"链接":r["url"],"采集时间":r["collected_at"]}
        row.update({k:v for k,v in r["data"].items() if k in data_fields}); w.writerow(row)
    return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{target_id}.csv"'})

app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
