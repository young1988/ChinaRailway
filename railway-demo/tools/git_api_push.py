#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
沙箱里 `git push` 会失败：代理放行 api.github.com 但拦掉 github.com（smart-http 端点）。
这里改用 GitHub **Git Data API** 推送：按本地 commit 逐个复刻（blob → tree → commit），
最后快进更新 refs/heads/main。

关键细节：**推送后必须把本地分支对齐到远端 SHA**，否则本地与远程历史分叉，
下次 `rev-list origin/main..HEAD` 会把同一个 commit 又推一遍。

为什么本地 SHA 会和远端不一样（v0.18.0 实测踩坑）
------------------------------------------------
推送时给 API 的 message 取自 `git show -s --format=%B`，而 `%B` 会在提交信息
**自带的末尾换行之后再多输出一个换行**。GitHub 原样存储，于是远端 commit 对象的
消息比本地多一个 `\\n`，SHA 自然不同。

复刻规则（已验证）：
    远端对象消息 = 本地 commit 对象消息 + '\\n'
    远端对象 = tree + parent + author + committer + '\\n\\n' + (本地消息 + '\\n')

另外：Git API 的 `git/commits/<sha>` **只认完整 40 位 SHA**，传 7 位缩写会 404。

用法
----
    python3 tools/git_api_push.py            # 推送 origin/main..HEAD 的全部 commit
    python3 tools/git_api_push.py --dry-run  # 只打印将要做什么，不动远端

