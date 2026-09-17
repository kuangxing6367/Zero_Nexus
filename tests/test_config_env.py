# -*- coding: utf-8 -*-
"""配置环境变量展开：config.yaml ${VAR} 语法 + extensions.yaml 合并路径 + 回写不落明文。"""
import io
import os
import sys
import tempfile

sys.path.insert(0, "E:/工程/zernus")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from core.config import _env_replace, load_config

results = []


def check(name, cond, extra=""):
    results.append(cond)
    print(("PASS" if cond else "FAIL") + f" {name}" + (f" | {extra}" if extra and not cond else ""))


# ── _env_replace 基础语义 ──
os.environ["ZN_TEST_TOKEN"] = "abc123"
check("标量替换", _env_replace("${ZN_TEST_TOKEN}") == "abc123")
check("默认值语法（变量存在）", _env_replace("x${ZN_TEST_TOKEN:-zzz}y") == "xabc123y")
check("默认值语法（变量缺失）",
      _env_replace("${ZN_TEST_NOPE:-fallback}") == "fallback")
check("变量缺失且无默认 → 原样保留", _env_replace("${ZN_TEST_NOPE}") == "${ZN_TEST_NOPE}")
check("dict/list 递归",
      _env_replace({"a": ["${ZN_TEST_TOKEN}", 1], "b": {"c": "${ZN_TEST_NOPE:-d}"}})
      == {"a": ["abc123", 1], "b": {"c": "d"}})
check("非字符串不动", _env_replace(42) == 42 and _env_replace(None) is None)

# ── load_config：主 config 段展开 ──
tmp = tempfile.mkdtemp()
cfg_path = os.path.join(tmp, "config.yaml")
with open(cfg_path, "w", encoding="utf-8") as f:
    f.write("security:\n  token: ${ZN_TEST_TOKEN}\nstatus_panel:\n  enabled: false\n")

# 隔离 extensions.yaml：临时目录里造一份，避免动真仓库文件
import core.config as cfgmod
real_yaml = cfgmod.EXTENSIONS_YAML
test_yaml = os.path.join(tmp, "extensions.yaml")
with open(test_yaml, "w", encoding="utf-8") as f:
    f.write("extensions:\n  webui:\n    enabled: false\n"
            "  http_inject:\n    enabled: false\n"
            "    token: ${ZN_TEST_TOKEN:-fallback}\n")
cfgmod.EXTENSIONS_YAML = test_yaml
try:
    config = load_config(cfg_path)
    check("主 config 展开生效", config["security"]["token"] == "abc123")
    check("extensions.yaml 合并路径也展开",
          config["http_inject"]["token"] == "abc123",
          f"got={config['http_inject'].get('token')!r}")
    check("回写不落明文（yaml 里保留 ${VAR} 引用）",
          "${ZN_TEST_TOKEN:-fallback}" in open(test_yaml, encoding="utf-8").read()
          or "${ZN_TEST_TOKEN}" in open(test_yaml, encoding="utf-8").read())
finally:
    cfgmod.EXTENSIONS_YAML = real_yaml

print(f"\n结果: {sum(results)}/{len(results)} PASS")
sys.exit(0 if all(results) else 1)
