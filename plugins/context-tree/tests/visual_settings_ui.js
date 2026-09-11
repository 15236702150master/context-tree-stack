const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const script = path.join(root, "scripts", "context_tree.py");
const artifacts = path.join(__dirname, "artifacts");
fs.mkdirSync(artifacts, { recursive: true });
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "context-tree-settings-"));
const store = path.join(temporary, ".context-tree");
const python = process.env.PYTHON || "python";
const server = spawn(python, [script, "--store", store, "ui", "--port", "0", "--no-open"], {
  stdio: ["ignore", "pipe", "pipe"],
  env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" },
});

function firstLine(stream) {
  return new Promise((resolve, reject) => {
    let buffer = "";
    stream.setEncoding("utf8");
    stream.on("data", (chunk) => {
      buffer += chunk;
      const newline = buffer.indexOf("\n");
      if (newline >= 0) resolve(buffer.slice(0, newline).trim());
    });
    server.once("exit", (code) => reject(new Error(`settings server exited with ${code}`)));
  });
}

function stopServer() {
  if (server.exitCode === null) {
    if (process.platform === "win32") {
      spawnSync("taskkill", ["/pid", String(server.pid), "/t", "/f"], { stdio: "ignore" });
    } else {
      server.kill();
    }
  }
  server.stdout.destroy();
  server.stderr.destroy();
  server.unref();
}

(async () => {
  const url = await firstLine(server.stdout);
  const browser = await chromium.launch({ headless: true });
  try {
    const desktop = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await desktop.goto(url);
    const toggle = desktop.locator("#float-persistent");
    await toggle.waitFor();
    assert.equal(await toggle.isChecked(), false);
    assert.equal(await desktop.locator("#float-persistent-state").innerText(), "空闲时自动隐藏");
    await toggle.check();
    await desktop.waitForFunction(() => (
      document.querySelector("#float-persistent-state")?.textContent === "始终保留折叠按钮"
    ));
    assert.equal(await desktop.locator("#float-persistent-state").innerText(), "始终保留折叠按钮");
    assert.equal(await desktop.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await desktop.screenshot({ path: path.join(artifacts, "settings-persistent-desktop.png"), fullPage: true });

    const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await mobile.goto(url);
    await mobile.locator("#float-persistent").waitFor();
    await mobile.waitForFunction(() => document.querySelector("#float-persistent")?.checked === true);
    assert.equal(await mobile.locator("#float-persistent").isChecked(), true);
    assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await mobile.screenshot({ path: path.join(artifacts, "settings-persistent-mobile.png"), fullPage: true });

    const widget = await browser.newPage({ viewport: { width: 380, height: 500 } });
    await widget.setContent(fs.readFileSync(path.join(root, "assets", "widget.html"), "utf8"));
    await widget.evaluate((value) => window.dispatchEvent(new MessageEvent("message", {
      source: window,
      data: { jsonrpc: "2.0", method: "ui/notifications/tool-result", params: { structuredContent: value } },
    })), {
      config: { consolidate_every: 5 }, topics: [], summary: { pending_count: 4 },
      usage: { mode: "real", capacity_tokens: 272000, sessions: [] },
      active_sessions: [{
        session_id: "thread-a", usage_session_id: "thread-a:7", session_title: "研究对话上下文延续",
        window_slot: "7", context_tokens: 202905, capacity_tokens: 353400, percent: 57.4,
        first_token_ms: 3823, compaction_count: 7, level: "warning",
        cost_profile_status: "partial_history", turn_cost_request_count: 105,
        turn_avg_cost: 0.07874957, turn_avg_context_cost: 0.07092929,
        turn_avg_first_token_ms: 4862.6, turn_avg_duration_ms: 10335,
        current_epoch_index: 10, current_epoch_avg_cost: 0.07874957,
        reference_epoch_label: "第3次压缩后", reference_avg_cost: 0.10919274,
        reference_epoch_extra_avg_cost: -0.03044317,
        attention_text: "历史较长，已使用增量读取；建议检查交接质量",
      }],
    });
    await widget.locator("#trigger").click();
    await widget.locator(".attention").waitFor();
    assert.equal(await widget.locator(".attention").innerText(), "历史较长，已使用增量读取；建议检查交接质量");
    assert.match(await widget.locator(".metrics").innerText(), /202,905 \/ 353,400/);
    assert.match(await widget.locator(".cost-profile").innerText(), /\$0\.0787/);
    assert.match(await widget.locator(".cost-profile").innerText(), /\$0\.1092/);
    assert.equal(await widget.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await widget.screenshot({ path: path.join(artifacts, "widget-new-session-warning.png"), fullPage: true });
    console.log("Settings visual checks passed: desktop and mobile");
  } finally {
    await browser.close();
    stopServer();
    fs.rmSync(temporary, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
})().catch((error) => {
  stopServer();
  fs.rmSync(temporary, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  console.error(error);
  process.exitCode = 1;
});
