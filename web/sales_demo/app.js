(function () {
  const DEFAULT_QUERY = "沿街奶茶店准备新装宽带，主要用于收银、外卖接单、店内 Wi-Fi 和监控。";
  const EXPECTED_RUNTIME_VERSION = "sales-demo-single-v5";
  const DEMO_CASES = Array.isArray(window.SALES_DEMO_CASES) ? window.SALES_DEMO_CASES : [];

  const QUESTION_TEMPLATES = {
    store: [
      "这是单门店还是多门店，门店大致地址或所在区域在哪里？",
      "收银和外卖平台是否必须持续在线，断网会不会直接影响营业？",
      "监控是本地存储还是云端上传，大概有多少路摄像头？",
      "店内是否需要无线网络覆盖？员工和顾客是否要使用不同的网络？主线路故障时是否需要一条备用网络？",
      "预算有限是大概多少金额，偏向年付、月付还是先试用？",
      "下月开通前是否需要上门勘查、资源覆盖确认或临时过渡方案？",
    ],
    voice: [
      "这是新建呼叫中心，还是已有总机、PBX 或号码需要改造迁移？",
      "30 个坐席中，最忙时预计有多少人会同时通话？是否所有坐席都需要拨打外部电话？",
      "是否需要保留原号码、统一总机号码或分机号？外部客户是否需要直接拨到具体坐席？",
      "客户来电后是否需要按键语音导航？另外是否需要录音、来电转接、黑白名单或外呼频次控制？",
      "是否有外呼合规、实名材料、授权盖章或行业监管要求？",
      "预算范围、月付/年付偏好、期望开通时间是什么？",
    ],
    overseas: [
      "海外 SaaS 或服务器具体在哪个国家/地区，主要访问什么应用？",
      "访问来源有几个地点，各有多少人或终端？",
      "当前慢的表现是什么，例如登录慢、下载慢、视频会议卡顿？",
      "是否有官网或服务器需要长期使用同一个网络地址供外部访问？另外，是否涉及备案或其他合规要求？",
      "预算范围、试用周期和期望开通时间是什么？",
    ],
    networking: [
      "需要互联的站点有几个，分别在哪些城市或地址？",
      "是点对点互联，还是总部、分公司、仓库等多点组网？",
      "每个站点大约多少人或终端使用，核心系统是什么？",
      "是否有时延、带宽、备份线路或安全隔离要求？",
      "预算范围、开通周期和月付/年付偏好是什么？",
    ],
    fixed_ip: [
      "服务器承载什么对外业务？这些业务是否需要长期使用同一个网络地址供外部访问？",
      "域名和备案是否已经完成，是否需要协助办理？",
      "期望上下行带宽和外部访问峰值是多少？",
      "是否需要防火墙、DDoS防护、端口开放或访问控制？",
      "预算范围、协议期和期望开通时间是什么？",
    ],
    pricing: [
      "客户办理地址或所在城市在哪里？",
      "期望的上行、下行带宽分别是多少？",
      "主要用于办公、视频会议、文件上传还是服务器对外？",
      "是否有官网或服务器需要长期使用同一个网络地址供外部访问？另外是否需要固话、无线覆盖或其他功能？",
      "需要对比年付、月付、协议期和一次性费用中的哪些项目？",
    ],
    service_process: [
      "确认是同城移机、跨区移机，还是原址拆机后新装？",
      "原业务号码、合同编号和客户名称是否已经明确？",
      "新办公地址在哪里，是否已经确认资源覆盖？",
      "营业执照、经办人证件、授权书和盖章材料是否已经准备？",
      "期望完成时间是什么，是否要求搬迁期间业务不中断？",
    ],
    general: [
      "客户从哪里访问或使用业务，例如办公室、门店、总部或分公司？",
      "客户要访问哪里或办理什么业务，例如海外 SaaS、国内总部、固定电话或 IDC？",
      "大约多少人或终端使用，预算和期望开通时间是什么？",
    ],
  };

  const state = {
    started: false,
    finished: false,
    round: 0,
    maxRounds: 6,
    sessionId: "",
    query: DEFAULT_QUERY,
    currentUserText: "",
    history: [],
    askedQuestions: [],
    snapshot: null,
    finalAnswer: "",
    selectedCaseId: DEMO_CASES[0]?.id || null,
    submitting: false,
    backendConnected: false,
    backendProblem: "",
    runtime: {},
  };

  const $ = (id) => document.getElementById(id);

  function create(tag, options = {}, children = []) {
    const node = document.createElement(tag);
    Object.entries(options).forEach(([key, value]) => {
      if (key === "className") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "html") node.innerHTML = value;
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else if (value !== undefined && value !== null) node.setAttribute(key, value);
    });
    children.forEach((child) => node.appendChild(typeof child === "string" ? document.createTextNode(child) : child));
    return node;
  }

  const slowScroller = (() => {
    let animationFrameId = null;
    let delayTimerId = null;
    let activeContainer = null;
    const PIXELS_PER_SECOND = 140;

    function cancel() {
      if (delayTimerId !== null) {
        clearTimeout(delayTimerId);
        delayTimerId = null;
      }
      if (animationFrameId !== null) {
        cancelAnimationFrame(animationFrameId);
        animationFrameId = null;
      }
      activeContainer = null;
    }

    function toBottom(container, delay = 140) {
      cancel();
      activeContainer = container;
      delayTimerId = setTimeout(() => {
        delayTimerId = null;
        if (!activeContainer) return;
        const start = activeContainer.scrollTop;
        const target = Math.max(0, activeContainer.scrollHeight - activeContainer.clientHeight);
        const distance = Math.max(0, target - start);
        if (distance < 2) {
          activeContainer = null;
          return;
        }
        const duration = Math.min(4600, Math.max(900, (distance / PIXELS_PER_SECOND) * 1000));
        const startedAt = performance.now();
        const step = (now) => {
          if (!activeContainer) return;
          const progress = Math.min(1, (now - startedAt) / duration);
          const eased = progress < 0.5
            ? 2 * progress * progress
            : 1 - Math.pow(-2 * progress + 2, 2) / 2;
          activeContainer.scrollTop = start + distance * eased;
          if (progress >= 1) {
            activeContainer.scrollTop = target;
            animationFrameId = null;
            activeContainer = null;
            return;
          }
          animationFrameId = requestAnimationFrame(step);
        };
        animationFrameId = requestAnimationFrame(step);
      }, delay);
    }

    return { cancel, toBottom };
  })();

  function init() {
    $("queryInput").value = DEFAULT_QUERY;
    renderScenarioButtons();
    bindEvents();
    resetDemo(false);
    detectBackendRuntime();
  }

  function bindEvents() {
    $("startButton").addEventListener("click", startDemo);
    $("resetButton").addEventListener("click", () => resetDemo(true));
    $("replyForm").addEventListener("submit", submitReply);
    $("queryInput").addEventListener("keydown", handleReplyKeydown);
    $("sampleReplyButton").addEventListener("click", fillSampleReply);
    $("copyAnswerButton").addEventListener("click", copyAnswer);
    $("queryInput").addEventListener("input", syncSelectedCaseFromQuery);
    const chatStream = $("chatStream");
    ["wheel", "touchstart", "pointerdown"].forEach((eventName) => {
      chatStream.addEventListener(eventName, slowScroller.cancel, { passive: true });
    });
  }

  function renderScenarioButtons() {
    $("scenarioStrip").replaceChildren(
      ...DEMO_CASES.map((item) =>
        create("button", {
          className: `scenario-button ${item.id === state.selectedCaseId ? "active" : ""}`.trim(),
          type: "button",
          onclick: () => {
            state.selectedCaseId = item.id;
            $("queryInput").value = item.query;
            resetDemo(false);
          },
        }, [
          create("strong", { text: item.title }),
          create("span", { text: item.type }),
        ])
      )
    );
    renderCaseSummary();
  }

  function renderCaseSummary() {
    const demoCase = selectedCase();
    if (!demoCase) {
      $("caseSummary").replaceChildren(create("p", { text: "自定义问题将使用通用追问策略。" }));
      return;
    }
    $("caseSummary").replaceChildren(
      create("h3", { text: "案例验收目标" }),
      create("dl", {}, [
        create("dt", { text: "预期分类" }),
        create("dd", { text: demoCase.expected.category }),
        create("dt", { text: "预期主推" }),
        create("dd", { text: demoCase.expected.primaryProduct }),
        create("dt", { text: "必须提示" }),
        create("dd", { text: demoCase.expected.requiredRisks.join("、") }),
        create("dt", { text: "禁止错误" }),
        create("dd", { text: demoCase.expected.forbidden.join("、") }),
      ])
    );
  }

  async function startDemo() {
    const query = $("queryInput").value.trim();
    if (!query) {
      showToast("请先输入客户问题");
      return;
    }
    if (!(await ensureBackendReady())) {
      appendMessage(
        "agent",
        "后端服务未连接",
        state.backendProblem ||
          "当前页面无法连接销售推荐后端。请先启动服务，再点击“开始演示”。"
      );
      renderStatus("后端服务未连接", "0 / 6");
      showToast("请先启动销售推荐后端服务");
      return;
    }
    state.started = true;
    state.finished = false;
    state.round = 0;
    state.query = query;
    state.currentUserText = query;
    state.sessionId = createSessionId();
    state.history = [];
    state.askedQuestions = [];
    state.submitting = false;
    state.maxRounds = 6;
    $("chatStream").replaceChildren();
    $("answerBox").classList.remove("ready");
    $("answerBox").textContent = "交互进行中。";
    clearRanking();
    appendMessage("system", "演示开始", `初始需求：${state.query}`);
    $("queryInput").value = "";
    syncQuestionInputMode();
    runNextTurn();
  }

  function resetDemo(clearQuery) {
    state.started = false;
    state.finished = false;
    state.round = 0;
    state.query = clearQuery ? DEFAULT_QUERY : $("queryInput").value.trim() || DEFAULT_QUERY;
    state.currentUserText = "";
    state.sessionId = createSessionId();
    state.history = [];
    state.askedQuestions = [];
    state.submitting = false;
    slowScroller.cancel();
    state.snapshot = analyzeDemand(state.query);
    state.finalAnswer = "";
    if (clearQuery) $("queryInput").value = DEFAULT_QUERY;
    $("queryInput").disabled = false;
    $("chatStream").replaceChildren(emptyMessage());
    $("answerBox").classList.remove("ready");
    $("answerBox").textContent = "完成交互后展示最终推荐。";
    clearRanking();
    renderSidePanel();
    renderScenarioButtons();
    syncQuestionInputMode();
    renderStatus("输入初始问题后开始演示", "准备中");
  }

  async function submitReply(event) {
    event.preventDefault();
    if (!state.started) {
      await startDemo();
      return;
    }
    if (state.finished || state.submitting) return;
    const reply = $("queryInput").value.trim();
    if (!reply) {
      showToast("请输入补充信息");
      return;
    }
    state.submitting = true;
    $("replyButton").disabled = true;
    try {
      $("queryInput").value = "";
      appendMessage("user", "销售/客户补充", reply);
      state.history.push({ user: reply, round: state.round });
      state.currentUserText = reply;
      await runNextTurn();
    } finally {
      state.submitting = false;
      $("replyButton").disabled = state.finished;
      if (!state.finished) $("queryInput").focus();
    }
  }

  function handleReplyKeydown(event) {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    if (state.finished || state.submitting) return;
    if (!state.started) {
      startDemo();
      return;
    }
    if (typeof $("replyForm").requestSubmit === "function") {
      $("replyForm").requestSubmit();
    } else {
      $("replyButton").click();
    }
  }

  async function runNextTurn() {
    state.round += 1;
    const thinkingStartedAt = performance.now();
    const thinkingNode = appendThinkingMessage();
    try {
      state.snapshot = await getDemoTurnSnapshot(state.currentUserText || state.query);
    } catch (error) {
      await keepThinkingVisible(thinkingStartedAt);
      state.round = Math.max(0, state.round - 1);
      replaceThinkingMessage(
        thinkingNode,
        buildMessageNode(
          "agent",
          error.kind === "backend" ? "后端服务未连接" : "大模型调用失败",
          error.kind === "backend"
            ? `浏览器无法连接本地销售推荐服务。\n${error.message || ""}`
            : `本轮没有使用本地规则生成结果。请检查模型服务后重试。\n${error.message || ""}`
        )
      );
      renderStatus(
        error.kind === "backend" ? "后端服务未连接" : "大模型调用失败，可重试",
        `${state.round} / ${state.maxRounds}`
      );
      return;
    }
    await keepThinkingVisible(thinkingStartedAt);
    state.sessionId = state.snapshot.sessionId || state.sessionId;
    state.maxRounds = state.snapshot.maxTurns || 6;
    renderSidePanel();
    renderBackendResults(state.snapshot);

    if (shouldContinueStandardDemo(state.snapshot)) {
      const turn = nextTurn(state.snapshot, state.askedQuestions);
      state.askedQuestions.push(...turn.questions.map((item) => item.text));
      replaceThinkingMessage(thinkingNode, buildQuestionMessage(turn));
      renderStatus(
        `第 ${state.round} 轮，继续补充关键需求`,
        `${state.round} / ${state.maxRounds}`
      );
      return;
    }

    if (state.snapshot.terminal) {
      finishDemo(thinkingNode);
      return;
    }

    if (state.snapshot.action === "clarify" || state.snapshot.status === "ask_clarification") {
      const turn = backendQuestionTurn(state.snapshot) || nextTurn(state.snapshot, state.askedQuestions);
      state.askedQuestions.push(...turn.questions.map((item) => item.text));
      replaceThinkingMessage(thinkingNode, buildQuestionMessage(turn));
      renderStatus(`第 ${state.round} 轮，等待补充`, `${state.round} / ${state.maxRounds}`);
      return;
    }

    finishDemo(thinkingNode);
  }

  function fillSampleReply() {
    if (!state.started || state.finished) {
      showToast("请先开始标准案例演示");
      return;
    }
    const demoCase = selectedCase();
    const round = demoCase?.rounds?.[state.round - 1];
    if (!round?.sampleReply) {
      showToast("当前轮次没有参考回答");
      return;
    }
    $("queryInput").value = round.sampleReply;
    $("queryInput").focus();
  }

  function shouldContinueStandardDemo(snapshot) {
    const demoCase = selectedCase();
    const minimumRounds = Number(demoCase?.minimumRounds || 0);
    if (!demoCase || minimumRounds <= 0 || state.round >= minimumRounds) return false;
    if (!snapshot?.terminal) return false;
    const nextRound = demoCase.rounds?.[state.round - 1];
    return Boolean(nextRound?.questions?.length && nextRound?.sampleReply);
  }

  async function getDemoTurnSnapshot(userText) {
    if (window.SALES_DEMO_API_ENDPOINT) {
      try {
        setBackendStatus("loading", state.runtime);
        renderStatus("大模型正在理解需求", `${state.round} / ${state.maxRounds}`);
        const response = await fetch(window.SALES_DEMO_API_ENDPOINT, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: state.sessionId,
            user_text: userText,
            max_turns: 6,
          }),
        });
        if (response.ok) {
          const payload = await response.json();
          assertRuntimeVersion(payload.runtime);
          setBackendStatus("connected", payload.runtime);
          return payload;
        }
        const errorPayload = await response.json().catch(() => ({}));
        const requestError = new Error(errorPayload.error || `HTTP ${response.status}`);
        requestError.code = errorPayload.code || "";
        requestError.runtime = errorPayload.runtime || {};
        throw requestError;
      } catch (error) {
        console.warn("Sales demo LLM request failed.", error);
        setBackendStatus("offline");
        const staleRuntime = error.code === "stale_runtime";
        const backendUnavailable =
          error instanceof TypeError ||
          /Failed to fetch|NetworkError|Load failed/i.test(error.message || "");
        const normalizedError = new Error(
          staleRuntime
            ? error.message
            : backendUnavailable
            ? "无法连接本地后端，请确认服务端口已启动。"
            : error.message || "请检查模型服务配置"
        );
        normalizedError.kind = staleRuntime || backendUnavailable ? "backend" : "llm";
        showToast(
          staleRuntime
            ? "后端仍是旧版本，请重启服务"
            : backendUnavailable
            ? "本地后端未连接"
            : `大模型调用失败：${normalizedError.message}`
        );
        throw normalizedError;
      }
    }
    return analyzeDemand(state.query);
  }

  function analyzeDemand(query) {
    const text = query.toLowerCase();
    const scene = detectScene(text);
    const knownFacts = extractFacts(query);
    return {
      source: "local",
      status: "ask_clarification",
      scene,
      primaryCategory: scene.primaryCategory,
      interactionCategory: scene.interactionCategory,
      candidateCount: scene.candidateCount,
      knownFacts,
      missingFacts: missingFacts(scene.id, knownFacts),
      confidence: scene.confidence,
      readiness: {
        decision: "pending",
        reason: "当前为本地预览模式，启动后端服务可查看真实就绪判断。",
        assumptions: [],
        clarificationPlan: [],
      },
      changedFields: [],
      conflicts: [],
    };
  }

  function detectScene(text) {
    if (hasAny(text, ["移机", "拆机", "续约", "变更", "办理流程", "办理材料"])) {
      return scene("service_process", "13_办理变更_续约_拆机_撤单", "13_办理变更_续约_拆机_撤单", 3, 0.9);
    }
    if (hasAny(text, ["固定公网ip", "固定ip", "公网地址", "服务器对外", "企业官网"])) {
      return scene("fixed_ip", "2_固定IP_高带宽_互联网专线", "2_固定IP_高带宽_互联网专线", 5, 0.9);
    }
    if (hasAny(text, ["餐饮", "门店", "商铺", "收银", "外卖", "监控", "小微"])) {
      return scene("store", "1_企业上网与办公宽带", "5_门店_商铺_小微经营", 8, 0.92);
    }
    if (hasAny(text, ["呼叫中心", "坐席", "总机", "中继线", "云中继", "商云通", "商继通"])) {
      return scene("voice", "6_固定电话_语音中继_呼叫业务", "6_固定电话_语音中继_呼叫业务", 8, 0.9);
    }
    if (hasAny(text, ["海外", "美国", "日本", "新加坡", "国外", "跨境", "saas"])) {
      return scene("overseas", "4_海外访问_跨境业务", "4_海外访问_跨境业务", 5, 0.86);
    }
    if (hasAny(text, ["总部", "分公司", "子公司", "仓库", "组网", "互通", "跨地域"])) {
      return scene("networking", "3_专线_组网_多点互联", "3_专线_组网_多点互联", 6, 0.82);
    }
    if (hasAny(text, ["资费", "多少钱", "年付", "月付", "套餐包含"])) {
      return scene("pricing", "1_企业上网与办公宽带", "1_企业上网与办公宽带", 8, 0.78);
    }
    return scene("general", "clarify_required", "clarify_required", 0, 0.35);
  }

  function scene(id, primaryCategory, interactionCategory, candidateCount, confidence) {
    return { id, primaryCategory, interactionCategory, candidateCount, confidence };
  }

  function extractFacts(query) {
    const peopleMatch = query.match(/(\d+)\s*(个)?\s*(人|坐席|终端|路)/);
    const budgetMatch = query.match(/预算\s*([0-9一二三四五六七八九十万千百]+[^，。\n\s]*)|([0-9]+)\s*(元|块|万)/);
    return {
      users: peopleMatch ? peopleMatch[0] : "",
      budget: budgetMatch ? budgetMatch[0] : "",
      period: hasAny(query, ["年付", "月付", "试用", "签约", "下月", "开通"]) ? extractPeriod(query) : "",
      access: extractAccess(query),
    };
  }

  function extractAccess(query) {
    const keywords = ["上海", "北京", "新疆", "门店", "总部", "分公司", "办公室", "美国", "海外"];
    return keywords.filter((item) => query.includes(item)).join("、");
  }

  function extractPeriod(query) {
    const keywords = ["年付", "月付", "试用", "下月开通", "下月", "签约"];
    return keywords.filter((item) => query.includes(item)).join("、");
  }

  function missingFacts(sceneId, facts) {
    const items = [];
    if (!facts.access) items.push(sceneId === "store" ? "门店地址/区域" : "访问来源/目标");
    if (!facts.users) items.push(sceneId === "voice" ? "坐席/并发" : "人数/终端");
    if (!facts.budget) items.push("预算");
    if (!facts.period) items.push("开通时间/计费周期");
    if (sceneId === "store") items.push("收银外卖连续性", "监控上传", "Wi-Fi/备线");
    return items.slice(0, 6);
  }

  function backendQuestionTurn(snapshot) {
    const clarification = snapshot?.clarification;
    if (!Array.isArray(clarification?.questions) || !clarification.questions.length) return null;
    return {
      acknowledgement: buildAcknowledgement(),
      transition: snapshot.readiness?.reason || "接下来，我想再确认影响方案选择的关键信息。",
      questions: clarification.questions.map((text, index) => ({
        text,
        helper: helperFromPlan(snapshot, clarification.fields?.[index], text),
      })),
    };
  }

  function helperFromPlan(snapshot, field, question) {
    const plan = snapshot?.readiness?.clarificationPlan || [];
    const intent = plan.find((item) => item.field === field);
    const explanations = intent?.termExplanations || [];
    if (explanations.length) {
      const explanationAlreadyIncluded = explanations.some(
        (item) => item.explanation && question.includes(item.explanation)
      );
      if (explanationAlreadyIncluded) return "";
      return explanations.map((item) => `${item.term}：${item.explanation}`).join(" ");
    }
    return helperForQuestion(question);
  }

  function nextTurn(snapshot, askedQuestions) {
    if (Array.isArray(snapshot.questions) && snapshot.questions.length) {
      const questions = normalizeQuestionItems(snapshot.questions);
      return {
        acknowledgement: snapshot.acknowledgement || buildAcknowledgement(),
        transition: snapshot.transition || buildTransition(questions),
        questions,
      };
    }
    const demoCase = selectedCase();
    const standardRound = demoCase?.rounds?.[state.round - 1];
    if (standardRound?.questions?.length) {
      const questions = normalizeQuestionItems(standardRound.questions);
      return {
        acknowledgement: standardRound.acknowledgement || buildAcknowledgement(),
        transition: standardRound.transition || `接下来，我想再了解一下${standardRound.topic}。`,
        questions,
      };
    }
    const template = QUESTION_TEMPLATES[snapshot.scene.id] || QUESTION_TEMPLATES.general;
    const fresh = template.filter((question) => !askedQuestions.includes(question));
    const questions = fresh.length
      ? normalizeQuestionItems(fresh.slice(0, 1))
      : normalizeQuestionItems([
          "您这边有没有一些比较明确的、方案必须满足的要求？例如业务不能中断、必须按期上线，或者需要更高的安全保障。",
        ]);
    return {
      acknowledgement: buildAcknowledgement(),
      transition: buildTransition(questions),
      questions,
    };
  }

  function finishDemo(thinkingNode = null) {
    state.finished = true;
    $("replyButton").disabled = true;
    $("queryInput").disabled = true;
    syncQuestionInputMode();
    state.finalAnswer =
      state.snapshot?.recommendation?.answerText ||
      buildFinalAnswer(state.snapshot, state.query, selectedCase());
    const finalNode = buildMessageNode("agent", "最终推荐", state.finalAnswer);
    if (thinkingNode?.isConnected) {
      replaceThinkingMessage(thinkingNode, finalNode);
    } else {
      appendMessageNode(finalNode);
    }
    $("answerBox").classList.add("ready");
    $("answerBox").replaceChildren(...renderAnswerBlocks(state.finalAnswer));
    const statusText = state.snapshot?.status === "no_candidate" ? "当前暂无合适候选" : "已生成最终推荐";
    renderStatus(statusText, `${state.round} / ${state.maxRounds}`);
    showToast(state.snapshot?.status === "no_candidate" ? "暂无候选产品" : "推荐已生成");
  }

  function buildFinalAnswer(snapshot, query, demoCase) {
    if (demoCase) return buildStandardCaseAnswer(snapshot, query, demoCase);
    const sceneId = snapshot.scene.id;
    if (sceneId === "voice") {
      return [
        "【推荐结论】\n主推“中国电信号百集团云中继”，用于总机、中继线、来电转接和坐席电话场景。",
        "【客户需求理解】\n客户有呼叫中心和坐席电话诉求，需要确认并发、号码保留、外呼合规和办理材料。",
        "【主推方案】\n产品/方案：中国电信号百集团云中继。\n推荐理由：贴合呼叫中心、总机、中继线、来电转接场景，适合按坐席规模配置。",
        "【风险与人工确认】\n需确认号码资源、实名材料、授权盖章、外呼合规和最终资费。",
        "【下一步动作】\n确认坐席并发、号码要求和材料清单，再做资费复核。",
      ].join("\n\n");
    }
    if (sceneId === "overseas") {
      return [
        "【推荐结论】\n主推海外访问优化或跨境加速方向，候选方案需结合合规和资源开通条件人工复核。",
        "【客户需求理解】\n客户存在海外 SaaS 访问慢的问题，需要关注访问来源、目标国家、人数、预算和试用周期。",
        "【主推方案】\n产品/方案：海外访问优化/专线类方案。\n推荐理由：围绕跨境访问质量、稳定性和业务连续性解决访问慢的问题。",
        "【风险与人工确认】\n跨境访问涉及合规、资源、开通周期和价格有效期，不可直接承诺。",
        "【下一步动作】\n确认 SaaS 地址、访问人数、预算和试用窗口，再做方案核价。",
      ].join("\n\n");
    }
    if (sceneId === "networking") {
      return [
        "【推荐结论】\n主推“智能专线/组网专线”方向，用于总部与分支之间稳定互联。",
        "【客户需求理解】\n客户需要跨地域访问提速或站点互通，关键是站点数量、地址、带宽、时延和备份要求。",
        "【主推方案】\n产品/方案：智能专线。\n推荐理由：适合总部、分公司、子公司之间的稳定访问和专线互联。",
        "【风险与人工确认】\n需确认本地资源覆盖、开通条件、协议期、价格和是否需要备线。",
        "【下一步动作】\n补齐站点地址、带宽目标和预算，再进行资源核查。",
      ].join("\n\n");
    }
    if (sceneId === "store") {
      return [
        "【推荐结论】\n主推“智云随选商企云宽基础版”，适合餐饮门店稳定上网、收银、外卖平台和监控使用。",
        "【客户需求理解】\n客户是门店小微经营场景，关注稳定宽带、预算有限、年付倾向、收银外卖不断网和监控可用性。",
        "【主推方案】\n产品/方案：智云随选商企云宽基础版。\n推荐理由：有明确年付价格和带宽档位，能覆盖 5 人门店日常上网、收银、外卖和监控需求。\n套餐/价格参考：100M/500M，约 6880 元/年，以人工复核为准。",
        "【备选方案】\n小微精品业务套餐可作为低成本备选核实；智能专线适合对稳定性要求更高但预算更充足的客户。",
        "【风险与人工确认】\n需确认门店地址资源覆盖、价格有效期、协议期、监控上传带宽和是否需要备线。",
        "【下一步动作】\n确认门店地址、预算上限、监控路数和是否需要 Wi-Fi/备线，然后提交资费与资源复核。",
      ].join("\n\n");
    }
    return [
      "【推荐结论】\n当前需求还不够明确，建议先继续澄清业务场景。",
      `【客户原始需求】\n${query}`,
      "【下一步动作】\n确认客户是上网、组网、海外访问、语音中继、云/IDC 还是办理材料诉求。",
    ].join("\n\n");
  }

  function buildStandardCaseAnswer(snapshot, query, demoCase) {
    const expected = demoCase.expected;
    const alternatives = expected.alternatives.length
      ? expected.alternatives.map((item) => `- ${item}：作为备选进一步核实套餐、价格和适用条件。`).join("\n")
      : "- 当前案例不要求推荐备选产品。";
    return [
      `【推荐结论】\n${expected.primaryProduct}`,
      `【客户需求理解】\n- 演示类型：${demoCase.type}\n- 预期分类：${expected.category}\n- 累计需求：${query}`,
      `【主推方案】\n- 产品/方案：${expected.primaryProduct}\n- 推荐依据：按照标准案例的需求标签、客户补充信息和候选产品证据进行匹配。\n- 当前候选数量：${snapshot.candidateCount}`,
      `【备选方案】\n${alternatives}`,
      `【风险与人工确认】\n${expected.requiredRisks.map((item) => `- ${item}`).join("\n")}`,
      `【验收约束】\n${expected.forbidden.map((item) => `- 不得出现：${item}`).join("\n")}`,
      "【下一步动作】\n1. 接入真实 Agent 接口后，用同一组参考回答运行回归。\n2. 对照预期分类、主推产品和风险提示进行验收。\n3. 价格、资源和开通条件以 published 产品数据及人工复核为准。",
    ].join("\n\n");
  }

  function renderAnswerBlocks(answer) {
    return answer.split(/\n\n+/).map((block) => {
      const [title, ...body] = block.split("\n");
      return create("div", { className: "answer-block" }, [
        create("h3", { text: title.replace(/[【】]/g, "") }),
        create("p", { text: body.join("\n") }),
      ]);
    });
  }

  function buildQuestionMessage(turn) {
    const content = [
      turn.acknowledgement ? create("p", { className: "acknowledgement", text: turn.acknowledgement }) : null,
      turn.transition ? create("p", { className: "transition", text: turn.transition }) : null,
      create(
        "div",
        { className: "question-list" },
        turn.questions.map((question, index) =>
          create("div", { className: "question-item" }, [
            create("p", { className: "question-text", text: `${index + 1}. ${question.text}` }),
            question.helper
              ? create("p", { className: "question-helper", text: `简单说明：${question.helper}` })
              : create("span"),
          ])
        )
      ),
    ].filter(Boolean);
    const node = create("div", { className: "message agent" }, [
      create("div", { className: "message-card" }, [
        messageMeta("智能体追问", `第 ${state.round} 轮`),
        ...content,
      ]),
    ]);
    return node;
  }

  function appendMessage(type, title, body) {
    const node = buildMessageNode(type, title, body);
    appendMessageNode(node);
    return node;
  }

  function buildMessageNode(type, title, body) {
    return create("div", { className: `message ${type}` }, [
      create("div", { className: "message-card" }, [messageMeta(title, currentTime()), create("p", { text: body })]),
    ]);
  }

  function appendMessageNode(node, { animate = true, delay = 140 } = {}) {
    $("chatStream").appendChild(node);
    revealMessage(node, animate);
    slowScroller.toBottom($("chatStream"), delay);
  }

  function appendThinkingMessage() {
    const node = create("div", {
      className: "message agent thinking-message",
      role: "status",
      "aria-label": "智能体正在思考",
    }, [
      create("div", { className: "message-card" }, [
        messageMeta("智能体", `第 ${state.round} 轮`),
        create("div", { className: "thinking-line" }, [
          create("span", { text: "正在理解需求并整理答案" }),
          create("span", { className: "thinking-dots", "aria-hidden": "true" }, [
            create("i"),
            create("i"),
            create("i"),
          ]),
        ]),
      ]),
    ]);
    appendMessageNode(node, { delay: 80 });
    return node;
  }

  function replaceThinkingMessage(thinkingNode, resultNode) {
    const container = $("chatStream");
    const previousScrollTop = container.scrollTop;
    if (thinkingNode?.isConnected) {
      thinkingNode.replaceWith(resultNode);
    } else {
      container.appendChild(resultNode);
    }
    // Replacing a short loading card with a long answer can make the browser
    // preserve the bottom edge. Restore the old position and let our scroller
    // reveal the new answer at a controlled pace.
    void resultNode.offsetHeight;
    container.scrollTop = previousScrollTop;
    revealMessage(resultNode, true);
    requestAnimationFrame(() => {
      container.scrollTop = previousScrollTop;
      slowScroller.toBottom(container, 180);
    });
  }

  function revealMessage(node, animate) {
    if (!animate || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    node.classList.remove("message-enter");
    requestAnimationFrame(() => node.classList.add("message-enter"));
  }

  async function keepThinkingVisible(startedAt, minimumMs = 650) {
    const remaining = minimumMs - (performance.now() - startedAt);
    if (remaining > 0) {
      await new Promise((resolve) => setTimeout(resolve, remaining));
    }
  }

  function messageMeta(left, right) {
    return create("div", { className: "message-meta" }, [create("span", { text: left }), create("span", { text: right })]);
  }

  function emptyMessage() {
    return create("div", { className: "message system" }, [
      create("div", { className: "message-card" }, [
        messageMeta("演示状态", "未开始"),
        create("p", { text: "点击开始后智能澄清需求，信息充分时立即推荐，最多交互 6 轮。" }),
      ]),
    ]);
  }

  function renderSidePanel() {
    const snapshot = state.snapshot || analyzeDemand(state.query);
    const known = snapshot.knownFacts || {};
    $("roundState").textContent = `${state.round} / ${state.maxRounds}`;
    $("sceneState").textContent = snapshot.interactionCategory;
    $("candidateCount").textContent = `候选 ${snapshot.candidateCount}`;
    const facts = [
      ["业务分类", snapshot.primaryCategory],
      ["交互场景", snapshot.interactionCategory],
      ["置信度", `${Math.round(snapshot.confidence * 100)}%`],
      ["核心目标", known.goal || "待补充"],
      ["使用场景", known.scene || "待补充"],
      ["人数/规模", known.users || "待补充"],
      ["带宽", known.bandwidth || "待补充"],
      ["预算", known.budget || "待补充"],
      ["地点/目标", known.access || "待补充"],
      ["缺失信息", (snapshot.missingFacts || []).join("、") || "暂无"],
    ];
    $("factList").replaceChildren(...facts.flatMap(([label, value]) => fact(label, value)));
    renderNeedProfile(snapshot);
    renderReadiness(snapshot);
  }

  function renderNeedProfile(snapshot) {
    const need = snapshot.customerNeed;
    if (!need) {
      $("needProfile").replaceChildren();
      return;
    }
    const groups = [
      {
        title: "业务范围",
        items: [
          ["办理动作", need.business_action],
          ["地点数量", need.site_count],
          ["安装区域", need.region],
          ["客户类型", need.customer_type],
        ],
      },
      {
        title: "能力要求",
        items: [
          ["固定公网 IP", formatNeedValue(need.fixed_ip_required)],
          ["语音/固定电话", formatNeedValue(need.voice_required)],
          ["海外访问", formatNeedValue(need.overseas_access)],
          ["稳定性", need.reliability_level],
        ],
      },
    ];
    $("needProfile").replaceChildren(
      ...groups.map((group) =>
        create("section", { className: "profile-group" }, [
          create("h3", { text: group.title }),
          create(
            "div",
            { className: "profile-tags" },
            group.items
              .filter(([, value]) => value && value !== "未知" && value !== "待确认")
              .map(([label, value]) =>
                create("span", { className: "profile-tag", text: `${label}：${value}` })
              )
          ),
        ])
      )
    );
  }

  function renderReadiness(snapshot) {
    const readiness = snapshot.readiness || {};
    const decision = readiness.decision || "pending";
    const display = {
      ask_clarification: ["需要补充", "clarify"],
      ready_with_assumptions: ["可先推荐", "assumption"],
      ready: ["信息充分", "ready"],
      pending: ["待分析", "pending"],
    }[decision] || ["待分析", "pending"];
    const badge = $("readinessBadge");
    badge.textContent = display[0];
    badge.className = `readiness-badge ${display[1]}`;
    $("readinessReason").textContent = readiness.reason || "开始对话后展示判断依据。";

    renderStatusGroup(
      $("changedFields"),
      "本轮已记录",
      (snapshot.changedFields || []).map((item) => item.label),
      "本轮暂未新增结构化信息",
      "positive"
    );
    renderGuardrails(snapshot.guardrails || []);
    renderStatusGroup(
      $("assumptionList"),
      "当前推荐假设",
      readiness.assumptions || [],
      decision === "ready" ? "当前不需要使用默认假设" : "暂无默认假设",
      "assumption"
    );

    const conflicts = (snapshot.conflicts || []).map(
      (item) => `${item.label}：原记录“${item.oldValue}”，本轮识别为“${item.newValue}”`
    );
    renderStatusGroup(
      $("conflictList"),
      "需要确认的信息冲突",
      conflicts,
      "未发现前后矛盾",
      "conflict"
    );
  }

  function renderGuardrails(values) {
    const container = $("guardrailList");
    container.hidden = !values.length;
    if (!values.length) {
      container.replaceChildren();
      return;
    }
    renderStatusGroup(container, "本轮规则校正", values, "", "correction");
  }

  function renderStatusGroup(container, title, values, emptyText, tone) {
    container.className = `status-group ${tone} ${values.length ? "" : "is-empty"}`.trim();
    container.replaceChildren(
      create("h3", { text: title }),
      create(
        "ul",
        {},
        (values.length ? values : [emptyText]).map((value) => create("li", { text: value }))
      )
    );
  }

  function formatNeedValue(value) {
    if (value === true) return "需要";
    if (value === false) return "不需要";
    return "待确认";
  }

  async function detectBackendRuntime() {
    try {
      const response = await fetch("/api/sales-demo/status", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      assertRuntimeVersion(payload.runtime);
      state.backendProblem = "";
      setBackendStatus("connected", payload.runtime);
    } catch (error) {
      if (error.code === "stale_runtime") {
        state.backendProblem = error.message;
        setBackendStatus("stale", error.runtime);
      } else {
        setBackendStatus("offline");
      }
    }
  }

  async function ensureBackendReady() {
    try {
      const response = await fetch("/api/sales-demo/status", {
        cache: "no-store",
        signal: window.AbortSignal?.timeout
          ? window.AbortSignal.timeout(3000)
          : undefined,
      });
      if (!response.ok) return false;
      const payload = await response.json();
      assertRuntimeVersion(payload.runtime);
      state.backendProblem = "";
      setBackendStatus("connected", payload.runtime);
      return true;
    } catch (error) {
      if (error.code === "stale_runtime") {
        state.backendProblem = error.message;
        setBackendStatus("stale", error.runtime);
      } else {
        state.backendProblem = "无法连接本地后端，请确认服务已经启动。";
        setBackendStatus("offline");
      }
      return false;
    }
  }

  function assertRuntimeVersion(runtime = {}) {
    if (runtime.version === EXPECTED_RUNTIME_VERSION) return;
    const error = new Error(
      `当前后端仍是旧进程（${runtime.version || "无版本信息"}），请关闭旧服务并重新启动。`
    );
    error.code = "stale_runtime";
    error.runtime = runtime;
    throw error;
  }

  function setBackendStatus(mode, runtime = {}) {
    const connected = mode === "connected" || mode === "loading";
    state.backendConnected = connected;
    if (runtime && Object.keys(runtime).length) state.runtime = runtime;
    const status = $("backendStatus");
    if (mode === "loading") {
      status.textContent =
        runtime?.mode === "single"
          ? "单次大模型分析中"
          : runtime?.llmEnabled
            ? "大模型分析中"
            : "后端分析中";
    } else if (connected && runtime?.llmEnabled) {
      const completedStages = runtime.completedStages || [];
      if (runtime.mode === "single") {
        status.textContent = completedStages.length
          ? "单次大模型已完成"
          : `单次大模型 · ${runtime.model || "LLM"}`;
      } else {
        status.textContent = completedStages.length
          ? `大模型已完成 · ${completedStages[completedStages.length - 1]}`
          : `大模型已连接 · ${runtime.model || "LLM"}`;
      }
    } else if (mode === "stale") {
      status.textContent = "后端版本过旧，请重启";
    } else {
      status.textContent = connected ? "规则后端已连接" : "本地预览模式";
    }
    const modelMode = $("modelModeState");
    if (modelMode) {
      modelMode.textContent =
        runtime?.mode === "single"
          ? "1 次 / 轮"
          : runtime?.mode === "double"
            ? "2 次 / 轮"
            : connected
              ? "本地规则"
              : "未连接";
    }
    status.title = runtime?.llmEnabled
      ? `模型：${runtime.model || "LLM"}；每轮调用 ${runtime.modelCallsPerTurn || "-"} 次${runtime.completedStages?.length ? `；已完成：${runtime.completedStages.join("、")}` : ""}`
      : "";
    status.classList.toggle("loading", mode === "loading");
    status.classList.toggle("connected", connected);
    status.classList.toggle("offline", !connected);
  }

  function clearRanking() {
    $("rankingCount").textContent = "0 个";
    $("rankingNote").textContent = "完成后端分析后展示 Top 3";
    $("rankingList").replaceChildren(create("div", { className: "empty-state", text: "暂无真实排序结果" }));
    $("pipelineStrip").replaceChildren();
    $("comparisonContent").replaceChildren(
      create("div", {
        className: "empty-state",
        text: "开始演示后，这里会展示真实后端的打分、排序和产品对比。",
      })
    );
  }

  function renderBackendResults(snapshot) {
    if (!Array.isArray(snapshot?.ranking) || !snapshot.ranking.length) {
      if (snapshot?.source === "backend") clearRanking();
      return;
    }
    renderRanking(snapshot.ranking, snapshot.scoreScale || 100);
    renderPipeline(snapshot.pipelineStats || {});
    renderComparison(snapshot.ranking, snapshot.comparisonDimensions || []);
    if (snapshot.timings?.total) {
      $("rankingNote").textContent =
        `本地 ${snapshot.timings.local} 秒 + 模型 ${snapshot.timings.model} 秒，共 ${snapshot.timings.total} 秒`;
    } else if (snapshot.elapsedSeconds) {
      $("rankingNote").textContent = `模型与流程耗时 ${snapshot.elapsedSeconds} 秒`;
    }
  }

  function renderRanking(ranking, scoreScale) {
    $("rankingCount").textContent = `${ranking.length} 个`;
    $("rankingNote").textContent = "后端程序评分，分数越高排序越靠前";
    $("rankingList").replaceChildren(
      ...ranking.map((item) => {
        const scoreWidth = Math.max(4, Math.min(100, (item.finalScore / scoreScale) * 100));
        const tone = item.rank === 1 ? "winner" : item.rank === 2 ? "second" : "third";
        return create("details", { className: `ranking-item ${tone}` }, [
          create("summary", {}, [
            create("span", { className: "rank-number", text: `${item.rank}` }),
            create("div", { className: "rank-main" }, [
              create("strong", { text: item.productName }),
              create("span", { text: [item.carrier, item.productFamily].filter(Boolean).join(" / ") || "产品候选" }),
              create("div", { className: "score-track" }, [
                create("div", { className: "score-fill", style: `width:${scoreWidth}%` }),
              ]),
            ]),
            create("div", { className: "score-value" }, [
              create("strong", { text: formatScore(item.finalScore) }),
              create("span", { text: "综合得分" }),
            ]),
          ]),
          renderScoreDetails(item),
        ]);
      })
    );
  }

  function renderScoreDetails(item) {
    const positiveReasons = (item.scoreReasons || []).filter((reason) => Number(reason.score_delta) > 0);
    const penalties = item.riskPenalties || [];
    return create("div", { className: "score-details" }, [
      detailGroup(
        "加分依据",
        positiveReasons.map(
          (reason) => `+${formatScore(reason.score_delta)} ${reason.message}${reason.evidence ? `：${reason.evidence}` : ""}`
        ),
        "暂无额外加分依据"
      ),
      detailGroup(
        "风险扣分",
        penalties.map(
          (reason) => `${formatScore(reason.score_delta)} ${reason.message}${reason.evidence ? `：${reason.evidence}` : ""}`
        ),
        "暂无程序风险扣分"
      ),
      detailGroup("待确认", [...(item.riskWarnings || []), ...(item.missingInfo || [])], "暂无明确缺失项"),
    ]);
  }

  function detailGroup(title, values, emptyText) {
    return create("section", { className: "detail-group" }, [
      create("h3", { text: title }),
      create(
        "ul",
        {},
        (values.length ? values : [emptyText]).slice(0, 5).map((value) => create("li", { text: value }))
      ),
    ]);
  }

  function renderPipeline(stats) {
    const items = [
      ["产品库", stats.publishedProducts ?? 0],
      ["召回", stats.retrieved ?? 0],
      ["过滤保留", stats.kept ?? 0],
      ["参与评分", stats.scored ?? 0],
    ];
    $("pipelineStrip").replaceChildren(
      ...items.map(([label, value]) =>
        create("div", { className: "pipeline-step" }, [
          create("span", { text: label }),
          create("strong", { text: String(value) }),
        ])
      )
    );
  }

  function renderComparison(ranking, dimensions) {
    const dimensionBand = create(
      "div",
      { className: "dimension-band" },
      dimensions.map((item) =>
        create("div", { className: "dimension-item" }, [
          create("strong", { text: item.dimension }),
          create("span", { text: item.summary }),
        ])
      )
    );
    const cards = create(
      "div",
      { className: "compare-grid" },
      ranking.map((item) => {
        const packages = item.packageSummary || {};
        const price = packages.price_range || "价格待确认";
        const speeds = (packages.speeds || []).join("、") || "速度待确认";
        const strengths = (item.matchedStrengths || []).slice(0, 2);
        const risks = [...(item.riskWarnings || []), ...(item.missingInfo || [])].slice(0, 2);
        return create("article", { className: `compare-card rank-${item.rank}` }, [
          create("div", { className: "compare-title" }, [
            create("span", { className: "badge", text: `第 ${item.rank} 名` }),
            create("strong", { text: item.productName }),
          ]),
          compareMetric("综合得分", formatScore(item.finalScore)),
          compareMetric("套餐速度", speeds),
          compareMetric("价格范围", price),
          compareMetric("基础套餐", `${packages.package_count || 0} 档`),
          create("div", { className: "compare-notes" }, [
            create("b", { text: "匹配优势" }),
            create("p", { text: strengths.join("；") || "暂无额外优势摘要" }),
            create("b", { text: "风险与缺失" }),
            create("p", { text: risks.join("；") || "暂无明确风险" }),
          ]),
        ]);
      })
    );
    $("comparisonContent").replaceChildren(dimensionBand, cards);
  }

  function compareMetric(label, value) {
    return create("div", { className: "compare-metric" }, [
      create("span", { text: label }),
      create("strong", { text: String(value) }),
    ]);
  }

  function formatScore(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "0";
    return Number.isInteger(number) ? String(number) : number.toFixed(1);
  }

  function selectedCase() {
    return DEMO_CASES.find((item) => item.id === state.selectedCaseId) || null;
  }

  function normalizeQuestionItems(questions) {
    return questions
      .map((question) => {
        if (question && typeof question === "object") {
          const text = String(question.text || "").trim();
          return { text, helper: String(question.helper || helperForQuestion(text)).trim() };
        }
        const text = String(question || "").trim();
        return { text, helper: helperForQuestion(text) };
      })
      .filter((question) => question.text);
  }

  function buildAcknowledgement() {
    if (!state.history.length) {
      return "好的，我先了解一下您的实际使用场景，这样后面的方案会更贴合。";
    }
    const reply = state.history[state.history.length - 1]?.user || "";
    if (hasAny(reply, ["年付", "月付", "预算", "元", "万"])) {
      return "好的，我理解您已经补充了预算和计费偏好，我们可以进一步缩小合适方案的范围。";
    }
    if (hasAny(reply, ["断网", "不中断", "持续在线", "稳定", "备份", "备用"])) {
      return "好的，我理解您比较看重业务持续可用，网络中断会直接影响实际经营或办公。";
    }
    if (hasAny(reply, ["监控", "摄像头", "云端上传", "wi-fi", "无线"])) {
      return "好的，我理解除了日常上网，监控和无线网络也是这次方案需要一起考虑的部分。";
    }
    if (hasAny(reply, ["坐席", "并发", "外呼", "总机", "号码"])) {
      return "好的，我理解您对坐席通话规模和号码能力已经有了比较明确的要求。";
    }
    if (hasAny(reply, ["总部", "分公司", "站点", "上海", "新疆", "地址"])) {
      return "好的，我理解您已经补充了主要使用地点和业务连接范围。";
    }
    return "好的，我已经记下您刚才补充的信息，会把它作为后续方案选择的重要依据。";
  }

  function buildTransition(questions) {
    const text = questions.map((item) => item.text).join(" ");
    if (hasAny(text, ["预算", "年付", "月付", "费用"])) return "接下来，我想再了解一下预算和计费方式。";
    if (hasAny(text, ["开通", "什么时候", "周期", "试用"])) return "接下来，我想再了解一下开通安排。";
    if (hasAny(text, ["监控", "无线", "wi-fi", "备用网络"])) return "接下来，我想再了解一下其他网络使用情况。";
    if (hasAny(text, ["坐席", "号码", "语音导航", "外呼"])) return "接下来，我想再了解一下电话和坐席的具体使用方式。";
    if (hasAny(text, ["地址", "地点", "站点", "门店"])) return "接下来，我想再了解一下实际使用地点和覆盖范围。";
    return "接下来，我想再确认一个会影响方案选择的问题。";
  }

  function helperForQuestion(question) {
    if (hasAny(question, ["固定公网 ip", "固定公网ip", "固定的网络地址", "同一个网络地址"])) {
      return "固定公网 IP 可以理解为一个长期不变化、可供外部访问的互联网地址，常用于官网、服务器或远程访问。";
    }
    if (hasAny(question, ["备用网络", "备线"])) {
      return "备用网络是主线路故障时用于临时接替的网络，可以减少收银、外卖或办公中断。";
    }
    if (hasAny(question, ["上下行对称", "上下行带宽"])) {
      return "上行是上传数据的速度，下行是下载数据的速度；上下行对称表示两者速度相同。";
    }
    if (hasAny(question, ["语音导航", "ivr"])) {
      return "语音导航就是客户打电话后听到的“按1、按2”菜单。";
    }
    if (hasAny(question, ["直接拨到具体坐席", "did"])) {
      return "这类号码可以让外部客户绕过总机，直接拨到指定坐席。";
    }
    if (hasAny(question, ["ddos", "恶意访问"])) {
      return "DDoS 防护用于降低服务器被大量恶意访问冲垮的风险。";
    }
    if (hasAny(question, ["pbx"])) {
      return "PBX 可以理解为企业内部管理分机和外线电话的总机系统。";
    }
    return "";
  }

  function syncSelectedCaseFromQuery() {
    if (state.started) return;
    const query = $("queryInput").value.trim();
    const matched = DEMO_CASES.find((item) => item.query === query);
    state.selectedCaseId = matched?.id || null;
    state.query = query || DEFAULT_QUERY;
    state.snapshot = analyzeDemand(state.query);
    renderScenarioButtons();
    renderSidePanel();
  }

  function syncQuestionInputMode() {
    const input = $("queryInput");
    const startButton = $("startButton");
    const sampleButton = $("sampleReplyButton");
    const replyButton = $("replyButton");
    const label = $("queryInputLabel");
    const isReplyMode = state.started && !state.finished;

    label.textContent = isReplyMode ? "补充客户信息" : state.finished ? "本轮演示已完成" : "客户问题";
    input.placeholder = isReplyMode
      ? "输入销售或客户补充信息，按 Enter 提交"
      : state.finished
        ? "点击重置后开始新的演示"
        : "输入客户的初始需求，按 Enter 开始";
    startButton.hidden = state.started;
    sampleButton.hidden = !isReplyMode;
    replyButton.hidden = !isReplyMode;
    sampleButton.textContent = "推荐回复";
  }

  function fact(label, value) {
    return [create("dt", { text: label }), create("dd", { text: value })];
  }

  function renderStatus(line, progress) {
    $("statusLine").textContent = line;
    $("progressPill").textContent = progress;
    $("roundState").textContent = `${state.round} / ${state.maxRounds}`;
  }

  async function copyAnswer() {
    const text = state.finalAnswer || "";
    if (!text) {
      showToast("暂无推荐可复制");
      return;
    }
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
    showToast("已复制推荐内容");
  }

  function showToast(text) {
    const toast = $("toast");
    toast.textContent = text;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 1800);
  }

  function hasAny(text, keywords) {
    const normalizedText = String(text || "").toLowerCase();
    return keywords.some((keyword) => normalizedText.includes(String(keyword).toLowerCase()));
  }

  function currentTime() {
    return new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }

  function createSessionId() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    return `sales-demo-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  init();
})();
