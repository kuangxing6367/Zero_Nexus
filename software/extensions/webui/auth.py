# -*- coding: utf-8 -*-
"""
认证接口：登录 / 登出 / 当前用户 / 改密 + 通讯安全校验
"""
import logging

import secrets
from flask import jsonify, request

from .password import hash_password, verify_password, needs_upgrade

logger = logging.getLogger('zernus')


def register(ctx):
    app = ctx.app
    db = ctx.db
    auth_sys = ctx.auth_sys
    require_auth = ctx.require_auth
    require_super = ctx.require_super
    get_client_ip = ctx.get_client_ip
    audit_log = ctx.audit_log
    _sync_token_cookie = ctx._sync_token_cookie
    _check_login_rate = ctx._check_login_rate
    _record_login_failure = ctx._record_login_failure
    _clear_login_failures = ctx._clear_login_failures

    # ---- 认证接口 ----

    @app.route('/api/login', methods=['POST'])
    def login():
        """管理员登录"""
        client_ip = get_client_ip()

        # 登录限速
        if not _check_login_rate(client_ip):
            return jsonify({'code': 429, 'msg': '尝试过于频繁，请 10 分钟后再试'}), 429

        data = request.get_json(silent=True) or {}
        username = data.get('username', '').strip()
        password = data.get('password', '')

        if not username or not password:
            return jsonify({'code': 400, 'msg': '用户名和密码不能为空'}), 400

        try:
            row = db.query_one(
                "SELECT id, username, password_hash, role, is_active FROM admin_users WHERE username = %s",
                (username,)
            )
        except Exception as e:
            logger.error(f"登录查询失败: {e}")
            return jsonify({'code': 500, 'msg': f'数据库错误: {e}'}), 500

        if not row:
            _record_login_failure(client_ip)
            audit_log(None, username, 'login', result='failure', error_message='用户不存在')
            return jsonify({'code': 401, 'msg': '用户名或密码错误'}), 401

        if not row['is_active']:
            _record_login_failure(client_ip)
            audit_log(row['id'], username, 'login', result='failure', error_message='账号已禁用')
            return jsonify({'code': 403, 'msg': '账号已禁用'}), 403

        # 验证密码
        try:
            if not verify_password(password, row['password_hash']):
                _record_login_failure(client_ip)
                audit_log(row['id'], username, 'login', result='failure', error_message='密码错误')
                return jsonify({'code': 401, 'msg': '用户名或密码错误'}), 401
        except Exception as e:
            logger.error(f"密码验证异常: {e}")
            return jsonify({'code': 500, 'msg': '密码验证失败'}), 500

        # 登录成功，清除失败计数
        _clear_login_failures(client_ip)

        # 遗留 bcrypt 哈希在成功登录后就地升级为 pbkdf2（可选依赖平滑退场）
        try:
            if needs_upgrade(row['password_hash']):
                db.execute(
                    "UPDATE admin_users SET password_hash = %s WHERE id = %s",
                    (hash_password(password), row['id'])
                )
        except Exception as e:
            logger.warning(f"密码哈希升级失败（忽略）: {e}")

        # 生成 2048 位随机 token
        token = secrets.token_hex(1024)  # 2048 字符
        db.execute(
            "UPDATE admin_users SET token = %s, token_created_at = NOW(), last_login_at = NOW(), last_login_ip = %s WHERE id = %s",
            (token, get_client_ip(), row['id'])
        )
        audit_log(row['id'], username, 'login', result='success')

        resp = jsonify({
            'code': 0,
            'msg': '登录成功',
            'data': {'token': token, 'username': username, 'role': row['role']}
        })
        _sync_token_cookie(resp, token)
        return resp

    @app.route('/api/logout', methods=['POST'])
    @require_auth
    def logout():
        """退出登录"""
        admin = request.admin
        try:
            db.execute(
                "UPDATE admin_users SET token = NULL, token_created_at = NULL WHERE id = %s",
                (admin['id'],)
            )
        except Exception as e:
            logger.error(f"清除 token 失败: {e}")
        audit_log(admin['id'], admin['username'], 'logout')
        resp = jsonify({'code': 0, 'msg': '已退出'})
        _sync_token_cookie(resp, '')
        return resp

    @app.route('/api/me', methods=['GET'])
    @require_auth
    def me():
        """获取当前登录信息"""
        return jsonify({'code': 0, 'data': request.admin})

    @app.route('/api/change_password', methods=['POST'])
    @require_auth
    def change_password():
        """修改密码"""
        data = request.get_json(silent=True) or {}
        old_pwd = data.get('old_password', '')
        new_pwd = data.get('new_password', '')

        if not old_pwd or not new_pwd:
            return jsonify({'code': 400, 'msg': '旧密码和新密码不能为空'}), 400
        if len(new_pwd) < 6:
            return jsonify({'code': 400, 'msg': '新密码至少6位'}), 400

        admin = request.admin
        row = db.query_one("SELECT password_hash FROM admin_users WHERE id = %s", (admin['id'],))

        try:
            if not verify_password(old_pwd, row['password_hash']):
                return jsonify({'code': 401, 'msg': '旧密码错误'}), 401
        except Exception:
            return jsonify({'code': 500, 'msg': '密码验证失败'}), 500

        new_hash = hash_password(new_pwd)
        db.execute("UPDATE admin_users SET password_hash = %s WHERE id = %s", (new_hash, admin['id']))
        audit_log(admin['id'], admin['username'], 'change_password', result='success')

        return jsonify({'code': 0, 'msg': '密码已修改'})

    # ---- 通讯安全校验 ----

    @app.route('/api/auth', methods=['POST'])
    def request_auth():
        """通讯安全校验入口：按 config.security 走 Token 校验或 RSA 回调。"""
        client_ip = get_client_ip()
        data = request.get_json(silent=True) or {}
        token = data.get('token')
        nonce = data.get('nonce')
        status, resp = auth_sys.handle_request(client_ip, token, nonce)
        return jsonify(resp), status

    @app.route('/api/security/status', methods=['GET'])
    @require_super
    def security_status():
        """查看通讯安全校验状态（加密开关 / Token / RSA 回调）"""
        return jsonify({'code': 0, 'data': auth_sys.get_status()})

    @app.route('/api/security/blacklist', methods=['GET'])
    @require_super
    def security_blacklist():
        """查看 IP 黑名单列表（含来源/原因/过期时间）"""
        return jsonify({'code': 0, 'data': auth_sys.get_blacklist()})

    @app.route('/api/security/blacklist', methods=['POST'])
    @require_super
    def security_blacklist_add():
        """手动将 IP 加入黑名单（可设置过期时间，不填则永久）"""
        admin = request.admin
        data = request.get_json(silent=True) or {}
        ip = str(data.get('ip') or '').strip()
        if not ip:
            return jsonify({'code': 400, 'msg': '缺少 ip'}), 400
        reason = str(data.get('reason') or '手动拉黑').strip()
        expires_at = None
        expires_in = data.get('expires_in')
        if isinstance(expires_in, (int, float)) and expires_in > 0:
            import time
            expires_at = time.time() + float(expires_in)
        auth_sys.add_manual_blacklist(ip, reason, expires_at=expires_at)
        audit_log(admin['id'], admin['username'], 'security_blacklist_add', 'security', ip,
                  {'reason': reason, 'expires_in': expires_in}, 'success')
        msg = f'IP [{ip}] 已加入黑名单'
        if expires_in:
            msg += f'（{int(expires_in)} 秒后自动解封）'
        return jsonify({'code': 0, 'msg': msg})

    @app.route('/api/security/unban', methods=['POST'])
    @require_super
    def security_unban():
        """从永久黑名单中移除指定 IP"""
        admin = request.admin
        data = request.get_json(silent=True) or {}
        ip = str(data.get('ip') or '').strip()
        if not ip:
            return jsonify({'code': 400, 'msg': '缺少 ip'}), 400
        ok = auth_sys.unblacklist(ip)
        audit_log(admin['id'], admin['username'], 'security_unban', 'security', ip, {'existed': ok})
        return jsonify({'code': 0, 'msg': f'IP [{ip}] 已{"从黑名单移除" if ok else "不在黑名单中"}'})
