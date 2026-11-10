# -*- coding: utf-8 -*-
"""模拟一次完整测评流程（用于自测页面与判分链路）"""
import json
import random
import re
import sqlite3
import sys
import urllib.parse
import urllib.request

BASE = 'http://127.0.0.1:8000'
DB = r'C:\Users\Administrator\WorkBuddy\2026-11-10-10-58-26\SmartGaokaoPastPapers\database\gaokao.db'
random.seed(7)


def post(path, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(BASE + path, data=body, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.geturl(), r.read().decode('utf-8', 'ignore')
    except urllib.error.HTTPError as e:
        return e.code, path, e.read().decode('utf-8', 'ignore')


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=120) as r:
        return r.status, r.read().decode('utf-8', 'ignore')


def run(subject, count, wrong_ratio=0.3):
    st, url, _ = post('/quiz/start', {'subject_code': subject, 'count': count})
    sid = url.rstrip('/').split('/')[-1]
    print(f'[{subject}] 生成测评 {sid} (HTTP {st})')

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    items = conn.execute('SELECT * FROM quiz_items WHERE session_id=? ORDER BY seq', (sid,)).fetchall()
    answers = {}
    for it in items:
        ans = (it['answer_text'] or '').strip()
        if it['grading'] == 'choice':
            correct = re.findall(r'[A-F]', ans.upper())
            if random.random() < wrong_ratio:
                pool = [c for c in 'ABCD' if c not in correct]
                answers[f"q{it['seq']}"] = random.choice(pool)
            else:
                answers[f"q{it['seq']}"] = correct[0] if correct else 'A'
        else:
            answers[f"q{it['seq']}"] = ans if random.random() > wrong_ratio else ''
    conn.close()

    st, url, body = post(f'/quiz/{sid}/submit', answers)
    print(f'  交卷 -> HTTP {st} {url}')
    st, html = get(f'/quiz/{sid}/report')
    print(f'  报告页 HTTP {st}; 含薄弱块: {"明显薄弱" in html}; 含巩固按钮: {"生成巩固卷" in html}')
    m = re.search(r'class="score-inner">\s*<b>([^<]+)</b>\s*<span>([^<]+)</span>', html)
    if m:
        print(f'  得分 {m.group(1)} 正确率 {m.group(2)}')

    # 巩固卷
    st, url, _ = post(f'/quiz/{sid}/consolidate', {'per_unit': '2'})
    csid = url.rstrip('/').split('/')[-1]
    print(f'  巩固卷 -> HTTP {st} sid={csid}')

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    c_items = conn.execute('''
        SELECT qi.seq, qi.unit_name, qi.answer_text, qi.grading, s.status
        FROM quiz_items qi JOIN quiz_sessions s ON s.session_id=qi.session_id
        WHERE qi.session_id=? ORDER BY qi.seq''', (csid,)).fetchall()
    print(f'  巩固卷题量 {len(c_items)}; 考点: {sorted({c["unit_name"] for c in c_items})}')
    # 巩固卷也作答（全对，验证对比显示）
    answers2 = {}
    for it in c_items:
        letters = re.findall(r'[A-F]', (it['answer_text'] or '').upper())
        answers2[f"q{it['seq']}"] = letters[0] if letters else (it['answer_text'] or '')
    conn.close()
    st, url, _ = post(f'/quiz/{csid}/submit', answers2)
    st, html = get(f'/quiz/{csid}/report')
    print(f'  巩固卷报告 HTTP {st}; 有对比提示: {"本次是巩固卷" in html}')

    st, html = get(f'/quiz/{sid}/paper?key=1')
    print(f'  打印版 HTTP {st}; 含答案页: {"参考答案与解析" in html}')
    return sid


if __name__ == '__main__':
    subj = sys.argv[1] if len(sys.argv) > 1 else 'math'
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 15
    run(subj, n)
