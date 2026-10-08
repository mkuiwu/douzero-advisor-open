# 实时识别标定素材

`tests/fixtures/live_capture_manifest.json` 引用本目录的本地截图作为当前识别服务的启动素材。真实游戏截图不随公开仓库提供。使用 `scripts/install_private_assets.py --source <本地素材包目录>` 安装自己持有的素材；脚本会依据 `config/private-assets.sha256.json` 校验 SHA-256。缺图或校验失败时不得用猜测结果启动识别。
