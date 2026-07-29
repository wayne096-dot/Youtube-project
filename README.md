# 全專案由Gemini生成

# 📺 YouTube Termux Player

一個專為 **Android Termux** 環境打造的輕量級 YouTube 影片/直播播放器。整合 `SDL2` 圖形介面、`mpv` 核心播放、`pytchat` 直播聊天室與 `yt-dlp` 樹狀留言解析。
可在被限速（15KB/s）的情況下觀看YouTube影片（144p）
---

## ✨ 核心特點

* 🎬 **影片與直播播放**：支援 YouTube 一般影片與 Live 直播串流（基於 `mpv` 與 `yt-dlp`）。
* 💬 **即時直播聊天室**：
  * 即時接收並顯示直播間訊息。
  * **智慧滾動機制**：手動瀏覽（按 `↑` / `↓`）時自動暫停跟隨，超過 **10 秒** 無操作會自動跳回並持續鎖定最新留言。
* 🌳 **樹狀影片留言系統**：
  * 完整支援多層級回覆展開與折疊（按 `Enter` 鍵）。
  * 支援顯示按讚數與相對時間（例如：`5 分鐘前`、`2 週前`）。
  * 支援分頁載入更多留言。
* ⚡ **針對 Termux 優化**：自動搭配 `deno` JS 執行環境提升 `yt-dlp` 解析效率。

---

## 📱 Termux 環境安裝指南

在 Termux 中運行需要安裝 C/C++ 編譯環境、SDL2 函式庫以及 Python 相關依賴。

### Step 1: 更新套件並安裝系統依賴

開啟 Termux 並執行以下指令：

```bash
# 更新套件庫
pkg update && pkg upgrade -y

# 安裝必備系統工具、編譯器與播放核心
pkg install python clang ffmpeg mpv mpv-x libsdl2 libsdl2-ttf libsdl2-image deno pkg-config -y
```

> 說明：deno 用於提供 yt-dlp 執行 YouTube JavaScript 解密所需的執行環境。
> 
Step 2: 安裝 Python 依賴套件
# 升級基礎建置工具

```bash
pip install --upgrade pip setuptools wheel
```


# 安裝專案所需 Python 套件
```bash
pip install yt-dlp pytchat PySDL2 python-mpv
```

🚀 執行方法
確定所有依賴安裝完成後，在專案目錄下執行：
```bash
export DISPLAY=:0
export LIBGL_ALWAYS_SOFTWARE=1
termux-x11 :0 &
python main.py
```

⌨️ 快捷鍵控制 (Keybindings)
| 按鍵 | 功能說明 |
|---|---|
| ↑ (Up) | 向上選擇留言 / 聊天訊息（直播模式下會暫停自動置底） |
| ↓ (Down) | 向下選擇留言 / 聊天訊息（直播模式下會暫停自動置底） |
| Enter | 展開/折疊子留言，或點擊最底部的「載入更多留言」 |
| Space | 暫停 / 繼續播放影片 |
| Q / Esc | 退出播放器 |
> 💡 直播聊天室說明：按下方向鍵手動瀏覽聊天紀錄時，上方標題會顯示倒數計時。停止操作 10 秒 後，系統將自動跳回最底部並重新啟用「📌 自動鎖定最新」。
> 
📁 專案結構
├── main.py           # 主程式（SDL2 渲染迴圈、事件處理、介面配置）
├── youtube.py        # yt-dlp 留言擷取與 parse_time_text 樹狀解析邏輯
└── README.md         # 專案說明文件

📝 常見問題與排除 (Troubleshooting)
 * yt-dlp 抓取留言速度變慢或失敗？
   * 請確定有安裝 deno（pkg install deno），youtube.py 會自動調用 deno 作為 JS runtime 來加速解析。
   * 定期更新 yt-dlp：pip install -U yt-dlp
 * 畫面提示 [vo/x11] Warning... 警告？
   * 這是 Termux X11 / GUI 繪製環境的正常提示，不影響播放與留言渲染。

# 建議
建議使用av01編碼（省流量）

[![操作影片](https://img.youtube.com/vi/WwgKyLvW9-4)](https://youtu.be/WwgKyLvW9-4?si=kP8iYiWA2nP36mh4)
