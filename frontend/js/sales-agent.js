const els = {
  chatStream: document.querySelector("#chatStream"),
  rawRequirement: document.querySelector("#rawRequirement"),
  generateBtn: document.querySelector("#generateBtn"),
  resetBtn: document.querySelector("#resetBtn"),
  serviceStatus: document.querySelector("#serviceStatus"),
  quickActions: document.querySelector(".quick-actions"),
};

const APP_VERSION = "20260515-chat-color-v11";
const SESSION_KEY = "sales-agent-session-id";
const VERSION_KEY = "sales-agent-frontend-version";
const CASE_SCENE_LABELS = {
  sd_wan: "组网互联",
  intl_route_optimization: "国际访问优化",
  isp_private_line: "互联网接入",
};

ensureCompatibleFrontendVersion();

const state = {
  sessionId: getOrCreateSessionId(),
  activeCaseId: null,
  businessContext: null,
  latestResponse: null,
  turnId: 0,
  pending: false,
};

function ensureCompatibleFrontendVersion() {
  const savedVersion = window.localStorage.getItem(VERSION_KEY);
  if (savedVersion === APP_VERSION) return;
  window.localStorage.removeItem(SESSION_KEY);
  window.localStorage.setItem(VERSION_KEY, APP_VERSION);
}

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

function formatMoney(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "";
  return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(amount)} 元`;
}

function formatArray(value) {
  if (!Array.isArray(value)) return "";
  return value
    .map((item) => formatSummaryValue(item))
    .filter(Boolean)
    .join("、");
}

function formatSummaryValue(value) {
  if (value === undefined || value === null || value === "" || value === "-") return "";
  if (Array.isArray(value)) return formatArray(value);
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return value;
  if (typeof value !== "object") return String(value);

  if (value.upper_cny !== undefined && value.upper_cny !== null) {
    return `${formatMoney(value.upper_cny)}以内`;
  }
  if (value.bandwidth_mbps) {
    const bandwidths = Array.isArray(value.bandwidth_mbps)
      ? value.bandwidth_mbps
      : [value.bandwidth_mbps];
    return bandwidths.map((item) => `${item}M`).join("、");
  }
  if (value.user_count) return `${value.user_count} 人`;

  const compact = Object.entries(value)
    .map(([key, item]) => {
      const formatted = formatSummaryValue(item);
      return formatted ? `${key}: ${formatted}` : "";
    })
    .filter(Boolean)
    .join("；");
  return compact || "";
}

function updateConversationState(response) {
  state.latestResponse = response;
  state.activeCaseId = response?.active_case_id || response?.case_id || state.activeCaseId;
  state.businessContext = response?.business_context || state.businessContext;
}

function currentCase() {
  return responseCase(state.latestResponse) || state.businessContext?.cases?.find((item) => item.case_id === state.activeCaseId);
}

function responseCase(response) {
  return response?.current_case || null;
}

function businessCaseCount() {
  return state.businessContext?.cases?.length || 0;
}

function renderActiveRequirement(activeRequirement) {
  const caseInfo = currentCase();
  if (!activeRequirement?.summary && !activeRequirement?.canonical_query && !caseInfo) return "";

  const summary = activeRequirement?.summary || {};
  const fields = [
    ["产品线", caseInfo?.product_module_name],
    ["场景", CASE_SCENE_LABELS[caseInfo?.product_module_key] || summary.scenarios || getSummaryValue(summary, ["scene", "scenario"])],
    ["产品模块", caseInfo?.product_module_name || summary.target_categories || getSummaryValue(summary, ["product_module", "module", "category"])],
    ["计费周期", summary.billing_cycles || getSummaryValue(summary, ["billing_cycle", "payment_cycle"])],
    ["签约方式", summary.contract_modes || getSummaryValue(summary, ["contract_mode"])],
    ["带宽", summary.spec_requirements?.bandwidth_mbps || getSummaryValue(summary, ["bandwidth", "bandwidth_mbps"])],
    ["预算", summary.budget || getSummaryValue(summary, ["budget", "budget_amount"])],
    ["当前业务", state.activeCaseId],
    ["业务数量", businessCaseCount()],
  ]
    .map(([label, value]) => [label, formatSummaryValue(value)])
    .filter(([, value]) => value);

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
        activeRequirement?.canonical_query
          ? `<p>${escapeHtml(activeRequirement.canonical_query)}</p>`
          : ""
      }
    </details>
  `;
}

function renderSelectedPackage(selectedPackage) {
  if (!selectedPackage?.product_id) return "";
  return `
    <div class="selected-package">
      <span>已选择方案</span>
      <strong>${escapeHtml(selectedPackage.product_id)}</strong>
      ${
        selectedPackage.billing_cycle
          ? `<em>${escapeHtml(selectedPackage.billing_cycle)}</em>`
          : ""
      }
    </div>
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
    ${renderSelectedPackage(response?.selected_package)}
    ${renderActiveRequirement(response?.active_requirement)}
    ${response?.response_type === "system" ? "" : renderFeedbackPrompt()}
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

async function sendAction(action) {
  const response = await fetch("/api/action", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: state.sessionId,
      case_id: state.activeCaseId,
      action,
    }),
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

async function createProductCase(productModule) {
  const response = await fetch("/api/case/new", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: state.sessionId,
      product_module: productModule,
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
    updateConversationState(response);
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

async function runAgentAction(action) {
  if (state.pending) return;

  state.turnId += 1;
  state.pending = true;
  setServiceStatus("处理中", "busy");

  const actionText = {
    generate_proposal: "生成方案",
    generate_quote_sheet: "生成报价单",
    calculate_quote: "计算最终报价",
  }[action] || action;

  appendMessage("user", `<p>${escapeHtml(actionText)}</p>`);
  els.generateBtn.disabled = true;
  const loading = renderLoading();

  try {
    const response = await sendAction(action);
    updateConversationState(response);
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

async function runProductCase(productModule, label) {
  if (state.pending) return;

  state.turnId += 1;
  state.pending = true;
  setServiceStatus("处理中", "busy");
  appendMessage("user", `<p>${escapeHtml(label)}</p>`);
  els.generateBtn.disabled = true;
  const loading = renderLoading();

  try {
    const response = await createProductCase(productModule);
    updateConversationState(response);
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
    state.activeCaseId = null;
    state.businessContext = null;
    state.latestResponse = null;
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
  const productButton = event.target.closest("[data-case-product]");
  if (productButton?.dataset.caseProduct) {
    runProductCase(productButton.dataset.caseProduct, productButton.textContent.trim());
    return;
  }

  const button = event.target.closest("[data-action-command], [data-quick]");
  if (!button) return;
  const legacyActionMap = {
    生成方案: "generate_proposal",
    生成报价单: "generate_quote_sheet",
    计算报价: "calculate_quote",
    计算最终报价: "calculate_quote",
  };
  const action = button.dataset.actionCommand || legacyActionMap[button.dataset.quick];
  if (!action) return;
  runAgentAction(action);
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
console.info(`Sales Agent frontend version: ${APP_VERSION}`);
