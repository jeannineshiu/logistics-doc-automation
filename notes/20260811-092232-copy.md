核心實作完成了。以下是實作摘要（繁體中文）：

## 完成內容（對應計畫 Day 1–7 的核心）

**抽取引擎（三層設計，`api/engine/`）**
- `rules.py` — deterministic 層：invoice/customs 共 13 種欄位的 regex 抽取 + 驗證（IBAN 用 schwifty checksum 驗證直接給 confidence 1.0、VAT 各國格式、歐式金額 `1.234,56` 正規化、HS code 章節驗證、日期不得在未來），加上關鍵字投票的文件類型分類器
- `llm_extractor.py` — GPT-4o Vision 層：**只要求規則層漏掉的欄位**、強制 JSON 輸出、解析失敗重試一次、模型自報每欄位 confidence
- `budget.py` — token 硬上限（8000/doc、最多 2 次呼叫），超限即中止轉人工
- `confidence.py` — 三段式路由（auto_approve ≥0.90 / review / reject），閾值走環境變數

**API（FastAPI + PostgreSQL）** — `/extract`（以 SHA-256 冪等，同檔重送不重複入庫也不重複花錢）、`/documents`（含 audit trail）、`/review/{id}`（人工修正記 diff、confidence 設 1.0，可當未來 fine-tuning 資料）、`/metrics`（Prometheus 格式）

**測試** — **65 個 pytest 全數通過**，ruff 全乾淨；OpenAI 完全 mock，CI 可跑不花錢。GitHub Actions workflow 已附。

**資料與評估** — 合成產生器（3 種 invoice 版型 EN/DE、2 種 customs、噪點掃描件、隨機缺欄位），已產 50 份 + ground truth。實測 rule-only baseline（`LLM_ENABLED=0`）：

| 指標                    | 結果                                |
| ----------------------- | ----------------------------------- |
| Auto-approve precision  | **100%**（34/34，零錯誤入庫）       |
| Human intervention rate | 32%（掃描件與缺欄位全部正確轉人工） |
| Rule-layer coverage     | 100%（文字層欄位零 token 成本）     |
| Latency p50/p95         | 30ms / 162ms                        |

**n8n** — `doc_processing.json`（Webhook → Extract(retry×3) → Switch 三分支 → HITL 用 Wait+Form node → `/review` 回寫）與 `error_handler.json` 已放在 `n8n/workflows/`，README 有匯入步驟；用 n8n Cloud 的話把 HTTP node 的 `http://api:8000` 換成 tunnel 出去的公開網址即可。

**其他** — docker-compose 一鍵起四服務、Streamlit dashboard、README（含架構圖與實測數字）。

## 接下來建議

1. `cp .env.example .env` 填入 `OPENAI_API_KEY` 後 `docker compose up --build`，跑 hybrid 評估補齊 README 的 LLM 數字（掃描件會走 Vision）
2. 匯入 n8n workflow 實測一次 HITL 流程並截圖（JSON 是手寫的，匯入後若有 node 參數需微調告訴我）
3. 這個資料夾目前在上層無關的 git repo 裡——發佈前建議獨立成自己的 repo，需要的話我可以處理