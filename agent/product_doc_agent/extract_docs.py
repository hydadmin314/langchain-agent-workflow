import json
import re
from datetime import datetime
from pathlib import Path

from docx import Document


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "raw"
OUTPUT_DIR = ROOT / "data" / "product_doc_agent" / "output"


def clean(text):
    return re.sub(r"\s+", " ", (text or "").replace("\u3000", " ")).strip()


def dedupe(items):
    result = []
    seen = set()
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else item
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def row_cells(row):
    cells = []
    previous = None
    for cell in row.cells:
        text = clean(cell.text)
        if text != previous:
            cells.append(text)
            previous = text
    return cells


def get_rows(table):
    return [row_cells(row) for row in table.rows]


def paragraphs(doc):
    return [clean(p.text) for p in doc.paragraphs if clean(p.text)]


def first_match(pattern, text, default=""):
    match = re.search(pattern, text)
    return match.group(1).strip() if match else default


def extract_meta(path, paras):
    path = Path(path).resolve()
    try:
        source_path = str(path.relative_to(ROOT))
    except ValueError:
        source_path = str(path)
    title = next((p for p in paras if "登记表" in p), paras[0] if paras else path.stem)
    issuer = paras[0] if paras else ""
    version = first_match(r"[（(]([^（）()]*\d{4}[^（）()]*)[）)]", title)
    effective_from = first_match(r"【(.+?)起】", path.name)
    product_name = re.split(r"申请登记表|登记表", title)[0]
    product_name = product_name.replace("中国电信股份有限公司上海分公司", "").strip()
    return {
        "filename": path.name,
        "source_path": source_path,
        "issuer": issuer,
        "title": title,
        "version": version,
        "effective_from": effective_from,
        "product_name": product_name or title,
    }


def extract_required_fields(rows):
    fields = []
    for row in rows:
        if row and "基础套餐申请信息" in row[0]:
            break
        for cell in row:
            if not cell or cell.startswith("□"):
                continue
            parts = re.split(r"\s{2,}|[：:]", cell)
            for part in parts:
                label = clean(part).replace(" ", "")
                if not label or len(label) > 24:
                    continue
                if label.startswith("□") or "银行账号" in label:
                    continue
                if "*" in label or label in {"企业代码", "联系人职务", "经办人职务", "传真", "E-MAIL"}:
                    fields.append({
                        "label": label.replace("*", ""),
                        "required": "*" in label,
                        "value": "",
                    })
    return dedupe(fields)


def checkbox_options(text):
    options = []
    for part in re.split(r"□", text):
        part = clean(part)
        if not part:
            continue
        part = re.split(r"(?=□)|。|；", part)[0].strip(" ，,")
        if part:
            options.append(part)
    return dedupe(options)


def parse_base_packages(text):
    packages = []
    pattern = re.compile(r"(\d+M/\d+M)[（(](\d+)元/(月|年|2年)(?:/线)?[）)]")
    for speed, amount, period in pattern.findall(text):
        packages.append({
            "speed": speed,
            "amount": int(amount),
            "currency": "CNY",
            "billing_period": period,
            "raw": f"{speed}（{amount}元/{period}）",
        })
    return dedupe(packages)


def parse_variable_package_rows(business_rows):
    packages = []
    for item in business_rows:
        name = item["name"] or item["category"]
        description = item["description"]
        if name not in {"小微上线", "小微在线"}:
            continue
        speed_range = first_match(r"[（(]([^（）()]*M[^（）()]*)[）)]", description)
        packages.append({
            "name": name,
            "speed": speed_range or "需填写",
            "amount": None,
            "currency": "CNY",
            "billing_period": "月",
            "raw": description,
            "pricing_type": "customer_input",
        })
    return packages


def parse_uplink_packages(text):
    packages = []
    current_speed = ""
    for token in re.split(r"\s+", text):
        speed = re.search(r"上行升速(\d+M)", token)
        if speed:
            current_speed = speed.group(1)
            continue
        price = re.search(r"(\d+)元/(月|年)", token)
        if current_speed and price:
            packages.append({
                "name": f"上行升速{current_speed}",
                "amount": int(price.group(1)),
                "currency": "CNY",
                "billing_period": price.group(2),
            })
    return packages


def classify_business_rows(rows):
    application_rows = []
    in_business = False
    for index, row in enumerate(rows):
        joined = " ".join(row)
        if "基础套餐申请信息" in joined or "业务申请信息" in joined or "申请信息" in joined:
            in_business = True
            continue
        if not in_business or not row:
            continue
        category = clean(row[0])
        name = clean(row[1]) if len(row) > 2 else category
        description = clean(" ".join(row[2:] if len(row) > 2 else row[1:]))
        if not description and len(row) == 2:
            description = clean(row[1])
        if category == "填表说明":
            category = "说明"
        application_rows.append({
            "row_index": index,
            "category": category,
            "name": name,
            "description": description,
            "options": checkbox_options(description),
        })
    return application_rows


def extract_sla(tables, business_rows):
    sla = {"eligibility": "", "plans": [], "compensation_rules": []}
    for item in business_rows:
        if "SLA" in item["name"] or "SLA" in item["category"]:
            sla["eligibility"] = item["description"]
    for table in tables:
        rows = get_rows(table)
        if not rows:
            continue
        if rows[0] and rows[0][0] == "产品" and len(rows) >= 2:
            headers = rows[0][1:] or ["标准"]
            for plan_index, header in enumerate(headers):
                plan = {"product": header, "metrics": {}}
                for row in rows[1:]:
                    if len(row) > plan_index + 1:
                        plan["metrics"][row[0]] = row[plan_index + 1]
                sla["plans"].append(plan)
        if rows[0] and "月可用率范围" in rows[0][0]:
            for row in rows[1:]:
                if len(row) >= 2:
                    sla["compensation_rules"].append({
                        "availability_range": row[0],
                        "compensation": row[1],
                    })
    return sla


