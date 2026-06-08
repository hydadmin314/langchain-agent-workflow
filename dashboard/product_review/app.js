(function () {
  const dataset = window.REVIEW_DASHBOARD_DATA || { documents: [], packages: [], sourceDir: "" };
  const state = {
    query: "",
    family: "all",
    carrier: "all",
    quality: "all",
    sort: "quality",
    selectedId: null,
    tab: "overview",
  };

  const $ = (id) => document.getElementById(id);
  const fmt = new Intl.NumberFormat("zh-CN");
  const money = new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 0 });

  function compact(value, fallback = "未填写") {
    if (value === null || value === undefined || value === "") return fallback;
    if (Array.isArray(value)) return value.filter(Boolean).join("、") || fallback;
    return String(value);
  }

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

  function badge(text, tone = "") {
    return create("span", { className: `badge ${tone}`.trim(), text });
  }

  function qualityClass(label) {
    if (label === "可使用") return "good";
    if (label === "可补充") return "warn";
    if (label === "需复核") return "bad";
    return "info";
  }

  function formatPrice(value, period) {
    if (typeof value !== "number") return "待确认";
    return `${money.format(value)}${period ? ` / ${period}` : ""}`;
  }

  function priceRange(summary) {
    if (!summary || !summary.count) return "暂无价格";
    if (summary.min === summary.max) return formatPrice(summary.min, compact(summary.periods, ""));
    return `${money.format(summary.min)} - ${money.format(summary.max)}`;
  }

  function summarize() {
    const docs = dataset.documents;
    const priced = dataset.packages.filter((item) => typeof item.price === "number");
    const materialCount = docs.reduce((sum, doc) => sum + doc.materials.length, 0);
    const needsReview = docs.filter((doc) => doc.qualityLabel === "需复核").length;
    const readyDocs = docs.filter((doc) => doc.qualityLabel === "可使用").length;
    return [
      ["产品文档", `${docs.length}`, dataset.sourceDir || "published"],
      ["基础套餐", `${dataset.packages.length}`, "可进入推荐召回"],
      ["办理材料", `${materialCount}`, "营业执照、授权、担保等"],
      ["可直接用", `${readyDocs}`, "质量较完整的候选"],
      ["需复核", `${needsReview}`, "进入人工确认清单"],
      ["最高档位", priced.length ? money.format(Math.max(...priced.map((item) => item.price))) : "待确认", "按已抽取价格统计"],
    ];
  }

  function renderMetrics() {
    $("metricStrip").replaceChildren(
      ...summarize().map(([label, value, hint]) =>
        create("div", { className: "metric" }, [
          create("span", { text: label }),
          create("strong", { text: value }),
          create("small", { text: hint }),
        ])
      )
    );
  }

  function fillSelect(select, allLabel, values) {
    select.replaceChildren(create("option", { value: "all", text: allLabel }));
    values.forEach((value) => select.appendChild(create("option", { value, text: value })));
  }

  function initControls() {
    fillSelect($("familyFilter"), "全部产品线", dataset.families || []);
    fillSelect($("carrierFilter"), "全部归属", dataset.carriers || []);
    $("searchInput").addEventListener("input", (event) => {
      state.query = event.target.value;
      render();
    });
    $("familyFilter").addEventListener("change", (event) => {
      state.family = event.target.value;
      render();
    });
    $("carrierFilter").addEventListener("change", (event) => {
      state.carrier = event.target.value;
      render();
    });
    $("sortSelect").addEventListener("change", (event) => {
      state.sort = event.target.value;
      render();
    });
    $("qualityFilter").addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      state.quality = button.dataset.quality;
      [...$("qualityFilter").querySelectorAll("button")].forEach((item) => item.classList.toggle("active", item === button));
      render();
    });
    $("tabs").addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      state.tab = button.dataset.tab;
      [...$("tabs").querySelectorAll("button")].forEach((item) => item.classList.toggle("active", item === button));
      renderDetail(selectedDoc());
    });
    $("copySource").addEventListener("click", copySource);
    $("exportSelection").addEventListener("click", exportSummary);
  }

  function docSearchText(doc) {
    const chunks = [
      doc.title,
      doc.productName,
      doc.productFamily,
      doc.carrier,
      doc.categoryPath,
      doc.filename,
      doc.sourcePath,
      ...doc.basePackages.map((item) => `${item.package_name || ""} ${item.speed || ""} ${item.price || ""}`),
      ...doc.optionalPackages.map((item) => `${item.name || ""} ${item.description || ""} ${item.fee_summary || ""}`),
      ...doc.materials.map((item) => `${item.material_name || ""} ${item.notes || ""}`),
      ...doc.feeRules.map((item) => `${item.name || ""} ${item.description || ""}`),
      ...doc.agreementRules.map((item) => `${item.title || ""} ${item.description || ""}`),
      ...doc.constraints.map((item) => `${item.name || ""} ${item.description || ""}`),
    ];
    return chunks.join(" ").toLowerCase();
  }

  function filteredDocs() {
    const query = state.query.trim().toLowerCase();
    return dataset.documents
      .filter((doc) => {
        if (state.family !== "all" && doc.productFamily !== state.family) return false;
        if (state.carrier !== "all" && doc.carrier !== state.carrier) return false;
        if (state.quality !== "all" && doc.qualityLabel !== state.quality) return false;
        if (query && !docSearchText(doc).includes(query)) return false;
        return true;
      })
      .sort((a, b) => {
        if (state.sort === "price-low") return (a.priceSummary.min ?? Number.MAX_SAFE_INTEGER) - (b.priceSummary.min ?? Number.MAX_SAFE_INTEGER);
        if (state.sort === "price-high") return (b.priceSummary.max ?? -1) - (a.priceSummary.max ?? -1);
        if (state.sort === "packages") return b.basePackages.length - a.basePackages.length;
        if (state.sort === "updated") return compact(b.generatedAt, "").localeCompare(compact(a.generatedAt, ""));
        return b.qualityScore - a.qualityScore || b.priceSummary.count - a.priceSummary.count;
      });
  }

  function selectedDoc() {
    return dataset.documents.find((doc) => doc.id === state.selectedId) || dataset.documents[0];
  }

  function selectDoc(id) {
    state.selectedId = id;
    render();
  }

  function renderDocList(docs) {
    $("resultCount").textContent = `${docs.length} 份文档`;
    $("dataStamp").textContent = dataset.generatedAt ? `生成 ${dataset.generatedAt.slice(0, 10)}` : "";
    const list = $("docList");
    list.replaceChildren();
    docs.forEach((doc) => {
      const button = create("button", {
        type: "button",
        className: `doc-item ${doc.id === state.selectedId ? "active" : ""}`.trim(),
        onclick: () => selectDoc(doc.id),
      });
      button.append(
        create("div", { className: "doc-title", text: doc.productName || doc.title }),
        create("div", { className: "doc-subtitle", text: doc.categoryPath }),
        create("div", { className: "doc-meta" }, [
          badge(doc.qualityLabel, qualityClass(doc.qualityLabel)),
          badge(doc.productFamily),
          doc.issueCount ? badge(`${doc.issueCount} 项问题`, "bad") : badge("无校验问题", "good"),
        ]),
        create("div", { className: "mini-line" }, [
          create("span", { text: `${doc.basePackages.length} 个基础档位` }),
          create("span", { text: priceRange(doc.priceSummary) }),
        ]),
        create("div", { className: "health" }, [create("div", { style: `width:${doc.qualityScore}%` })])
      );
      list.appendChild(button);
    });
    if (!docs.length) list.appendChild(create("div", { className: "empty", text: "没有匹配的 published 文档。" }));
  }

  function insight(label, value, hint) {
    return create("div", { className: "insight" }, [
      create("span", { text: label }),
      create("strong", { text: value }),
      create("small", { text: hint }),
    ]);
  }

  function renderDetail(doc) {
    if (!doc) return;
    $("detailBadges").replaceChildren(
      badge(doc.qualityLabel, qualityClass(doc.qualityLabel)),
      badge(doc.reviewStatus || "published", "info"),
      badge(doc.documentStatus || "active", doc.documentStatus === "active" ? "good" : "warn"),
      badge(doc.version || "无版本号", doc.version ? "info" : "warn")
    );
    $("detailTitle").textContent = doc.title || doc.productName;
    $("detailMeta").textContent = [doc.carrier, doc.region, doc.filename].filter(Boolean).join(" / ");
    $("detailScore").textContent = `${doc.qualityScore}%`;
    $("detailScoreBar").style.width = `${doc.qualityScore}%`;
    $("insightGrid").replaceChildren(
      insight("基础套餐", `${doc.basePackages.length} 档`, priceRange(doc.priceSummary)),
      insight("可选包", `${doc.optionalPackages.length} 项`, doc.optionalPackages.slice(0, 2).map((item) => item.name).join("、") || "暂无"),
      insight("办理材料", `${doc.materials.length} 项`, doc.materials.slice(0, 2).map((item) => item.material_name).join("、") || "暂无"),
      insight("规则风险", `${doc.feeRules.length + doc.agreementRules.length + doc.constraints.length} 条`, doc.issueCount ? `${doc.issueCount} 项抽取问题` : "校验通过")
    );
    renderTab(doc);
  }

  function salesNotes(doc) {
    const notes = [];
    if (doc.basePackages.length) notes.push(`可用于报价推荐，当前抽取到 ${doc.basePackages.length} 个基础套餐档位。`);
    else notes.push("缺少基础套餐档位，推荐时只能作为补充知识，最好先人工补价。");
    if (doc.priceSummary.count) notes.push(`价格区间为 ${priceRange(doc.priceSummary)}，适合做预算约束匹配。`);
    else notes.push("未抽取到明确价格，进入最终答案时要提示客户人工确认。");
    if (doc.materials.length) notes.push(`办理材料已抽取 ${doc.materials.length} 项，可支撑下一步交付提醒。`);
    if (doc.constraints.some((item) => item.blocks_recommendation)) notes.push("存在阻断类约束，推荐前需要优先复核开通条件。");
    if (doc.issueCount || doc.warningCount) notes.push("存在校验问题或 schema warning，展示给客户前建议复核来源原文。");
    return notes;
  }

  function renderOverview(doc) {
    const packageNames = doc.basePackages.map((item) => item.package_name || item.speed).filter(Boolean).slice(0, 5);
    return create("div", { className: "overview-grid" }, [
      create("section", { className: "plain-section" }, [
        create("h3", { text: "推荐使用判断" }),
        create("ul", { className: "note-list" }, salesNotes(doc).map((item) => create("li", { text: item }))),
      ]),
      create("section", { className: "plain-section" }, [
        create("h3", { text: "命中线索" }),
        create("div", { className: "tag-cloud" }, [
          badge(doc.productFamily, "info"),
          badge(doc.categoryPath, "info"),
          badge(doc.carrier, "info"),
          ...packageNames.map((item) => badge(item, "good")),
        ]),
      ]),
      create("section", { className: "plain-section wide" }, [
        create("h3", { text: "Demo 推荐时可带出的下一步" }),
        create("div", { className: "action-grid" }, [
          actionTile("确认客户人数与带宽", "用于筛选套餐档位和预算上限。"),
          actionTile("确认地址与资源", "判断本地开通条件、跨域接入或固定 IP。"),
          actionTile("确认计费周期", "区分月付、年付、协议期和优惠。"),
          actionTile("确认材料清单", "把办理材料作为成交后的交付提醒。"),
        ]),
      ]),
    ]);
  }

  function actionTile(title, body) {
    return create("div", { className: "action-tile" }, [create("b", { text: title }), create("span", { text: body })]);
  }

  function packageChart(packages) {
    const priced = packages.filter((item) => typeof item.price === "number");
    if (!priced.length) return null;
    const max = Math.max(...priced.map((item) => item.price));
    return create(
      "div",
      { className: "bar-list" },
      priced.slice(0, 12).map((item) =>
        create("div", { className: "bar-row" }, [
          create("span", { className: "bar-label", text: compact(item.speed, item.package_name || "套餐"), title: compact(item.package_name) }),
          create("div", { className: "bar-track" }, [
            create("div", { className: "bar-fill", style: `width:${Math.max(6, Math.round((item.price / max) * 100))}%` }),
          ]),
          create("span", { className: "bar-value", text: formatPrice(item.price, item.billing_period) }),
        ])
      )
    );
  }

  function table(headers, rows) {
    const thead = create("thead", {}, [create("tr", {}, headers.map((head) => create("th", { text: head })))]);
    const tbody = create("tbody");
    rows.forEach((cells) => tbody.appendChild(create("tr", {}, cells.map((cell) => create("td", {}, [cell instanceof Node ? cell : create("span", { text: compact(cell) })])))));
    return create("div", { className: "table-wrap" }, [create("table", {}, [thead, tbody])]);
  }

  function renderPackages(doc) {
    if (!doc.basePackages.length) return empty("这份文档没有抽取到基础套餐档位。");
    const rows = doc.basePackages.map((pkg) => [
      pkg.package_name || doc.productName,
      pkg.speed || `${compact(pkg.upstream_speed, "")}/${compact(pkg.downstream_speed, "")}`,
      create("span", { className: "price", text: formatPrice(pkg.price, pkg.billing_period) }),
      pkg.contract_period || "待确认",
      pkg.has_voice === true ? "带语音" : pkg.has_voice === false ? "不带语音" : "未标明",
      create("span", { className: "evidence", text: compact(pkg.source_evidence, "无证据片段") }),
    ]);
    const panel = create("div");
    const chart = packageChart(doc.basePackages);
    if (chart) panel.appendChild(chart);
    panel.appendChild(table(["套餐", "速率", "价格", "协议期", "语音", "来源证据"], rows));
    return panel;
  }

  function materialRecord(item) {
    return create("article", { className: "record" }, [
      create("div", { className: "record-head" }, [
        create("h3", { text: item.material_name || "未命名材料" }),
        badge(item.required ? "必需" : "可选", item.required ? "bad" : "info"),
      ]),
      create("p", { text: compact(item.notes || item.condition, "暂无说明") }),
      create("div", { className: "kv" }, [
        create("b", { text: "适用" }),
        create("span", { text: compact(item.applies_to) }),
        create("b", { text: "形式" }),
        create("span", { text: compact([item.format, item.copies].filter(Boolean)) }),
        create("b", { text: "盖章" }),
        create("span", { text: item.seal_required ? compact(item.seal_type, "需要") : "不需要" }),
        create("b", { text: "模板" }),
        create("span", { text: item.template_required ? compact(item.template_document, "指定模板") : "不需要" }),
      ]),
    ]);
  }

  function ruleRecord(item, fallbackTitle) {
    return create("article", { className: "record" }, [
      create("div", { className: "record-head" }, [
        create("h3", { text: item.name || item.title || fallbackTitle }),
        badge(item.rule_type || item.constraint_type || item.severity || "规则", "info"),
      ]),
      create("p", { text: compact(item.description || item.condition || item.result, "暂无说明") }),
      item.source_evidence ? create("p", { className: "evidence", text: item.source_evidence }) : create("span"),
    ]);
  }

  function renderMaterials(doc) {
    if (!doc.materials.length) return empty("这份文档没有抽取到办理材料。");
    return create("div", { className: "record-list" }, doc.materials.map(materialRecord));
  }

  function renderRules(doc) {
    const rules = [
      ...doc.feeRules.map((item) => [item, "费用规则"]),
      ...doc.agreementRules.map((item) => [item, "协议规则"]),
      ...doc.constraints.map((item) => [item, "约束条件"]),
      ...doc.supplementalRules.map((item) => [item, "补充规则"]),
    ];
    if (!rules.length) return empty("这份文档没有抽取到费用、协议或约束规则。");
    return create("div", { className: "record-list" }, rules.map(([item, title]) => ruleRecord(item, title)));
  }

  function renderOptional(doc) {
    if (!doc.optionalPackages.length) return empty("这份文档没有抽取到可选包。");
    return table(
      ["名称", "类型", "费用", "说明", "适用条件"],
      doc.optionalPackages.map((item) => [
        item.name,
        item.package_type || item.category,
        item.fee_summary || compact(item.price_items, "待确认"),
        item.description,
        compact(item.applicable_conditions),
      ])
    );
  }

  function renderIssues(doc) {
    const items = [
      ...doc.validationIssues.map((item) => ({ type: "校验问题", detail: item })),
      ...doc.schemaWarnings.map((item) => ({ type: "Schema Warning", detail: item })),
    ];
    if (!items.length) return empty("当前文档没有记录校验问题。");
    return create(
      "div",
      { className: "record-list" },
      items.map((item) =>
        create("article", { className: "record" }, [
          create("div", { className: "record-head" }, [create("h3", { text: item.type }), badge("待复核", "bad")]),
          create("pre", { text: JSON.stringify(item.detail, null, 2) }),
        ])
      )
    );
  }

  function renderRaw(doc) {
    return create("pre", { className: "raw-json", text: JSON.stringify(doc.raw, null, 2) });
  }

  function empty(text) {
    return create("div", { className: "empty", text });
  }

  function renderTab(doc) {
    const panel = $("tabPanel");
    const views = {
      overview: renderOverview,
      packages: renderPackages,
      materials: renderMaterials,
      rules: renderRules,
      optional: renderOptional,
      issues: renderIssues,
      raw: renderRaw,
    };
    panel.replaceChildren((views[state.tab] || renderOverview)(doc));
  }

  function copySource() {
    const doc = selectedDoc();
    if (!doc) return;
    navigator.clipboard?.writeText(doc.reviewFile || doc.sourcePath);
    toast("已复制 published JSON 路径");
  }

  function exportSummary() {
    const doc = selectedDoc();
    if (!doc) return;
    const lines = [
      `产品：${doc.productName}`,
      `分类：${doc.categoryPath}`,
      `质量：${doc.qualityLabel} ${doc.qualityScore}%`,
      `基础套餐：${doc.basePackages.length} 档`,
      `价格：${priceRange(doc.priceSummary)}`,
      `办理材料：${doc.materials.length} 项`,
      `来源：${doc.reviewFile}`,
    ];
    navigator.clipboard?.writeText(lines.join("\n"));
    toast("已复制当前摘要");
  }

  function toast(message) {
    const node = $("toast");
    node.textContent = message;
    node.classList.add("show");
    window.clearTimeout(toast.timer);
    toast.timer = window.setTimeout(() => node.classList.remove("show"), 1800);
  }

  function render() {
    const docs = filteredDocs();
    if (!state.selectedId && docs.length) state.selectedId = docs[0].id;
    if (state.selectedId && docs.length && !docs.some((doc) => doc.id === state.selectedId)) state.selectedId = docs[0].id;
    renderDocList(docs);
    renderDetail(selectedDoc());
  }

  renderMetrics();
  initControls();
  render();
})();
