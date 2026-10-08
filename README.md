# DouZero Advisor

DouZero Advisor 是 Windows 上的斗地主视觉建议工具。它读取公开屏幕画面，识别牌局状态，并结合 DouZero / ResNet2 模型给出建议。界面的普通自动选牌与局前按钮默认关闭；正式运行配置中的自动“不出”默认开启，会在模型明确建议 PASS 且执行门禁通过后尝试点击。识别证据不足或模型出错时会等待，不会猜测牌面。

> 这是公开候选版本。真实游戏截图不随仓库提供；在 Windows 实机回归和最终发布审查完成前，请勿把此候选仓库设为 Public。

## 架构

- `java-runtime/`：牌局状态、任务编排、结果关联和桌面事件。
- `src/douzero_advisor/recognition_service/`：窗口截图、视觉识别与稳定性判断。
- `src/douzero_advisor/model_service/`：模型加载、合法动作与推理。
- `desktop-ui/`：Electron 桌面界面。
- `contracts/`：Java 与 Python 间的版本化协议。

服务边界见 [架构说明](docs/architecture/service-boundaries.md)。代码仍包含迁移基线，不能把目录名称当作完成状态。

## 本地构建与运行

当前运行目标是 Windows。需要 Python 3.11、Java 17、Maven 和 Node.js。克隆后初始化 `vendor/DouZero` 子模块，并先安装自己持有的本地标定素材：

```powershell
git submodule update --init
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip install -r requirements\torch-cpu.txt
.\.venv\Scripts\python.exe scripts\install_private_assets.py --source <本地素材包目录>
.\.venv\Scripts\python.exe scripts\install_private_assets.py --check
cd desktop-ui
npm ci
cd ..
.\Run-DouZeroAdvisor-GUI.cmd
```

本地素材包以仓库根目录为相对路径，包含 `config/live-calibration/` 和 `tests/fixtures/core_cases/` 下的图片。安装器先按 `config/private-assets.sha256.json` 校验全部 113 张图片，再复制到 Git 忽略的本地目录；缺少或不匹配时会报错。素材包只保留在用户本机，不从网络下载。

桌面入口会构建并打开 Electron；Java 与 Python 服务在界面内按本次配置启动。也可单独运行 `Run-DouZeroRecognitionService.cmd`、`Run-DouZeroModelService.cmd` 和 `Run-DouZeroRuntime.cmd`。模型服务会校验 `models/resnet2/manifest.json` 中的权重；识别服务会使用 `tests/fixtures/live_capture_manifest.json` 中的标定素材。

局前建议默认关闭。需要启用时，先把 `DOUZERO_PREPLAY_LEGACY_ROOT` 设置为自己持有的 FullAuto 模型目录，再在界面中开启局前建议；启用而未设置目录会明确报错。正式出牌建议无需此目录。普通自动选牌与局前按钮也只能在界面中按本次启动显式开启；自动“不出”的默认值见 `java-runtime/src/main/resources/application.yml`。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests
mvn.cmd -pl java-runtime test
cd desktop-ui
npm test
npm run build
```

单元测试和离线回放不等于真实 Windows 牌局验收。真实窗口捕获、不同角色识别与建议结果应分别核验。

默认 Python 测试会跳过需要真实截图的回归。安装本地素材包后，运行 `.\.venv\Scripts\python.exe -m pytest tests --with-private-assets` 执行这些回归。额外的个人回放可放在 `private-assets/`，或用 `DOUZERO_PRIVATE_ASSET_ROOT` 指定其父目录；该目录已被 Git 忽略。

## 来源与发布边界

- `vendor/DouZero` 是上游 Apache-2.0 子模块。
- `assets/templates/` 来自第三方 Apache-2.0 项目，见 [素材来源](assets/ATTRIBUTION.md)。
- `src/douzero_advisor/_vendor/resnet2/models.py` 改编自 GPLv3 项目，见 [上游记录](vendor/DouZero_ResNet2/UPSTREAM.md)。
- 本项目自身代码按仓库根目录的 GPLv3 许可证发布；模型代码和权重保留 GPLv3 上游来源，Apache-2.0 子模块与模板保留各自的许可证和署名。
- 真实游戏截图不包含在仓库中；公开图片只有合成测试图和已署名的第三方模板。
