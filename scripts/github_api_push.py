#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 GitHub REST API 把本地 HEAD 同步到远端分支（git push 走不通时的兜底）。

适用场景：公司网络 / 校园网对 github.com 的 git 协议（443 上的 git-upload-pack）做了拦截，
或者本机 schannel 证书吊销列表拉不到导致 `git push` 报 CRYPT_E_REVOCATION_OFFLINE，
但 HTTPS 访问 api.github.com 正常。

做法是增量同步：先取远端当前树，与本地 HEAD 的树逐文件比对 sha，
只上传缺失 / 变更的 blob，被删掉的文件以 sha=null 提交，最后建 tree → commit → 移动分支指针。
一次几十个文件的改动通常几秒到几十秒完成，不会重复传几千个未变文件。

Token 取用顺序：--token 参数 → 环境变量 GITHUB_TOKEN / GH_TOKEN → git 凭据管理器
（`git credential fill`，Windows 上即「凭据管理器 → 普通凭据 → git:https://github.com」）。
Token 需要该仓库的 Contents: Read and write 权限。

用法：
    python scripts/github_api_push.py -m "feat: 新增中考地区卷"
    python scripts/github_api_push.py --dry-run
    python scripts/github_api_push.py --repo owner/repo --branch main -m "..." --token ghp_xxx
