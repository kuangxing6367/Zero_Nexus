# -*- coding: utf-8 -*-
"""
密码哈希工具（stdlib hashlib，零第三方依赖）

哈希格式：pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>

对遗留 bcrypt 哈希（$2a/$2b/$2y）做惰性兼容：仅当遇到旧哈希且 bcrypt
已安装时才用它验证；新建 / 改密一律使用 pbkdf2。这样内核与 Web 扩展
默认都不依赖 bcrypt，遗留账号也能平滑过渡。
"""
import hashlib
import hmac
import os

_ITER = 200_000
_PREFIX = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    """生成 pbkdf2_sha256 哈希串"""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, _ITER)
    return f"{_PREFIX}${_ITER}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """验证密码。支持 pbkdf2 与遗留 bcrypt 哈希。"""
    if not stored:
        return False
    if stored.startswith('pbkdf2_sha256$'):
        try:
            _, iter_s, salt_hex, hash_hex = stored.split('$', 3)
            dk = hashlib.pbkdf2_hmac(
                'sha256', password.encode('utf-8'),
                bytes.fromhex(salt_hex), int(iter_s))
            return hmac.compare_digest(dk.hex(), hash_hex)
        except Exception:
            return False
    # 遗留 bcrypt 哈希兼容性（可选依赖）
    if stored.startswith('$2'):
        try:
            import bcrypt
            if isinstance(stored, str):
                stored = stored.encode('utf-8')
            if isinstance(password, str):
                password = password.encode('utf-8')
            return bcrypt.checkpw(password, stored)
        except Exception:
            return False
    return False


def needs_upgrade(stored: str) -> bool:
    """是否为遗留哈希（下次成功登录时升级为 pbkdf2）。"""
    return bool(stored) and not stored.startswith('pbkdf2_sha256$')
