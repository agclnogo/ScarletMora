# ScarletMora

音素裁切音库管理器 —— 素材 → 强制对齐 → 切点校对 → モーラ切片 → 拼接。

Windows 桌面程序，Python + Tkinter 编写，单窗口，四个主标签：
**切点校对** / **音库索引** / **人力试作** / **音库管理**。

当前版本：**1.1.1**（切点校对 / 音库索引 / 人力试作 / 音库管理 四个主功能）

「人力试作」（旧名「拼句子」）已返工验收，**1.1.0 起正式纳入**。

**1.1.1** 修的是用户版第一次就撞墙的问题：发行包以前不含声学模型，解压后点
「分析」只会弹「找不到声学模型」。现在 `models\` 直接打进 zip，解压即用；
模型被删掉或放在别处时，才会弹出选择窗口（选过的路径记进 `settings.json`）。

- 仓库：<https://github.com/agclnogo/ScarletMora>
- 用户版下载：<https://github.com/agclnogo/ScarletMora/releases/latest>
  （`ScarletMora-1.1.1-win64.zip`，约 470MB，解压即用）
- 用户版里已经带好声学模型（`models\`，361MB）和运行用的 `app\` 文件夹，两个都别删；
  另外还需要本机装 Python + torch 才能跑分析，见下方「运行环境」

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
- 切点表和当前音显示成 `な(na)` 这样的「假名 + 罗马音」
- 切点表点击定位，未保存换曲会提示保存/放弃/取消

### 音库索引
- 按音/母音浏览候选，显示成 `な(na)`（假名 + 罗马音），可搜假名或 `na`；
  可按「只看可疑」「只看句头」筛选，并显示干净候选数量
- 候选表双击/空格试听
- 波形预览：红框是候选切出来的范围，点哪从哪试听
- 导出的 index.html 里也能按假名 / 罗马音筛选

### 人力试作（旧名「拼句子」，1.1.0 正式纳入）
> 第四个标签。缩放/平移、轨道顺序、音块编辑、播放头、总波形、Solo/Mute
> 等返工内容已完成并验收，**从 1.1.0 起随正式版发布**。

- 左边搜索音/母音，显示成 `な(na)` 这样的「假名 + 罗马音」；可直接搜 `na`
- 中间候选表（左上「＋ 加到本轨」），右边上方是来源波形小预览
- 时间轴从上往下 = 最新句 → 第一句；新轨插到最上面，空轨也保留可见
- 时间轴左上「＋ 新轨 / 删轨 / 吸附」；上下分隔条可拖动，把时间轴上沿往上拉
- 加音只有两种：点「＋ 加到本轨」，或把候选拖到某条轨道上；双击不再加音
- 单击候选 = 先试听该候选；拖动时试听自动取消
- 拖音块中间 = 移动（吸附关（默认）= 自由水平移动/跨轨，吸附开 = 排序/插入）
- 拖音块左右边缘 = 裁剪来源的 start/end；Delete 删除；Ctrl+Z/Ctrl+Y 撤销重做
- 点时间轴空白 / 拖标尺或播放头 = 定位；播放从播放头开始
- 点音块 = 选中并试听该音块（来源片段）
- 时间刻度（0.00 / 0.10…）固定在时间轴顶部，总波形固定在底部，滚轮只滚动轨道
- 总波形 = 所有可听轨道的 mix；编辑后延迟刷新，播放头对齐
- 每条轨道有 S（Solo）/ M（Mute）；试听、总波形、导出都遵守
- 缩放：Ctrl+滚轮（以鼠标为中心），没有缩放滑块；滚轮上下滚动轨道
- 平移：底部滑块（只有滑块和右侧范围）；缩小到内容很小时滑块也能移动
- ▶ 试听：按当前编辑渲染临时 wav 播放，播完可重播
- 「从歌词自动填充」= 弹出小窗口直接输入歌词（一行一句），不用再选 txt；
  **只支持假名 / 罗马音**（汉字先换成假名），格式例 `a i shi te ru / あいしてる`，
  也可写 `aishiteru`；右上实时显示「行数 · 音数」，不支持的字符会在左下标出并拒绝填充；
  「填入示例」可以一键插入示例行，上次填过的歌词会留在输入框里
- 填充后还可手工微调；会替换当前轨道，可 Ctrl+Z 撤销
- 自动保存到 `libraries/<音库>/edit.json`（schema 2，含 at / solo / mute），
  Ctrl+Z/Ctrl+Y 撤销重做
- 导出 `render/*.wav` + `timeline.csv`，参数与「拼接成歌」一致；
  Solo/Mute 会反映到导出结果

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

**用户版不用管这一步** —— `ScarletMora-1.1.1-win64.zip` 里已经把模型放在

```text
models\japanese-hubert-base-phoneme-ctc-v4\
```

了（解压后别删）。下面是从源码跑、或者模型被删掉时怎么补。

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

**不放 `models\` 也可以**：点「分析」时如果找不到模型，会弹窗让你选模型文件夹
（选模型本体、`models\`、甚至上一级都行，会自动往下找
`japanese-hubert-base-phoneme-ctc-v4`）。选过的路径会记进 `settings.json` 的
`"model"`，下次不再问。也可以手写：

```json
{ "model": "D:\\somewhere\\japanese-hubert-base-phoneme-ctc-v4" }
```

判断「能用」的条件是这三样齐了：`config.json` / `vocab.json` / 权重
（`model.safetensors` 或 `pytorch_model.bin`）。文件夹空的、下载到一半的都会
在弹窗里写明缺什么。

---

## 运行

```bat
python app\main.py
```

不开窗口自检（检查音库、播放器、四个面板、波形点击行为、人力试作的缩放/裁剪/
试听/导出）：

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

产物是根目录的 `ScarletMora.exe`。

注意：

- exe 旁边的 `app\` 文件夹**不能删**。分析时调用的是 `app\scarlet\worker.py`，用的是系统 Python，不是 exe 内置的解释器。
- `models\`、`libraries\` 也不在 exe 里，运行时放在 exe 同级目录。

---

## 目录结构

```text
app/
  main.py                入口（--selftest 也在这里）
  scarlet/
    gui.py               主窗口 / 四个标签
    review_win.py        切点校对
    bank_win.py          音库索引
    assemble_win.py      人力试作（时间轴 / 试听 / 拖放）
    waveview.py          波形绘制与鼠标操作
    player.py            Windows MCI 播放器
    library.py           音库读写
    importer.py          素材导入
    worker.py            外部 Python 分析入口
    core/                对齐、切片、拼接、导出、kana
                          （人力试作用 assemble.render_items / render_edit）
build.bat                打包脚本（exe → 自检 → 打用户版 zip）
make_release.py          打用户版 zip（exe + app\ + models\ + 说明，模型一起带上）
models/                  声学模型（不进仓库；打用户版 zip 时会打进去）
libraries/               音库（不进仓库，首次运行生成）
workdir/                 临时分析产物（不进仓库）
logs/                    日志（不进仓库）
```

首次运行会生成 `libraries\`、`logs\`、`settings.json`。
人力试作的轨道存在 `libraries\<音库>\edit.json`。

---

## 仓库里有什么 / 没有什么

**进仓库：**

- `app/` 源码
- `build.bat` / `make_release.py`
- `requirements.txt` / `requirements-analysis.txt`
- `说明.txt` / `开发任务.txt`
- `README.md`

**不进仓库：**

| 内容 | 原因 | 去哪拿 |
|---|---|---|
| `ScarletMora.exe`、`*.exe.bak` | 约 99MiB，接近 GitHub 单文件上限 | GitHub Releases（发行包 `ScarletMora-1.1.1-win64.zip`） |
| `models/` | 约 361MB，二进制权重 | **已打进发行包**（解压即用）；源码开发另按上面的 Hugging Face 链接下载 |
| `libraries/` | 个人音源和切点数据，可能涉及版权 | 自己导入 |
| `workdir/` | 分析临时产物，可重建 | 运行时生成 |
| `logs/`、`settings.json` | 本机运行记录和绝对路径 | 运行时生成 |

---

## 使用说明

完整操作和快捷键见 [说明.txt](说明.txt)。几个关键点：

- 点波形 = 移动播放头，并选中播放头所在的音
- 拖底部假名带的竖线 = 改切点；吸附开着时左右两个音一起动
- 人力试作：单击候选试听；「＋ 加到本轨」或拖入轨道加音；空格试听 mix，Ctrl+S 保存
- 空格 = 播放/暂停 · Ctrl+S = 保存 · Ctrl+Z = 撤销 · Ctrl+Y = 重做 · PageUp/PageDown = 换轨/换曲 · Ctrl+L = 日志

---

## 已知限制

- 播放依赖 Windows MCI，只支持 wav
- CTC 对齐需要本机 Python + torch/transformers，首次加载模型约 10–30 秒
- 模型约 361MB，不进源码仓库；用户版 zip 里已内置
- 人力试作导出的淡入淡出/交叉淡化/响度参数暂固定为与「拼接成歌」相同

---

## 许可证

代码许可证待定。

第三方模型 [prj-beatrice/japanese-hubert-base-phoneme-ctc-v4](https://huggingface.co/prj-beatrice/japanese-hubert-base-phoneme-ctc-v4)
使用 **Apache-2.0**，版权归原作者所有。

音源和音库由使用者自行准备，请确认拥有相应的使用权。
