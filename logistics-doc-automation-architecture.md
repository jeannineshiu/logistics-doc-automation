# logistics-doc-automation — 專案架構設計

> **一句話定位：** 用 n8n + GPT-4o Vision 打造物流文件（invoice / customs form）的智慧處理管線，內建 confidence-based Human-in-the-Loop 路由、deterministic-first 抽取策略、以及 token-budget 成本治理 — 對齊 Zalando Senior Enterprise Automation Engineer JD 的全部核心需求。

---

## 1. 專案目標與履歷賣點

### 業務場景（README 開頭要講的故事）

物流團隊每天收到大量非結構化文件：供應商發票、報關單、運輸單據。人工輸入這些資料進系統既慢又容易出錯。本專案示範一條**受監督的自動化管線**：

- 文件進來 → 自動抽取結構化欄位 → 驗證 → 高信心自動入庫，低信心轉人工審核
- 目標不是「全自動」，而是 **supervised autonomy**：讓人只處理例外，不做例行輸入

### 對應 JD 的四個直接命中點

| JD 要求 | 本專案的對應 |
|---------|-------------|
| n8n workflow automation | 整條 pipeline 由 n8n orchestrate |
| LLM 整合進業務流程 + prompt engineering | GPT-4o Vision 結構化抽取 + 結構化輸出 schema |
| Intelligent Document Processing (OCR/IDP) | invoice / customs form 欄位抽取 |
| Human-in-the-Loop 架構 | confidence-based 三段式路由（auto / review / reject） |

### 面試差異化賣點（刻意設計進架構的）

1. **Deterministic-first 抽取** — 發票號碼、日期、IBAN、金額等有固定格式的欄位先用 regex / 規則抽，抽不到或格式異常才 fallback 到 LLM。呼應你的 MuseXR 故事：**hot path 上零 token 成本、零額外延遲**。
2. **成本治理寫在程式碼裡** — 每份文件有 token budget 硬上限，超過即中止並轉人工。呼應 aws-ai-agent 的 cost governance 敘事。
3. **評估先行** — 用 ground-truth 標註集量測 field-level extraction accuracy，dashboard 顯示 human intervention rate。呼應「evaluation visibility 是 2026 最大差異化」原則。

---

## 2. 系統架構總覽

```
                          ┌─────────────────────────────────────┐
                          │            n8n (Docker)             │
                          │                                     │
  文件輸入                 │  ┌─────────┐   ┌──────────────┐    │
  ─────────►  Webhook ────┼─►│ Trigger  │──►│ Call Extract  │───┼──┐
  (PDF/JPG/PNG)           │  │ Node     │   │ API (HTTP)    │    │  │
                          │  └─────────┘   └──────────────┘    │  │
                          │        ▲                            │  │
                          │        │      ┌──────────────┐     │  │
                          │        │      │ IF: decision │◄────┼──┘
                          │        │      └──┬────┬────┬─┘     │
                          │        │         │    │    │       │
                          │   ┌────┴───┐  auto review reject   │
                          │   │ Retry / │     │    │    │       │
                          │   │ Error   │     ▼    ▼    ▼       │
                          │   │ Handler │  ┌────┐ ┌────────┐ ┌─────┐
                          │   └────────┘  │ DB │ │Approval│ │Alert│
                          │               │Node│ │ (HITL) │ │Node │
                          └───────────────┴──┬─┴─┴───┬────┴─┴──┬──┘
                                             │       │         │
                                             ▼       ▼         ▼
                          ┌──────────────────────────────────────┐
                          │       FastAPI Backend (Docker)       │
                          │                                      │
                          │  /extract   ← 抽取服務（核心）        │
                          │  /documents ← 查詢已處理文件          │
                          │  /review    ← 人工審核提交            │
                          │  /metrics   ← Prometheus 格式指標     │
                          └──────────────────┬───────────────────┘
                                             │
                    ┌────────────────────────┼─────────────────────┐
                    ▼                        ▼                     ▼
            ┌──────────────┐        ┌───────────────┐    ┌──────────────┐
            │ PostgreSQL   │        │ Extraction     │    │  Dashboard   │
            │ (documents,  │        │ Engine         │    │  (Streamlit) │
            │  audit_log)  │        │ ├ Rule layer   │    │  處理量/準確率│
            └──────────────┘        │ ├ GPT-4o Vision│    │  介入率/成本  │
                                    │ └ Confidence   │    └──────────────┘
                                    │   scorer       │
                                    └───────────────┘
```

