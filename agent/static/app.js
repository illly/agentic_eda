const $ = (id) => document.getElementById(id);

// 아래 esc · md는 example/agent-sim/static/app.js에서 그대로 가져왔다.
const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/* ── 마크다운 ────────────────────────────────────────── */
/* 모델 답변에 흔히 나오는 것만 그린다 — 제목, 목록, 굵게·기울임, 코드. 외부 라이브러리 없이.
   먼저 HTML을 이스케이프하므로 모델이 태그를 보내도 그대로 글자로 보인다. */
function md(src) {
  const inline = (s) => esc(s)
    .replace(/\\\((.+?)\\\)/g, (_, x) => `<code>${plainMath(x).trim()}</code>`)   // 인라인 수식 \( \)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<i>$2</i>");
  const lines = String(src == null ? "" : src).replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let list = null, para = [], code = null, gap = false, math = null;
  const flushPara = () => { if (para.length) { out.push(`<p>${para.join("<br>")}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.tag}>${list.items.join("")}</${list.tag}>`); list = null; } gap = false; };
  // 모델이 가끔 수식을 LaTeX로 쓴다. 렌더링 도구가 없으므로 기호만 바꿔 고정폭 상자에 보여 준다.
  const plainMath = (s) => s.replace(/\\times/g, "×").replace(/\\cdot/g, "·").replace(/\\le\b/g, "≤").replace(/\\ge\b/g, "≥")
    .replace(/\\sum/g, "Σ").replace(/\\pm/g, "±").replace(/\{,\}/g, ",").replace(/\\text\{([^}]*)\}/g, "$1")
    .replace(/\\[a-zA-Z]+/g, "").replace(/[{}]/g, "");
  for (const raw of lines) {
    if (code !== null) {
      if (/^```/.test(raw)) { out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`); code = null; }
      else code.push(raw);
      continue;
    }
    if (math !== null) {
      if (/^\s*\\\]\s*$/.test(raw)) { out.push(`<pre class="math">${esc(plainMath(math.join(" ")))}</pre>`); math = null; }
      else math.push(raw.trim());
      continue;
    }
    const line = raw.trimEnd();
    let m;
    if (/^```/.test(line)) { flushPara(); flushList(); code = []; continue; }
    if (/^\s*\\\[\s*$/.test(line)) { flushPara(); flushList(); math = []; continue; }
    if ((m = line.match(/^\s*\\\[(.*)\\\]\s*$/))) { flushPara(); flushList(); out.push(`<pre class="math">${esc(plainMath(m[1]))}</pre>`); continue; }
    // 빈 줄 — 목록은 바로 닫지 않는다. 모델이 항목 사이에 빈 줄을 두는 일이 잦아,
    // 다음 줄이 같은 종류의 항목이면 번호를 이어 간다.
    if (!line.trim()) { flushPara(); if (list) gap = true; continue; }
    if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
      flushPara(); flushList();
      const level = Math.min(m[1].length + 2, 6);   // 답변 안의 제목은 카드 제목보다 작게
      out.push(`<h${level}>${inline(m[2])}</h${level}>`);
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line)) {               // 표 — |로 시작하고 끝나는 줄의 연속
      flushPara(); flushList();
      const cells = line.trim().slice(1, -1).split("|").map((c) => c.trim());
      if (cells.every((c) => /^:?-{2,}:?$/.test(c))) continue;   // |---|---| 구분줄
      const last = out[out.length - 1];
      const tag = last && last.startsWith("<table") ? "td" : "th";
      const row = `<tr>${cells.map((c) => `<${tag}>${inline(c)}</${tag}>`).join("")}</tr>`;
      if (tag === "th") out.push(`<table class="md-table">${row}`);
      else out[out.length - 1] = last.replace(/<\/table>$/, "") + row;
      out[out.length - 1] += "</table>";
      continue;
    }
    if (/^\s*-{3,}\s*$/.test(line)) { flushPara(); flushList(); out.push("<hr>"); continue; }
    if ((m = line.match(/^\s*(?:[-*•]|(\d+)[.)])\s+(.*)$/))) {
      flushPara();
      const tag = m[1] ? "ol" : "ul";
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      gap = false;
      list.items.push(`<li>${inline(m[2])}</li>`);
      continue;
    }
    if (list && !gap && /^\s{2,}\S/.test(raw)) {    // 목록 항목에 이어지는 들여쓴 줄
      list.items[list.items.length - 1] = list.items[list.items.length - 1]
        .replace(/<\/li>$/, `<br>${inline(line.trim())}</li>`);
      continue;
    }
    flushList();
    para.push(inline(line));
  }
  flushPara(); flushList();
  if (code !== null) out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
  if (math !== null) out.push(`<pre class="math">${esc(plainMath(math.join(" ")))}</pre>`);
  return out.join("");
}

// 게이트웨이가 만든 차트 주소만 화면에 끼워 넣는다. 다른 주소는 글자로만 보인다.
const GATEWAY = "http://127.0.0.1:8001";
const CHART_URL = /http:\/\/127\.0\.0\.1:8001\/charts\/[0-9a-f]+\.html/g;

// 도구 결과와 모델 답변은 외부 데이터다. textContent로만 넣는다.
// 예외는 에이전트 답변 하나: md()가 HTML을 먼저 이스케이프한 뒤 마크다운만 태그로 바꾼다.
function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

async function post(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return res.json();
}

async function loadState() {
  const data = await (await fetch("/api/state")).json();
  $("errors").textContent = data.errors.join(" ");
  $("errors").hidden = !data.errors.length;
  $("chipTools").textContent = "도구 " + data.tools.length;
  $("toolCount").textContent = data.tools.length + "개";
  const list = $("tools");
  list.replaceChildren();
  if (!data.tools.length) list.append(el("div", "쓸 수 있는 도구가 없습니다. MCP 게이트웨이가 켜져 있는지 확인하세요.", "empty"));
  for (const tool of data.tools) {
    // 왼쪽 칸이 좁아 설명은 접어 둔다
    const head = el("summary", undefined, "merged-head");
    head.append(el("code", tool.name), el("span", tool.source, "badge"));
    const item = el("details", undefined, "merged-tool");
    item.append(head, el("p", tool.description));
    item.dataset.name = tool.name;
    list.append(item);
  }
  markUsed();
}

// 에이전트가 고른 데이터셋(파이프라인의 소스 노드)을 도구 목록에 표시한다
function markUsed() {
  const used = new Set(PIPE.filter((n) => n.kind === "source").map((n) => n.id));
  for (const item of $("tools").children) item.classList.toggle("used", used.has(item.dataset.name));
}

function chartBlock(url) {
  const frame = el("iframe");
  frame.src = url;
  frame.title = "차트";
  frame.loading = "lazy";
  frame.setAttribute("sandbox", "allow-scripts");   // 차트 스크립트만 허용. 게이트웨이 권한은 주지 않는다
  const link = el("a", "새 창에서 열기: " + url);
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener";
  const block = el("div", undefined, "chart");
  block.append(frame, link);
  return block;
}

function addBubble(kind, who, text, stat, calls) {
  const bubble = el("div", undefined, "bubble " + kind);
  const head = el("div", undefined, "who");
  head.append(who);
  if (stat) head.append(el("span", stat, "stat"));
  const body = el("div", text, "body");
  if (kind === "to") {
    body.innerHTML = md(text);
    body.classList.add("md");
  }
  bubble.append(head, body);

  const charts = new Set(text.match(CHART_URL) || []);
  for (const call of calls || []) for (const url of call.result.match(CHART_URL) || []) charts.add(url);
  // 차트는 좁은 대화창 대신 가운데 시각화 칸에 크게 둔다. 최근 것이 위.
  for (const url of charts) $("charts").prepend(chartBlock(url));
  if (charts.size) {
    $("resultCard").hidden = false;
    const jump = el("button", "시각화 " + charts.size + "개 보기", "ghost");
    jump.type = "button";
    jump.addEventListener("click", () => $("resultCard").scrollIntoView({ block: "start" }));
    bubble.append(jump);
  }

  if (calls && calls.length) {
    const details = el("details");
    details.append(el("summary", "호출한 도구 " + calls.length + "개"));
    for (const call of calls) {
      const did = el("div", undefined, "did");
      did.append(el("div", call.tool, "did-head"), el("pre", call.args, "code"), el("pre", call.result));
      details.append(did);
    }
    bubble.append(details);
  }
  $("starters").hidden = true;
  $("log").append(bubble);
  bubble.scrollIntoView({ block: "nearest" });
  return bubble;
}

/* ── 파이프라인 그래프 ───────────────────────────────── */
// 게이트웨이의 노드 목록을 왼쪽(원본 데이터셋)에서 오른쪽(결과 노드)으로 그린다.
// 채팅이 도는 동안 0.5초마다 다시 읽어, 노드가 생기고 바뀌는 과정이 그대로 보인다.
const SVG_NS = "http://www.w3.org/2000/svg";
const KIND = { source: "데이터셋", sql: "SQL", output: "결과" };
const STATUS = { running: "실행 중…", done: "실행 완료", error: "실행 실패" };
const SHOWN_COLUMNS = 6;
let PIPE = [];                // 직전에 그린 노드들. 무엇이 바뀌었는지 찾는 데 쓴다
let CARDS = new Map();        // 노드 id -> 카드 요소. 연결선을 그릴 때 위치를 잰다

/* ── 노드 공유 링크 ───────────────────────────────────── */
// 노드마다 게이트웨이의 테이블 뷰어 주소가 있다. 이 주소가 공유 링크다.
// 링크를 대화창에 붙이고 지시하면 에이전트가 그 노드를 기준으로 시각화한다(지시문: lab/runner.py).
const nodeLink = (id) => GATEWAY + "/view/" + encodeURIComponent(id);

async function copyLink(id, button) {
  try {
    await navigator.clipboard.writeText(nodeLink(id));
    button.textContent = "링크 복사됨";
    setTimeout(() => { button.textContent = "공유"; }, 1800);
  } catch (err) {
    window.prompt("이 링크를 복사하세요", nodeLink(id));   // 복사가 막힌 환경
  }
}

function nodeCard(n, byId, changed) {
  const card = el("div", undefined,
    "pipe-node " + n.kind + (n.error ? " broken" : "") + (n.status ? " " + n.status : "") + (changed ? " changed" : ""));
  const head = el("div", undefined, "pipe-head");
  head.append(el("code", n.id), el("span", KIND[n.kind], "badge"));
  card.append(head);
  if (n.kind === "output") {
    card.append(el("div", n.title + " · " + STATUS[n.status], "pipe-meta"));
  } else {
    if (n.title) card.append(el("div", n.title, "pipe-title"));
    // 스키마 변화: 입력 노드들의 컬럼과 이름으로 비교한다
    const fromInputs = new Set(n.inputs.flatMap((i) => ((byId.get(i) || {}).columns || []).map((c) => c[0])));
    const isNew = (name) => n.kind === "sql" && !fromInputs.has(name);
    const names = new Set(n.columns.map((c) => c[0]));
    let meta = (n.rows === undefined ? "" : n.rows.toLocaleString() + "행 × ") + n.columns.length + "열";
    if (n.kind === "sql" && !n.error) {
      meta += " · 새 컬럼 " + n.columns.filter((c) => isNew(c[0])).length
        + " · 빠진 컬럼 " + [...fromInputs].filter((c) => !names.has(c)).length;
    }
    card.append(el("div", meta, "pipe-meta"));
    const ordered = n.columns.filter((c) => isNew(c[0])).concat(n.columns.filter((c) => !isNew(c[0])));   // 새 컬럼을 앞에
    const list = el("ul", undefined, "pipe-cols");
    for (const [name, type] of ordered.slice(0, SHOWN_COLUMNS)) {
      const item = el("li", (isNew(name) ? "+ " : "") + name, isNew(name) ? "new" : "");
      item.append(el("span", type));
      list.append(item);
    }
    if (ordered.length > SHOWN_COLUMNS) list.append(el("li", "… 외 " + (ordered.length - SHOWN_COLUMNS) + "개", "more"));
    if (ordered.length) card.append(list);
    if (n.sql) {
      const details = el("details");
      details.append(el("summary", "SQL"), el("pre", n.sql));
      details.addEventListener("toggle", drawLines);      // 카드 높이가 바뀌면 선을 다시 긋는다
      card.append(details);
    }
  }
  if (n.error) card.append(el("div", n.error, "pipe-error"));
  // 결과 노드의 데이터는 그 입력 노드의 데이터다(최종 데이터). 중간 노드는 자기 데이터를 공유한다.
  const target = n.kind === "output" ? n.inputs[0] : n.id;
  if (target) {
    const view = el("a", n.kind === "output" ? "최종 데이터 보기" : "데이터 보기", "ghost");
    view.href = nodeLink(target);
    view.target = "_blank";
    view.rel = "noopener";
    view.setAttribute("aria-label", target + " 데이터를 새 창에서 보기");
    const share = el("button", "공유", "ghost");
    share.type = "button";
    share.setAttribute("aria-label", target + " 공유 링크 복사");
    share.addEventListener("click", () => copyLink(target, share));
    const ask = el("button", "대화에 넣기", "ghost");
    ask.type = "button";
    ask.setAttribute("aria-label", target + " 링크를 대화창에 넣기");
    ask.addEventListener("click", () => {
      $("message").value = nodeLink(target) + " " + $("message").value;
      $("message").focus();
    });
    const actions = el("div", undefined, "pipe-actions");
    actions.append(view, share, ask);
    card.append(actions);
  }
  return card;
}

function drawLines() {
  const graph = $("pipeline");
  const svg = graph.querySelector("svg");
  if (!svg) return;
  svg.replaceChildren();
  const base = graph.getBoundingClientRect();
  for (const n of PIPE) {
    const to = CARDS.get(n.id).getBoundingClientRect();
    for (const input of n.inputs) {
      if (!CARDS.has(input)) continue;
      const from = CARDS.get(input).getBoundingClientRect();
      const x1 = from.right - base.left, y1 = from.top + from.height / 2 - base.top;
      const x2 = to.left - base.left, y2 = to.top + to.height / 2 - base.top;
      const bend = Math.max(20, (x2 - x1) / 2);
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", "M" + x1 + "," + y1 + " C" + (x1 + bend) + "," + y1 + " " + (x2 - bend) + "," + y2 + " " + x2 + "," + y2);
      if (n.kind === "output") path.setAttribute("class", "to-output");
      svg.append(path);
    }
  }
}

function drawPipeline(nodes, touched) {
  const graph = $("pipeline");
  graph.replaceChildren();
  $("pipeEmpty").hidden = nodes.length > 0;
  $("pipeCount").textContent = "노드 " + nodes.length + "개";
  CARDS = new Map();
  if (!nodes.length) return;
  const byId = new Map(nodes.map((n) => [n.id, n]));
  // 단계 = 입력 노드의 단계 + 1. 노드를 고치면 목록 순서와 입력 순서가 어긋날 수 있어 재귀로 구한다
  const depth = new Map();
  const level = (id) => {
    if (!depth.has(id)) {
      const inputs = byId.get(id).inputs.filter((i) => byId.has(i));
      depth.set(id, 0);
      depth.set(id, inputs.length ? 1 + Math.max(...inputs.map(level)) : 0);
    }
    return depth.get(id);
  };
  const columns = [];
  for (const n of nodes) {
    const card = nodeCard(n, byId, touched.has(n.id));
    CARDS.set(n.id, card);
    const at = level(n.id);
    columns[at] = columns[at] || el("div", undefined, "pipe-col");
    columns[at].append(card);
  }
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "pipe-lines");
  svg.setAttribute("aria-hidden", "true");
  graph.append(svg, ...columns.filter(Boolean));
  drawLines();
  const last = [...touched].map((id) => CARDS.get(id)).filter(Boolean).pop();
  if (last) last.scrollIntoView({ block: "nearest", inline: "nearest" });
}

// 직전 모습과 비교해 변경 기록을 만든다
function changes(before, after) {
  const old = new Map(before.map((n) => [n.id, JSON.stringify(n)]));
  const log = [], touched = new Set();
  for (const n of after) {
    const was = old.get(n.id);
    old.delete(n.id);
    if (was === JSON.stringify(n)) continue;
    touched.add(n.id);
    if (n.kind === "output") log.push(STATUS[n.status] + " — " + n.inputs[0] + " → " + n.title + (n.error ? ": " + n.error : ""));
    else if (n.error) log.push("깨짐 — " + n.id + ": " + n.error);
    else log.push((was ? "변경" : n.kind === "source" ? "데이터셋 추가" : "노드 추가") + " — " + n.id + (n.title ? " (" + n.title + ")" : "") + " · " + n.columns.length + "열");
  }
  for (const id of old.keys()) if (id !== "result") log.push("삭제 — " + id);
  if (before.length && !after.length) return { log: ["파이프라인 비움"], touched };
  return { log, touched };
}

async function loadPipeline(quiet) {
  let nodes;
  try {
    nodes = await (await fetch("/api/pipeline")).json();
  } catch (err) {
    return;
  }
  if (!Array.isArray(nodes)) return;      // 게이트웨이가 꺼져 있으면 오류 객체가 온다. 그림은 그대로 둔다
  if (JSON.stringify(nodes) === JSON.stringify(PIPE)) return;
  const { log, touched } = changes(PIPE, nodes);
  PIPE = nodes;
  if (quiet !== true) {
    for (const line of log) $("pipeLog").append(el("li", line));
    $("pipeLog").scrollTop = $("pipeLog").scrollHeight;
  }
  drawPipeline(nodes, quiet === true ? new Set() : touched);
  markUsed();
}

window.addEventListener("resize", drawLines);

$("form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = $("message").value.trim();
  if (!message) return;
  addBubble("user", "나", message);
  $("message").value = "";
  $("send").disabled = true;
  const waiting = addBubble("to typing", "에이전트", "파이프라인을 만들며 분석하는 중…");
  const poll = setInterval(loadPipeline, 500);
  let out;
  try {
    out = await post("/api/chat", { message });
  } catch (err) {
    out = { ok: false, message: "서버에 연결하지 못했습니다." };
  }
  clearInterval(poll);
  await loadPipeline();
  waiting.remove();
  if (out.ok) {
    addBubble("to", "에이전트", out.answer, out.steps + "단계 · " + out.tokens.toLocaleString() + "토큰 · " + out.elapsed + "초", out.calls);
  } else {
    addBubble("fail", "오류", out.message);
  }
  $("send").disabled = false;
  $("message").focus();
  loadState();
});

$("reset").addEventListener("click", async () => {
  await post("/api/chat/reset");
  $("log").replaceChildren();
  $("charts").replaceChildren();
  $("resultCard").hidden = true;
  $("starters").hidden = false;
  $("pipeLog").replaceChildren();
  await loadPipeline(true);
});

/* ── 대화창 너비 조절 ─────────────────────────────────── */
// 가운데와 대화창 사이의 막대를 끌거나, 막대에 초점을 두고 좌우 화살표 키를 누른다.
function setChatWidth(px) {
  const width = Math.round(Math.max(300, Math.min(px, window.innerWidth - 420)));
  $("workspace").style.setProperty("--chat-w", width + "px");
  $("splitter").setAttribute("aria-valuenow", width);
  try {
    localStorage.setItem("chatWidth", width);
  } catch (err) { /* 저장이 막힌 브라우저에서도 조절은 된다 */ }
}

$("splitter").addEventListener("pointerdown", (event) => {
  const bar = $("splitter");
  bar.setPointerCapture(event.pointerId);     // 차트(iframe) 위를 지나가도 끌기가 끊기지 않는다
  const move = (e) => setChatWidth($("chatPane").getBoundingClientRect().right - e.clientX - bar.offsetWidth / 2);
  const stop = () => bar.removeEventListener("pointermove", move);
  bar.addEventListener("pointermove", move);
  bar.addEventListener("pointerup", stop, { once: true });
  bar.addEventListener("pointercancel", stop, { once: true });
});

$("splitter").addEventListener("keydown", (event) => {
  const step = { ArrowLeft: 32, ArrowRight: -32 }[event.key];
  if (!step) return;
  event.preventDefault();
  setChatWidth($("chatPane").getBoundingClientRect().width + step);
});

try {
  const saved = Number(localStorage.getItem("chatWidth"));
  if (saved) setChatWidth(saved);
} catch (err) { /* 저장된 값이 없으면 기본 너비 */ }

for (const starter of document.querySelectorAll(".starter")) {
  starter.addEventListener("click", () => {
    $("message").value = starter.textContent;
    $("message").focus();
  });
}

$("refresh").addEventListener("click", loadState);
loadState();
loadPipeline(true);
