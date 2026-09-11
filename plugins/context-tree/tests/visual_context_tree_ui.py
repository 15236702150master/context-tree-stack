import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "context_tree.py"
ARTIFACTS = ROOT / "tests" / "artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
SPEC = importlib.util.spec_from_file_location("context_tree", SCRIPT)
ct = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(ct)


def sample_store(path: Path) -> tuple[str, dict]:
    db = ct.initialize_store(path)
    topic_id, _ = ct.create_topic(db, path, "Context Tree 插件", "构建跨任务的紧凑记忆树")
    ct.attach_session(db, path, "visual-session", topic_id)
    first = ct.commit_turn(db, path, {
        "event_key": "visual-1", "session_id": "visual-session",
        "user_intent": "确定本地记忆结构", "response_summary": "采用 SQLite 和 JSONL",
        "outcome_summary": "本地存储已经可用", "next_step": "增加紧凑面板",
        "nodes": [
            {"id": "goal", "type": "goal", "label": "降低上下文开销", "capsule": "在新任务中只加载 Active Frontier"},
            {"id": "store", "type": "decision", "label": "使用 SQLite 与 JSONL", "capsule": "SQLite 作为投影，JSONL 作为事件日志"},
            {"id": "ready", "type": "result", "label": "本地存储验证通过", "capsule": "数据库、快照和 pending tail 正常"},
        ],
        "edges": [
            {"from": "goal", "to": "store", "relation": "requires"},
            {"from": "store", "to": "ready", "relation": "produced"},
        ],
    })
    second = ct.commit_turn(db, path, {
        "event_key": "visual-2", "session_id": "visual-session",
        "user_intent": "设计折叠面板", "response_summary": "按钮点击后展开主题列表",
        "outcome_summary": "Widget 原型完成", "next_step": "验证不同窗口尺寸",
        "nodes": [
            {"id": "widget", "type": "milestone", "label": "紧凑 Widget 完成", "capsule": "默认显示一个按钮，展开后管理主题"},
            {"id": "graph", "type": "artifact", "label": "可缩放图谱页面", "capsule": "支持平移、缩放、搜索和节点详情"},
        ],
        "edges": [
            {"from": "ready", "to": "widget", "relation": "enabled"},
            {"from": "widget", "to": "graph", "relation": "opens"},
        ],
    })
    branch_id = ct.uid("branch")
    db.execute(
        "INSERT INTO branches VALUES(?,?,?,?,?,?,?)",
        (branch_id, topic_id, first["branch_id"], "子主题识别", "active", None, ct.now()),
    )
    db.commit()
    ct.attach_session(db, path, "visual-branch-session", topic_id, branch_id)
    branch_turn = ct.commit_turn(db, path, {
        "event_key": "visual-branch", "session_id": "visual-branch-session",
        "user_intent": "识别并续接子主题", "response_summary": "增加分支级 Active Frontier",
        "outcome_summary": "子主题续接指针可用", "next_step": "验证跨分支依赖",
        "nodes": [{
            "id": "branch-focus", "type": "result", "label": "子主题续接完成",
            "capsule": "保存主题、子主题和前沿节点，下一新会话只加载对应分支",
            "exact_data": {"pointer": "topic_id + branch_id + node_id"},
        }],
    })
    db.execute(
        "INSERT INTO edges VALUES(?,?,?,?,?,?,?)",
        (ct.uid("edge"), topic_id, "graph", "branch-focus", "depends_on", branch_turn["turn_id"], ct.now()),
    )
    db.execute(
        "INSERT INTO edges VALUES(?,?,?,?,?,?,?)",
        (ct.uid("edge"), topic_id, "branch-focus", "goal", "references", branch_turn["turn_id"], ct.now()),
    )
    db.commit()
    state = ct.settings_payload(db, path)
    state["usage"] = {
        "mode": "real",
        "connection": {"server_url": "https://usage.example.test"},
        "sessions": [
            {
                "session_id": "019fa82a-601e-7a61-934b-de4b60628c75:3",
                "thread_id": "019fa82a-601e-7a61-934b-de4b60628c75",
                "window_slot": "3", "context_tokens": 241400,
                "first_token_ms": 3159, "recent_cost": 0.428269,
                "percent": 88.8, "level": "critical",
            },
            {
                "session_id": "019fa82a-601e-7a61-934b-de4b60628c75:4",
                "thread_id": "019fa82a-601e-7a61-934b-de4b60628c75",
                "window_slot": "4", "context_tokens": 96242,
                "first_token_ms": 3415, "recent_cost": 0.322914,
                "percent": 35.4, "level": "normal",
            },
        ],
    }
    state["active_sessions"] = [
        {
            **state["usage"]["sessions"][0],
            "capacity_tokens": 272000,
            "session_title": "研究对话上下文延续",
            "compaction_count": 7,
            "cost_profile_status": "ready",
            "turn_cost_request_count": 27,
            "turn_avg_cost": 0.11651059,
            "turn_avg_context_cost": 0.03902418,
            "turn_avg_first_token_ms": 6150,
            "turn_avg_duration_ms": 13400,
            "baseline_avg_cost": 0.03931421,
            "current_epoch_index": 7,
            "current_epoch_avg_cost": 0.09519638,
            "current_epoch_extra_avg_cost": 0.05588217,
            "epoch_summaries": [
                {
                    "epoch_index": 0, "label": "压缩前", "request_count": 18,
                    "avg_cost": 0.03931421, "avg_context_cost": 0.021412,
                    "avg_first_token_ms": 2280, "avg_duration_ms": 7120,
                    "configuration": "gpt-5.6-sol · medium · default",
                },
                {
                    "epoch_index": 7, "label": "第7次压缩后", "request_count": 31,
                    "avg_cost": 0.09519638, "avg_context_cost": 0.052119,
                    "avg_first_token_ms": 5410, "avg_duration_ms": 11840,
                    "configuration": "混合配置(2)",
                },
            ],
        },
        {
            **state["usage"]["sessions"][1],
            "capacity_tokens": 272000,
            "session_title": "本地阶段回退",
            "compaction_count": 2,
            "cost_profile_status": "server_unavailable",
            "turn_request_count": 5,
            "turn_avg_window_percent": 24.6,
            "turn_cache_hit_percent": 93.4,
            "current_epoch_index": 2,
            "epoch_summaries": [
                {"epoch_index": 0, "label": "压缩前", "request_count": 8, "avg_context_tokens": 42000, "avg_window_percent": 15.4, "cache_hit_percent": 88.0, "configuration": "gpt-test · medium"},
                {"epoch_index": 1, "label": "第1次压缩后", "request_count": 12, "avg_context_tokens": 68000, "avg_window_percent": 25.0, "cache_hit_percent": 91.2, "configuration": "gpt-test · medium"},
                {"epoch_index": 2, "label": "第2次压缩后", "request_count": 5, "avg_context_tokens": 81000, "avg_window_percent": 29.8, "cache_hit_percent": 93.4, "configuration": "gpt-test · medium"},
            ],
        },
    ]
    db.close()
    return topic_id, state


