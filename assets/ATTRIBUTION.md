# 第三方素材来源

`assets/templates/` 中的牌面模板来自 `zl19911110/DouZero_AI_auto_play_for_HLDDZ`，固定提交为 `513dc14cff293f821c816db3b727204cddfdf88a`。其上游仓库标注 Apache-2.0，原版许可证文本保存在 `assets/templates/LICENSE`。模板包括 `images/my`、`images/play`、`images/three` 以及 `images/other` 中的 `buchu.png` 和 `three_cards_front_cover.png`。

`vendor/DouZero` 是 `kwai/DouZero` 的 Git 子模块，固定提交为 `718a5c920bf3361e34178a38f3b80458e176b351`，上游许可证为 Apache-2.0。

`src/douzero_advisor/_vendor/resnet2/models.py` 改编自 `EdwardPooh/douzero-resnet-2.0`，固定提交为 `85afd773abd01c411f543d6ade5b99a4fde327d2`，上游许可证为 GPLv3。完整来源说明和上游许可证见 `vendor/DouZero_ResNet2/`。

`models/resnet2/` 中的三个权重逐一匹配上述 GPLv3 上游提交的 `Douzero_Resnet/baseline/best/` 文件，哈希见 `models/resnet2/manifest.json`。真实游戏截图不随公开仓库提供；`config/private-assets.sha256.json` 仅保留本地安装所需的路径和哈希。
