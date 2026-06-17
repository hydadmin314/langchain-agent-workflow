# Inkprint OCR Prototype

独立的铅印版扫描表单 OCR 原型，不接入现有 agent/workflow。

当前目标：

- 判断 PDF 是否为扫描件。
- 将 PDF 页面渲染为图片。
- 根据模板裁剪关键业务区域。
- 可选调用 PaddleOCR 识别裁剪区域。
- 输出带坐标、置信度和原始文本的 JSON 证据。

## 使用

先跑不调用 OCR 的分析和裁剪：

```powershell
.\.venv\Scripts\python.exe -m inkprint_ocr.pipeline `
  --pdf "data\raw\联通\1.1 沃专线\1 【铅印版】中国联通互联网专线接入业务（沃专线类）新装申请表.pdf" `
  --template "inkprint_ocr\templates\unicom_wo_zhuanxian_v1.json" `
  --out "data\inkprint_ocr_runs\unicom_wo_zhuanxian"
```

如果本机 PaddleOCR 模型可用，再加 `--ocr`：

```powershell
.\.venv\Scripts\python.exe -m inkprint_ocr.pipeline `
  --pdf "data\raw\联通\1.1 沃专线\1 【铅印版】中国联通互联网专线接入业务（沃专线类）新装申请表.pdf" `
  --template "inkprint_ocr\templates\unicom_wo_zhuanxian_v1.json" `
  --out "data\inkprint_ocr_runs\unicom_wo_zhuanxian" `
  --ocr
```

## 输出

- `pages/page_001.png`: 渲染后的整页图。
- `regions/*.png`: 模板裁剪出的区域图。
- `manifest.json`: PDF 页面、扫描件判断、区域坐标。
- `ocr_result.json`: OCR 证据结果，未启用 OCR 时文本为空。

## 方向

这个目录只做证据层：图像预处理、模板定位、区域 OCR、置信度记录。
后续再由独立转换器把 `ocr_result.json` 映射到业务 schema，避免 OCR 逻辑污染主框架。
