# 2026-08-11 重要決策、問題與解法

接續 [2026-08-11-core-implementation-summary.md](2026-08-11-core-implementation-summary.md) 的初版實作，這份記錄今天後續把系統跑通、量測、補文件的過程——重點是**踩過的坑**，因為這些坑下次換機器/換專案還會再遇到。

## 1. n8n 2.x 的匯入/發佈流程整套變了

**問題**：舊版 README 步驟寫的是 GUI 匯入 + 手動下拉選單指定 Error Workflow，但 n8n 2.x 已經沒有 `delete:workflow` CLI，GUI 匯入器也搬進了編輯器內部（`⋯ → Import from File`），照舊步驟做不下去。

**決策**：兩個 workflow JSON 都給**固定 ID**（`LogisticsDocProc`、`LogisticsErrorWf`），並讓 `doc_processing` 在 `settings.errorWorkflow` 直接寫死指向 error handler 的 ID。

**效果**：`docker compose exec n8n n8n import:workflow --separate --input=...` 變成**冪等**操作——重跑就是更新，clone 下來一個指令就自動接好錯誤處理，不用再手動選。（commit `8f95261`）

## 2. n8n webhook 傳檔案到 API，欄位名對不起來

**問題**：HTTP Request 節點原本設定讀取 binary field `'file'`，結果每次上傳都報錯 `"The item has no binary field 'file'"`。

**根因**：n8n 的 Webhook 節點在收 multipart 上傳時，**會自動在你設定的 property name 後面加索引**——你設 `binaryPropertyName: "file"`，n8n 實際存成 `"file0"`。這是 n8n 本身的行為，不是設定錯誤，第一次遇到很容易誤以為是自己哪裡打錯字。

**解法**：把 `Call Extract API` 節點的 `inputDataFieldName` 從 `"file"` 改成 `"file0"`；multipart 送到 FastAPI 的欄位名（`name` 參數）維持 `"file"` 不變，因為那是 FastAPI `/extract` 端點認的欄位名，跟 n8n 內部的 binary property 是兩件事，不要搞混。（commit `db32eb5`）

**同一個 commit 也發現並補了**：n8n 2.x 把「Active」改名叫「Publish」了，而且**未發佈的 Error Workflow 會被靜默跳過**，只在 log 留一行「is not active and cannot be executed」，畫面上完全看不出來哪裡沒接上。兩個 workflow 都要 publish，且 CLI 發佈完要 `docker compose restart n8n` 才生效。

## 3. n8n Form 節點的標題/說明文字沒有代入變數

**問題**：Human-review 表單的 `formTitle`/`formDescription` 設定成一般字串，結果審核員在畫面上看到的是**字面上的 `{{ $json.document_id }}`**，不是真正的文件 ID。

**根因**：n8n 的規則是——**參數值必須以 `=` 開頭，才會被當成 expression 求值**；沒有 `=` 前綴就是純文字，大括號語法完全不會被解析。

**解法**：兩個欄位都補上 `=` 前綴。順便把整條 HITL 路徑跑了一次端到端驗證：上傳缺欄位文件 → 進 human_review → 表單正確顯示文件 ID 與被標記的欄位 → 提交修正 → resume workflow → 回寫 `POST /review/{id}` → 文件狀態變 approved（`method=human`、`confidence=1.0`）→ audit log 有 diff 紀錄。全部照預期跑完。（commit `75633db`）

## 4. 「deterministic-first 比較省」這句話一開始只是斷言，不是實測

**決策**：加一個 pure-LLM 對照組——`RULE_LAYER_ENABLED=0` 讓所有欄位都走 GPT-4o Vision，兩組跑同一套 50 份語料、同樣的 threshold、同一個模型，只有這一個變數不同。

**踩到的坑**：第一次跑 hybrid 組只有 332/334，比預期低一筆。查下去發現是**資料庫沒清乾淨**——直接沿用了剛剛示範 HITL 流程時的資料庫，裡面有一筆文件已經被人工修正過，於是重新 extract 時 `/extract` 的 SHA-256 冪等機制直接回傳了舊的（已經是人工修正結果的）紀錄，把人工修正值當成「模型抽取結果」計分，數字失真。

**解法**：對一個乾淨的資料庫重跑。README 也補了警語——「evaluate against a fresh database，否則審核修正會被誤計為抽取輸出」。

