"""validate —— 声明式参数校验（机制包）。

框架提供的一等公民程序化接口：命令/HTTP 入口参数校验的标配，
一次 check 声明全部约束，拿回清洗后的数据。

稳定 API 表面：
    check(data, schema) -> dict          校验+类型收敛+默认值填充，失败抛 ValidationError
    ValidationError                      .errors = [(field, msg), ...]

schema 字段规则（每字段一个 dict，均可选）：
    type:      str|int|float|bool|list|dict   （提供则做类型收敛：int("3")→3）
    required:  bool                           缺失且无 default → 报错
    default:   Any                            缺失时填充
    min/max:   数值范围或 str/list 长度范围
    choices:   合法值枚举
    strip:     bool                           str 先去首尾空白（默认 True）

设计约定：
- 返回的 dict 只含 schema 声明过的字段（多余输入一律丢弃，防脏数据入库）；
- str→int/float 自动收敛（"3"→3），收敛失败记 errors 而非抛类型异常。
"""
from __future__ import annotations

_TYPES = (str, int, float, bool, list, dict)


class ValidationError(Exception):
    """校验失败。.errors 为 [(field, msg), ...]。"""

    def __init__(self, errors):
        self.errors = list(errors)
        detail = "; ".join(f"{f}: {m}" for f, m in self.errors)
        super().__init__(f"参数校验失败 — {detail}")


def check(data: dict, schema: dict) -> dict:
    """按 schema 校验并清洗 data，返回仅含声明字段的 dict。"""
    data = data if isinstance(data, dict) else {}
    out, errors = {}, []
    for field, rule in schema.items():
        rule = rule or {}
        val = data.get(field)
        # 缺失处理：默认值 / 必填
        if val is None or val == "":
            if "default" in rule:
                out[field] = rule["default"]
            elif rule.get("required"):
                errors.append((field, "必填"))
            continue
        # 类型收敛
        want = rule.get("type")
        if want is not None:
            if want is bool:
                if isinstance(val, str):
                    low = val.strip().lower()
                    if low in ("true", "1", "yes", "on"):
                        val = True
                    elif low in ("false", "0", "no", "off"):
                        val = False
                    else:
                        errors.append((field, f"无法解析为 bool: {val!r}"))
                        continue
                elif not isinstance(val, bool):
                    val = bool(val)
            elif want in (int, float):
                try:
                    val = want(val)
                except (TypeError, ValueError):
                    errors.append((field, f"无法解析为 {want.__name__}: {val!r}"))
                    continue
            elif want is str:
                val = str(val)
            elif not isinstance(val, want):
                errors.append(
                    (field, f"类型应为 {want.__name__}，实为 {type(val).__name__}"))
                continue
        # str 清洗
        if isinstance(val, str) and rule.get("strip", True):
            val = val.strip()
        # 范围：数值比大小，str/list/dict 比长度
        try:
            if rule.get("min") is not None and val < rule["min"]:
                errors.append((field, f"不能小于 {rule['min']}"))
                continue
            if rule.get("max") is not None and val > rule["max"]:
                errors.append((field, f"不能大于 {rule['max']}"))
                continue
        except TypeError:
            if rule.get("min") is not None and len(val) < rule["min"]:
                errors.append((field, f"长度不能小于 {rule['min']}"))
                continue
            if rule.get("max") is not None and len(val) > rule["max"]:
                errors.append((field, f"长度不能大于 {rule['max']}"))
                continue
        # 枚举
        if "choices" in rule and val not in rule["choices"]:
            errors.append((field, f"必须是 {'/'.join(map(str, rule['choices']))}"))
            continue
        out[field] = val
    if errors:
        raise ValidationError(errors)
    return out
