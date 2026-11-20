# 电子原理图 PDF 差异扫描

离线比较两版原理图 PDF，并在旧页、新页和局部特写中定位差异。

工具面向矢量 PDF、扫描 PDF 和多页原理图，重点解决：

- 页面、文字、连线、符号和注释的变化定位。
- 元件位号、元件值和网络标号等语义信息的关联。
- 同页换位置和跨页搬运的局部电路识别。
- 两份文件不属于同一套原理图时的误传拦截。
- 不联网、不调用云 OCR、在线 API、CDN 或自动更新。

## 主要能力

| 能力 | 说明 |
| --- | --- |
| 离线运行 | 比对期间阻断 Python 外发套接字；报告不加载外部资源 |
| 页面配对 | 文字指纹、墨迹缩略图和保序动态规划，允许插入或删除页面 |
| 文档预检 | 匹配覆盖率和相似度过低时直接拒绝，弱匹配显示人工复核提示 |
| 文字差异 | PDF 文字层优先，扫描页可使用本机 Tesseract OCR |
| 视觉差异 | 阈值化像素差、细线保留和变化区域分组 |
| 语义融合 | 关联位号、元件值、网络、注释和局部几何证据 |
| 移动块识别 | 识别同页大范围换位和跨页局部电路搬运 |
| 独立报告 | CSS、JavaScript、数据和高清证据图全部内嵌到单个 HTML |
| 鼠标交互 | 滚轮围绕光标缩放、拖动平移、双击复位 |

更多说明：

- [通用架构](docs/architecture.md)
- [中文架构概览](docs/zh-safe/架构概览.md)
- [中文实现原理](docs/zh-safe/实现原理.md)
- [仓库隐私规则](docs/PRIVACY.md)
- [第三方依赖说明](THIRD_PARTY_NOTICES.md)

## 环境要求

- Python 3.12+
- 可选：Tesseract OCR，用于无文字层扫描页

安装开发环境：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/pip install -r requirements-dev.lock
.venv/bin/pip install -e .
```

## 命令行

```bash
./run-schemadiff OLD.pdf NEW.pdf -o output/comparison
```

或：

```bash
.venv/bin/schemadiff OLD.pdf NEW.pdf -o output/comparison
```

常用参数：

```text
--dpi 180
--display-dpi 110
--alignment auto|translation|affine|off
--ocr auto|always|never
--ocr-language eng|eng+chi_sim
--visual-threshold 20
--verbose
```

退出码：

| 退出码 | 含义 |
| --- | --- |
| `0` | 比对完成 |
| `1` | 运行失败 |
| `2` | 参数错误 |
| `3` | 两份 PDF 疑似不是同一套原理图，已拦截 |

## 本机 Web

```bash
./scripts/start_web.sh
```

默认访问：

```text
http://127.0.0.1:8899
```

可以通过环境变量修改端口、前缀和输出目录：

```bash
SCHEMADIFF_WEB_PORT=8899 \
SCHEMADIFF_WEB_PREFIX=/schematic-diff \
SCHEMADIFF_WEB_OUTPUT=/path/to/output \
./scripts/start_web.sh
```

停止服务：

```bash
./scripts/stop_web.sh
```

## 输出

```text
comparison/
  report.html
  results.json
  changes.pdf
  assets/
  logs/events.jsonl
  run_manifest.json
```

`report.html` 是可独立分发的单文件，不依赖同目录中的图片、JSON 或 Web 服务。

## 文档预检

逐页差异之前会先检查两份文件是否属于同一套原理图：

- 无可靠匹配页：直接拒绝。
- 可靠匹配覆盖率低于 50%：直接拒绝。
- 匹配页相似度中位数低于 55%：直接拒绝。
- 弱匹配但未达到拒绝条件：继续比对，并在报告中显示人工复核警告。

预检结果写入 `run_manifest.json` 的 `document_compatibility` 字段。

## 测试

```bash
.venv/bin/pytest
```

私有项目 PDF、变更清单、生成报告和截图均不随仓库分发。需要本地验收数据时，请将其放在仓库之外，或使用仓库忽略规则覆盖的工作目录中。

## 目录

```text
src/schemadiff/       核心比较引擎与 Web 服务
src/schemadiff/web/   上传页和独立报告模板
tests/                单元、同步合成、扫描件和 Web 回归测试
docs/                 通用架构与研究说明
docs/zh-safe/         不含项目数据的中文说明
scripts/              启动与通用验收辅助脚本
```

## 隐私与仓库卫生

仓库默认忽略：

- `*.pdf`、Excel、Word、PowerPoint 等可能包含项目资料的文档。
- `output/`、`tmp/`、日志、运行清单和证据图。
- 本地基准映射、真实项目测试和内部报告源文件。
- `.env`、虚拟环境和系统缓存。

提交前仍应人工检查是否混入客户名称、位号、网络、原理图截图或访问凭据。

## 范围

工具用于发现版本差异并辅助人工审阅，不替代电气设计验证、网表比较或 EDA 原生工程分析。
