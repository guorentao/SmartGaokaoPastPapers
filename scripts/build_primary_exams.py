#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成小学期中 / 期末测试卷（data/primary/exams/*.json + 程序出题）

为什么计算题由程序生成：小学计算题答案唯一、可校验，按各册课标范围出题
（20以内加减法、表内乘法、小数乘除、分数加减……）能保证答案不出错。
随机种子取自卷号，同一份数据每次重建结果完全一致。

数据文件只写「非计算题」部分，计算题由本脚本按 calc 字段自动加在最前面。
可重复执行：先清除该学科已有的期中/期末卷，再重建，不影响高考与单元练习数据。
"""
import glob
import hashlib
import json
import os
import random
import sqlite3
from decimal import Decimal
from fractions import Fraction

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, 'database', 'gaokao.db')
SRC = os.path.join(ROOT, 'data', 'primary', 'exams')


# ================================================================ 计算题生成
def _rng(seed):
    return random.Random(int(hashlib.md5(seed.encode('utf-8')).hexdigest()[:8], 16))


def _dec(num, digits):
    """整数 num 按 digits 位小数显示，去掉末尾多余的 0"""
    return format((Decimal(num).scaleb(-digits)).normalize(), 'f')


def _frac(fr):
    fr = Fraction(fr)
    return str(fr.numerator) if fr.denominator == 1 else f"{fr.numerator}/{fr.denominator}"


def _layout(pairs, per_line=3):
    """把算式排版成每行 per_line 个"""
    lines, line = [], []
    for expr, _ in pairs:
        line.append(f"{expr}=")
        if len(line) == per_line:
            lines.append('　　'.join(line))
            line = []
    if line:
        lines.append('　　'.join(line))
    return '\n'.join(lines)


def _answers(pairs):
    return '　　'.join(f"{e}={a}" for e, a in pairs)


def _quick(kind, r):
    """直接写得数：6 小题"""
    out = []
    if kind == 'g1a':                     # 一年级上：20 以内加减
        for _ in range(6):
            if r.random() < 0.5:
                b = r.randint(1, 9)
                a = r.randint(1, 20 - b)
                out.append((f"{a}+{b}", str(a + b)))
            else:
                a = r.randint(11, 20)
                b = r.randint(1, 9)
                out.append((f"{a}-{b}", str(a - b)))
    elif kind == 'g1b':                   # 一年级下：100 以内加减
        for _ in range(6):
            if r.random() < 0.5:
                a, b = r.randint(20, 78), r.randint(2, 20)
                out.append((f"{a}+{b}", str(a + b)))
            else:
                a, b = r.randint(30, 99), r.randint(2, 28)
                out.append((f"{a}-{b}", str(a - b)))
    elif kind == 'g2a':                   # 二年级上：表内乘法
        for _ in range(6):
            a, b = r.randint(2, 9), r.randint(2, 9)
            out.append((f"{a}×{b}", str(a * b)))
    elif kind == 'g2b':                   # 二年级下：表内除法
        for _ in range(6):
            b, q = r.randint(2, 9), r.randint(2, 9)
            out.append((f"{b * q}÷{b}", str(q)))
    elif kind == 'g3a':                   # 三年级上：三位数加减
        for _ in range(4):
            a, b = r.randint(120, 880), r.randint(20, 199)
            if r.random() < 0.5:
                out.append((f"{a}+{b}", str(a + b)))
            else:
                hi, lo = max(a, b), min(a, b)
                out.append((f"{hi}-{lo}", str(hi - lo)))
    elif kind == 'g3b':                   # 三年级下：两位数乘一位数
        for _ in range(4):
            a, b = r.randint(12, 49), r.randint(3, 9)
            out.append((f"{a}×{b}", str(a * b)))
    elif kind == 'g4a':                   # 四年级上：三位数乘两位数
        for _ in range(4):
            a, b = r.randint(102, 499), r.randint(11, 49)
            out.append((f"{a}×{b}", str(a * b)))
    elif kind == 'g4b':                   # 四年级下：小数加减
        for _ in range(4):
            a, b = r.randint(10, 99), r.randint(1, 99)
            out.append((f"{_dec(a, 1)}+{_dec(b, 2)}", _dec(a * 10 + b, 2)))
    elif kind == 'g5a':                   # 五年级上：小数乘整十/整数
        for _ in range(4):
            a, b = r.randint(2, 99), r.randint(2, 9)
            if r.random() < 0.5:
                out.append((f"{_dec(a, 1)}×{b}", _dec(a * b, 1)))
            else:
                out.append((f"{_dec(a * b, 1)}÷{b}", _dec(a, 1)))
    elif kind == 'g5b':                   # 五年级下：分数加减
        for _ in range(4):
            d = r.choice([4, 6, 8, 10, 12])
            x, y = Fraction(r.randint(1, d - 1), d), Fraction(r.randint(1, d - 1), d)
            if r.random() < 0.6:
                out.append((f"{_frac(x)}+{_frac(y)}", _frac(x + y)))
            else:
                hi, lo = max(x, y), min(x, y)
                out.append((f"{_frac(hi)}-{_frac(lo)}", _frac(hi - lo)))
    elif kind == 'g6a':                   # 六年级上：分数乘除
        for _ in range(4):
            a = Fraction(r.randint(1, 8), r.randint(2, 9))
            b = Fraction(r.randint(1, 7), r.randint(2, 9))
            if r.random() < 0.5:
                out.append((f"{_frac(a)}×{_frac(b)}", _frac(a * b)))
            else:
                out.append((f"{_frac(a * b)}÷{_frac(b)}", _frac(a)))
    elif kind == 'g6b':                   # 六年级下：百分数
        for _ in range(4):
            p = r.choice([10, 20, 25, 40, 50, 75, 80])
            n = r.choice([40, 60, 80, 100, 200])
            out.append((f"{n}×{p}%", _dec(n * p, 2)))
    return out


def gen_calc(kind, seed):
    """返回 3 道计算题：直接写得数 / 竖式计算 / 脱式计算"""
    r = _rng(seed + kind)
    quick, vert, step = _quick(kind, r), [], []

    # ---------------- 竖式
    if kind == 'g1a':
        for _ in range(3):
            a = r.randint(11, 19)
            b = r.randint(1, a - 1)
            vert.append((f"{a}-{b}", str(a - b)))
    elif kind == 'g1b':
        for _ in range(3):
            a, b = r.randint(25, 89), r.randint(11, 60)
            if r.random() < 0.5:
                vert.append((f"{a}+{b}", str(a + b)))
            else:
                hi, lo = max(a, b), min(a, b)
                vert.append((f"{hi}-{lo}", str(hi - lo)))
    elif kind == 'g2a':
        for _ in range(2):
            a, b = r.randint(12, 49), r.randint(2, 9)
            vert.append((f"{a}×{b}", str(a * b)))
    elif kind == 'g2b':
        for _ in range(3):
            b, q, rem = r.randint(3, 9), r.randint(2, 9), r.randint(1, 4)
            rem = min(rem, b - 1)
            vert.append((f"{b * q + rem}÷{b}", f"{q} 余 {rem}"))
    elif kind == 'g3a':
        for _ in range(3):
            a, b = r.randint(102, 399), r.randint(3, 9)
            vert.append((f"{a}×{b}", str(a * b)))
    elif kind == 'g3b':
        for _ in range(3):
            b, q = r.randint(3, 9), r.randint(11, 99)
            vert.append((f"{b * q}÷{b}", str(q)))
    elif kind == 'g4a':
        for _ in range(3):
            b, q = r.randint(12, 39), r.randint(12, 39)
            vert.append((f"{b * q}÷{b}", str(q)))
    elif kind == 'g4b':
        for _ in range(3):
            a, b = r.randint(200, 900), r.randint(10, 190)
            hi, lo = max(a, b), min(a, b)
            vert.append((f"{_dec(hi, 2)}-{_dec(lo, 2)}", _dec(hi - lo, 2)))
    elif kind == 'g5a':
        for _ in range(3):
            a, b = r.randint(100, 999), r.randint(2, 9)
            vert.append((f"{_dec(a, 2)}×{b}", _dec(a * b, 2)))
    elif kind == 'g6b':
        for _ in range(2):
            p, n = r.choice([15, 25, 35, 45]), r.choice([80, 120, 200])
            vert.append((f"{n}×{p}%", _dec(n * p, 2)))

    # ---------------- 脱式
    if kind == 'g1a':
        a, b, c = r.randint(9, 15), r.randint(2, 5), r.randint(1, 4)
        step.append((f"{a}-{b}+{c}", str(a - b + c)))
        x, y, z = r.randint(3, 8), r.randint(2, 6), r.randint(1, 5)
        step.append((f"{x}+{y}-{z}", str(x + y - z)))
    elif kind == 'g1b':
        x, y, z = r.randint(30, 60), r.randint(5, 20), r.randint(3, 15)
        step.append((f"{x}+{y}-{z}", str(x + y - z)))
        x, y = r.randint(50, 90), r.randint(10, 30)
        z = min(r.randint(5, 20), 100 - (x - y))
        step.append((f"{x}-{y}+{z}", str(x - y + z)))
    elif kind == 'g2a':
        a, b, c = r.randint(2, 9), r.randint(2, 9), r.randint(3, 20)
        step.append((f"{a}×{b}+{c}", str(a * b + c)))
        a, b, c, d = r.randint(3, 9), r.randint(2, 9), r.randint(2, 6), r.randint(2, 6)
        if a * b > c * d:
            step.append((f"{a}×{b}-{c}×{d}", str(a * b - c * d)))
        else:
            step.append((f"{a}×{b}+{c}×{d}", str(a * b + c * d)))
    elif kind == 'g2b':
        a, b, c = r.randint(2, 9), r.randint(2, 9), r.randint(2, 9)
        step.append((f"{a * b}÷{b}×{c}", str(a * c)))
        x, y, z = r.randint(20, 60), r.randint(2, 9), r.randint(2, 9)
        step.append((f"{x}+{y}×{z}", str(x + y * z)))
    elif kind == 'g3a':
        a, b, c = r.randint(20, 60), r.randint(3, 9), r.randint(2, 20)
        step.append((f"{a}×{b}+{c}", str(a * b + c)))
        a, b, c = r.randint(100, 400), r.randint(2, 9), r.randint(1, 60)
        if a > b * c:
            step.append((f"{a}-{b}×{c}", str(a - b * c)))
        else:
            step.append((f"{a}+{b}×{c}", str(a + b * c)))
    elif kind in ('g3b', 'g4a'):
        a, b, c = r.randint(12, 40), r.randint(3, 9), r.randint(10, 60)
        step.append((f"{a}×{b}+{c}", str(a * b + c)))
        b, q, m = r.randint(3, 9), r.randint(11, 40), r.randint(2, 9)
        step.append((f"{b * q}÷{b}×{m}", str(q * m)))
    elif kind == 'g4b':
        a, b, c = r.randint(10, 99), r.randint(2, 9), r.randint(10, 50)
        step.append((f"{_dec(a, 1)}+{_dec(c, 1)}×{b}", _dec(a + c * b, 1)))
        x, y, z = r.randint(200, 500), r.randint(10, 90), r.randint(5, 40)
        step.append((f"{_dec(x, 1)}-{_dec(y, 1)}+{_dec(z, 1)}", _dec(x - y + z, 1)))
    elif kind == 'g5a':
        a, b, c = r.randint(10, 99), r.randint(2, 9), r.randint(10, 60)
        step.append((f"{_dec(a, 1)}×{b}+{_dec(c, 1)}", _dec(a * b + c, 1)))
        x, y = r.randint(100, 900), r.choice([2, 4, 5, 8])
        step.append((f"{_dec(x, 2)}÷{y}×10",
                     format((Decimal(x) / 100 / y * 10).normalize(), 'f')))
    elif kind == 'g5b':
        a = Fraction(r.randint(1, 5), 6)
        b = Fraction(r.randint(1, 3), 4)
        step.append((f"{_frac(a)}+{_frac(b)}-1/4", _frac(a + b - Fraction(1, 4))))
        step.append((f"1-{_frac(a)}-1/6", _frac(1 - a - Fraction(1, 6))))
    elif kind == 'g6a':
        a = Fraction(r.randint(1, 4), 5)
        b = Fraction(r.randint(1, 3), 4)
        step.append((f"{_frac(a)}×{_frac(b)}÷{_frac(a)}", _frac(b)))
        step.append((f"({_frac(a)}+{_frac(b)})×20", _frac((a + b) * 20)))
    elif kind == 'g6b':
        step.append(("把 3∶5 化成最简整数比", "3∶5"))
        step.append(("求 24 和 36 的最大公因数", "12"))

    items = []
    if quick:
        items.append({'type': '计算题', 'section': '一、直接写出得数', 'score': 10,
                      'stem': _layout(quick), 'answer': _answers(quick),
                      'analysis': '口算题：注意进位、退位，小数计算先对齐小数点再算。'})
    if vert:
        items.append({'type': '计算题', 'section': '二、用竖式计算', 'score': 12,
                      'stem': '\n'.join(f"{e}=" for e, _ in vert), 'answer': _answers(vert),
                      'analysis': '竖式要对齐数位；有余数的除法余数必须小于除数。'})
    if step:
        items.append({'type': '计算题', 'section': '三、脱式计算（能简算的要简算）', 'score': 12,
                      'stem': '\n'.join(e for e, _ in step), 'answer': _answers(step),
                      'analysis': '先乘除后加减，有括号先算括号内；能约分的先约分。'})
    return items


# ================================================================ 入库
def load_grouped():
    """同一学科可能分散在多个文件（如语文分 1-3 年级、4-6 年级），先合并再入库"""
    groups = {}
    for path in sorted(glob.glob(os.path.join(SRC, '*.json'))):
        data = json.load(open(path, encoding='utf-8'))
        code = data.get('subject_code')
        if not code:
            continue
        g = groups.setdefault(code, {'exams': [], 'files': []})
        g['exams'].extend(data.get('exams', []))
        g['files'].append(os.path.basename(path))
    return groups


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    for code, group in load_grouped().items():
        sub = conn.execute('SELECT id, subject_name FROM subjects WHERE subject_code=?', (code,)).fetchone()
        if not sub:
            print(f"  ! 学科 {code} 不存在，请先运行 import_primary.py")
            continue
        subject_id = sub['id']

        # 按学科整体清理（不按文件，避免同科多文件互相覆盖）
        old = [r['id'] for r in conn.execute(
            "SELECT id FROM papers WHERE subject_id=? AND paper_type IN ('期中','期末')", (subject_id,))]
        if old:
            marks = ','.join('?' * len(old))
            qids = [r['id'] for r in conn.execute(
                f'SELECT id FROM questions WHERE paper_id IN ({marks})', old)]
            if qids:
                m2 = ','.join('?' * len(qids))
                conn.execute(f'DELETE FROM question_kp_relations WHERE question_id IN ({m2})', qids)
                conn.execute(f'DELETE FROM questions WHERE id IN ({m2})', qids)
            conn.execute(f'DELETE FROM papers WHERE id IN ({marks})', old)

        kp_by_code = {r['kp_id']: r['id'] for r in conn.execute(
            'SELECT id, kp_id FROM knowledge_points WHERE subject_id=?', (subject_id,))}
        source = ','.join(group['files'])

        n_paper = n_q = 0
        for exam in group['exams']:
            cur = conn.execute('''INSERT INTO papers
                (paper_id, subject_id, paper_title, year, region, paper_type, paper_version,
                 total_score, total_questions, duration, source_file, file_type, parsed_status, parsed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,CURRENT_TIMESTAMP)''',
                               (exam['paper_id'], subject_id, exam['title'], exam.get('year'),
                                f"{exam['grade']}年级{exam['semester']}册",
                                exam.get('exam_type', '期末'),
                                exam.get('version') or f"{exam.get('exam_type', '期末')}测试卷（含答案）",
                                exam.get('score', 100), 0, exam.get('duration', 60),
                                source, 'json'))
            pid = cur.lastrowid

            items = []
            if exam.get('calc'):
                items += gen_calc(exam['calc'], exam['paper_id'])
            items += exam.get('questions', [])

            for i, q in enumerate(items, 1):
                opts = {chr(65 + k): t for k, t in enumerate(q.get('options') or [])}
                qid = f"{exam['paper_id']}-{i:02d}"
                h = hashlib.md5((q['stem'] + json.dumps(opts, ensure_ascii=False)).encode('utf-8')).hexdigest()
                cur2 = conn.execute('''INSERT INTO questions
                    (question_id, subject_id, paper_id, question_number, question_type,
                     question_section, difficulty_level, score_value, question_text, options_json,
                     answer_text, analysis_text, has_formula, content_hash, source_type, grade, semester)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,?,'original',?,?)''',
                                    (qid, subject_id, pid, str(i), q.get('type', '填空题'),
                                     q.get('section') or '', q.get('difficulty', 2), q.get('score', 5),
                                     q['stem'], json.dumps(opts, ensure_ascii=False) if opts else None,
                                     q.get('answer') or '', q.get('analysis') or '', h,
                                     exam['grade'], exam['semester']))
                if q.get('kp') and q['kp'] in kp_by_code:
                    conn.execute('''INSERT OR IGNORE INTO question_kp_relations
                        (question_id, kp_id, relation_type, relevance_score) VALUES (?,?,'main',1.0)''',
                                 (cur2.lastrowid, kp_by_code[q['kp']]))
                n_q += 1

            conn.execute('UPDATE papers SET total_questions=? WHERE id=?', (len(items), pid))
            n_paper += 1

        conn.commit()
        print(f"{sub['subject_name']}: {n_paper} 卷 / {n_q} 题")

    conn.close()


if __name__ == '__main__':
    main()
