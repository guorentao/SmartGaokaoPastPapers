#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""导入小学题库（data/primary/*.json）

每道题带年级(1-6)、学期(上/下)，知识点按「学科+年级+单元」建，
导入后诊断模块即可按学生年级只抽已学过的题。
可重复执行：会先清空该学科的小学题目再重建，不影响高考题库。
"""
import glob
import hashlib
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, 'database', 'gaokao.db')
SRC = os.path.join(ROOT, 'data', 'primary')

PREFIX = {'p_math': 'PMA', 'p_chinese': 'PCH', 'p_english': 'PEN'}
SORT = {'p_math': 101, 'p_chinese': 102, 'p_english': 103}
SEM_TAG = {'上': 'A', '下': 'B'}
LETTERS = ['A', 'B', 'C', 'D']


def ensure_columns(conn):
    """给老库补上 年级/学期/学段 字段"""
    for stmt in (
        "ALTER TABLE subjects ADD COLUMN stage TEXT",
        "ALTER TABLE knowledge_points ADD COLUMN grade INTEGER",
        "ALTER TABLE knowledge_points ADD COLUMN semester TEXT",
        "ALTER TABLE questions ADD COLUMN grade INTEGER",
        "ALTER TABLE questions ADD COLUMN semester TEXT",
    ):
        try:
            conn.execute(stmt)
        except Exception:
            pass
    # 现有 9 个高考学科标为高中
    conn.execute("UPDATE subjects SET stage='senior' WHERE stage IS NULL")
    conn.commit()


def upsert_subject(conn, code, name, desc):
    row = conn.execute('SELECT id FROM subjects WHERE subject_code=?', (code,)).fetchone()
    if row:
        conn.execute('UPDATE subjects SET subject_name=?, description=?, sort_order=?, stage=? WHERE id=?',
                     (name, desc, SORT.get(code, 100), 'primary', row['id']))
        return row['id']
    cur = conn.execute('INSERT INTO subjects (subject_code, subject_name, description, sort_order, stage) '
                       'VALUES (?,?,?,?,?)', (code, name, desc, SORT.get(code, 100), 'primary'))
    return cur.lastrowid


def upsert_kp(conn, subject_id, kp_code, name, grade, semester, content):
    row = conn.execute('SELECT id FROM knowledge_points WHERE kp_id=?', (kp_code,)).fetchone()
    if row:
        conn.execute('''UPDATE knowledge_points SET kp_name=?, content=?, grade=?, semester=?,
                        subject_id=?, kp_level=1, difficulty_level=1 WHERE id=?''',
                     (name, content, grade, semester, subject_id, row['id']))
        return row['id']
    cur = conn.execute('''INSERT INTO knowledge_points
        (kp_id, subject_id, kp_name, kp_level, description, content, difficulty_level,
         importance_level, sort_order, grade, semester)
        VALUES (?,?,?,1,?,?,1,3,?,?,?)''',
                       (kp_code, subject_id, name, name[:100], content, grade * 100, grade, semester))
    return cur.lastrowid


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    ensure_columns(conn)

    total_q = 0
    for path in sorted(glob.glob(os.path.join(SRC, '*.json'))):
        data = json.load(open(path, encoding='utf-8'))
        # 跳过非题库文件（如 graph_map.json、以 _ 开头的草稿）
        if not isinstance(data, dict) or 'subject_code' not in data:
            continue
        code = data['subject_code']
        prefix = PREFIX.get(code, code.upper()[:4])
        subject_id = upsert_subject(conn, code, data['subject_name'], data.get('description') or '')

        # 清空该学科旧题（可重复执行）
        old = [r['id'] for r in conn.execute('SELECT id FROM questions WHERE subject_id=?', (subject_id,))]
        if old:
            marks = ','.join('?' * len(old))
            conn.execute(f'DELETE FROM question_kp_relations WHERE question_id IN ({marks})', old)
            conn.execute('DELETE FROM questions WHERE subject_id=?', (subject_id,))

        cnt = 0
        for ui, unit in enumerate(data['units'], 1):
            grade, semester = int(unit['grade']), unit['semester']
            kp_code = f"{prefix}-G{grade}{SEM_TAG.get(semester, 'A')}{ui:02d}"
            kp_db_id = upsert_kp(conn, subject_id, kp_code, unit['kp'], grade, semester,
                                 unit.get('note') or '')
            for qi, q in enumerate(unit['questions'], 1):
                opts = {LETTERS[i]: t for i, t in enumerate(q['options'])}
                stem = q['stem']
                qid = f"{prefix}-G{grade}{SEM_TAG.get(semester, 'A')}-{ui:02d}{qi:02d}"
                h = hashlib.md5((stem + json.dumps(opts, ensure_ascii=False)).encode('utf-8')).hexdigest()
                cur = conn.execute('''INSERT INTO questions
                    (question_id, subject_id, paper_id, question_number, question_type,
                     difficulty_level, score_value, question_text, options_json, answer_text,
                     analysis_text, has_formula, content_hash, source_type, grade, semester)
                    VALUES (?,?,NULL,?,?,1,3,?,?,?,?,0,?,'original',?,?)''',
                                   (qid, subject_id, str(qi), '选择题', stem,
                                    json.dumps(opts, ensure_ascii=False), q['answer'],
                                    q.get('analysis') or '', h, grade, semester))
                conn.execute('''INSERT OR IGNORE INTO question_kp_relations
                    (question_id, kp_id, relation_type, relevance_score) VALUES (?,?,'main',1.0)''',
                             (cur.lastrowid, kp_db_id))
                cnt += 1
        conn.commit()
        print(f"{data['subject_name']}: {len(data['units'])} 个单元 / {cnt} 道题")
        total_q += cnt

    # 统计
    print('-' * 40)
    for r in conn.execute('''SELECT s.subject_name, s.stage, COUNT(q.id) n
        FROM subjects s LEFT JOIN questions q ON q.subject_id=s.id
        WHERE s.stage='primary' GROUP BY s.id ORDER BY s.sort_order'''):
        print(f"  {r['subject_name']}（{r['stage']}）{r['n']} 题")
    print(f'小学题目合计：{total_q}')
    conn.close()


if __name__ == '__main__':
    main()