"""

import argparse
import base64
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = 'https://api.github.com'

# 部分环境的根证书链校验不通过（典型是企业代理中间人），这里降级为不校验，
# 只用于 api.github.com 这一固定域名，且请求都带 Token、只传本项目文件。
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def api(method, path, token, payload=None, retries=4):
    """调 GitHub REST API，网络抖动重试，4xx 里除 422 外不重试（重试也没用）。"""
    data = json.dumps(payload).encode() if payload is not None else None
    last = None
    for i in range(retries):
        req = urllib.request.Request(API + path, data=data, method=method)
        req.add_header('Authorization', f'Bearer {token}')
        req.add_header('Accept', 'application/vnd.github+json')
        if data:
            req.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(req, timeout=90, context=CTX) as r:
                body = r.read().decode()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code == 422:                 # 已存在 / 无需变更
                return {'sha': None, 'noop': True}
            last = f'HTTP {e.code}: {body[:300]}'
            if e.code in (401, 403, 404):     # 权限或不存在，重试无意义
                raise RuntimeError(f'{method} {path} -> {last}')
        except Exception as e:
            last = str(e)
        time.sleep(2 * (i + 1))
    raise RuntimeError(f'{method} {path} 失败: {last}')


def resolve_token(explicit=None):
    if explicit:
        return explicit.strip()
    for key in ('GITHUB_TOKEN', 'GH_TOKEN'):
        if os.environ.get(key):
            return os.environ[key].strip()

    # 交给 git 自己去问凭据管理器，避免把 token 明文写在脚本或 .git/config 里
    try:
        p = subprocess.run(
            ['git', 'credential', 'fill'],
            input='protocol=https\nhost=github.com\n\n',
            capture_output=True, text=True, timeout=20,
        )
        for line in p.stdout.splitlines():
            if line.startswith('password='):
                return line[len('password='):].strip()
    except Exception:
        pass
    return None


def repo_of(local):
    """从 origin 的 URL 反解 owner/repo，省得每次都手敲。"""
    try:
        url = subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=local,
                             capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:
        return None
    m = re.search(r'github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$', url)
    return f'{m.group(1)}/{m.group(2)}' if m else None


def local_tree(local):
    """本地 HEAD 的 {路径: (mode, blob sha)}。

    用 -z 取 NUL 分隔，避免中文路径被 core.quotePath 转义成 \\346\\226\\207 这种八进制。
    """
    out = subprocess.run(['git', 'ls-tree', '-r', '-z', 'HEAD'], cwd=local,
                         capture_output=True).stdout.decode('utf-8', 'replace')
    tree = {}
    for entry in out.split('\0'):
        if not entry:
            continue
        meta, path = entry.split('\t', 1)
        mode, _type, sha = meta.split()
        tree[path] = (mode, sha)
    return tree


def main():
    ap = argparse.ArgumentParser(description='把本地 HEAD 增量同步到 GitHub 分支（REST API 版）')
    ap.add_argument('-m', '--message', default='chore: 同步本地改动', help='提交信息')
    ap.add_argument('--repo', help='owner/repo，默认从 origin 远程地址推断')
    ap.add_argument('--branch', default='main', help='目标分支，默认 main')
    ap.add_argument('--dir', default='.', help='本地仓库路径，默认当前目录')
    ap.add_argument('--token', help='GitHub Token，默认读 GITHUB_TOKEN 或 git 凭据管理器')
    ap.add_argument('--dry-run', action='store_true', help='只列出差异，不实际推送')
    args = ap.parse_args()

    local = os.path.abspath(args.dir)
    repo = args.repo or repo_of(local)
    if not repo:
        sys.exit('无法确定仓库：请用 --repo owner/repo 指定')

    token = resolve_token(args.token)
    if not token and not args.dry_run:
        sys.exit('未找到 GitHub Token：设置环境变量 GITHUB_TOKEN，或存进 git 凭据管理器')

    # 工作区有未提交改动时提醒一句：脚本同步的是 HEAD 的树，未提交的内容不会被带上去
    dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=local,
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        print('⚠ 工作区有未提交改动，这些改动不会出现在本次推送里（脚本同步的是 HEAD）')

    lt = local_tree(local)
    print(f'本地 HEAD 文件数: {len(lt)}')

    ref = api('GET', f'/repos/{repo}/git/ref/heads/{args.branch}', token or '')
    head_sha = ref['object']['sha']
    head = api('GET', f'/repos/{repo}/git/commits/{head_sha}', token or '')
    remote_tree_sha = head['tree']['sha']

    remote = {}
    tree = api('GET', f'/repos/{repo}/git/trees/{remote_tree_sha}?recursive=1', token or '')
    for node in tree.get('tree', []):
        if node['type'] == 'blob':
            remote[node['path']] = node['sha']
    print(f'远端 {repo}@{args.branch} @ {head_sha[:8]}，文件数: {len(remote)}')

    changed = [p for p, (_m, s) in lt.items() if remote.get(p) != s]
    deleted = [p for p in remote if p not in lt]
    print(f'需上传: {len(changed)}，需删除: {len(deleted)}')

    if not changed and not deleted:
        print('✓ 远端已是最新，无需推送')
        return

    if args.dry_run:
        for p in changed:
            print(f'  ↑ {p}')
        for p in deleted:
            print(f'  ✗ {p}')
        print('（--dry-run，未实际推送）')
        return

    entries = []
    for path in changed:
        raw = subprocess.run(['git', 'show', f'HEAD:{path}'], cwd=local,
                             capture_output=True).stdout
        blob = api('POST', f'/repos/{repo}/git/blobs', token, {
            'content': base64.b64encode(raw).decode(), 'encoding': 'base64'})
        entries.append({'path': path, 'mode': lt[path][0], 'type': 'blob', 'sha': blob['sha']})
        print(f'  ↑ {path}')
    for path in deleted:
        entries.append({'path': path, 'mode': '100644', 'type': 'blob', 'sha': None})
        print(f'  ✗ {path}')

    new_tree = api('POST', f'/repos/{repo}/git/trees', token,
                   {'base_tree': remote_tree_sha, 'tree': entries})
    commit = api('POST', f'/repos/{repo}/git/commits', token,
                 {'message': args.message, 'tree': new_tree['sha'], 'parents': [head_sha]})
    api('PATCH', f'/repos/{repo}/git/refs/heads/{args.branch}', token,
        {'sha': commit['sha'], 'force': False})
    print(f'✓ 已推送 {commit["sha"][:8]} → https://github.com/{repo}/tree/{args.branch}')
    print('  提示：这次提交只在远端，本地 git 历史里没有；下次本地再提交前建议先 '
          'git fetch && git reset --soft origin/' + args.branch)


if __name__ == '__main__':
    main()
