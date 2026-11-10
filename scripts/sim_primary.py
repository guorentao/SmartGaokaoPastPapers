#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""小学学情诊断全流程自测：带学生信息建卷 → 作答 → 报告 → 打印卷"""
import sqlite3
import sys
import urllib.parse
import urllib.request

BASE = 'http://127.0.0.1:8000'
DB = 'database/gaokao.db'


def post(path, data):
    body = urllib.parse.urlencode(data, encoding='utf-8').encode()
    req = urllib.request.Request(BASE + path, data=body, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, r.geturl(), r.read().decode('utf-8', 'ignore')
    except urllib.error.HTTPError as e:
        return e.code, e.geturl(), e.read().decode('utf-8', 'ignore')


def get(path):
    r = urllib.request.urlopen(BASE + path, timeout=60)
    return r.status, r.read().decode('utf-8', 'ignore')


def main(subject='p_math', grade='2', semester='下', name='张小明', cls='二(3)班', count='12'):
    st, url, _ = post('/quiz/start', {
        'subject_code': subject, 'count': count, 'student_name': name,
        'student_class': cls, 'grade': grade, 'semester': semester,
    })
    sid = url.rstrip('/').split('/')[-1]
    print(f'建卷 {sid}（{subject} {grade}年级{semester}学期 {name}）')

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    items = conn.execute('SELECT seq, unit_name, grade, semester, answer_text FROM quiz_items '
                         'WHERE session_id=? ORDER BY seq', (sid,)).fetchall()
    print(f'题目 {len(items)} 道，年级分布 {sorted(set((i["grade"], i["semester"]) for i in items))}')

    # 前 60% 答对，其余故意答错
    answers = {}
    for i, it in enumerate(items):
        right = it['answer_text'].strip().upper()[:1]
        if i < int(len(items) * 0.6):
            answers[f'q{it["seq"]}'] = right
        else:
            answers[f'q{it["seq"]}'] = 'A' if right != 'A' else 'B'
    answers['_ts'] = '0'
    st, url, _ = post(f'/quiz/{sid}/submit', answers)
    print('交卷 ->', url)

    st, html = get(f'/quiz/{sid}/report')
    print('报告页', st, '含姓名', name in html, '含年级', f'{grade}年级' in html)
    for kw in ['明显薄弱', '不够稳固', '掌握扎实', '巩固卷']:
        print('  ', kw, kw in html)

    st, html = get(f'/quiz/{sid}/paper')
    print('打印卷', st, '抬头信息', all(k in html for k in [name, cls, f'{grade}年级{semester}学期']))
    st, html = get(f'/quiz/{sid}/paper?key=1')
    print('答案卷', st, '含参考答案', '参考答案与解析' in html)

    # 生成巩固卷
    st, url, _ = post(f'/quiz/{sid}/consolidate', {'per_unit': '2'})
    csid = url.rstrip('/').split('/')[-1]
    sql = ('SELECT student_name, student_grade, student_semester, title, question_count, kind '
           'FROM quiz_sessions WHERE session_id=?')
    row = conn.execute(sql, (csid,)).fetchone()
    print('巩固卷', csid, dict(row) if row else 'FAIL')
    conn.close()


if __name__ == '__main__':
    args = sys.argv[1:]
    main(*args) if args else main()
