#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键把仓库里的种子数据重建为可运行的 SQLite 数据库。

所有数据都在仓库的 data/ 目录下：
  data/gaokao_bench/*.json   GAOKAO-Bench 高考真题（2010-2022，九科）
  data/kp_content/*.json     538 个知识点的解析正文
  data/primary/*.json        小学三科题库 + 图谱编排表

用法:
  python scripts/bootstrap.py            # 已有库则跳过前两步，补齐缺失数据
  python scripts/bootstrap.py --force    # 删库重建（约 2-4 分钟）
  python scripts/bootstrap.py --check    # 只做数据校验，不写库

产物: database/gaokao.db
"""

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
DB = os.path.join(ROOT, 'database', 'gaokao.db')

STEPS = [
    ('初始化表结构', ['database/init_db.py']),
    ('构建九科知识图谱', ['src/knowledge_graph/build_all_kg.py']),
    ('导入高考真题', ['scripts/import_gaokao_bench.py']),
    ('写入知识点解析', ['scripts/import_kp_content.py']),
    ('重建题目-知识点关联', ['scripts/relink_kp.py', '--apply']),
    ('导入小学题库', ['scripts/import_primary.py']),
    ('构建小学知识图谱', ['scripts/build_primary_graph.py']),
]

EXPECT = [
    ('subjects', 12, '学科（高中 9 + 小学 3）'),
    ('papers', 251, '试卷'),
    ('questions', 2845, '题目（高考 2653 + 小学 192）'),
    ('knowledge_points', 698, '知识点（高中 538 + 小学 160）'),
    ('question_kp_relations', 2300, '题目-知识点关联'),
    ('kp_relations', 130, '知识点前置关系'),
]


def run(desc, cmd):
    print(f'\n▶ {desc}')
    print(f'  $ {" ".join([os.path.basename(PY)] + cmd)}')
    t0 = time.time()
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    r = subprocess.run([PY] + cmd, cwd=ROOT, env=env)
    if r.returncode != 0:
        print(f'✗ 步骤失败：{desc}（退出码 {r.returncode}）')
        sys.exit(r.returncode)
    print(f'  ✓ 用时 {time.time() - t0:.1f}s')


def check():
    """校验关键表的数据量，返回是否通过。"""
    import sqlite3

    if not os.path.exists(DB):
        print(f'✗ 数据库不存在：{DB}')
        return False

    conn = sqlite3.connect(DB)
    ok = True
    print('\n' + '=' * 56)
    print('数据校验')
    print('=' * 56)
    for table, expect, label in EXPECT:
        try:
            n = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        except sqlite3.Error as e:
            print(f'  ✗ {label:<24} 读取失败: {e}')
            ok = False
            continue
        flag = '✓' if n >= expect else '!'
        if n < expect:
            ok = False
        print(f'  {flag} {label:<24} {n:>6}  (期望 ≥ {expect})')

    # 知识点解析覆盖率
    row = conn.execute(
        "SELECT COUNT(*) FROM knowledge_points WHERE content IS NOT NULL AND TRIM(content) != ''"
    ).fetchone()[0]
    total = conn.execute('SELECT COUNT(*) FROM knowledge_points').fetchone()[0]
    pct = row * 100.0 / total if total else 0
    print(f'  {"✓" if pct > 95 else "!"} 知识点解析覆盖率          {pct:5.1f}%  ({row}/{total})')
    if pct <= 95:
        ok = False

    conn.close()
    print('=' * 56)
    print('✓ 校验通过' if ok else '! 校验未通过，请检查上面的步骤输出')
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true', help='删除现有数据库后重建')
    ap.add_argument('--check', action='store_true', help='只校验数据，不重建')
    args = ap.parse_args()

    if args.check:
        sys.exit(0 if check() else 1)

    existing = os.path.exists(DB)
    if args.force and existing:
        for suffix in ('', '-shm', '-wal'):
            p = DB + suffix
            if os.path.exists(p):
                os.remove(p)
        print(f'已删除旧数据库 {DB}')
        existing = False

    print('=' * 56)
    print('SmartGaokaoPastPapers · 数据重建')
    print(f'Python : {PY}')
    print(f'数据库 : {DB}')
    print(f'模式   : {"全量重建" if not existing else "增量补齐"}')
    print('=' * 56)

    # 已有库且非 force：表结构 / 图谱已存在，跳过，避免重复插入
    steps = STEPS if not existing else STEPS[2:]
    t0 = time.time()
    for desc, cmd in steps:
        run(desc, cmd)

    print(f'\n全部完成，总用时 {time.time() - t0:.1f}s')
    sys.exit(0 if check() else 1)


if __name__ == '__main__':
    main()
