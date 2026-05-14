const els = {
  chatStream: document.querySelector("#chatStream"),
  rawRequirement: document.querySelector("#rawRequirement"),
  generateBtn: document.querySelector("#generateBtn"),
  resetBtn: document.querySelector("#resetBtn"),
  serviceStatus: document.querySelector("#serviceStatus"),
  quickActions: document.querySelector(".quick-actions"),
};

const SESSION_KEY = "sales-agent-session-id";

const state = {
  sessionId: getOrCreateSessionId(),
  latestResponse: null,
  turnId: 0,
  pending: false,
};

function getOrCreateSessionId() {
  const saved = window.localStorage.getItem(SESSION_KEY);
  if (saved) return saved;

  const generated =
    window.crypto?.randomUUID?.() ||
    `sales-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  window.localStorage.setItem(SESSION_KEY, generated);
  return generated;
}

function setServiceStatus(text, variant = "online") {
  els.serviceStatus.textContent = text;
  els.serviceStatus.classList.toggle("offline", variant === "offline");
  els.serviceStatus.classList.toggle("busy", variant === "busy");

  const dot = document.createElement("i");
  els.serviceStatus.prepend(dot);
}

function collectInput() {
  return els.rawRequirement.value.trim();
}

function scrollToBottom() {
  els.chatStream.scrollTo({
    top: els.chatStream.scrollHeight,
    behavior: "smooth",
  });
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    const map = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return map[char];
  });
}

function appendMessage(role, html) {
  const section = document.createElement("section");
  section.className = `message ${role}`;
  section.innerHTML = `
    <div class="avatar">${role === "user" ? "S" : "A"}</div>
    <div class="bubble">${html}</div>
  `;
  els.chatStream.appendChild(section);
  scrollToBottom();
  return section;
}

function renderLoading() {
  return appendMessage(
    "assistant",
    `<div class="typing">
      <span></span><span></span><span></span>
    </div>`
  );
}

function getSummaryValue(summary, keys) {
  for (const key of keys) {
    const value = summary?.[key];
    if (value !== undefined && value !== null && value !== "" && value !== "-") {
      return value;
    }
  }
  return "";
}

function renderActiveRequirement(activeRequirement) {
  if (!activeRequirement?.summary && !activeRequirement?.canonical_query) return "";

  const summary = activeRequirement.summary || {};
  const fields = [
    ["场景", getSummaryValue(summary, ["scene", "scenario"])],
    ["产品模块", getSummaryValue(summary, ["product_module", "module", "category"])],
    ["计费周期", getSummaryValue(summary, ["billing_cycle", "payment_cycle"])],
    ["签约方式", getSummaryValue(summary, ["contract_mode"])],
    ["带宽", getSummaryValue(summary, ["bandwidth", "bandwidth_mbps"])],
    ["预算", getSummaryValue(summary, ["budget", "budget_amount"])],
  ].filter(([, value]) => value);

  const fieldHtml = fields
    .map(
      ([label, value]) => `
        <div>
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
        </div>`
    )
    .join("");

  return `
    <details class="active-requirement">
      <summary>当前业务状态</summary>
      ${fieldHtml ? `<div class="state-grid">${fieldHtml}</div>` : ""}
      ${
        activeRequirement.canonical_query
          ? `<p>${escapeHtml(activeRequirement.canonical_query)}</p>`
          : ""
      }
    </details>
  `;
}

function renderAnswer(response) {
  const answer = response?.answer || "后端没有返回内容。";
  const resultType = answer.includes("报价单模板数据")
    ? "quote"
    : answer.includes("方案书 / 报价说明")
      ? "proposal"
      : "recommendation";

  return `
    <article class="agent-answer ${resultType}">
      ${escapeHtml(answer)}
    </article>
    ${renderActiveRequirement(response?.active_requirement)}
    ${renderFeedbackPrompt()}
  `;
}

function renderError(error) {
  return `
    <div class="error-card">
      <strong>后端连接失败</strong>
      <p>${escapeHtml(error?.message || error || "请确认 Web 服务已启动。")}</p>
    </div>
  `;
}

function renderFeedbackPrompt() {
  return `
    <div class="feedback-card" data-feedback>
      <span>这次结果是否可用？</span>
      <div>
        <button type="button" data-action="feedback" data-value="good">可用</button>
        <button type="button" data-action="feedback" data-value="bad">需调整</button>
      </div>
    </div>
  `;
}

async function sendChatMessage(message) {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: state.sessionId,
      message,
    }),
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

async function resetSession() {
  await fetch("/api/reset", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: state.sessionId,
    }),
  });
}

async function runRecommendation(messageOverride) {
  if (state.pending) return;

  const message = (messageOverride || collectInput()).trim();
  if (!message) {
    els.rawRequirement.focus();
    return;
  }

  state.turnId += 1;
  state.pending = true;
  setServiceStatus("处理中", "busy");
  appendMessage("user", `<p>${escapeHtml(message)}</p>`);

  if (!messageOverride) {
    els.rawRequirement.value = "";
    autoResizeTextarea();
  }

  els.generateBtn.disabled = true;
  const loading = renderLoading();

  try {
    const response = await sendChatMessage(message);
    state.latestResponse = response;
    loading.querySelector(".bubble").innerHTML = renderAnswer(response);
    setServiceStatus("服务正常", "online");
  } catch (error) {
    loading.querySelector(".bubble").innerHTML = renderError(error);
    setServiceStatus("服务异常", "offline");
  } finally {
    els.generateBtn.disabled = false;
    state.pending = false;
    scrollToBottom();
  }
}

function autoResizeTextarea() {
  els.rawRequirement.style.height = "auto";
  els.rawRequirement.style.height = `${Math.min(els.rawRequirement.scrollHeight, 140)}px`;
}

els.generateBtn.addEventListener("click", () => runRecommendation());

els.resetBtn.addEventListener("click", async () => {
  if (state.pending) return;
  try {
    await resetSession();
    els.chatStream.innerHTML = "";
    appendMessage(
      "assistant",
      `<strong>已清空当前会话。</strong><p>可以重新输入客户需求，我会从新的业务状态开始。</p>`
    );
    setServiceStatus("服务正常", "online");
  } catch (error) {
    appendMessage("assistant", renderError(error));
    setServiceStatus("服务异常", "offline");
  }
});

els.quickActions.addEventListener("click", (event) => {
  const button = event.target.closest("[data-quick]");
  if (!button) return;
  runRecommendation(button.dataset.quick);
});

els.rawRequirement.addEventListener("input", autoResizeTextarea);

els.rawRequirement.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    runRecommendation();
  }
});

els.chatStream.addEventListener("click", (event) => {
  const feedbackBtn = event.target.closest('[data-action="feedback"]');
  if (!feedbackBtn) return;
  const card = feedbackBtn.closest("[data-feedback]");
  if (!card) return;

  const label = feedbackBtn.dataset.value === "good" ? "已记录：可用" : "已记录：需调整";
  card.innerHTML = `<span>${label}</span>`;
});

autoResizeTextarea();
setServiceStatus("服务正常", "online");
