# -*- coding: utf-8 -*-
"""image_renderer 背景图 + 模糊冒烟：bg_image/bg_blur/bg_dim 与 Canvas paste→blur。"""
import os
import sys
import io as _io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("SKIP - Pillow 未安装（可选依赖）")
    sys.exit(0)

import importlib
ir = importlib.import_module("software.extensions.image_renderer.main")

PASS = []


def check(name, cond):
    PASS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL") + " - " + name)


def _pattern_png():
    """随机噪声测试图（线性渐变是高斯模糊的不动点，测不出模糊效果）"""
    import random
    rnd = random.Random(42)
    data = bytes(rnd.randrange(256) for _ in range(300 * 200 * 3)) + b"\xff" * (300 * 200)
    img = Image.frombytes("RGBA", (300, 200), data)
    buf = _io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _global_diff(img_a, img_b):
    """全图平均绝对差（0-255）"""
    a = img_a.convert("RGB").resize((64, 64))
    b = img_b.convert("RGB").resize((64, 64))
    pa, pb = list(a.getdata()), list(b.getdata())
    total = sum(abs(x[c] - y[c]) for x, y in zip(pa, pb) for c in range(3))
    return total / (64 * 64 * 3)


def main():
    png = _pattern_png()

    # 卡片：无模糊 vs 模糊
    opt0 = {'bg_image': png, 'title': '', 'content': 'x'}
    c0 = ir._render_card_image("标题", "第一行\n第二行", 600, 30, dict(opt0))
    c1 = ir._render_card_image("标题", "第一行\n第二行", 600, 30,
                               dict(opt0, bg_blur=8))
    check("bg_image 渲染出图", c0 is not None and c1 is not None)
    i0 = c0 if isinstance(c0, Image.Image) else Image.open(_io.BytesIO(c0))
    i1 = c1 if isinstance(c1, Image.Image) else Image.open(_io.BytesIO(c1))
    check("尺寸一致", i0.size == i1.size)
    d01 = _global_diff(i0, i1)
    check(f"模糊确实生效（全图均差 {d01:.1f}）", d01 > 3)

    # bg_dim 压暗
    c2 = ir._render_card_image("标题", "第一行", 600, 30,
                               dict(opt0, bg_blur=8, bg_dim=120))
    i2 = c2 if isinstance(c2, Image.Image) else Image.open(_io.BytesIO(c2))
    l1 = i1.convert("L").resize((1, 1)).getpixel((0, 0))
    l2 = i2.convert("L").resize((1, 1)).getpixel((0, 0))
    check(f"bg_dim 压暗（{l1} -> {l2}）", l2 < l1)

    # 文字 / 榜单渲染器同链路不炸
    t1 = ir._render_text_image("你好世界", 500, 20, {'bg_image': png, 'bg_blur': 4})
    check("text 渲染器支持 bg_image", t1 is not None)
    l1 = ir._render_list_image("榜单", [{"name": "a", "value": "1", "rank": "1"},
                                        {"name": "b", "value": "2", "rank": "2"}],
                               600, 30, {'bg_image': png, 'bg_blur': 4})
    check("list 渲染器支持 bg_image", l1 is not None)

    # 非法 bg_image 不炸（回退默认背景）
    ok = True
    try:
        bad = ir._render_card_image("标题", "内容", 600, 30, {'bg_image': b"notpng"})
    except Exception:
        ok = False
    check("非法背景图回退不炸", ok)

    # Canvas 手工链路：paste → blur → text（无字体文件时跳过）
    font = ir._find_font_path()
    if font:
        canvas = ir._get_native_or_pil_canvas(400, 300, (20, 20, 30, 255), font)
        canvas.paste(png, 0, 0, 400, 300)
        canvas.blur(6)
        canvas.text(20, 40, "模糊背景上的文字", 24, (255, 255, 255, 255))
        out = canvas.to_png()
        check("Canvas paste→blur→text 链路可用", len(out) > 1000)
    else:
        print("SKIP - Canvas 链路（沙箱无字体文件）")

    failed = [n for n, ok in PASS if not ok]
    print(f"\n结果: {len(PASS) - len(failed)}/{len(PASS)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
