# ScarletPhoneme

音素裁切音库管理器 —— 素材 → 强制对齐 → 切点校对 → モーラ切片 → 拼接。

Windows 桌面程序，Python + Tkinter 编写，单窗口，三个主标签：
**切点校对** / **音库索引** / **音库管理**。

> 本仓库只放源码和构建脚本。exe、声学模型、个人音库都不进仓库，
> 说明见下方「仓库里有什么 / 没有什么」。

---

## 功能

### 切点校对
- 波形查看、滚轮缩放、Shift+滚轮平移
- 点波形 = 移动播放头并选中播放头所在的音
- 单音循环：反复播放当前选中的音，改切点后循环长度自动跟上
- 吸附开关：开 = 拖一条边界左右两个音一起动；关 = 只动一侧，可以留空隙
- 多选/整体偏移：切点表 Ctrl/Shift 多选，按 10ms 步长整体平移
- 拖底部音名带的竖线改切点，Ctrl+Z 撤销
- 切点表点击定位，未保存换曲会提示保存/放弃/取消

### 音库索引
- 按音/母音浏览候选，可按「只看可疑」「只看句头」筛选，并显示干净候选数量
- 候选表双击/空格试听
- 波形预览：红框是候选切出来的范围，点哪从哪试听

### 音库管理
- 导入素材、预览匹配、再次导入 = 更新素材
- CTC 强制对齐（调用外部 Python + torch/transformers）
- 重建索引、导出切片、拼接成歌

---

## 环境要求

- Windows 10/11（播放使用系统 MCI，其他平台未测试）
- Python 3.10+（开发环境为 3.14）

### 安装运行依赖

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 安装分析依赖（可选）

只有「重新分析 / 强制对齐」需要：

```bat
pip install -r requirements-analysis.txt
```

Windows 想装纯 CPU 版 torch，可以先执行：

```bat
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install transformers
```

---

## 声学模型

分析需要把模型放到：

```text
models\japanese-hubert-base-phoneme-ctc-v4\
```

模型来自 Hugging Face（Apache-2.0）：

- <https://huggingface.co/prj-beatrice/japanese-hubert-base-phoneme-ctc-v4>

下载到项目根目录：

```bat
pip install huggingface_hub
hf download prj-beatrice/japanese-hubert-base-phoneme-ctc-v4 --local-dir models\japanese-hubert-base-phoneme-ctc-v4
```

旧版 `huggingface_hub` 的命令是：

```bat
huggingface-cli download prj-beatrice/japanese-hubert-base-phoneme-ctc-v4 --local-dir models\japanese-hubert-base-phoneme-ctc-v4
```

下载完成后目录里应有 `config.json`、`model.safetensors`、`preprocessor_config.json` 等文件（约 361MB）。

---

## 运行

```bat
python app\main.py
```

不开窗口自检（检查音库、播放器、三个面板、波形点击行为）：

```bat
python app\main.py --selftest
```

结果写在 `logs\selftest.txt`，最后一行是 `SELFTEST OK` 即通过。

---

## 打包 exe

```bat
pip install pyinstaller
build.bat
```

产物是根目录的 `ScarletPhoneme.exe`。

注意：

- exe 旁边的 `app\` 文件夹**不能删**。分析时调用的是 `app\scarlet\worker.py`，用的是系统 Python，不是 exe 内置的解释器。
- `models\`、`libraries\` 也不在 exe 里，运行时放在 exe 同级目录。

---

## 目录结构

```text
app/
  main.py                入口（--selftest 也在这里）
  scarlet/
    gui.py               主窗口 / 三个标签
    review_win.py        切点校对
    bank_win.py          音库索引
    waveview.py          波形绘制与鼠标操作
    player.py            Windows MCI 播放器
    library.py           音库读写
    importer.py          素材导入
    worker.py            外部 Python 分析入口
    core/                对齐、切片、拼接、导出、kana
build.bat                打包脚本
models/                  声学模型（不进仓库）
libraries/               音库（不进仓库，首次运行生成）
workdir/                 临时分析产物（不进仓库）
logs/                    日志（不进仓库）
```

首次运行会生成 `libraries\`、`logs\`、`settings.json`。

---

## 仓库里有什么 / 没有什么

**进仓库：**

- `app/` 源码
- `build.bat`
- `requirements.txt` / `requirements-analysis.txt`
- `说明.txt` / `开发任务.txt`
- `README.md`

**不进仓库：**

| 内容 | 原因 | 去哪拿 |
|---|---|---|
| `ScarletPhoneme.exe`、`*.exe.bak` | 约 99MiB，接近 GitHub 单文件上限 | GitHub Releases |
| `models/` | 约 361MB | 按上面的 Hugging Face 链接下载 |
| `libraries/` | 个人音源和切点数据，可能涉及版权 | 自己导入 |
| `workdir/` | 分析临时产物，可重建 | 运行时生成 |
| `logs/`、`settings.json` | 本机运行记录和绝对路径 | 运行时生成 |

---

## 使用说明

完整操作和快捷键见 [说明.txt](说明.txt)。几个关键点：

- 点波形 = 移动播放头，并选中播放头所在的音
- 拖底部假名带的竖线 = 改切点；吸附开着时左右两个音一起动
- 空格 = 播放/暂停 · Ctrl+S = 保存 · Ctrl+Z = 撤销 · PageUp/PageDown = 换曲 · Ctrl+L = 日志

---

## 已知限制

- 播放依赖 Windows MCI，只支持 wav
- CTC 对齐需要本机 Python + torch/transformers，首次加载模型约 10–30 秒
- 模型约 361MB，不随仓库分发
- 「拼句子」功能尚未实现

---

## 许可证

代码许可证待定。

第三方模型 [prj-beatrice/japanese-hubert-base-phoneme-ctc-v4](https://huggingface.co/prj-beatrice/japanese-hubert-base-phoneme-ctc-v4)
使用 **Apache-2.0**，版权归原作者所有。

音源和音库由使用者自行准备，请确认拥有相应的使用权。