**設計原則：n8n 負責 orchestration（流程、分支、重試、通知），FastAPI 負責 computation（抽取、驗證、評分）。** 這個關注點分離本身就是面試可以講的架構決策 — n8n 的 node 保持薄，複雜邏輯進 Python 有 pytest 覆蓋。

---

## 3. 技術棧

| 層 | 選型 | 理由 |
|----|------|------|
| Orchestration | n8n (self-hosted, Docker) | JD 直接點名；開源可展示 workflow JSON |
| 抽取 LLM | GPT-4o Vision (OpenAI API) | 你已在 MuseXR 用過；PDF 頁面轉圖直接餵 |
| 確定性抽取 | `regex` + `dateutil` + `schwifty` (IBAN 驗證) | deterministic-first 賣點 |
| PDF 處理 | `pypdfium2`（PDF → PNG render） | 輕量、無 GPL 疑慮 |
| API | FastAPI + Pydantic v2 | 你的主力棧 |
| DB | PostgreSQL（docker-compose 附帶）| 比 SQLite 更貼近企業場景；audit log 需要 |
| HITL UI | n8n 內建 approval + Streamlit 審核頁二選一（見 §7）| 快速交付 |
| Dashboard | Streamlit | 你 electricity-hyperband 用過 |
| 指標 | `/metrics` endpoint（Prometheus 格式）| 呼應你的 MLOps 敘事，不必真跑 Grafana |
| 測試 | pytest（目標 30+ tests）| 延續「80+ tests」的履歷慣例 |
| 部署 | docker-compose 一鍵起全套 | README 的 quickstart 體驗 |

---

## 4. Repo 結構

```
logistics-doc-automation/
├── README.md                        # 架構圖 + quickstart + 評估結果
├── docker-compose.yml               # n8n + api + postgres + dashboard
├── .env.example
├── docs/
│   ├── architecture.png             # 架構圖（README 內嵌）
│   └── sample_workflow.png          # n8n workflow 截圖
├── n8n/
│   └── workflows/
│       └── doc_processing.json      # 匯出的 workflow（可一鍵匯入）
├── api/
│   ├── Dockerfile
│   ├── main.py                      # FastAPI app
│   ├── routers/
│   │   ├── extract.py               # POST /extract
│   │   ├── documents.py             # GET /documents, GET /documents/{id}
│   │   ├── review.py                # POST /review/{id}
│   │   └── metrics.py               # GET /metrics
│   ├── engine/
│   │   ├── rules.py                 # ★ deterministic 抽取層
│   │   ├── llm_extractor.py         # ★ GPT-4o Vision 抽取層
│   │   ├── confidence.py            # ★ 信心評分 + 路由決策
│   │   ├── budget.py                # ★ token budget 硬上限
│   │   └── pdf_utils.py             # PDF → image
│   ├── models/
│   │   ├── schemas.py               # Pydantic：InvoiceFields, CustomsFields...
│   │   └── db.py                    # SQLAlchemy models
│   └── tests/
│       ├── test_rules.py
│       ├── test_confidence.py
│       ├── test_budget.py
│       └── test_api.py
├── dashboard/
│   ├── Dockerfile
│   └── app.py                       # Streamlit
├── data/
│   ├── samples/                     # 10–15 份合成測試文件
│   ├── generate_synthetic.py        # 合成文件產生器
│   └── ground_truth.json            # 標註集（評估用）
└── eval/
    └── evaluate.py                  # field-level accuracy 評估腳本
```

---

## 5. 核心資料模型（Pydantic）

