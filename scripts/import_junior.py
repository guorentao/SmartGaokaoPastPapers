#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""导入中考题库（data/junior/*.json）

数据形态：每个学科一个 JSON，含
  kps        知识点（code / name / content）
  papers     试卷（paper_id / title / year / region / duration / score）
  questions  题目（paper / no / type / stem / options / answer / analysis / kp / score）

导入后：
  - subjects.stage = 'junior'，sort_order 从 51 起
  - questions.grade 固定为 9（中考面向初三），semester 留空
  - 题目写入 paper_id，可直接在 /papers 预览与下载

可重复执行：先清空该学科旧题与旧卷，不影响高考/小学题库。
"""
import glob
import hashlib
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, 'database', 'gaokao.db')
SRC = os.path.join(ROOT, 'data', 'junior')

LETTERS = ['A', 'B', 'C', 'D', 'E', 'F']
GRADE = 9


def ensure_schema(conn):
    """老库补列：subjects.stage / paper_type 等已存在，这里只兜底"""
    for stmt in (
        "ALTER TABLE subjects ADD COLUMN stage TEXT",
        "ALTER TABLE questions ADD COLUMN grade INTEGER",
        "ALTER TABLE questions ADD COLUMN semester TEXT",
    ):
        try:
            conn.execute(stmt)
        except Exception:
            pass
    conn.commit()


def upsert_subject(conn, code, name, desc, sort_order):
    row = conn.execute('SELECT id FROM subjects WHERE subject_code=?', (code,)).fetchone()
    if row:
        conn.execute('UPDATE subjects SET subject_name=?, description=?, sort_order=?, stage=? WHERE id=?',
                     (name, desc, sort_order, 'junior', row['id']))
        return row['id']
    cur = conn.execute('INSERT INTO subjects (subject_code, subject_name, description, sort_order, stage) '
                       'VALUES (?,?,?,?,?)', (code, name, desc, sort_order, 'junior'))
    return cur.lastrowid


def upsert_kp(conn, subject_id, code, name, content, order):
    row = conn.execute('SELECT id FROM knowledge_points WHERE kp_id=?', (code,)).fetchone()
    if row:
        conn.execute('''UPDATE knowledge_points SET kp_name=?, content=?, subject_id=?,
                        kp_level=1, difficulty_level=2 WHERE id=?''',
                     (name, content, subject_id, row['id']))
        return row['id']
    cur = conn.execute('''INSERT INTO knowledge_points
        (kp_id, subject_id, kp_name, kp_level, description, content, difficulty_level,
         importance_level, sort_order, grade)
        VALUES (?,?,?,1,?,?,2,3,?,?)''',
                       (code, subject_id, name, name[:100], content, order, GRADE))
    return cur.lastrowid


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)

    total_q = total_p = 0
    for path in sorted(glob.glob(os.path.join(SRC, '*.json'))):
        data = json.load(open(path, encoding='utf-8'))
        if not isinstance(data, dict) or 'subject_code' not in data:
            continue
        code = data['subject_code']
        subject_id = upsert_subject(conn, code, data['subject_name'],
                                    data.get('description') or '', data.get('sort_order', 60))

        # 清空该学科旧数据（可重复执行）
        old = [r['id'] for r in conn.execute('SELECT id FROM questions WHERE subject_id=?', (subject_id,))]
        if old:
            marks = ','.join('?' * len(old))
            conn.execute(f'DELETE FROM question_kp_relations WHERE question_id IN ({marks})', old)
            conn.execute('DELETE FROM questions WHERE subject_id=?', (subject_id,))
        conn.execute('DELETE FROM papers WHERE subject_id=?', (subject_id,))

        kp_ids = {}
        for i, kp in enumerate(data.get('kps', []), 1):
            kp_ids[kp['code']] = upsert_kp(conn, subject_id, kp['code'], kp['name'],
                                           kp.get('content') or '', i)

        paper_ids = {}
        for i, p in enumerate(data.get('papers', []), 1):
            cur = conn.execute('''INSERT INTO papers
                (paper_id, subject_id, paper_title, year, region, paper_type, paper_version,
                 total_score, total_questions, duration, source_file, file_type, parsed_status, parsed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,CURRENT_TIMESTAMP)''',
                               (p['paper_id'], subject_id, p['title'], p.get('year'),
                                p.get('region'), p.get('exam_type', '中考试卷'),
                                p.get('version', '仿真卷'), p.get('score'), 0, p.get('duration'),
                                os.path.basename(path), 'json'))
            paper_ids[p['paper_id']] = cur.lastrowid

        cnt = 0
        for q in data.get('questions', []):
            pid = paper_ids.get(q['paper'])
            if pid is None:
                print(f"  ! 跳过未知试卷 {q.get('paper')}")
                continue
            opts = {LETTERS[i]: t for i, t in enumerate(q.get('options') or [])}
            stem = q['stem']
            qid = f"{q['paper']}-{str(q['no']).zfill(2)}"
            h = hashlib.md5((stem + json.dumps(opts, ensure_ascii=False)).encode('utf-8')).hexdigest()
            cur = conn.execute('''INSERT INTO questions
                (question_id, subject_id, paper_id, question_number, question_type,
                 difficulty_level, score_value, question_text, options_json, answer_text,
                 analysis_text, has_formula, content_hash, source_type, grade, semester)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?,'original',?,NULL)''',
                               (qid, subject_id, pid, str(q['no']), q.get('type', '选择题'),
                                q.get('difficulty', 3), q.get('score', 3), stem,
                                json.dumps(opts, ensure_ascii=False) if opts else None,
                                q.get('answer') or '', q.get('analysis') or '', h, GRADE))
            q_db_id = cur.lastrowid
            if q.get('kp') and q['kp'] in kp_ids:
                conn.execute('''INSERT OR IGNORE INTO question_kp_relations
                    (question_id, kp_id, relation_type, relevance_score) VALUES (?,?,'main',1.0)''',
                             (q_db_id, kp_ids[q['kp']]))
            cnt += 1

        # 回填每卷题数
        for pid in paper_ids.values():
            n = conn.execute('SELECT COUNT(*) FROM questions WHERE paper_id=?', (pid,)).fetchone()[0]
            conn.execute('UPDATE papers SET total_questions=? WHERE id=?', (n, pid))

        conn.commit()
        print(f"{data['subject_name']}: {len(data.get('papers', []))} 卷 / {cnt} 题 / "
              f"{len(data.get('kps', []))} 知识点")
        total_q += cnt
        total_p += len(data.get('papers', []))

    print('-' * 46)
    for r in conn.execute('''SELECT s.subject_name, COUNT(DISTINCT p.id) p, COUNT(q.id) n
        FROM subjects s LEFT JOIN papers p ON p.subject_id=s.id
        LEFT JOIN questions q ON q.paper_id=p.id
        WHERE s.stage='junior' GROUP BY s.id ORDER BY s.sort_order'''):
        print(f"  {r['subject_name']}: {r['p']} 卷 / {r['n']} 题")
    print(f'初中合计：{total_p} 卷 / {total_q} 题')
    conn.close()


if __name__ == '__main__':
    main()
