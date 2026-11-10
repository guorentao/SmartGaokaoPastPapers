#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 data/kp_content/*.json 中的知识点解析内容写入数据库 knowledge_points.content"""

import glob
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, 'database', 'gaokao.db')
DIR = os.path.join(ROOT, 'data', 'kp_content')


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    db_ids = {r['kp_id'] for r in conn.execute('SELECT kp_id FROM knowledge_points')}

    provided = {}
    for fp in sorted(glob.glob(os.path.join(DIR, '*.json'))):
        name = os.path.basename(fp)
        if name.startswith('_'):
            continue
        try:
            data = json.load(open(fp, encoding='utf-8'))
        except json.JSONDecodeError as e:
            print(f'❌ {name} JSON 解析失败: {e}')
            continue
        unknown = set(data) - db_ids
        if unknown:
            print(f'⚠️  {name} 含未知知识点ID: {sorted(unknown)}')
        for k, v in data.items():
            if v and v.strip():
                provided.setdefault(k, v)

    missing = db_ids - set(provided)
    updated = 0
    for kp_id, content in provided.items():
        cur = conn.execute(
            'UPDATE knowledge_points SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE kp_id = ?',
            (content, kp_id))
        updated += cur.rowcount
    conn.commit()

    total = len(db_ids)
    filled = conn.execute(
        "SELECT COUNT(*) FROM knowledge_points WHERE content IS NOT NULL AND TRIM(content)!=''"
    ).fetchone()[0]
    conn.close()

    print('=' * 50)
    print(f'写入 {updated} 个知识点解析（去重后待写入 {len(provided)} 个）')
    print(f'覆盖情况: {filled}/{total} 个知识点已有解析')
    if missing:
        print(f'⚠️  仍缺少解析的知识点 ({len(missing)} 个): {sorted(missing)}')
    else:
        print('✅ 全部知识点均已有解析内容')


if __name__ == '__main__':
    main()