with tempfile.TemporaryDirectory() as temporary:
    store = Path(temporary) / ".context-tree"
    topic_id, state = sample_store(store)
    process = subprocess.Popen(
        [sys.executable, str(SCRIPT), "--store", str(store), "ui", "--port", "0", "--no-open"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        assert process.stdout
        url = process.stdout.readline().strip()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)

            graph = browser.new_page(viewport={"width": 1440, "height": 900})
            graph.goto(f"{url}graph?topic={topic_id}")
            graph.locator(".subtopic-node").first.wait_for()
            graph.locator(".branch", has_text="主线").click()
            if graph.locator(".node").count() == 0 and graph.locator(".workstream-node").count():
                graph.locator(".workstream-node").first.click()
            graph.locator(".node").first.wait_for()
            assert graph.locator(".node").count() >= 1
            assert graph.locator(".branch").count() == 2
            graph.locator(".branch", has_text="子主题识别").click()
            focus_count = graph.locator(".node.focus").count()
            dim_count = graph.locator(".node.dim").count()
            assert focus_count == 1, (focus_count, dim_count, graph.locator(".branch").all_inner_texts())
            assert dim_count == 0
            graph.locator(".node.focus").click()
            assert graph.locator("#detail-title").inner_text() != "选择一个节点"
            assert graph.locator("#detail-content").inner_text().strip()
            graph.locator("#resume").click()
            graph.wait_for_function("document.querySelector('#resume').textContent.includes('已指向')")
            graph.screenshot(path=str(ARTIFACTS / "graph-desktop.png"), full_page=True)

            mobile_graph = browser.new_page(viewport={"width": 390, "height": 844})
            mobile_graph.goto(f"{url}graph?topic={topic_id}")
            mobile_graph.locator(".subtopic-node").first.wait_for()
            mobile_graph.locator(".branch", has_text="主线").click()
            if mobile_graph.locator(".node").count() == 0 and mobile_graph.locator(".workstream-node").count():
                mobile_graph.locator(".workstream-node").first.click()
            mobile_graph.locator(".node").first.wait_for()
            mobile_graph.locator(".node").first.click()
            assert "open" in (mobile_graph.locator("#details").get_attribute("class") or "")
            mobile_graph.screenshot(path=str(ARTIFACTS / "graph-mobile.png"), full_page=True)

            settings = browser.new_page(viewport={"width": 1440, "height": 900})
            settings.goto(url)
            settings.locator("#float-persistent").wait_for()
            assert not settings.locator("#float-persistent").is_checked()
            assert settings.locator("#float-persistent-state").inner_text() == "空闲时自动隐藏"
            settings.locator("#float-persistent").check()
            settings.wait_for_function(
                "document.querySelector('#float-persistent-state').textContent === '始终保留折叠按钮'"
            )
            assert settings.locator("#float-persistent").is_checked()
            assert settings.locator("#float-persistent-state").inner_text() == "始终保留折叠按钮"
            assert settings.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            settings.screenshot(path=str(ARTIFACTS / "settings-persistent-desktop.png"), full_page=True)

            mobile_settings = browser.new_page(viewport={"width": 390, "height": 844})
            mobile_settings.goto(url)
            mobile_settings.locator("#float-persistent").wait_for()
            mobile_settings.wait_for_function(
                "document.querySelector('#float-persistent').checked === true"
            )
            assert mobile_settings.locator("#float-persistent").is_checked()
            assert mobile_settings.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            mobile_settings.screenshot(path=str(ARTIFACTS / "settings-persistent-mobile.png"), full_page=True)

            widget_html = (ROOT / "assets" / "widget.html").read_text(encoding="utf-8")
            widget = browser.new_page(viewport={"width": 380, "height": 500})
            widget.set_content(widget_html)
            widget.evaluate("""value => window.dispatchEvent(new MessageEvent('message', {source: window, data: {jsonrpc:'2.0', method:'ui/notifications/tool-result', params:{structuredContent:value}}}))""", state)
            widget.locator("#trigger").click()
            widget.locator("#panel").wait_for(state="visible")
            topic_count = widget.locator(".topic").count()
            assert topic_count == 2, topic_count
            assert "平均计费" in widget.locator(".cost-profile").first.inner_text()
            assert "压缩前" in widget.locator(".cost-profile").first.inner_text()
            assert "第2次压缩后" in widget.locator(".cost-profile").nth(1).inner_text()
            assert "费用待服务端" in widget.locator(".cost-profile").nth(1).inner_text()
            widget.locator(".session").first.click()
            assert "第7次压缩后" in widget.locator(".epoch-list, .requests").first.inner_text()
            widget.screenshot(path=str(ARTIFACTS / "widget-expanded.png"), full_page=True)

            narrow = browser.new_page(viewport={"width": 280, "height": 500}, color_scheme="dark")
            narrow.set_content(widget_html)
            narrow.evaluate("""value => window.dispatchEvent(new MessageEvent('message', {source: window, data: {jsonrpc:'2.0', method:'ui/notifications/tool-result', params:{structuredContent:value}}}))""", state)
            narrow.locator("#trigger").click()
            panel = narrow.locator("#panel").bounding_box()
            assert panel and panel["x"] >= 0 and panel["x"] + panel["width"] <= 280
            narrow.screenshot(path=str(ARTIFACTS / "widget-narrow-dark.png"), full_page=True)
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
