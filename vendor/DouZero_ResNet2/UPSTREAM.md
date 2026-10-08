# DouZero ResNet 2.0 vendored inference model

- Repository: https://github.com/EdwardPooh/douzero-resnet-2.0
- Pinned commit: `85afd773abd01c411f543d6ade5b99a4fde327d2`
- Upstream file: `Douzero_Resnet/douzero/dmc/models_res.py`
- Packaged code: `src/douzero_advisor/_vendor/resnet2/models.py`
- Local scope: only `BasicBlock` and `ResnetModel`, required for card-play inference
- License: GNU GPL v3; the unmodified upstream license is retained in `LICENSE`

This directory archives the upstream license and provenance. Local code changes
are limited to formatting, type annotations, removal of unrelated models and
training wrappers, and a fail-closed exploration branch. Module and parameter
names used by the published checkpoints are unchanged.