```python
# models/schemas.py
from datetime import date
from decimal import Decimal
from enum import Enum
from pydantic import BaseModel, Field

class DocType(str, Enum):
    INVOICE = "invoice"
    CUSTOMS_FORM = "customs_form"

class ExtractionMethod(str, Enum):
    RULE = "rule"          # deterministic 抽到
    LLM = "llm"            # LLM 抽到
    MISSING = "missing"    # 兩層都沒抽到

class FieldResult(BaseModel):
    value: str | None
    method: ExtractionMethod
    confidence: float = Field(ge=0.0, le=1.0)

class InvoiceFields(BaseModel):
    invoice_number: FieldResult
    invoice_date: FieldResult
    supplier_name: FieldResult
    supplier_vat_id: FieldResult      # 歐盟 VAT 格式可 regex 驗證
    currency: FieldResult             # ISO 4217
    total_amount: FieldResult
    iban: FieldResult                 # schwifty checksum 驗證

class CustomsFields(BaseModel):
    declaration_number: FieldResult
    hs_code: FieldResult              # 海關 HS code，6-10 碼數字
    country_of_origin: FieldResult    # ISO 3166-1 alpha-2
    gross_weight_kg: FieldResult
    declared_value: FieldResult
    currency: FieldResult

class Decision(str, Enum):
    AUTO_APPROVE = "auto_approve"     # 全欄位高信心 → 直接入庫
    HUMAN_REVIEW = "human_review"     # 部分欄位低信心 → HITL
    REJECT = "reject"                 # 無法辨識文件類型 / budget 超限

class ExtractionResponse(BaseModel):
    document_id: str
    doc_type: DocType
    fields: InvoiceFields | CustomsFields
    decision: Decision
    overall_confidence: float
    tokens_used: int
    cost_usd: float
    latency_ms: int
    flagged_fields: list[str]         # 需要人工看的欄位清單
```

**設計重點：每個欄位都帶 `method` 和 `confidence`** — 這讓 dashboard 能回答「多少比例的欄位是規則抽的（零成本）vs LLM 抽的」，直接量化 deterministic-first 的價值。

---

## 6. 抽取引擎：三層設計（面試核心素材）

### Layer 1 — Rule layer（`engine/rules.py`）

先跑 `pypdfium2` 的文字層抽取（大多數電子 PDF 有文字層，根本不需要 OCR/LLM），對有固定格式的欄位用 regex + 驗證函式：

| 欄位 | 方法 | 驗證 |
|------|------|------|
| invoice_number | regex（`INV[-/]?\d+` 等常見 pattern）| — |
| invoice_date | regex + `dateutil.parser` | 日期合理性（不在未來）|
| IBAN | regex + `schwifty` | **checksum 數學驗證** — 抽到即 confidence 1.0 |
| VAT ID | 各國格式 regex | 格式驗證 |
| currency | ISO 4217 白名單 | 白名單比對 |
| HS code | `\d{6,10}` | 長度 + 前綴表 |
| total_amount | 金額 regex（含千分位/歐式逗號小數）| 與 line items 加總比對（若有）|

**規則抽到且通過驗證 → confidence 直接給高分（0.95–1.0），不呼叫 LLM。**

### Layer 2 — LLM layer（`engine/llm_extractor.py`）

只對 Layer 1 沒抽到的欄位呼叫 GPT-4o Vision：

- PDF 頁面 render 成 PNG（150 dpi 夠用，控制 image token 成本）
- Prompt 只要求**缺失的欄位**，不重抽已有的 — 再省一次成本
- 強制 JSON 輸出（`response_format={"type": "json_object"}`）+ Pydantic 解析失敗即重試一次
- 要求模型對每個欄位自報 confidence（0–1）+ 在圖中的依據簡述

### Layer 3 — Confidence scorer & router（`engine/confidence.py`）

```python
THRESHOLDS = {
    "auto_approve": 0.90,   # 所有必要欄位 ≥ 0.90 → 自動入庫
    "review_floor": 0.50,   # 任一必要欄位 < 0.50 → 整份轉人工
}

def route(fields: dict[str, FieldResult], doc_type_conf: float) -> Decision:
    if doc_type_conf < 0.6:
        return Decision.REJECT                      # 連文件類型都不確定
    confs = [f.confidence for f in fields.values() if f.value is not None]
    missing = [k for k, f in fields.items() if f.value is None]
    if missing or min(confs) < THRESHOLDS["review_floor"]:
        return Decision.HUMAN_REVIEW
    if all(c >= THRESHOLDS["auto_approve"] for c in confs):
        return Decision.AUTO_APPROVE
    return Decision.HUMAN_REVIEW
```

閾值寫成環境變數可調 — 面試可以講「閾值是 business decision：調高 auto_approve 閾值 = 人工介入率上升但錯誤入庫率下降，這條 trade-off curve 我在 dashboard 上畫出來了」。**這句話就是 senior-level 判斷力的展示。**

### Token budget（`engine/budget.py`）

