# 销售推荐 Demo 展示台

这是给业务部门演示销售推荐 6 轮交互、产品打分和方案对比的页面。

## 打开方式

使用真实后端打分与排序：

```powershell
.\.venv\Scripts\python.exe web\sales_demo\server.py --port 8766
```

浏览器访问 `http://127.0.0.1:8766`。

页面也可以直接打开 `web/sales_demo/index.html`，但此时仅用于本地交互预览，不会运行真实产品库评分。

## 当前范围

- 前端本地状态机展示 6 轮追问流程。
- 内置 8 个标准演示案例，覆盖产品推荐、产品对比、资费查询、办理流程和需求澄清。
- 每个案例包含预期分类、5 轮参考补充、第 6 轮推荐、预期主推、风险提示和禁止错误。
- 演示过程中可以点击“填入参考回答”，快速完成标准路径回归。
- 后端直接复用项目现有的 `ProductRepository`、`CandidateRetriever`、`CandidateRuleFilter`、`CandidateScorer` 和 `CandidateComparator`。
- 页面展示 Top 3、综合得分、加分依据、风险扣分、产品库处理数量和多维对比。
- 默认使用本地规则解析，确保演示速度稳定；添加 `--use-llm-parser` 可启用项目的大模型需求解析。
- 不修改主 agent 框架，不影响 `web/product_review` 产品 JSON 展示页。

## 接口

页面默认请求：

```text
POST /api/sales-demo/turn
```

请求示例：

```json
{
  "query": "餐饮门店5个人用，主要收银、外卖平台、监控和日常上网",
  "history": [],
  "round": 1,
  "max_rounds": 6,
  "min_rounds_before_answer": 6
}
```
