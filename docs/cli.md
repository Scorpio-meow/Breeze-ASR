# 命令列工具、MCP server 與 Agent Skill

[README](../README.md) · [部署](deployment.md) · [設定](configuration.md) · [運作方式](how-it-works.md) · [API](api.md) · **命令列與 MCP** · [效能實測](benchmarks.md)

兩者都是呼叫執行中的服務（先用 `scripts\start.ps1` 或桌面捷徑啟動），檔案從這台電腦上傳到服務，不經過其他地方。要在專案資料夾用 `uv run --env-file .env` 執行，才會讀到 `.env` 的這些變數：

| 環境變數 | 用途 |
| --- | --- |
| `ASR_HOST`、`ASR_PORT` | 服務網址（`0.0.0.0` 會改連 `127.0.0.1`）；命令列工具也可以用 `--url` 指定 |
| `ASR_ACCESS_TOKEN` | 服務設定了[存取權杖](configuration.md#存取權杖)時，以 `Authorization: Bearer` 帶給服務；權杖沒設或不正確時，會說明是權杖的問題 |

用 `--url` 連到別台電腦的服務時，先把 `ASR_ACCESS_TOKEN` 設成那台服務的權杖；那台服務的 [`ASR_ALLOWED_HOSTS`](configuration.md#接受的主機名稱) 也要包含網址裡的名稱，否則會回 400。

## 命令列工具

```powershell
uv run --no-sync --env-file .env python -m app.cli transcribe 週會.m4a 訪談.mp4 --speakers auto --punctuation on --formats md,srt
```

| 指令 | 說明 |
| --- | --- |
| `transcribe 檔案…` | 送出一個或多個檔案（全部先排隊，再依序等結果），完成後寫出結果，並印出寫出的路徑 |
| `jobs` | 列出服務裡保留中的工作：id、狀態、檔名、長度、段數 |
| `export 工作id --format srt [--output 檔案]` | 下載某個工作的結果；沒給 `--output` 時輸出到標準輸出 |
| `status` | 服務網址、硬體設定、各模型的狀態與實測速度（每秒音訊要花幾秒）、分辨說話者與自動標點是否可用、上傳上限 |
| `cancel 工作id` | 取消排隊中或處理中的工作 |
| `rename 工作id S1=王小明 [S2=陳美玲…]` | 替說話者改名，會解除這位說話者和語者庫的對應 |
| `render 工作id 影片 [--output 檔案]` | 把目前的字幕燒進原始影片（長度要相差 2 秒以內），完成後下載 MP4；沒給 `--output` 時寫在影片旁邊，例如 `週會-字幕.mp4` |
| `glossary [--set 檔案]` | 印出詞彙表（熱詞與取代規則）的 JSON；`--set` 用 UTF-8 的 JSON 檔整份取代 |
| `voices list`、`voices remember 工作id S1`、`voices forget 語者id` | 列出語者庫、以目前的名字記住說話者的聲音、從語者庫刪除 |

`transcribe` 的參數：

| 參數 | 必填 | 說明 |
| --- | --- | --- |
| `--speakers` | 是 | `off`、`auto` 或人數 |
| `--punctuation` | 是 | `on` 或 `off` |
| `--formats` | 是 | 以逗號分隔：`txt`、`srt`、`vtt`、`ass`、`md`、`docx`、`json` |
| `--model` | 否 | `models.json` 的模型 id；沒給時用服務的預設模型 |
| `--hotwords 文字` | 否 | 這次辨識的熱詞 |
| `--glossary` | 否 | 改用網頁「詞彙」裡的熱詞（和 `--hotwords` 擇一） |
| `--out 資料夾` | 否 | 結果寫到這個資料夾；沒給時寫在原檔旁邊。檔名是原檔名加格式，例如 `週會.m4a.md` |

進度印在標準錯誤，完成時印出段數與說話者人數（例如「完成，25 段，說話者 2 位」），分辨說話者失敗時改印原因，字幕照常寫出；某個檔案送不出去（例如沒有音訊、排隊已滿）時印出原因，其餘檔案照常辨識；有任何檔案失敗時結束碼是 1。詞彙表的取代規則由服務套用，命令列送出的工作也一樣。

中文的名字與詞彙表都由命令列工具以 UTF-8 送出，Windows PowerShell 也不會變成亂碼。詞彙表與語者庫是整台服務共用的，`glossary --set` 會整份取代，先用 `glossary` 印出目前的內容再修改。聲紋屬於個人生物特徵，記住別人的聲音前請先取得對方同意。

沒有專案資料夾的電腦（例如只用 Docker，或服務在另一台電腦）可以改用 [Agent Skill](#agent-skill) 附的 `skills/listenmiao/scripts/listenmiao.py`：只需要 Python 3.8 以上的標準函式庫，指令與參數和這裡相同，服務網址用 `--url` 指定，權杖同樣從環境變數 `ASR_ACCESS_TOKEN` 讀取。

## MCP server

給 Claude Code 等 MCP 用戶端直接呼叫，只走 stdio。依 MCP [`2026-07-28`](https://modelcontextprotocol.io/specification/2026-07-28) 規格實作（無狀態，用戶端以 `server/discover` 取得版本與功能），也接受用 `initialize` 握手的舊版用戶端（`2024-11-05` 到 `2025-11-25`）。

在 Claude Code 加入（路徑換成專案資料夾）：

```powershell
claude mcp add listenmiao -- uv run --directory C:/code/ListenMiao --no-sync --env-file .env python -m app.mcp_server
```

server 回報的名稱是 `listenmiao`，標題 `ListenMiao`，版本與網址取自 `pyproject.toml`，圖示是 `static/icon.svg`。日誌寫到標準錯誤，由 MCP 用戶端保留；服務本身的日誌見[部署](deployment.md#日誌)。

> [!CAUTION]
> 逐字稿會進到 MCP 用戶端的對話裡，可能因此送到外部 AI 服務。內容不能交給外部 AI 的錄音，只用網頁或命令列工具。

### 工具

| 工具 | 標題 | 說明 |
| --- | --- | --- |
| `list_models` | 可用的模型 | 模型的 id、名稱、準備狀態、是否支援熱詞，以及服務能不能分辨說話者（最多幾人）、自動加標點 |
| `transcribe` | 辨識錄音 | 辨識這台電腦上的檔案，參數見下表 |
| `job_status` | 工作進度 | 狀態與進度；完成後附上說話者清單（編號、名字、講話秒數）與 Markdown 逐字稿的資源 URI |
| `get_transcript` | 取得逐字稿 | 結果全文：`md`、`txt`、`srt`、`vtt`、`ass` 或 `json` |
| `list_jobs` | 工作清單 | 服務裡保留中的工作，最新的在前 |
| `cancel_job` | 取消工作 | 取消排隊中或處理中的工作；處理中的工作要等推論停下，回傳時可能仍是 `running` |
| `rename_speaker` | 替說話者改名 | 用 `job_status` 的說話者編號（例如 `S1`）改名，會解除這位說話者和語者庫的對應 |

`transcribe` 的參數：

| 參數 | 必填 | 說明 |
| --- | --- | --- |
| `path` | 是 | 檔案在這台電腦上的完整路徑 |
| `speakers` | 是 | `off`、`auto` 或人數 |
| `punctuation` | 是 | 是否自動加標點 |
| `wait` | 是 | `true` 等辨識完成並回傳 Markdown 逐字稿；長錄音請用 `false`，再用 `job_status` 查進度 |
| `model` | 否 | `list_models` 的模型 id；沒給時用服務的預設模型 |
| `hotwords` | 否 | 這次辨識的熱詞 |
| `use_glossary` | 否 | 改用網頁「詞彙」裡的熱詞（和 `hotwords` 擇一） |

`wait` 為 `true` 時，server 每 2 秒查一次進度；用戶端有要求進度通知時，每次都送出 `notifications/progress`，`progress` 是已等待的秒數，`message` 是進度說明（例如「辨識中 42%」）。用戶端取消這個呼叫不會停止辨識，要停止請用 `cancel_job`。

除了 `get_transcript` 直接回傳文字，其他工具都有 `outputSchema`，結果放在 `structuredContent`，同時附上相同內容的 JSON 文字。出錯時（找不到檔案、工作已過期、連不上服務）回傳 `isError: true` 與原因。

每個工具都帶有 `title` 與下列 annotations，讓用戶端決定呼叫前要不要先問使用者；工具都只連這台電腦的服務，`openWorldHint` 都是 `false`：

| 工具 | `readOnlyHint` | `destructiveHint` | `idempotentHint` |
| --- | --- | --- | --- |
| `list_models`、`job_status`、`get_transcript`、`list_jobs` | `true` | `false` | `true` |
| `transcribe` | `false` | `false` | `false` |
| `cancel_job`、`rename_speaker` | `false` | `true` | `true` |

### 資源

| URI | 類型 | 內容 |
| --- | --- | --- |
| `listenmiao://glossary` | `application/json` | 網頁「詞彙」裡的熱詞與取代規則 |
| `listenmiao://jobs/{job_id}/transcript.md` | `text/markdown` | 含時間戳與說話者的逐字稿 |
| `listenmiao://jobs/{job_id}/transcript.txt` | `text/plain` | 純文字 |
| `listenmiao://jobs/{job_id}/transcript.srt` | `application/x-subrip` | SRT 字幕 |
| `listenmiao://jobs/{job_id}/transcript.vtt` | `text/vtt` | WebVTT 字幕 |
| `listenmiao://jobs/{job_id}/transcript.ass` | `text/x-ssa` | ASS 字幕 |
| `listenmiao://jobs/{job_id}/transcript.json` | `application/json` | 每段的時間、文字、說話者編號，以及說話者清單 |

逐字稿資源每次讀取都向服務重新取得，網頁上的修改會立即反映。工作不存在或已過期時回傳 JSON-RPC 錯誤 `-32602`，連不上服務是 `-32603`。

### 提示

| 提示 | 標題 | 參數 | 內容 |
| --- | --- | --- | --- |
| `meeting_minutes` | 整理會議紀錄 | `job_id` | 摘要、討論重點、決議與待辦事項（負責人、期限） |
| `proofread_transcript` | 校對逐字稿 | `job_id` | 可能辨識錯的字詞，以及可以貼到網頁「詞彙」的熱詞與取代規則 |

兩個提示都把 Markdown 逐字稿當成內嵌資源放在第一則訊息，第二則是整理的要求。提示與逐字稿資源的 `job_id` 可以自動補全，只列出已完成的工作。

## Agent Skill

[`skills/listenmiao/`](../skills/listenmiao/SKILL.md) 是給 Claude Code 等支援 [Agent Skills](https://agentskills.io/) 的 AI 工具用的 Skill：使用者說「轉逐字稿」「幫這支影片上字幕」「把會議錄音整理成會議紀錄」時，AI 會照它用命令列工具辨識、選模型與說話者人數、先問人名與術語再帶熱詞、替說話者改名、整理會議紀錄、燒錄字幕，以及在服務沒開或出錯時排除問題。

| 內容 | 用途 |
| --- | --- |
| `SKILL.md` | 主要流程與規則（英文） |
| `references/setup.md` | 安裝、啟動、第一次啟動、更新、錯誤對照、日誌 |
| `references/api.md` | 命令列工具沒有的操作（改字幕段落、重新調校）直接呼叫 API 的方式 |
| `scripts/listenmiao.py` | 只用標準函式庫的命令列工具，指令與 `python -m app.cli` 相同，給沒有專案資料夾的電腦 |

Skill 只呼叫執行中的服務，錄音不會離開這台電腦；但 AI 讀到的逐字稿會進入對話，內容不能交給外部 AI 的錄音，請只用網頁或命令列工具。

開發這個 Skill 用的測試情境放在 repo 根目錄的 [`evals/`](../evals/)，不在 Skill 資料夾裡，安裝時不會帶到。每個情境一個資料夾，`prompt.md` 是使用者會說的話，`graders/` 是評分標準，`files/` 是測試用的錄音與影片（語音合成的週會、29 秒的課程片段）：

| 情境 | 檢查什麼 |
| --- | --- |
| `meeting-minutes-two-speakers` | 兩人週會：帶與會者熱詞、判斷誰是誰並寫回服務、決議與待辦附時間戳、標成未校對的草稿 |
| `burn-subtitles-into-video` | 燒錄字幕：不標說話者、用服務的燒錄功能、燒錄前不自己猜著改字 |
| `confidential-transcript-unread` | 機密錄音：只轉成 Word，AI 不讀、不轉述逐字稿內容 |

### 安裝 Skill

Skill 只是教 AI 怎麼操作服務，要先照 [README](../README.md#快速開始) 安裝並啟動 ListenMiao。AI 工具要能在這台電腦執行指令，並連得到服務。這台電腦有 ListenMiao 專案資料夾時，Skill 用專案裡的命令列工具；沒有的話（只用 Docker，或服務在另一台電腦），改用 Skill 附的腳本，需要 Python 3.8 以上。

用 [`skills`](https://github.com/vercel-labs/skills) 指令一行安裝，它會偵測你電腦上的 AI 工具並放到對應的資料夾：

```bash
bunx skills add Scorpio-meow/ListenMiao --skill listenmiao -g
```

- `-g` 裝到個人目錄（所有專案都能用）；拿掉 `-g` 則只裝到目前的專案。
- 只裝給特定工具，加上 `-a`，例如 `-a claude-code -a codex`；各工具的值見[下一節的表格](#各工具的-skill-資料夾)。
- 沒有 [Bun](https://bun.sh) 的話，把 `bunx` 換成 `npx` 也可以。

裝好後直接請 AI 處理錄音就會自動啟用，例如：

> 把 D:\錄音\週會.m4a 轉成逐字稿，分出說話者，再整理成會議紀錄

第一次執行指令時，多數工具會先請你同意。

`skills` 指令要用 git 下載 Skill。電腦沒有 git 時，安裝腳本最後會問你要不要直接把專案裡的 `skills/listenmiao` 複製到 `~/.claude/skills/` 與 `~/.agents/skills/`（直接按 Enter 就是安裝；已經有 `listenmiao` 的資料夾會略過）；已經裝好 ListenMiao 的，也可以在專案資料夾執行 `uv run --no-sync python -m app.update --root . skills` 做同樣的事，或照下一節手動複製。`~/.agents/skills/` 是多個工具共用的資料夾，Codex、Cline 等都會讀。之後在網頁上更新 ListenMiao 時，用安裝腳本裝的 Skill 會一起換成新版（你改過的保留不動）；用 `bunx` 或 `npx` 裝的，請用 `bunx skills update listenmiao`（或把 `bunx` 換成 `npx`）更新。

#### 各工具的 Skill 資料夾

手動安裝就是把 `listenmiao` 資料夾複製到工具讀取的 Skill 資料夾，讓 `SKILL.md` 落在 `<Skill 資料夾>/listenmiao/SKILL.md`。個人資料夾給這台電腦上的所有專案用；專案資料夾放在專案根目錄下。

| 工具 | `-a` 參數 | 個人資料夾 | 專案資料夾 | 手動叫用 |
| --- | --- | --- | --- | --- |
| Claude Code | `claude-code` | `~/.claude/skills/` | `.claude/skills/` | `/listenmiao` |
| OpenAI Codex（CLI、IDE 擴充套件、ChatGPT 桌面版） | `codex` | `~/.agents/skills/` | `.agents/skills/` | `/skills` 或 `$listenmiao` |
| GitHub Copilot（VS Code、Copilot CLI、JetBrains） | `github-copilot` | `~/.copilot/skills/` | `.github/skills/` | `/listenmiao` |
| Cursor | `cursor` | `~/.cursor/skills/` | `.cursor/skills/` | 在 Agent 對話輸入 `/` 選擇 |
| Gemini CLI | `gemini-cli` | `~/.gemini/skills/` | `.gemini/skills/` | `/skills list` 可確認是否載入 |
| Google Antigravity | `antigravity` | `~/.gemini/config/skills/` | `.agents/skills/` | — |
| Devin Desktop（原 Windsurf） | `devin` | `~/.config/devin/skills/` | `.devin/skills/` | `@listenmiao` |
| OpenCode | `opencode` | `~/.config/opencode/skills/` | `.opencode/skills/` | — |
| Cline | `cline` | `~/.cline/skills/`，也會讀 `~/.agents/skills/` | `.cline/skills/` | `/listenmiao` |
| Kiro | `kiro-cli` | `~/.kiro/skills/` | `.kiro/skills/` | 輸入 `/` 選擇 |
| JetBrains Junie | `junie` | `~/.junie/skills/` | `.junie/skills/` | `/listenmiao` 或 `$listenmiao` |
| Roo Code | `roo` | `~/.roo/skills/` | `.roo/skills/` | — |

`~` 在 Windows 是使用者資料夾（`%USERPROFILE%`）。所有工具都會在對話相關時自動使用 Skill，「手動叫用」欄只是指定使用的方式。

- Codex、Copilot、Cursor、Gemini CLI、OpenCode、Devin Desktop、Junie、Roo Code 也會讀 `~/.agents/skills/`，同時用好幾種工具的話，放一份在這裡就能共用。Claude Code 只讀 `~/.claude/skills/`。
- `bunx skills` 會把 Skill 放在共用的 `.agents/skills/`（加 `-g` 時是 `~/.agents/skills/`），再替其他工具建立連結。裝完工具沒讀到的話，改用下面的手動安裝，放到表中的資料夾。
- 表上沒有的工具（Amp、Goose、Kilo Code、Trae、Qwen Code 等）也能用 `bunx skills` 安裝，`-a` 的值見 [skills 的說明](https://github.com/vercel-labs/skills)。

以複製到 `~/.agents/skills/` 為例，換成其他工具時，把 `TARGET` 改成上表的路徑。已經有 ListenMiao 專案資料夾的話，在專案資料夾執行：

macOS／Linux：

```bash
TARGET=~/.agents/skills
mkdir -p "$TARGET"
cp -r skills/listenmiao "$TARGET/"
```

Windows PowerShell：

```powershell
$TARGET = "$HOME\.agents\skills"
New-Item -ItemType Directory -Force $TARGET | Out-Null
Copy-Item -Recurse -Force skills\listenmiao "$TARGET\"
```

沒有專案資料夾的話，先只下載 Skill 所在的資料夾，再到 `listenmiao-skill` 資料夾執行上面的指令：

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/Scorpio-meow/ListenMiao.git listenmiao-skill
git -C listenmiao-skill sparse-checkout set skills/listenmiao
```

工具若沒有偵測到新的 Skill，重新啟動一次即可。

#### 其他安裝方式

有些工具有自己的安裝指令：

- **GitHub CLI**（2.90 以上，`gh skill` 目前是 public preview）：`--agent` 的值和上表的 `-a` 相同。

  ```bash
  gh skill install Scorpio-meow/ListenMiao listenmiao --agent claude-code --scope user
  ```

- **OpenAI Codex**：在 Codex 輸入

  ```text
  $skill-installer install https://github.com/Scorpio-meow/ListenMiao/tree/main/skills/listenmiao
  ```

  Codex 預設在沙箱裡執行指令、不開網路，連到服務時會先請你核准。想一律允許，在 `~/.codex/config.toml` 加上：

  ```toml
  [sandbox_workspace_write]
  network_access = true
  ```

- **GitHub Copilot CLI**：在專案資料夾執行 `copilot skill add skills/listenmiao`；工作階段中途加入的，用 `/skills reload` 載入。
- **Gemini CLI**：預設裝到個人範圍，加上 `--scope workspace` 只裝到目前專案。

  ```bash
  gemini skills install https://github.com/Scorpio-meow/ListenMiao.git --path skills/listenmiao
  ```

- **Kiro**：Kiro 面板的 **Agent Steering & Skills** → **+** → **Import a skill** → **GitHub**，網址要指到 Skill 所在的子資料夾：`https://github.com/Scorpio-meow/ListenMiao/tree/main/skills/listenmiao`。

Claude 應用程式（網頁版、桌面版）與 ChatGPT 的 Skill 在雲端的沙箱執行，連不到你電腦上的服務，錄音也得先上傳到雲端，所以不適用；請在自己的電腦上用 Claude Code、Codex 等工具。

#### 更新與移除

| 安裝方式 | 更新 | 移除 |
| --- | --- | --- |
| `bunx skills` | `bunx skills update listenmiao` | `bunx skills remove listenmiao` |
| `gh skill` | `gh skill update` | 刪除 `listenmiao` 資料夾 |
| Codex（`$skill-installer`） | 刪除 `~/.codex/skills/listenmiao` 資料夾，再安裝一次 | 刪除 `~/.codex/skills/listenmiao` 資料夾 |
| Gemini CLI | 移除後重新安裝 | `gemini skills uninstall listenmiao` |
| 手動複製 | 更新專案資料夾（或在 `listenmiao-skill` 執行 `git pull`），再複製一次 | 刪除 `listenmiao` 資料夾 |

Skill 在 0.7.0 從 `breeze-asr` 改名為 `listenmiao`。用安裝腳本裝的，在網頁上更新時會換成新名字：從 0.6.x 更新到 0.7.0 時先維持舊名 `breeze-asr`（內容換成新版，照樣用 `/breeze-asr`），下一次更新再換成 `listenmiao`；用 `bunx skills` 裝過舊名的，先執行 `bunx skills remove breeze-asr`，再照上面重新安裝；其他方式裝的，刪掉 `breeze-asr` 資料夾後重新安裝。

路徑或介面如有變動，以官方文件為準：[Claude Code](https://code.claude.com/docs/en/skills)・[OpenAI Codex](https://learn.chatgpt.com/docs/build-skills)・[GitHub Copilot](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills)・[VS Code](https://code.visualstudio.com/docs/agent-customization/agent-skills)・[Cursor](https://cursor.com/docs/skills)・[Gemini CLI](https://geminicli.com/docs/cli/skills/)・[Google Antigravity](https://antigravity.google/docs/skills)・[Devin Desktop](https://docs.devin.ai/desktop/cascade/skills)・[OpenCode](https://opencode.ai/docs/skills)・[Cline](https://docs.cline.bot/customization/skills)・[Kiro](https://kiro.dev/docs/skills/)・[JetBrains Junie](https://junie.jetbrains.com/docs/agent-skills.html)・[Roo Code](https://roocodeinc.github.io/Roo-Code/features/skills/)・[skills](https://github.com/vercel-labs/skills)・[gh skill](https://cli.github.com/manual/gh_skill_install)