**量測結果**（記在 README 的 Evaluation 段落）：79.5% 欄位靠規則層零成本解掉，換來單份文件成本降 67%（$0.00589 → $0.00195）、p50 延遲降 58 倍（3768ms → 65ms），field accuracy 兩組打平（333/334）、auto-approve precision 兩組都是 100%。兩組唯一同時答錯的欄位，是 `invoice_048.pdf` 被刻意拿掉的 total 金額——GPT-4o Vision 照樣掰了一個 `4720.55` 出來，但同一次刪除也把 currency 一起拿掉了，信心分數不夠，兩組都被路由去人工審核而不是直接寫進資料庫。這正是「confidence-based routing」這個架構存在的意義：防線不是靠更好的 prompt，是靠「有缺口就不自動核准」。（commit `1be5a40`）

## 5. 架構圖從 ASCII 換成 SVG

**決策**：把 README 裡的 ASCII 示意圖換成 SVG，順便把上面第 4 點量測出來的關鍵數字（成本 -67%、延遲 -58×）直接畫進圖裡，讓讀者不用讀到 Evaluation 章節就能看到結論。（commit `5e7d068`）

## 6. 這次對話：把 README 每一句話對照原始碼、截圖核對一遍

使用者要求「檢查 README 上所有資訊正確無誤」，逐項核對的結果：

- Port 對照表、env 變數、API 路由、n8n workflow ID、`<budget_exceeded>` 字串、72 個 pytest 測試數、corpus 組成（50/10 掃描/7 缺欄位）、334 個欄位總數、43/50 auto_approve、14% 人工介入率、SHA-256 冪等、`audit_log`、`method=human`——**全部跟程式碼與截圖吻合，沒發現錯誤**。
- 唯一發現的落差：`Dashboard02.png` 截圖顯示 p95 latency 是 6907ms，跟 README 表格寫的 5440ms/5526ms 對不上。**判斷這不是文件錯誤**，是兩次不同時間點打真實 GPT-4o Vision API 的延遲本來就會抖動（p95 是被少數掃描件的網路延遲拉出來的尾端值），不需要修正數字，只是值得知道這類「即時 API 延遲」的數字每次重跑都可能不一樣。

## 7. Demo GIF 錄影計畫，後來喊停

原本規劃了一支 60 秒 demo GIF（乾淨發票秒回 auto_approve → 掃描件走 Vision → 缺欄位走 HITL 表單 → 回 n8n 看執行變綠 → 切到 dashboard），錄製腳本存在 [2026-08-11-demo-gif-recording-steps.md](2026-08-11-demo-gif-recording-steps.md)。

過程中發現一個原本沒想到的坑：**n8n Webhook 節點設定 `responseMode: lastNode`，workflow 卡在等人審核的節點時，curl 會一直吊著不回應**，而且 Wait-for-form 節點的 resume URL 是**執行期才產生**，沒有現成連結可以直接貼——必須先去 n8n 的 Executions 頁面找到狀態是 `Waiting` 的那筆執行，從網址列的 execution ID 拼出 `http://localhost:5678/form-waiting/<執行ID>` 才能打開真正的審核表單。這比原本想的「複製貼上一個連結」麻煩不少，使用者後來決定不錄了，但拼 URL 的方法留在錄影腳本筆記裡，之後真的要錄的話可以直接照做。

另外也發現 `invoice_006.pdf`（原本規劃拿來示範「補欄位」的樣本）其實是**合成語料裡刻意讓欄位完全不存在**的那一類（ground truth 該欄位本身就是 `null`），不是「有印在文件上但被漏抓」，如果要示範「審核員照著文件把正確答案填回去」這種更真實的情境，要挑另一種樣本（欄位存在但信心分數不夠的），下次要錄的話這點要先選對樣本。

## 給下次接手的人的重點提醒

1. **改 n8n Form 節點的任何字串參數，只要想代變數，開頭一定要打 `=`**，不然畫面上會看到原始 `{{ }}` 語法。
2. **改 n8n webhook 上傳邏輯時，binary property name 記得 n8n 會自動加索引**（`file` → `file0`），這不是 bug。
3. **跑評估腳本前一定要換乾淨資料庫**，`/extract` 的冪等機制會讓審核修正值被誤算成抽取準確率。
4. **n8n 2.x 的 Publish 是分開的兩顆開關**（workflow 本身 + error workflow 都要各自 publish），CLI 發佈完要重啟才生效。
5. Demo/錄影用的樣本要先查 `ground_truth.json` 確認欄位是「genuinely missing」還是「有但信心不足」，兩種示範情境的說服力差很多。
