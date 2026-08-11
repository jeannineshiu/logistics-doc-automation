# Demo GIF 錄製步驟（60 秒版本）

## 前置準備（錄影前 5 分鐘完成）

**1. 確認服務都在跑**
```bash
docker compose ps
# 確認 api / n8n / dashboard 都是 healthy
```

**2. 確認 n8n workflow 已 Publish**（不是灰的），且 webhook production URL 可用：
```bash
curl -F "file=@data/samples/invoice_000.pdf" http://localhost:5678/webhook/doc-upload
```
先跑一次，若這份文件之前測過會因為 idempotent（SHA-256 命中）直接回舊結果——录影前最好换一批「乾淨」的資料庫，或用還沒送過的樣本，才能在畫面上看到真的在跑。

**3. 準備好三個測試檔案**（已幫你從專案裡挑好，避免臨場找檔案）：

| 用途                        | 檔案                                | 為什麼選它                                                                           |
| --------------------------- | ----------------------------------- | ------------------------------------------------------------------------------------ |
| 乾淨發票，秒回 auto_approve | `data/samples/invoice_000.pdf`      | text-layer，README 範例就用它，規則層 100% 解出                                      |
| 掃描件，Vision 接手         | `data/samples/invoice_004_scan.pdf` | 無 text layer，會走 GPT-4o Vision → human_review                                     |
| 缺欄位，走審核表單          | `data/samples/invoice_006.pdf`      | 缺 `invoice_number` 一個欄位、非掃描件，適合現場示範「補一個欄位就好」，畫面乾淨好講 |

**4. 若資料庫已有這些檔案的紀錄，先清掉再錄**（否則 `/extract` 會直接回快取結果，不會展示真的處理過程）：
```bash
docker compose exec api rm -f /app/data/app.db   # 依實際 DATABASE_URL 路徑調整
docker compose restart api
```

**5. 開好四個視窗，按錄影時的順序排好（用 Mission Control 或手動疊放，避免錄影中切換卡頓）：**
- 視窗 A：Terminal（跑 curl 指令）
- 視窗 B：瀏覽器分頁 1 → n8n workflow canvas（`http://localhost:5678`），先打開對應的 workflow
- 視窗 C：瀏覽器分頁 2 → 審核表單（會在 curl 後才出現連結/webhook URL，先開好空白分頁待命）
- 視窗 D：瀏覽器分頁 3 → Streamlit dashboard（`http://localhost:8501`）

字體建議調大（Terminal 字級 16pt 以上），因為 GIF 通常會被壓縮，字太小會糊。

---

## 錄影階段：Cmd+Shift+5

1. 按 `Cmd+Shift+5` 叫出擷取工具列
2. 選「錄製選取範圍」而非全螢幕——只框住你需要的視窗區域，這樣輸出檔案小、畫面聚焦
3. 點「選項」→ 儲存位置設成 `docs/`（或先存到 Desktop 再搬過去）；不用倒數計時
4. 點「錄製」

### 60 秒腳本（含每步大約秒數，錄的時候心裡跟著數）

| 時間   | 動作                                                                                                                                                                              | 畫面                                                                          |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| 0–3s   | 停在 n8n canvas，讓觀眾看到整體 workflow 長相                                                                                                                                     | 視窗 B                                                                        |
| 3–15s  | 切到 Terminal，貼上並執行：`curl -F "file=@data/samples/invoice_000.pdf" http://localhost:5678/webhook/doc-upload \| jq`                                                          | 視窗 A，秒回結果，`decision: auto_approve`, `cost_usd: 0`（截圖重點：0 成本） |
| 15–18s | 切回 n8n canvas，展示這次執行變綠、走的是 rule-layer 分支                                                                                                                         | 視窗 B                                                                        |
| 18–32s | 切回 Terminal，執行：`curl -F "file=@data/samples/invoice_004_scan.pdf" http://localhost:5678/webhook/doc-upload \| jq`（這次會慢個 2-4 秒，正好展示 Vision fallback 的延遲差異） | 視窗 A                                                                        |
| 32–36s | 切到 n8n canvas，展示這次走的是 LLM 分支、決策是 `human_review`                                                                                                                   | 視窗 B                                                                        |
| 36–42s | 執行第三份：`curl -F "file=@data/samples/invoice_006.pdf" http://localhost:5678/webhook/doc-upload \| jq`，回傳結果同樣是 `human_review`，並印出 review 表單連結                  | 視窗 A                                                                        |
| 42–50s | 切到瀏覽器分頁 C，貼上該連結，打開 n8n Form，把缺的 `invoice_number` 欄位填上正確值，按 Submit                                                                                    | 視窗 C                                                                        |
| 50–53s | 切回 n8n canvas，展示 workflow 從暫停變成執行完成（綠勾）                                                                                                                         | 視窗 B                                                                        |
| 53–60s | 切到 Streamlit dashboard，重新整理，指著剛才三筆的數字變化（volume、decision 分佈、cost）                                                                                         | 視窗 D                                                                        |

> 每個步驟切換前**停留 1–2 秒不動**再操作，GIF 轉檔後幀率會被砍，動作太快容易糊成一團看不清。

5. 錄完按選取工具列上的「停止」（或 `Cmd+Ctrl+Esc`），檔案自動存成 `.mov`

---

## 後製：mov → GIF

### 方法 A：Gifski（畫質最好，推薦）
```bash
brew install gifski   # 若還沒裝
gifski --fps 12 --width 900 -o docs/demo.gif docs/Screen\ Recording*.mov
```
- `--fps 12`：GIF 不需要 30fps，12 已經夠流暢，檔案小很多
- `--width 900`：限制寬度，避免檔案過大（GitHub README 顯示夠用）

### 方法 B：ffmpeg + gifsicle（更可控，可先裁切/加速）
```bash
# 若需要先裁掉錄影前後的空白，或稍微加速讓 60 秒濃縮到 45 秒
ffmpeg -i docs/Screen\ Recording*.mov -ss 00:00:02 -t 00:00:58 -vf "fps=12,scale=900:-1:flags=lanczos" docs/demo_raw.gif

# 再用 gifsicle 壓一次，去掉重複幀、降色數
brew install gifsicle
gifsicle -O3 --colors 128 docs/demo_raw.gif -o docs/demo.gif
```

### 方法 C：線上 ezgif（不想裝套件時）
1. 上傳 `.mov` 到 https://ezgif.com/video-to-gif
2. FPS 設 10–12，寬度設 900px
3. 下載後再丟進 https://ezgif.com/optimize 跑一次 lossy compression（建議 60-80）降檔案大小

**目標檔案大小**：GitHub README 內嵌建議 < 10MB，最好壓到 5MB 以內，太大會被 GitHub 拒絕預覽。

---

## 驗證與收尾

```bash
ls -lh docs/demo.gif   # 檢查檔案大小
open docs/demo.gif     # 用 Finder/Preview 快速看一次，確認文字看得清楚、時序正確
```

確認沒問題後，加進 README：
```markdown
## Demo

![60-second demo: clean invoice auto-approves at zero cost, a scan triggers GPT-4o Vision fallback, a missing-field doc pauses for human review via an n8n Form, then resolves on the dashboard](docs/demo.gif)
```

要我現在幫你檢查 `docs/` 底下現有的截圖（`Dashboard01.png`、`HITL.png` 等），確認錄影時要呈現的畫面跟這些截圖是不是同一套流程、避免重複或矛盾嗎？