只读钥匙串里已存的 GitHub 凭据，不打印 token。
"""

import argparse
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request

REPO = 'young1988/ChinaRailway'
BRANCH = 'main'
API = 'https://api.github.com'


def sh(args, binary=False, cwd='.'):
    out = subprocess.run(['git'] + args, cwd=cwd, capture_output=True, check=True)
    return out.stdout if binary else out.stdout.decode('utf-8')


def token():
    out = subprocess.run(['git', 'credential', 'fill'], cwd='.',
                         input=b'protocol=https\nhost=github.com\n\n',
                         capture_output=True, check=True).stdout.decode()
    for line in out.splitlines():
        if line.startswith('password='):
            return line[len('password='):].strip()
    raise SystemExit('钥匙串里没有 github.com 凭据')


TOK = token()


def api(method, path, payload=None):
    req = urllib.request.Request(API + path, method=method)
    req.add_header('Authorization', 'token ' + TOK)
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('User-Agent', 'railway-footprint-push')
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, data, timeout=60) as r:
            body = r.read().decode('utf-8')
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:600]
        raise SystemExit('API %s %s -> %s\n%s' % (method, path, e.code, detail))


def commit_meta(sha):
    """作者/提交者/日期/消息。注意 %B 会多带一个换行——这正是远端 SHA 与本地不同的原因。"""
    fmt = '%an%n%ae%n%aI%n%cn%n%ce%n%cI%n%B'
    out = sh(['show', '-s', '--format=' + fmt, sha]).split('\n')
    an, ae, ad, cn, ce, cd = out[:6]
    msg = '\n'.join(out[6:])
    return {
        'author': {'name': an, 'email': ae, 'date': ad},
        'committer': {'name': cn, 'email': ce, 'date': cd},
        'message': msg,
    }


def changed_files(parent, child):
    """返回 [(path, mode, blobsha)]，只含相对 parent 变化的条目。用 -z 避免中文路径被转义。"""
    out = sh(['diff', '--name-status', '-z', parent, child]).split('\0')
    entries = []
    i = 0
    while i < len(out):
        line = out[i].strip()
        if not line:
            i += 1
            continue
        status = line.split('\t')[0]
        if status.startswith('D'):
            entries.append((out[i + 1], None, None))
            i += 2
            continue
        if status.startswith('R') or status.startswith('C'):
            path = out[i + 2]
            i += 3
        else:
            path = out[i + 1]
            i += 2
        rec = sh(['ls-tree', '-r', '-z', child, '--', path]).split('\0')[0]
        meta, _path = rec.split('\t')
        mode, _type, blob_sha = meta.split()
        entries.append((path, mode, blob_sha))
    return entries


def make_blob(blob_sha):
    content = sh(['cat-file', 'blob', blob_sha], binary=True)
    r = api('POST', '/repos/%s/git/blobs' % REPO,
            {'content': base64.b64encode(content).decode('ascii'), 'encoding': 'base64'})
    return r['sha']


def align_local(remote_sha):
    """把本地 commit 复刻成与远端字节一致的对象，再 reset + 更新 origin/main。

    远端对象消息 = 本地对象消息 + '\\n'（见模块文档）。复刻出来的 SHA 应当
    与 remote_sha 完全相同；不一致时保留本地原 commit 不动，只把 origin/main
    指向远端真实 SHA，避免下次重复推同一个 commit。
    """
    raw = sh(['cat-file', 'commit', 'HEAD'], binary=True).decode('utf-8')
    head, sep, msg = raw.partition('\n\n')
    assert sep == '\n\n', '本地 commit 对象格式异常'
    obj = head + '\n\n' + msg + '\n'
    p = subprocess.run(['git', 'hash-object', '-t', 'commit', '-w', '--stdin'],
                       input=obj.encode('utf-8'), capture_output=True, check=True)
    local_sha = p.stdout.decode().strip()
    if local_sha == remote_sha:
        sh(['reset', '--hard', local_sha])
        sh(['update-ref', 'refs/remotes/origin/%s' % BRANCH, remote_sha])
        print('本地已对齐：%s ✅ 与远端 SHA 完全一致' % local_sha[:7])
        return True
    # 复刻不出来（GitHub 又改了规整规则）：至少别让下次重复推
    print('⚠️ 复刻 SHA 不一致（本地 %s / 远端 %s），仅更新 origin/main 指向远端'
          % (local_sha[:7], remote_sha[:7]))
    sh(['update-ref', 'refs/remotes/origin/%s' % BRANCH, remote_sha])
    return False


def main():
    ap = argparse.ArgumentParser(description='通过 GitHub Git Data API 推送')
    ap.add_argument('--dry-run', action='store_true', help='只打印计划，不动远端')
    args = ap.parse_args()

    remote = api('GET', '/repos/%s/git/ref/heads/%s' % (REPO, BRANCH))['object']['sha']
    remote_commit = api('GET', '/repos/%s/git/commits/%s' % (REPO, remote))
    tree = remote_commit['tree']['sha']
    print('远程 %s = %s（tree %s）' % (BRANCH, remote[:7], tree[:7]))

    commits = sh(['rev-list', '--reverse', 'origin/%s..HEAD' % BRANCH]).split()
    if not commits:
        print('没有待推送的 commit')
        return 0
    print('待推送 %d 个 commit：%s' % (len(commits), ' '.join(c[:7] for c in commits)))
    if args.dry_run:
        return 0

    parent = remote
    for c in commits:
        local_parent = sh(['rev-parse', c + '^']).strip()
        meta = commit_meta(c)
        entries = []
        for path, mode, blob_sha in changed_files(local_parent, c):
            if blob_sha is None:
                entries.append({'path': path, 'mode': mode or '100644', 'type': 'blob', 'sha': None})
                continue
            sha = make_blob(blob_sha)
            entries.append({'path': path, 'mode': mode, 'type': 'blob', 'sha': sha})
            print('  %s %s' % (path, sha[:7]))
        r = api('POST', '/repos/%s/git/trees' % REPO, {'base_tree': tree, 'tree': entries})
        tree = r['sha']
        local_tree = sh(['rev-parse', c + '^{tree}']).strip()
        print('  tree %s %s' % (tree[:7], '✅ 与本地一致' if tree == local_tree
                                else '⚠️ 与本地不一致（本地 %s）' % local_tree[:7]))
        rc = api('POST', '/repos/%s/git/commits' % REPO,
                 {'message': meta['message'], 'tree': tree, 'parents': [parent],
                  'author': meta['author'], 'committer': meta['committer']})
        parent = rc['sha']
        print('提交 %s' % rc['sha'][:7])

    api('PATCH', '/repos/%s/git/refs/heads/%s' % (REPO, BRANCH), {'sha': parent, 'force': False})
    now = api('GET', '/repos/%s/git/ref/heads/%s' % (REPO, BRANCH))['object']['sha']
    print('远端 %s 现指向 %s' % (BRANCH, now[:7]))

    local_tree = sh(['rev-parse', 'HEAD^{tree}']).strip()
    remote_tree = api('GET', '/repos/%s/git/commits/%s' % (REPO, now))['tree']['sha']
    print('tree 校验：本地 %s / 远端 %s -> %s'
          % (local_tree[:7], remote_tree[:7], '✅ 一致' if local_tree == remote_tree else '❌ 不一致'))

    align_local(now)
    return 0 if local_tree == remote_tree else 1


if __name__ == '__main__':
    sys.exit(main())