```python
MAX_TOKENS_PER_DOC = 8000          # 硬上限
MAX_LLM_CALLS_PER_DOC = 2          # 初抽 + 一次重試

# 超限 → 立即中止，decision = HUMAN_REVIEW，
# flagged_fields = ["<budget_exceeded>"]，寫入 audit log
```

與 aws-ai-agent 完全同款的敘事：**cost cap enforced in code, not prompts**。

---

## 7. n8n Workflow 設計

### 主 workflow：`doc_processing.json`

| # | Node | 類型 | 說明 |
|---|------|------|------|
| 1 | Webhook Trigger | Webhook | 接收文件上傳（multipart）；另加一個 Schedule Trigger 定期掃描資料夾展示兩種觸發方式 |
| 2 | Call Extract API | HTTP Request | POST `/extract`，附 retry（3 次、指數退避）|
| 3 | Switch on decision | Switch | 三分支：auto / review / reject |
| 4a | Auto → Postgres | Postgres Node | 寫入 `documents`（status=approved）|
| 4b | Auto → Notify | Slack/Email | 「已自動處理：INV-xxxx, €yyy」摘要 |
| 5a | Review → Create Task | HTTP Request | POST 建立審核任務（status=pending_review）|
| 5b | Review → Wait for Approval | **n8n Wait/Form node** | 產生審核連結，人工在表單上修正欄位後 resume |
| 5c | Review → Submit | HTTP Request | POST `/review/{id}` 寫回人工修正值 |
| 6 | Reject → Alert | Slack/Email | 附原始檔連結請人工處理 |
| 7 | Error Handler | Error Trigger workflow | 任何 node 失敗 → 獨立 error workflow：記 log + 告警。JD 的「self-healing / exception-handling」就靠這個展示 |

### HITL 介面二選一（建議 A）

- **A. n8n Wait + Form node（建議）**：零額外前端開發，n8n 原生產生審核表單連結，人工修改後 workflow 自動 resume — 而且「我用 n8n 原生能力做 HITL」比自己寫前端更貼 JD。
- B. Streamlit 審核頁：更好看，但多花 1–2 天，優先級低。

### 交付物

- workflow 匯出成 JSON 放進 repo（reviewer 可一鍵匯入）
- README 附 workflow 截圖 — **這張圖是整個專案的視覺門面**

---

## 8. 資料：合成文件產生器

不要用真實企業文件（隱私 + 版權問題），自己產：

```python
# data/generate_synthetic.py
# 用 reportlab 產生 PDF：
#   - 3 種 invoice 版型（不同 layout、字型、語言：EN/DE 混合）
#   - 2 種 customs form 版型（模仿 CN22/CN23 的欄位結構，不複製官方表格）
#   - 隨機注入「困難樣本」：掃描噪點（轉圖再壓 JPEG）、手寫欄位（手寫字型）、
#     缺欄位、歐式數字格式（1.234,56）
# 同步輸出 ground_truth.json — 評估集就是這樣來的
```

產 **50 份**：35 份當測試/展示、15 份當 held-out 評估集。德文發票是刻意設計 — 對柏林職缺是隱性加分。

---

## 9. 評估（履歷 bullet 的數字來源）

`eval/evaluate.py` 對 held-out 集輸出：

| 指標 | 定義 | 目標 |
|------|------|------|
| Field-level accuracy | 抽取值 == ground truth 的欄位比例 | ≥ 95% |
| Doc-level auto-approve precision | auto_approve 的文件中全欄位正確的比例 | ≥ 98%（這是最重要的 — 錯誤自動入庫是最貴的失敗）|
| Human intervention rate | decision != auto_approve 的比例 | 量測並報告 trade-off，不硬追低 |
| Rule-layer coverage | 由規則層（零 token）抽出的欄位比例 | 量測 — deterministic-first 的價值證明 |
| Cost per document | 平均 USD | 量測，附「純 LLM 抽取」對照組 |
| p50 / p95 latency | 端到端 | 量測 |

**務必做一個對照實驗：** 全部欄位純 LLM 抽 vs. deterministic-first。預期結果是成本降 40–60%、規則欄位準確率更高 — 這一行對比數字就是你 bullet 公式「quantified impact vs. baseline」的完美素材。

---

## 10. API 規格摘要

