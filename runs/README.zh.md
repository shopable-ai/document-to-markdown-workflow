# 运行证据目录

`runs/real/` 保存本地真实 Workflow 运行的原始证据和输出包。运行目录默认不提交 Git，但需要保留在本机供审计、复核和恢复使用。

```text
runs/
└── real/
    └── real-runs-<批次>/
        └── <package-name>/
```

`tests/runs/` 是 fixture 或测试记录，两者不能互相替代。真实包的生产结论必须以 `tests/validate_real_output_root.py` 的结果为准；只移动父目录，不改包内 `manifest.yaml`、报告、资产路径和来源指纹。
