// yaya 0.1 preview UI — vanilla JS client for the web adapter.
// Connects to /ws, renders assistant deltas as streaming text, shows
// tool start/result frames inline, and fetches /api/plugins once at
// boot to populate the status panel. The full pi-web-ui replacement
// is tracked as a future PR; this shell exists so the Python wheel
// works end-to-end the moment a user runs `yaya serve`.

const statusEl = document.getElementById("status");
const transcriptEl = document.getElementById("transcript");
const pluginsEl = document.getElementById("plugins");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");

/** Active streaming assistant bubble, keyed by session id. */
const activeAssistant = new Map();

let ws = null;

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = "status status--" + cls;
}

function appendMsg(kind, text, meta) {
  const wrap = document.createElement("div");
  wrap.className = "msg msg--" + kind;
  if (meta) {
    const m = document.createElement("span");
    m.className = "msg__meta";
    m.textContent = meta;
    wrap.appendChild(m);
  }
  const body = document.createElement("span");
  body.textContent = text;
  wrap.appendChild(body);
  transcriptEl.appendChild(wrap);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
  return body;
}

function handleFrame(frame) {
  const type = frame.type;
  const sid = frame.session_id || "broadcast";
  if (type === "assistant.delta") {
    let body = activeAssistant.get(sid);
    if (!body) {
      body = appendMsg("assistant", "", "assistant");
      activeAssistant.set(sid, body);
    }
    body.textContent += frame.content || "";
  } else if (type === "assistant.done") {
    const body = activeAssistant.get(sid);
    if (body && frame.content) body.textContent = frame.content;
    activeAssistant.delete(sid);
  } else if (type === "tool.start") {
    appendMsg(
      "tool",
      "→ " + (frame.name || "?") + " " + JSON.stringify(frame.args || {}),
      "tool.start",
    );
  } else if (type === "tool.result") {
    const ok = frame.ok ? "ok" : "error";
    const payload = frame.ok ? frame.value : frame.error;
    appendMsg("tool", ok + " ← " + JSON.stringify(payload), "tool.result");
  } else if (type === "kernel.error" || type === "plugin.error") {
    appendMsg("error", JSON.stringify(frame), type);
  } else if (type === "plugin.loaded" || type === "plugin.removed") {
    loadPlugins();
  }
}

async function loadPlugins() {
  try {
    const resp = await fetch("/api/plugins");
    if (!resp.ok) return;
    const data = await resp.json();
    pluginsEl.replaceChildren();
    for (const row of data.plugins || []) {
      const li = document.createElement("li");
      li.textContent = row.name + " · " + row.category + " · " + row.status;
      pluginsEl.appendChild(li);
    }
  } catch (_err) {
    /* ignore — panel is best-effort */
  }
}

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const url = proto + "//" + location.host + "/ws";
  ws = new WebSocket(url);
  setStatus("connecting…", "pending");
  ws.onopen = () => setStatus("connected", "ok");
  ws.onclose = () => {
    setStatus("disconnected", "error");
    setTimeout(connect, 2000);
  };
  ws.onerror = () => setStatus("error", "error");
  ws.onmessage = (ev) => {
    try {
      handleFrame(JSON.parse(ev.data));
    } catch (_err) {
      /* malformed server frame — ignore */
    }
  };
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
  appendMsg("user", text, "you");
  ws.send(JSON.stringify({ type: "user.message", text }));
  input.value = "";
  input.focus();
});

input.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    form.requestSubmit(sendBtn);
  }
});

connect();
loadPlugins();