```
POST /extract
  multipart: file (pdf/jpg/png)
  → ExtractionResponse

GET  /documents?status=&doc_type=&page=
  → 分頁列表

GET  /documents/{id}
  → 完整記錄 + audit trail（誰/何時/機器或人工/改了什麼）

POST /review/{id}
  body: {corrected_fields: {...}, reviewer: "..."}
  → 寫回修正值，status → approved，audit log 記 diff
  ★ 人工修正值同時存檔 — README 註明「可作為未來 fine-tuning / few-shot 資料集」，
    展示你想到了 continuous improvement loop

GET  /metrics
  → Prometheus text format（documents_processed_total、
    human_review_rate、tokens_used_total、extraction_errors_total）
```

---

## 11. 測試策略（pytest ≥ 30 tests）

- `test_rules.py`：每個 regex/驗證函式的正例反例（IBAN checksum、歐式金額格式、日期邊界）— 純函式最好測，這裡衝數量
- `test_confidence.py`：路由決策表 — 給定各種欄位信心組合，斷言 decision 正確（包含 budget 超限案例）
- `test_budget.py`：token 累計、超限中止
- `test_api.py`：FastAPI TestClient + **mock OpenAI client**（測試不花錢、CI 可跑）
- GitHub Actions：push 即跑 pytest + ruff — 延續你所有 repo 的 CI/CD 慣例

---

## 12. 8 天建構計畫

| 天 | 交付 |
|----|------|
| 1 | repo 骨架 + docker-compose（n8n + postgres + api 空殼跑通）+ 合成文件產生器 v1 |
| 2 | Rule layer 完成 + 測試（這天結束就有「零 LLM 的可用抽取器」— 先有 baseline）|
| 3 | GPT-4o Vision 抽取層 + Pydantic 解析 + budget 模組 |
| 4 | Confidence scorer + 路由 + `/extract` 完整 + audit log |
| 5 | n8n workflow：主流程 + 三分支 + error workflow |
| 6 | HITL：Wait/Form node + `/review` 回寫 + 端到端跑通一份「困難文件」 |
| 7 | 評估腳本 + 對照實驗（純 LLM vs. hybrid）+ Streamlit dashboard |
| 8 | README（架構圖、quickstart、評估表格）+ workflow 截圖 + 錄 1 分鐘 demo GIF |

**風險控制：** 若進度落後，砍 Streamlit dashboard（用 `/metrics` + README 表格替代），不砍評估 — 沒有數字的專案在 2026 求職市場等於沒做。

---

## 13. 完成後的履歷 bullet 草稿（Tier 1 語言）

> **Logistics Document Automation — n8n + LLM Intelligent Document Processing**
> *n8n · GPT-4o Vision · FastAPI · Pydantic · PostgreSQL · Docker · pytest · Streamlit*
>
> - Built a human-in-the-loop document pipeline (invoices, customs forms) orchestrated in n8n: rule-based extraction first, GPT-4o Vision fallback for ambiguous fields, and confidence-based routing to auto-approve, human review, or rejection — [X]% field-level accuracy, [Y]% auto-approve precision on a held-out evaluation set.
> - Engineered cost governance in code: deterministic layer resolves [Z]% of fields at zero token cost, per-document token budgets enforced as hard caps — cutting LLM cost per document by [W]% vs. a pure-LLM baseline.

（[X][Y][Z][W] 等實測數字出來後填入 — 每個都有 baseline 對照，符合 bullet 公式。）

---

## 14. 面試 Q&A 預備（做專案時就想好答案）

1. **「為什麼不全用 LLM 抽？」** → deterministic-first 的成本/延遲/可靠性論證 + 對照實驗數字 + MuseXR 同款哲學
2. **「HITL 閾值怎麼定？」** → trade-off curve：auto-approve precision vs. intervention rate，是 business decision 不是純技術決定
3. **「怎麼防止錯誤資料自動入庫？」** → 三重防線：格式驗證（IBAN checksum 等數學驗證）、confidence floor、audit log 可回溯
4. **「n8n 掛了怎麼辦？」** → error workflow + API 冪等設計（同一文件 hash 重送不會重複入庫）
5. **「怎麼 scale 到每天一萬份？」** → 誠實回答：目前是單機 demo；scale 路徑是 queue（n8n 支援）+ API 水平擴展 + Postgres 換 managed service — 承認這是 demo 級但講得出路徑
