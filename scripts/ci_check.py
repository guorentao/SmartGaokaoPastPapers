#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI 用：校验构建出来的数据库是否完整（条数下限 + 知识点解析覆盖率）。

用法: python scripts/ci_check.py [db_path]
"""

import os
import sqlite3
import sys

MIN = {
    'papers': 251,
    'questions': 2845,
    'knowledge_points': 698,
    'question_kp_relations': 2300,
}


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else 'database/gaokao.db'
    if not os.path.exists(db):
        print(f'✗ 数据库不存在: {db}')
        return 1
    size = os.path.getsize(db)
    if size < 5_000_000:
        print(f'✗ 数据库体积异常: {size} 字节')
        return 1
    print(f'数据库: {db}（{size / 1024 / 1024:.1f} MB）')

    conn = sqlite3.connect(db)
    ok = True
    for table, expect in MIN.items():
        n = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        flag = '✓' if n >= expect else '✗'
        print(f'  {flag} {table}: {n}（期望 ≥ {expect}）')
        ok = ok and n >= expect

    empty = conn.execute(
        "SELECT COUNT(*) FROM knowledge_points WHERE content IS NULL OR TRIM(content) = ''"
    ).fetchone()[0]
    print(f'  {"✓" if empty == 0 else "✗"} 缺失解析的知识点: {empty}')
    ok = ok and empty == 0
    conn.close()
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
