# -*- coding: utf-8 -*-
"""
框架操作：版本 / 检查更新 / 更新源码 / 重启 / 终端命令
"""
import asyncio
import io
import contextlib
import json
import logging
import os
import sys
import threading
import zipfile

import requests
from flask import jsonify, request

logger = logging.getLogger('zernus')


def register(ctx):
    app = ctx.app
    framework = ctx.framework
    require_auth = ctx.require_auth
    require_super = ctx.require_super
    audit_log = ctx.audit_log
    get_client_ip = ctx.get_client_ip
    _project_root = ctx._project_root
    _get_framework_local_version = ctx._get_framework_local_version
    _install_new_requirements = ctx._install_new_requirements
    _parse_version_tuple = ctx._parse_version_tuple
    _latest_release_tag = ctx._latest_release_tag
    _download_zip_file = ctx._download_zip_file
    _github_url_candidates = ctx._github_url_candidates
    _check_public_rate = ctx._check_public_rate

    # 框架源码更新白名单与回滚机制统一由 service/updater.py 提供：
    # 只覆盖框架代码/文档，保护 data/、repo/、config.yaml、software/plugins/。

    def _get_framework_local_commit() -> str:
        """获取本地 git 当前提交短 SHA（无 .git 时返回空）"""
        git_dir = os.path.join(_project_root(), '.git')
        if not os.path.isdir(git_dir):
            return ''
        try:
            head_file = os.path.join(git_dir, 'HEAD')
            if not os.path.isfile(head_file):
                return ''
            with open(head_file, 'r', encoding='utf-8') as f:
                ref = f.read().strip()
            if ref.startswith('ref:'):
                ref_path = os.path.join(git_dir, ref[5:].strip().replace('/', os.sep))
                if os.path.isfile(ref_path):
                    with open(ref_path, 'r', encoding='utf-8') as f:
                        return f.read().strip()[:7]
            return ref[:7]
        except Exception:
            return ''

    @app.route('/api/version', methods=['GET'])
    def public_version():
        """公开版本信息（登录页/未登录页展示，无需认证；限速防刷）"""
        if not _check_public_rate(get_client_ip()):
            return jsonify({'code': 429, 'msg': '请求过于频繁，请稍后再试'}), 429
        ver = _get_framework_local_version()
        return jsonify({
            'code': 0,
            'data': {
                'name': 'Zeronus',
                'version': ver or 'unknown',
                'alpha': 'alpha' in ver,
            }
        })

    @app.route('/api/framework/check_update', methods=['GET'])
    @require_auth
    def check_framework_update():
        """检查框架是否有更新（主依据 GitHub 最新 Release 版本号；无 Release 时回退 commit 对比）"""
        repo = 'kuangxing6367/Zero_Nexus'
        branch = 'main'
        try:
            local_ver = _get_framework_local_version()
            local_sha = _get_framework_local_commit()
            lt = _parse_version_tuple(local_ver)

            # 主依据：GitHub Release 列表（tag_name 即版本号，含 pre-release）
            # 不用 /releases/latest：它会跳过 pre-release，导致 alpha 版检测不到。
            # 从所有 Release 中取版本号最高的一个（而非发布时间最新，避免低版本覆盖）。
            release = None
            all_releases = []
            try:
                rresp = requests.get(
                    f"https://api.github.com/repos/{repo}/releases?per_page=30",
                    headers={'Accept': 'application/vnd.github+json'},
                    timeout=15,
                )
                if rresp.status_code == 200:
                    releases = rresp.json()
                    if isinstance(releases, list) and releases:
                        best = None
                        best_t = None
                        for rel in releases:
                            t = rel.get('tag_name') or ''
                            tv = _parse_version_tuple(t[1:] if t.startswith('v') else t)
                            if tv is None:
                                continue
                            all_releases.append({
                                'tag': t,
                                'version': t[1:] if t.startswith('v') else t,
                                'name': rel.get('name') or t,
                                'published_at': rel.get('published_at', ''),
                            })
                            if best_t is None or tv > best_t:
                                best, best_t = rel, tv
                        release = best
            except Exception:
                pass

            if release:
                tag = release.get('tag_name', '') or ''
                remote_version = tag[1:] if tag.startswith('v') else tag
                rt = _parse_version_tuple(remote_version)
                if lt is not None and rt is not None:
                    n = max(len(lt), len(rt))
                    has_update = (rt + (0,) * (n - len(rt))) > (lt + (0,) * (n - len(lt)))
                else:
                    has_update = None  # 本地版本缺失，无法判断
                body = release.get('body') or ''
                name = (release.get('name') or '').strip()
                commit_msg = name or (body.split('\n')[0] if body else '') or f"Release {tag}"
                # 可用版本列表（按版本号从高到低），供前端指定版本更新
                all_releases.sort(
                    key=lambda x: _parse_version_tuple(x['version']) or (0,),
                    reverse=True)
                return jsonify({
                    'code': 0,
                    'data': {
                        'repo': repo,
                        'branch': branch,
                        'local_version': local_ver or '未知',
                        'latest_version': remote_version or '未知',
                        'local_commit': local_sha or '未知',
                        'latest_commit': tag,
                        'commit_message': commit_msg,
                        'commit_date': release.get('published_at', ''),
                        'author': (release.get('author') or {}).get('login', ''),
                        'has_update': has_update,
                        'available_versions': all_releases,
                    }
                })

            # 回退：仓库无任何 Release → 按 main 分支最新提交对比
            api_url = f"https://api.github.com/repos/{repo}/commits/{branch}"
            resp = requests.get(api_url, headers={'Accept': 'application/vnd.github.v3+json'}, timeout=15)
            if resp.status_code == 404:
                return jsonify({'code': 404, 'msg': '仓库或分支不存在'}), 404
            if resp.status_code != 200:
                return jsonify({'code': 500, 'msg': f'GitHub API 返回 {resp.status_code}'}), 500

            data = resp.json()
            latest_sha = data.get('sha', '')[:7]
            commit_msg = data.get('commit', {}).get('message', '').split('\n')[0]
            commit_date = data.get('commit', {}).get('author', {}).get('date', '')
            author = data.get('commit', {}).get('author', {}).get('name', '')
            has_update = bool(local_sha) and local_sha != latest_sha

            return jsonify({
                'code': 0,
                'data': {
                    'repo': repo,
                    'branch': branch,
                    'local_version': local_ver or '未知',
                    'latest_version': '未知（仓库无 Release）',
                    'local_commit': local_sha or '未知',
                    'latest_commit': latest_sha,
                    'commit_message': commit_msg,
                    'commit_date': commit_date,
                    'author': author,
                    'has_update': has_update,
                }
            })
        except requests.exceptions.Timeout:
            return jsonify({'code': 500, 'msg': 'GitHub API 请求超时'}), 500
        except Exception as e:
            logger.error(f"检查框架更新失败: {e}")
            return jsonify({'code': 500, 'msg': str(e)}), 500

    @app.route('/api/framework/update', methods=['POST'])
    @require_super
    def update_framework():
        """
        从 GitHub 更新框架源码到指定版本（默认最新 Release）
        只覆盖框架代码与文档（`service/updater.py` 的 FRAMEWORK_INCLUDE 白名单），
        保留用户数据（software/plugins/、data/、repo/、config.yaml、extensions.yaml 等），
        更新后需重启生效。请求体可选 version 指定目标版本号（如 1.0.0），
        可选 dry_run: true 只校验不落盘。

        供应链安全（fail closed）：更新 ZIP 必须通过 sha256 校验后才落盘覆盖——
        - config.security.update_expected_sha256 配置了期望哈希 → 强制比对；
        - 未配置 → 默认拒绝更新；确要跳过校验须显式设置
          config.security.update_allow_unsigned: true（不推荐）。
        """
        admin = request.admin
        repo = 'kuangxing6367/Zero_Nexus'
        branch = 'main'
        root = _project_root()
        body = request.json or {}
        req_version = body.get('version') or ''
        dry_run = bool(body.get('dry_run'))

        sec_cfg = framework.config.get('security', {}) or {}
        expected_sha = str(sec_cfg.get('update_expected_sha256') or '').strip().lower()
        allow_unsigned = bool(sec_cfg.get('update_allow_unsigned'))
        if not expected_sha and not allow_unsigned:
            return jsonify({
                'code': 403,
                'msg': ('框架更新已启用供应链校验：请先在 config.yaml → security.'
                        'update_expected_sha256 填入目标 ZIP 的 sha256，或显式设置 '
                        'security.update_allow_unsigned: true 跳过校验（不推荐）。'),
            }), 403

        try:
            # 指定版本 → 用对应 tag；否则用最新 Release tag；无 Release 回退 main 分支 ZIP
            tag = ''
            if req_version:
                want = req_version[1:] if req_version.startswith('v') else req_version
                tag = f'v{want}' if want and not want.startswith('v') else want
                zip_url = f"https://github.com/{repo}/archive/refs/tags/{tag}.zip"
                logger.info(f"正在下载框架更新（指定版本 {tag}）: {zip_url}")
            else:
                tag = _latest_release_tag(repo)
                if tag:
                    zip_url = f"https://github.com/{repo}/archive/refs/tags/{tag}.zip"
                    logger.info(f"正在下载框架更新（Release {tag}）: {zip_url}")
                else:
                    zip_url = f"https://github.com/{repo}/archive/refs/heads/{branch}.zip"
                    logger.info(f"仓库无 Release，回退下载分支 ZIP: {zip_url}")

            # 代理 → 镜像 → 直连，逐个候选下载并校验 ZIP 有效性
            tmp_zip = _download_zip_file(_github_url_candidates(zip_url))
            if tmp_zip is None:
                return jsonify({'code': 500, 'msg': '框架更新 ZIP 下载失败（代理/镜像/直连均不可用）'}), 500

            # 校验 + 暂存 + 编译检查 + 备份 + 替换 + 失败自动回滚，
            # 全部逻辑在 service/updater.py（WebUI 与节点代理共用同一机制）。
            from service.updater import UpdateError, apply_source_zip
            try:
                report = apply_source_zip(
                    tmp_zip, root, expected_sha256=expected_sha,
                    allow_unsigned=allow_unsigned, dry_run=dry_run)
            except UpdateError as e:
                audit_log(admin['id'], admin['username'], 'update_framework',
                          'system', 'framework',
                          {'target': tag or branch, 'dry_run': dry_run},
                          'failure', str(e))
                return jsonify({'code': 400, 'msg': f'更新失败: {e}'}), 400
            finally:
                try:
                    os.unlink(tmp_zip)
                except Exception:
                    pass

            updated = report.get('updated') or []
            if dry_run:
                audit_log(admin['id'], admin['username'], 'update_framework',
                          'system', 'framework',
                          {'files': updated, 'target': tag or branch,
                           'dry_run': True}, 'success')
                return jsonify({
                    'code': 0,
                    'msg': f'更新包校验通过（dry-run，{len(updated)} 项），未改动任何文件。',
                    'data': {'updated': updated, 'target': tag or 'latest',
                             'dry_run': True},
                })

            # 新版本可能引入新增依赖：自动安装缺失项（不覆盖已安装包）
            try:
                _install_new_requirements()
            except Exception as e:
                logger.warning(f"框架更新：依赖自动安装异常: {e}")

            audit_log(admin['id'], admin['username'], 'update_framework', 'system', 'framework',
                      {'files': updated, 'target': tag or branch,
                       'backup': report.get('backup')}, 'success')
            return jsonify({
                'code': 0,
                'msg': (f'框架已更新到 {tag or "最新"}（{len(updated)} 项），'
                        '请重启框架生效；如需回滚可调用 /api/framework/rollback。'),
                'data': {'updated': updated, 'target': tag or 'latest',
                         'backup': report.get('backup'),
                         'restart_required': True},
            })
        except zipfile.BadZipFile:
            return jsonify({'code': 400, 'msg': '下载的 ZIP 文件无效'}), 400
        except Exception as e:
            logger.error(f"更新框架失败: {e}", exc_info=True)
            audit_log(admin['id'], admin['username'], 'update_framework', 'system', 'framework',
                      {}, 'failure', str(e))
            return jsonify({'code': 500, 'msg': f'更新失败: {e}'}), 500

    @app.route('/api/framework/backups', methods=['GET'])
    @require_auth
    def framework_backups():
        """列出可回滚的框架更新备份（只读）。"""
        from service.updater import list_backups
        try:
            return jsonify({'code': 0, 'data': list_backups(_project_root())})
        except Exception as e:
            logger.error(f"读取框架更新备份失败: {e}")
            return jsonify({'code': 500, 'msg': str(e)}), 500

    @app.route('/api/framework/rollback', methods=['POST'])
    @require_super
    def framework_rollback():
        """回滚到指定框架更新备份（默认最新一份），回滚后需重启。"""
        admin = request.admin
        name = (request.json or {}).get('backup') or ''
        from service.updater import UpdateError, rollback_source_update
        try:
            report = rollback_source_update(_project_root(), name)
        except UpdateError as e:
            audit_log(admin['id'], admin['username'], 'rollback_framework',
                      'system', 'framework', {'backup': name}, 'failure', str(e))
            return jsonify({'code': 400, 'msg': f'回滚失败: {e}'}), 400
        except Exception as e:
            logger.error(f"回滚框架失败: {e}", exc_info=True)
            audit_log(admin['id'], admin['username'], 'rollback_framework',
                      'system', 'framework', {'backup': name}, 'failure', str(e))
            return jsonify({'code': 500, 'msg': f'回滚失败: {e}'}), 500
        audit_log(admin['id'], admin['username'], 'rollback_framework',
                  'system', 'framework', report, 'success')
        return jsonify({
            'code': 0,
            'msg': f'已回滚到备份 {report["backup"]}，请重启框架生效。',
            'data': report,
        })

    @app.route('/api/restart', methods=['POST'])
    @require_super
    def restart_framework():
        """重启框架：用 os.execv 原地替换进程，不依赖外部进程管理器"""
        admin = request.admin
        audit_log(admin['id'], admin['username'], 'restart_framework', 'system', 'framework')

        # 异步停止并重启，先返回响应
        def _do_restart():
            import time
            time.sleep(1)
            try:
                loop = getattr(framework, 'loop', None)
                if loop is not None and loop.is_running():
                    fut = asyncio.run_coroutine_threadsafe(framework.stop(), loop)
                    fut.result(timeout=15)
                else:
                    asyncio.run(framework.stop())
            except Exception:
                pass
            # os.execv 用当前 Python 解释器重载 main.py，原地替换进程
            python = sys.executable
            main_py = os.path.join(_project_root(), 'main.py')
            os.chdir(os.path.dirname(main_py))
            os.execv(python, [python, main_py] + sys.argv[1:])

        threading.Thread(target=_do_restart, daemon=False).start()
        return jsonify({'code': 0, 'msg': '框架正在重启...'})

    @app.route('/api/terminal/exec', methods=['POST'])
    @require_super
    def terminal_exec():
        """执行终端命令并返回输出"""
        admin = request.admin
        data = request.json or {}
        command = (data.get('command') or '').strip()
        if not command:
            return jsonify({'code': 400, 'msg': '命令不能为空'})

        from core.terminal import terminal_commands

        # 查找命令处理器
        cmd_name = command.split()[0].lower()
        handler = terminal_commands.get(cmd_name)
        if handler is None:
            return jsonify({'code': 404, 'msg': f'未知命令: {cmd_name}，输入 help 查看可用命令'})

        args = command[len(cmd_name):].strip()

        # 捕获 stdout
        output_buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(output_buffer):
                handler(args)
        except Exception as e:
            output_buffer.write(f"\n执行异常: {e}")

        output = output_buffer.getvalue()
        audit_log(admin['id'], admin['username'], 'terminal_exec', 'system', 'terminal', {'command': command})
        return jsonify({'code': 0, 'data': {'output': output, 'command': command}})
