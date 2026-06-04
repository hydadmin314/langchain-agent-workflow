# 产品推荐知识库看板

这个静态看板展示 `data/product_doc_agent/published/*.json` 中已经发布的产品文档数据，方便检查销售推荐 Demo 的候选产品、套餐档位、办理材料、费用规则和抽取质量。

## 重新生成数据

同事更新 published JSON 后，在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe scripts\build_product_review_dashboard.py
```

生成结果会写入：

```text
dashboard/product_review/data.js
```

## 打开方式

直接用浏览器打开：

```text
dashboard/product_review/index.html
```

看板只读取同目录下的 `data.js`，不会改动主 agent 框架。
