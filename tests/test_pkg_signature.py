# -*- coding: utf-8 -*-
"""包签名（HMAC-SHA256）：build 打包附带签名 / loader 三态校验（通过、错钥拒绝、缺钥拒绝）。"""
import hashlib
import hmac
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

results = []


def check(name, cond, extra=""):
    results.append(cond)
    print(("PASS" if cond else "FAIL") + f" {name}" + (f" | {extra}" if extra and not cond else ""))


KEY = "test-signing-key-2026"
env_backup = os.environ.get("ZKG_SIGNING_KEY")
os.environ["ZKG_SIGNING_KEY"] = KEY

# ── 1. build.py 带密钥打包：索引条目带 signature 且 HMAC 正确 ──
sys.path.insert(0, os.path.join(ROOT, "repo"))
import build as repo_build

idx = repo_build.build()
entry = next(p for p in idx["packages"] if p["id"] == "store")
check("索引条目带 signature", bool(entry.get("signature")))
tar_path = os.path.join(ROOT, "repo", "pool", "store-1.0.0.tar.gz")
with open(tar_path, "rb") as f:
    expect = hmac.new(KEY.encode(), f.read(), hashlib.sha256).hexdigest()
check("signature = HMAC-SHA256(密钥, 包体)", entry["signature"] == expect)

# 还原无签名索引（供其他测试/生产默认使用）
os.environ.pop("ZKG_SIGNING_KEY")
idx_plain = repo_build.build()
check("无密钥打包不签名", not any(p.get("signature")
                                 for p in idx_plain["packages"]))

# ── 2. loader 三态校验（走下载路径：屏蔽 dev-mode） ──
from service.zkg import defaults
from service.zkg.loader import Loader


def make_signed_source(tmp: str, key: str = None, tamper: bool = False) -> str:
    """构造单包本地源（store），可选签名/篡改。sha256 始终取当前包体，
    保证各用例只考察签名语义（sha256 校验已有专门测试）。"""
    src = os.path.join(tmp, "repo_src")
    os.makedirs(os.path.join(src, "dist"))
    os.makedirs(os.path.join(src, "pool"))
    e = dict(next(p for p in idx["packages"] if p["id"] == "store"))
    with open(tar_path, "rb") as f:
        data = f.read()
    e["sha256"] = hashlib.sha256(data).hexdigest()
    if key:
        sig = hmac.new(key.encode(), data, hashlib.sha256).hexdigest()
        e["signature"] = sig[:-8] + ("0" * 8 if tamper else sig[-8:])
    else:
        e["signature"] = "deadbeef" * 8
    with open(os.path.join(src, "dist", "index.json"), "w", encoding="utf-8") as f:
        json.dump({"packages": [e]}, f)
    shutil.copy(tar_path, os.path.join(src, "pool", "store-1.0.0.tar.gz"))
    return src


def write_plugin(root, deps='["store"]'):
    d = os.path.join(root, "myapp")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "manifest.toml"), "w", encoding="utf-8") as f:
        f.write('[package]\nid = "myapp"\nname = "myapp"\n'
                'type = "plugin"\nversion = "1.0.0"\n'
                f"dependencies = {deps}\n")


def run_loader(src, plugins):
    real = defaults.PROJECT_ROOT
    defaults.PROJECT_ROOT = tempfile.mkdtemp()   # 屏蔽 dev-mode 目录
    try:
        ld = Loader(src, tempfile.mkdtemp(),
                    sources_cfg=[{"id": "local", "type": "local",
                                  "path": src, "enabled": True}],
                    scan_roots=[plugins])
        return ld.run()
    finally:
        defaults.PROJECT_ROOT = real


try:
    tmp = tempfile.mkdtemp()
    src = make_signed_source(tmp, key=KEY)
    plugins = os.path.join(tmp, "plugins")
    write_plugin(plugins)
    os.environ["ZKG_SIGNING_KEY"] = KEY
    out = run_loader(src, plugins)
    check("密钥匹配 → 加载成功", out["loaded_tools"] == ["store"])

    tmp = tempfile.mkdtemp()
    src = make_signed_source(tmp, key=KEY, tamper=True)
    plugins = os.path.join(tmp, "plugins")
    write_plugin(plugins)
    out = run_loader(src, plugins)
    check("签名被篡改 → 拒绝加载", out["loaded_tools"] == [])

    tmp = tempfile.mkdtemp()
    src = make_signed_source(tmp, key=None)   # 带坏签名
    plugins = os.path.join(tmp, "plugins")
    write_plugin(plugins)
    os.environ.pop("ZKG_SIGNING_KEY")
    out = run_loader(src, plugins)
    check("带签名但本机无密钥 → fail closed 拒绝", out["loaded_tools"] == [])

    tmp = tempfile.mkdtemp()
    src = make_signed_source(tmp, key=KEY)
    plugins = os.path.join(tmp, "plugins")
    write_plugin(plugins, '[]')   # 无依赖 → 不触发该包
    os.environ["ZKG_SIGNING_KEY"] = KEY
    out = run_loader(src, plugins)
    check("无依赖不加载（签名路径不受影响）", out["loaded_tools"] == [])
finally:
    if env_backup is not None:
        os.environ["ZKG_SIGNING_KEY"] = env_backup
    else:
        os.environ.pop("ZKG_SIGNING_KEY", None)

print(f"\n结果: {sum(results)}/{len(results)} PASS")
sys.exit(0 if all(results) else 1)
