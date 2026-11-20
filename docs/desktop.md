# 桌面版

桌面版把本地 Web 服务嵌入原生窗口。用户双击应用即可上传 PDF、查看进度并打开独立报告，不需要先启动终端或浏览器。

## 运行方式

1. 启动时仅监听 `127.0.0.1` 的随机端口。
2. 页面由本机 Flask 服务和系统 WebView 打开。
3. 上传文件、任务状态、日志和报告保存在本机用户数据目录。
4. 关闭窗口后，本地服务随应用退出。
5. 应用不包含自动更新器，也不会主动访问网络。

比对完成后会显示应用内结果页，可返回上传页或开始下一次比对。任务记录会持久化在本机，重新打开桌面应用后仍可从“比对记录”进入原结果、下载 ZIP 或查看失败原因。

## 数据目录

macOS：

```text
~/Library/Application Support/ElectronicSchematicPdfDiff/
```

Windows：

```text
%LOCALAPPDATA%\ElectronicSchematicPdfDiff\
```

可通过 `SCHEMADIFF_DESKTOP_DATA` 覆盖。

## Windows

发布包包含单个 `ElectronicSchematicPdfDiff.exe`。

- 支持 Windows 10/11 x64。
- 使用 Microsoft Edge WebView2 显示界面。Windows 11 通常已内置；缺少时系统会显示启动错误。
- 未配置商业代码签名证书时，SmartScreen 可能显示“未知发布者”。

## macOS

发布包包含 `.app`，并提供 Intel 与 Apple Silicon 两个架构版本。

- 最低目标版本为 macOS 12。
- 未配置 Apple Developer ID 和公证时，首次启动可能需要在 Finder 中右键选择“打开”。
- 不要在两个架构之间混用构建产物。

## OCR

矢量 PDF 的文字比较不需要 Tesseract。扫描 PDF 的 OCR 仍使用本机 Tesseract；没有安装时，视觉比较继续工作，但不会补充 OCR 文字。

## 本地构建

```bash
python3.12 -m venv .venv
uv pip install --python .venv/bin/python -r requirements-dev.lock
uv pip install --python .venv/bin/python -e ".[desktop,build]"
.venv/bin/python scripts/build_desktop.py
```

Windows 将 `.venv/bin/python` 替换为 `.venv\Scripts\python.exe`。

构建结果位于：

```text
release/
```

## GitHub Actions

`.github/workflows/desktop-build.yml` 会生成：

- Windows x64 ZIP。
- macOS Intel ZIP。
- macOS Apple Silicon ZIP。

推送 `v*` 标签时，三个平台包会自动附加到 GitHub Release。

发布包未签名时需要说明系统警告。正式对外分发前，应配置 Windows Authenticode 和 Apple Developer ID/公证凭据。
