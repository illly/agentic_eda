const $ = (id) => document.getElementById(id);

// 도구 결과는 외부 데이터다. 항상 textContent로만 넣는다 (innerHTML 금지).
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
  const list = $("tools");
  list.replaceChildren();
  if (!data.tools.length) list.append(el("li", "등록된 도구가 없습니다."));
  for (const tool of data.tools) {
    const item = el("li");
    item.append(el("strong", tool.name), el("span", " · " + tool.source), el("p", tool.description));
    list.append(item);
  }
}

function addMessage(who, text, calls) {
  const box = el("div", undefined, "msg " + who);
  box.append(el("p", text));
  for (const call of calls || []) {
    const details = el("details");
    details.append(el("summary", "도구 호출: " + call.tool + " " + call.args), el("pre", call.result));
    box.append(details);
  }
  $("log").append(box);
  box.scrollIntoView({ block: "end" });
}

$("form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = $("message").value.trim();
  if (!message) return;
  addMessage("user", message);
  $("message").value = "";
  $("send").disabled = true;
  try {
    const out = await post("/api/chat", { message });
    if (out.ok) {
      addMessage("agent", out.answer + "\n(" + out.steps + "단계 · " + out.tokens + "토큰 · " + out.elapsed + "초)", out.calls);
    } else {
      addMessage("error", out.message);
    }
  } catch (err) {
    addMessage("error", "서버에 연결하지 못했습니다.");
  } finally {
    $("send").disabled = false;
    loadState();
  }
});

$("message").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("form").requestSubmit();
  }
});

$("reset").addEventListener("click", async () => {
  await post("/api/chat/reset");
  $("log").replaceChildren();
});

$("refresh").addEventListener("click", loadState);
loadState();