def extract_supporting_forms(tables):
    forms = []
    for idx, table in enumerate(tables):
        rows = get_rows(table)
        if not rows:
            continue
        title = clean(" ".join(rows[0]))
        if "IP地址报备表" in title:
            forms.append({"table_index": idx, "name": "IP地址报备表", "required_fields": extract_required_fields(rows)})
        elif "ICP备案号" in title:
            forms.append({"table_index": idx, "name": "域名备案信息表", "columns": rows[0]})
        elif "APP下载URL" in title:
            forms.append({"table_index": idx, "name": "APP应用备案信息表", "columns": rows[0]})
    return forms


def extract_marketing_rules(paras):
    start = 0
    for index, para in enumerate(paras):
        if "营销规则" in para or "客户特别关注" in para:
            start = index + 1
            break
    rules = []
    for para in paras[start:]:
        if len(rules) >= 10:
            break
        if "登记表" in para or len(para) < 12:
            continue
        rules.append(para)
    return rules


def build_document(path):
    doc = Document(str(path))
    paras = paragraphs(doc)
    tables = doc.tables
    main_rows = get_rows(tables[0]) if tables else []
    business_rows = classify_business_rows(main_rows)
    all_business_text = " ".join(item["description"] for item in business_rows)

    included_items = [
        item for item in business_rows
        if item["category"] in {"套餐内 包含内容", "基础套餐"} or "基础套餐" in item["category"]
    ]
    optional_services = [
        item for item in business_rows
        if "可选" in item["category"] or "移动业务" in item["category"] or "商云通" in item["category"]
        or "固话" in item["category"] or "上行升速包" in item["category"] or "配套业务" in item["category"]
        or "云享" in item["description"]
    ]
    fees = [
        item for item in business_rows
        if "费用" in item["description"] or "一次性" in item["category"] or "新装优惠" in item["category"]
    ]

    base_packages = parse_base_packages(all_business_text)
    base_packages.extend(parse_variable_package_rows(business_rows))
    uplink_packages = parse_uplink_packages(all_business_text)
    if uplink_packages and not any(item["name"] == "上行升速包" for item in optional_services):
        optional_services.append({
            "row_index": None,
            "category": "可选增值服务",
            "name": "上行升速包",
            "description": "、".join(f"{p['name']} {p['amount']}元/{p['billing_period']}" for p in uplink_packages),
            "options": [f"{p['name']} {p['amount']}元/{p['billing_period']}" for p in uplink_packages],
        })

    meta = extract_meta(path, paras)
    warnings = []
    if not any(field["value"] for field in extract_required_fields(main_rows)):
        warnings.append("文档是空白申请登记表，未发现已填写的客户值；当前结果主要反映套餐规则和可选项。")
    if not base_packages:
        warnings.append("未解析到明确的基础套餐价格项，建议人工复核原表格。")

    extracted = {
        "document_type": "telecom_product_application_form",
        **meta,
        "customer_application_fields": extract_required_fields(main_rows),
        "filled_customer_values": [],
        "base_packages": base_packages,
        "included_items": dedupe(included_items),
        "optional_services": dedupe(optional_services),
        "fees": dedupe(fees),
        "sla": extract_sla(tables, business_rows),
        "supporting_forms": extract_supporting_forms(tables),
        "marketing_rules": extract_marketing_rules(paras),
        "raw_business_rows": business_rows,
        "quality": {
            "overall_score": 0.82 if base_packages else 0.68,
            "method": "docx_table_rule_extraction",
            "warnings": warnings,
            "suggestions": [
                "如果后续配置 LLM API Key，可用项目原 LangGraph 链路做语义字段补全和冲突校验。",
                "业务验收时建议重点核对基础套餐价格、协议期、一次性费用和可选增值服务。"
            ],
        },
        "summary": "",
    }
    extracted["summary"] = (
        f"{meta['title']}，识别到 {len(base_packages)} 个基础套餐价格项、"
        f"{len(optional_services)} 个可选/增值服务项、{len(fees)} 条费用相关规则。"
    )
    return extracted


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    documents = []
    for index, path in enumerate(sorted(DATA_DIR.glob("*.docx")), 1):
        document = build_document(path)
        document["id"] = f"doc-{index}"
        documents.append(document)
        safe_name = f"document_{index}.json"
        (OUTPUT_DIR / safe_name).write_text(
            json.dumps(document, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    bundle = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source_directory": str(DATA_DIR.relative_to(ROOT)),
        "document_count": len(documents),
        "notes": [
            "本结果由 agent.product_doc_agent 本地规则抽取生成。",
            "当前环境未配置 LLM API Key，因此结果来自 DOCX 表格规则抽取，适合先给业务人员做可用性预览。"
        ],
        "documents": documents,
    }
    (OUTPUT_DIR / "all_documents.json").write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({
        "document_count": len(documents),
        "outputs": [str((OUTPUT_DIR / f"document_{i}.json").relative_to(ROOT)) for i in range(1, len(documents) + 1)],
        "bundle": str((OUTPUT_DIR / "all_documents.json").relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
