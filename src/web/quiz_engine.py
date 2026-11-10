#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""学情诊断引擎

流程：建诊断卷 → 在线作答 → 自动判分 → 按知识点/题型统计掌握度 → 针对薄弱点组巩固卷

关键设计：
- 只抽「可自动判分」的题（单选选择题 / 短答案填空题），保证诊断结论客观
- 材料题组（一行里含 2~3 个小题，答案形如 "B、C"）自动拆成独立小题，共享材料作为背景
- 诊断单元优先用知识点；无知识点关联时退化为题型单元，保证各科都有题可测
- 答题记录保存题目快照（题干/选项/答案/解析），试卷不随题库变化而变
"""
import json
import random
import re
import sqlite3
import uuid
from datetime import datetime

OPT_LABELS = ('A', 'B', 'C', 'D', 'E', 'F')
OPT_SPLIT_RE = re.compile(r'(?<![\w])' + r'([A-F])' + r'\s*[.．、:：]\s*')
SUB_RE = re.compile(r'(?<![0-9])([1-9]\d?)\s*[.．]\s*(?:[（(][^）)]{0,10}[）)])?\s*')
QNUM_RE = re.compile(r'^\s*\d{1,2}\s*[.．、]\s*(?:[（(][^）)]{0,10}[）)])?\s*')

# ==================== 年级 / 学期 ====================
GRADE_LABELS = {1: '一年级', 2: '二年级', 3: '三年级', 4: '四年级', 5: '五年级', 6: '六年级',
                7: '初一', 8: '初二', 9: '初三', 10: '高一', 11: '高二', 12: '高三'}
STAGE_OF_GRADE = {}
for _g in range(1, 7):
    STAGE_OF_GRADE[_g] = 'primary'
for _g in range(7, 10):
    STAGE_OF_GRADE[_g] = 'junior'
for _g in range(10, 13):
    STAGE_OF_GRADE[_g] = 'senior'

STAGE_LABELS = {'primary': '小学', 'junior': '初中', 'senior': '高中'}
SEMESTERS = ('上', '下')
GRADE_GROUPS = [
    ('primary', '小学', [1, 2, 3, 4, 5, 6]),
    ('senior', '高中', [10, 11, 12]),
]


def grade_label(grade):
    try:
        return GRADE_LABELS.get(int(grade), '')
    except (TypeError, ValueError):
        return ''


def stage_of_grade(grade):
    try:
        return STAGE_OF_GRADE.get(int(grade), '')
    except (TypeError, ValueError):
        return ''


def scope_text(grade, semester):
    """'三年级上学期'"""
    g = grade_label(grade)
    if not g:
        return ''
    return g + (f'{semester}学期' if semester else '')


def in_scope(q_grade, q_semester, grade, semester):
    """题目是否落在学生已学范围内：低年级全收；同年级只收本学期及之前"""
    if not grade or q_grade is None:
        return True
    try:
        qg, g = int(q_grade), int(grade)
    except (TypeError, ValueError):
        return True
    if qg < g:
        return True
    if qg > g:
        return False
    if not semester or not q_semester:
        return True
    return str(q_semester) <= str(semester)


# 语文选择题按题干特征细分到语言运用类知识点（知识点名不会直接出现在题干中）
CHN_MCQ_RULES = [
    ('CHN-LANG-102', ['语病', '有语病', '修改病句', '没有语病']),
    ('CHN-LANG-101', ['成语', '加点词语', '加点成语', '加点字']),
    ('CHN-LANG-103', ['衔接', '填入横线', '填入文中', '语意连贯', '排序']),
    ('CHN-LANG-009', ['修辞', '比喻', '拟人']),
    ('CHN-LANG-004', ['词语', '依次填入']),
]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS quiz_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT UNIQUE NOT NULL,
    subject_id INTEGER NOT NULL,
    kind TEXT DEFAULT 'diagnostic',
    parent_session_id TEXT,
    target_units TEXT,
    title TEXT,
    question_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'ongoing',
    correct_count INTEGER DEFAULT 0,
    accuracy REAL DEFAULT 0,
    duration_sec INTEGER DEFAULT 0,
    student_name TEXT,
    student_class TEXT,
    student_grade INTEGER,
    student_semester TEXT,
    stage TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    submitted_at TEXT
);
CREATE TABLE IF NOT EXISTS quiz_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    item_uid TEXT,
    unit_key TEXT,
    unit_name TEXT,
    kp_id INTEGER,
    q_code TEXT,
    question_type TEXT,
    grading TEXT,
    stem TEXT,
    options_json TEXT,
    answer_text TEXT,
    analysis_text TEXT,
    year TEXT,
    region TEXT,
    grade INTEGER,
    semester TEXT,
    user_answer TEXT,
    is_correct INTEGER,
    answered_at TEXT
);
CREATE TABLE IF NOT EXISTS kp_mastery (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_key TEXT UNIQUE NOT NULL,
    unit_name TEXT,
    kp_id INTEGER,
    subject_id INTEGER NOT NULL,
    attempts INTEGER DEFAULT 0,
    correct INTEGER DEFAULT 0,
    accuracy REAL DEFAULT 0,
    level TEXT DEFAULT 'unknown',
    last_tested_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_quiz_items_session ON quiz_items(session_id);
CREATE INDEX IF NOT EXISTS idx_quiz_sessions_subject ON quiz_sessions(subject_id);
"""


def ensure_schema(conn):
    conn.executescript(SCHEMA_SQL)
    for stmt in (
        'ALTER TABLE quiz_sessions ADD COLUMN duration_sec INTEGER DEFAULT 0',
        'ALTER TABLE quiz_sessions ADD COLUMN student_name TEXT',
        'ALTER TABLE quiz_sessions ADD COLUMN student_class TEXT',
        'ALTER TABLE quiz_sessions ADD COLUMN student_grade INTEGER',
        'ALTER TABLE quiz_sessions ADD COLUMN student_semester TEXT',
        'ALTER TABLE quiz_sessions ADD COLUMN stage TEXT',
        'ALTER TABLE quiz_items ADD COLUMN grade INTEGER',
        'ALTER TABLE quiz_items ADD COLUMN semester TEXT',
    ):
        try:
            conn.execute(stmt)
        except Exception:
            pass
    conn.commit()


# ==================== 文本解析 ====================
def _label_positions(text):
    """定位 A./B./C./D. 标签（按字母顺序递进）"""
    positions, search_from = [], 0
    for lab in OPT_LABELS[:4]:
        m = re.compile(r'(?<![\w])' + lab + r'\s*[.．、:：]\s*').search(text, search_from)
        if not m:
            break
        positions.append((lab, m.start(), m.end()))
        search_from = m.end()
    return positions


def split_mcq(text, options_json=None):
    """拆出 (题干, {A:..})；options_json 完整时优先，并剔除题干里内嵌的选项段"""
    if options_json:
        try:
            raw = json.loads(options_json)
            if isinstance(raw, dict):
                opts = {k.strip().upper()[:1]: str(v).strip() for k, v in raw.items()
                        if k and str(v).strip()}
                if len(opts) >= 3 and all(k in OPT_LABELS for k in opts):
                    stem = (text or '').strip()
                    pos = _label_positions(stem)
                    if len(pos) >= 3:  # 题干里同时内嵌了选项文本 → 截断
                        stem = stem[:pos[0][1]].strip()
                    return stem, opts
        except Exception:
            pass
    if not text:
        return text, {}
    positions = _label_positions(text)
    if len(positions) < 3:
        return text.strip(), {}
    stem = text[:positions[0][1]].strip()
    opts = {}
    for i, (lab, s, e) in enumerate(positions):
        end = positions[i + 1][1] if i + 1 < len(positions) else len(text)
        val = text[e:end].strip(' \n\t　，,；;')
        if val:
            opts[lab] = val
    if len(opts) < 3:
        return text.strip(), {}
    return stem, opts


def _longest_run(matches):
    """取编号严格递增 1 的最长连续片段"""
    best, cur = [], []
    for m in matches:
        n = int(m.group(1))
        if not cur:
            cur = [m]
        elif n == int(cur[-1].group(1)) + 1:
            cur.append(m)
        else:
            if len(cur) > len(best):
                best = cur
            cur = [m]
    if len(cur) > len(best):
        best = cur
    return best


def split_question_group(text, answer_text):
    """把「材料 + 多个小题」拆成独立小题列表 [(stem, options, answer_letter)]"""
    if not text:
        return []
    runs = _longest_run(list(SUB_RE.finditer(text)))
    if len(runs) < 2:
        return []
    letters = re.findall(r'[A-F]', (answer_text or '').upper())
    if len(letters) != len(runs):
        return []
    material = text[:runs[0].start()].strip()
    material = re.sub(r'^[一二三四五六七八九十]+[、.．]\s*[^\n]{0,30}\n', '', material).strip()
    if len(material) > 400:
        material = material[:400] + '…'
    out = []
    for i, m in enumerate(runs):
        start = m.start()
        end = runs[i + 1].start() if i + 1 < len(runs) else len(text)
        seg = text[start:end].strip()
        stem, opts = split_mcq(seg)
        if len(opts) < 3:
            return []
        stem = QNUM_RE.sub('', stem).strip()
        if material:
            stem = f'【材料】{material}\n\n{stem}'
        out.append((stem, opts, letters[i]))
    return out


def normalize_text(s):
    if not s:
        return ''
    out = []
    for ch in s:
        code = ord(ch)
        if code == 0x3000:
            code = 32
        elif 0xFF01 <= code <= 0xFF5E:
            code -= 0xFEE0
        out.append(chr(code).lower())
    t = ''.join(out)
    return re.sub(r'[\s，,。.；;：:、\"\'“”‘’()（）【】\[\]<>《》…—\-_*/\\|]+', '', t)


def answer_letters(ans):
    return set(re.findall(r'[A-F]', (ans or '').upper()))


def grade_item(item, user_answer):
    """返回 (是否得分, 参考答案)"""
    ans = (item.get('answer_text') or '').strip()
    ua = (user_answer or '').strip()
    if not ua:
        return 0, ans
    if item.get('grading') == 'choice':
        correct = answer_letters(ans)
        if not correct:
            return 0, ans
        return int(correct == answer_letters(ua)), ans
    ua_n = normalize_text(ua)
    if not ua_n:
        return 0, ans
    for v in re.split(r'[；;/\n]|或者|或', ans):
        vn = normalize_text(v)
        if vn and (vn == ua_n or (len(vn) >= 2 and vn in ua_n)):
            return 1, ans
    return 0, ans


# ==================== 题池 ====================
def _best_kp_map(conn, subject_id):
    rows = conn.execute('''
        SELECT r.question_id, kp.id, kp.kp_id, kp.kp_name, kp.parent_kp_id, kp.kp_level
        FROM question_kp_relations r
        JOIN questions q ON q.id = r.question_id
        JOIN knowledge_points kp ON kp.id = r.kp_id
        WHERE q.subject_id = ?
        ORDER BY r.question_id,
                 CASE r.relation_type WHEN 'main' THEN 0 ELSE 1 END,
                 r.relevance_score DESC
    ''', (subject_id,)).fetchall()
    best = {}
    for r in rows:
        best.setdefault(r['question_id'], dict(r))
    return best


def _classify_chinese(text):
    for kp_code, kws in CHN_MCQ_RULES:
        if any(k in text for k in kws):
            return kp_code
    return None


def build_units(conn, subject_id, exclude_uids=None, grade=None, semester=None):
    """构建诊断单元: {unit_key: {key,name,kp_id,parent_kp_id,kind,grade,questions:[...]}}

    grade/semester 给定时只保留学生已学过的题（低年级 + 本年级本学期及以前）
    """
    exclude_uids = set(exclude_uids or [])
    kp_map = _best_kp_map(conn, subject_id)
    kp_by_code = {r['kp_id']: dict(r) for r in conn.execute(
        'SELECT id, kp_id, kp_name, parent_kp_id, kp_level FROM knowledge_points WHERE subject_id=?',
        (subject_id,))}

    rows = conn.execute('''
        SELECT q.id, q.question_id, q.question_text, q.options_json, q.answer_text,
               q.analysis_text, q.question_type, q.difficulty_level,
               q.grade, q.semester,
               p.year, p.region
        FROM questions q LEFT JOIN papers p ON p.id = q.paper_id
        WHERE q.subject_id=? AND q.question_type IN ('选择题','填空题')
          AND q.answer_text IS NOT NULL AND TRIM(q.answer_text)<>''
    ''', (subject_id,)).fetchall()

    units = {}

    def push(item, kp):
        if item['uid'] in exclude_uids:
            return
        if not in_scope(item.get('grade'), item.get('semester'), grade, semester):
            return
        if kp is None and item['question_type'] == '选择题':
            code = _classify_chinese(item.get('raw_text') or '')
            if code and code in kp_by_code:
                kp = kp_by_code[code]
        if kp:
            key = 'KP:' + kp['kp_id']
            u = units.setdefault(key, {'key': key, 'name': kp['kp_name'], 'kp_id': kp['id'],
                                       'kp_code': kp['kp_id'], 'parent_kp_id': kp['parent_kp_id'],
                                       'kind': 'kp', 'grade': item.get('grade'),
                                       'questions': []})
            item['kp_id'] = kp['id']
        else:
            key = 'TYPE:' + item['question_type']
            u = units.setdefault(key, {'key': key, 'name': item['question_type'], 'kp_id': None,
                                       'kp_code': None, 'parent_kp_id': None,
                                       'kind': 'type', 'grade': item.get('grade'),
                                       'questions': []})
            item['kp_id'] = None
        u['questions'].append(item)

    for r in rows:
        base = dict(r)
        qtype = base['question_type']
        if qtype == '选择题':
            stem, opts = split_mcq(base['question_text'], base['options_json'])
            letters = re.findall(r'[A-F]', (base['answer_text'] or '').upper())
            if len(opts) >= 3 and len(set(letters)) == 1:
                item = {
                    'id': base['id'], 'uid': str(base['id']), 'q_code': base['question_id'],
                    'question_type': qtype, 'grading': 'choice', 'stem': QNUM_RE.sub('', stem).strip(),
                    'options': opts, 'answer_text': base['answer_text'].strip(),
                    'analysis_text': base['analysis_text'], 'year': base['year'],
                    'region': base['region'], 'grade': base['grade'],
                    'semester': base['semester'], 'raw_text': base['question_text'],
                }
                push(item, kp_map.get(base['id']))
                continue
            # 材料题组 → 拆小题
            for i, (stem_i, opts_i, letter) in enumerate(
                    split_question_group(base['question_text'], base['answer_text']), 1):
                item = {
                    'id': base['id'], 'uid': f"{base['id']}#{i}", 'q_code': f"{base['question_id']}-{i}",
                    'question_type': qtype, 'grading': 'choice', 'stem': stem_i,
                    'options': opts_i, 'answer_text': letter,
                    'analysis_text': base['analysis_text'], 'year': base['year'],
                    'region': base['region'], 'grade': base['grade'],
                    'semester': base['semester'], 'raw_text': base['question_text'],
                }
                push(item, kp_map.get(base['id']))
        else:  # 填空题：仅短答案可自动判分
            ans = (base['answer_text'] or '').strip()
            if not ans or len(ans) > 40 or '\n' in ans:
                continue
            item = {
                'id': base['id'], 'uid': str(base['id']), 'q_code': base['question_id'],
                'question_type': qtype, 'grading': 'fill',
                'stem': QNUM_RE.sub('', (base['question_text'] or '').strip()).strip(),
                'options': {}, 'answer_text': ans, 'analysis_text': base['analysis_text'],
                'year': base['year'], 'region': base['region'],
                'grade': base['grade'], 'semester': base['semester'],
            }
            push(item, kp_map.get(base['id']))
    return units


def sample(conn, subject_id, count=20, exclude_uids=None, rng=None, grade=None, semester=None):
    """分层抽样：先每个单元各出 1 题，再轮转补足，保证覆盖面优先

    给定 grade 时按年级加权：本年级权重 3、上一个年级 2、更早 1，
    既保证不抽到没学过的高年级内容，又让练习重心落在当前年级。
    """
    rng = rng or random.Random()
    units = build_units(conn, subject_id, exclude_uids, grade=grade, semester=semester)
    if not units:
        return []
    for u in units.values():
        rng.shuffle(u['questions'])

    def weight(k):
        g = units[k].get('grade')
        if not grade or g is None:
            return 2
        try:
            g, cur = int(g), int(grade)
        except (TypeError, ValueError):
            return 2
        if g == cur:
            return 3
        if g == cur - 1:
            return 2
        return 1

    keys = sorted(units.keys(), key=lambda k: (-(int(units[k].get('grade') or 0)),
                                               -len(units[k]['questions'])))
    buckets = {}
    for k in keys:
        mod = units[k]['parent_kp_id'] or units[k]['key']
        buckets.setdefault(mod, []).append(k)
    bucket_list = list(buckets.values())
    order = []
    while any(bucket_list):
        for b in bucket_list:
            if b:
                order.append(b.pop(0))

    # 按权重展开成队列：第一轮所有单元，第二轮只放权重大于 1 的……
    queue = []
    for r in range(3):
        for k in order:
            if weight(k) > r:
                queue.append(k)

    chosen, idx, used = [], {k: 0 for k in units}, set()
    # 单元出题上限：单元少时放宽；本年级的题量按「本年级单元数」均摊，既保证重心又保证覆盖
    per_unit_cap = max(3, -(-count // max(1, len(units))) + 2)
    n_cur = sum(1 for k in units if grade and str(units[k].get('grade')) == str(grade))
    cur_cap = max(per_unit_cap, -(-int(count * 0.6) // max(1, n_cur)) + 1)

    def cap_for(k):
        if grade and str(units[k].get('grade')) == str(grade):
            return cur_cap
        return per_unit_cap

    def take_from(k):
        if sum(1 for x in chosen if x.get('unit_key') == k) >= cap_for(k):
            return False
        qs, i = units[k]['questions'], idx[k]
        while i < len(qs) and (qs[i]['uid'] in used):
            i += 1
        idx[k] = i
        if i >= len(qs):
            return False
        q = dict(qs[i])
        q['unit_key'], q['unit_name'] = units[k]['key'], units[k]['name']
        chosen.append(q)
        used.add(q['uid'])
        idx[k] = i + 1
        return True

    while len(chosen) < count:
        if not any(take_from(k) for k in queue):
            break
    return chosen


# ==================== 会话 ====================
def create_session(conn, subject_id, questions, kind='diagnostic',
                   parent_session_id=None, target_units=None, title=None, student=None):
    ensure_schema(conn)
    student = student or {}
    sid = uuid.uuid4().hex[:12]
    conn.execute('''INSERT INTO quiz_sessions
        (session_id, subject_id, kind, parent_session_id, target_units, title, question_count,
         status, student_name, student_class, student_grade, student_semester, stage)
        VALUES (?,?,?,?,?,?,?, 'ongoing', ?,?,?,?,?)''',
                 (sid, subject_id, kind, parent_session_id,
                  json.dumps(target_units or [], ensure_ascii=False),
                  title or ('诊断测评' if kind == 'diagnostic' else '巩固练习'), len(questions),
                  student.get('name') or None, student.get('class_name') or None,
                  int(student['grade']) if student.get('grade') else None,
                  student.get('semester') or None,
                  stage_of_grade(student.get('grade')) or None))
    for i, q in enumerate(questions, 1):
        conn.execute('''INSERT INTO quiz_items
            (session_id, seq, question_id, item_uid, unit_key, unit_name, kp_id, q_code,
             question_type, grading, stem, options_json, answer_text, analysis_text, year, region,
             grade, semester)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                     (sid, i, q['id'], q.get('uid'), q.get('unit_key'), q.get('unit_name'),
                      q.get('kp_id'), q.get('q_code'), q.get('question_type'), q.get('grading'),
                      q.get('stem'), json.dumps(q.get('options') or {}, ensure_ascii=False),
                      q.get('answer_text'), q.get('analysis_text'), q.get('year'), q.get('region'),
                      q.get('grade'), q.get('semester')))
    conn.commit()
    return sid


def load_session(conn, session_id):
    s = conn.execute('SELECT * FROM quiz_sessions WHERE session_id=?', (session_id,)).fetchone()
    if not s:
        return None, []
    rows = conn.execute('SELECT * FROM quiz_items WHERE session_id=? ORDER BY seq', (session_id,)).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        try:
            d['options'] = json.loads(d.get('options_json') or '{}')
        except Exception:
            d['options'] = {}
        items.append(d)
    return dict(s), items


def submit_session(conn, session, items, answers, duration_sec=0):
    correct, now = 0, datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for it in items:
        ua = (answers.get(str(it['seq'])) or '').strip()
        ok, _ = grade_item(it, ua)
        correct += ok
        it['user_answer'], it['is_correct'] = ua, ok
        conn.execute('UPDATE quiz_items SET user_answer=?, is_correct=?, answered_at=? WHERE id=?',
                     (ua, ok, now, it['id']))
    total = len(items) or 1
    conn.execute('''UPDATE quiz_sessions SET status='submitted', correct_count=?, accuracy=?,
                    duration_sec=?, submitted_at=? WHERE session_id=?''',
                 (correct, round(correct / total * 100, 1), int(duration_sec or 0), now,
                  session['session_id']))
    conn.commit()
    update_mastery(conn, session['subject_id'], items)
    return correct


def update_mastery(conn, subject_id, items):
    agg = {}
    for it in items:
        if not it.get('unit_key'):
            continue
        a = agg.setdefault(it['unit_key'], {'name': it.get('unit_name'), 'kp_id': it.get('kp_id'),
                                            'n': 0, 'c': 0})
        a['n'] += 1
        a['c'] += 1 if it.get('is_correct') else 0
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for key, a in agg.items():
        row = conn.execute('SELECT attempts, correct FROM kp_mastery WHERE unit_key=?', (key,)).fetchone()
        att = (row['attempts'] if row else 0) + a['n']
        cor = (row['correct'] if row else 0) + a['c']
        acc = round(cor / att * 100, 1)
        level = 'good' if acc >= 80 else ('shaky' if acc >= 60 else 'weak')
        conn.execute('''INSERT INTO kp_mastery (unit_key, unit_name, kp_id, subject_id, attempts, correct, accuracy, level, last_tested_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(unit_key) DO UPDATE SET attempts=?, correct=?, accuracy=?, level=?,
                last_tested_at=?, unit_name=?, kp_id=?''',
                     (key, a['name'], a['kp_id'], subject_id, att, cor, acc, level, now,
                      att, cor, acc, level, now, a['name'], a['kp_id']))
    conn.commit()


# ==================== 诊断分析 ====================
def analyze(conn, session, items, for_report=True):
    per = {}
    for it in items:
        key = it.get('unit_key') or ('TYPE:' + (it.get('question_type') or '未分类'))
        d = per.setdefault(key, {'key': key, 'name': it.get('unit_name') or it.get('question_type'),
                                 'kp_code': None, 'kp_id': it.get('kp_id'),
                                 'kind': 'kp' if it.get('kp_id') else 'type',
                                 'module': None, 'n': 0, 'correct': 0, 'items': []})
        d['n'] += 1
        d['correct'] += 1 if it.get('is_correct') else 0
        d['items'].append(it)

    rows = []
    for d in per.values():
        d['accuracy'] = round(d['correct'] / d['n'] * 100) if d['n'] else 0
        if d['accuracy'] >= 80:
            d['level'], d['label'] = 'good', '掌握扎实'
        elif d['accuracy'] >= 60:
            d['level'], d['label'] = 'shaky', '不够稳固'
        else:
            d['level'], d['label'] = 'weak', '明显薄弱'
        d['low_confidence'] = d['n'] == 1
        if d['kp_id']:
            row = conn.execute('''SELECT kp.kp_id, kp.kp_name, par.kp_name AS parent_name
                FROM knowledge_points kp LEFT JOIN knowledge_points par ON par.id=kp.parent_kp_id
                WHERE kp.id=?''', (d['kp_id'],)).fetchone()
            if row:
                d['kp_code'], d['name'] = row['kp_id'], row['kp_name']
                d['module'] = row['parent_name'] or row['kp_name']
        if not d['module']:
            d['module'] = '题型 · ' + d['name']
        d['wrong'] = [i for i in d['items'] if not i.get('is_correct')]
        rows.append(d)
    rows.sort(key=lambda x: (x['accuracy'], -x['n']))

    modules = {}
    for d in rows:
        m = modules.setdefault(d['module'], {'name': d['module'], 'n': 0, 'correct': 0, 'units': 0})
        m['n'] += d['n']
        m['correct'] += d['correct']
        m['units'] += 1
    module_rows = []
    for m in modules.values():
        m['accuracy'] = round(m['correct'] / m['n'] * 100) if m['n'] else 0
        m['level'] = 'good' if m['accuracy'] >= 80 else ('shaky' if m['accuracy'] >= 60 else 'weak')
        module_rows.append(m)
    module_rows.sort(key=lambda x: x['accuracy'])

    return {
        'rows': rows,
        'weak': [d for d in rows if d['accuracy'] < 60],
        'shaky': [d for d in rows if 60 <= d['accuracy'] < 80],
        'good': [d for d in rows if d['accuracy'] >= 80],
        'modules': module_rows,
        'unit_total': len(rows),
    }


# ==================== 巩固卷 ====================
def build_consolidation(conn, subject_id, parent_session_id, per_unit=2, max_total=30):
    ensure_schema(conn)
    parent, pitems = load_session(conn, parent_session_id)
    if not parent:
        return None, [], []
    report = analyze(conn, parent, pitems)
    used = {it.get('item_uid') for it in pitems if it.get('item_uid')}
    g, sem = parent.get('student_grade'), parent.get('student_semester')
    student = {'name': parent.get('student_name'), 'class_name': parent.get('student_class'),
               'grade': g, 'semester': sem}
    units = build_units(conn, subject_id, exclude_uids=used, grade=g, semester=sem)

    targets = (report['weak'] + report['shaky'])[:8]
    if not targets:
        targets = report['rows'][:2]

    picked, seen = [], set()

    def take(key, limit):
        u = units.get(key)
        if not u:
            return 0
        cnt = 0
        for q in u['questions']:
            if cnt >= limit or len(picked) >= max_total:
                break
            if q['uid'] in seen:
                continue
            q = dict(q)
            q['unit_key'], q['unit_name'] = u['key'], u['name']
            picked.append(q)
            seen.add(q['uid'])
            cnt += 1
        return cnt

    for t in targets:
        take(t['key'], per_unit)

    # 题目不足时，用同模块兄弟单元补足
    if len(picked) < per_unit * len(targets):
        want = per_unit * len(targets)
        for t in targets:
            for key, u in units.items():
                if len(picked) >= want:
                    break
                if key in {x['key'] for x in targets}:
                    continue
                mod = module_name(conn, u)
                if mod == t['module']:
                    take(key, 1)

    # 仍不足则允许重复出现过的单元（不排除已做题）
    if not picked:
        units2 = build_units(conn, subject_id, grade=g, semester=sem)
        for t in targets:
            u = units2.get(t['key'])
            if u:
                for q in u['questions'][:per_unit]:
                    q = dict(q)
                    q['unit_key'], q['unit_name'] = u['key'], u['name']
                    picked.append(q)
                if picked:
                    break

    names = [t['name'] for t in targets]
    title = '巩固卷 · ' + '、'.join(names[:3]) + (' 等' if len(names) > 3 else '')
    sid = create_session(conn, subject_id, picked, kind='consolidate',
                         parent_session_id=parent_session_id,
                         target_units=[t['key'] for t in targets], title=title,
                         student=student)
    return sid, picked, targets


def module_name(conn, unit):
    if unit.get('parent_kp_id'):
        r = conn.execute('SELECT kp_name FROM knowledge_points WHERE id=?', (unit['parent_kp_id'],)).fetchone()
        if r:
            return r[0]
    return unit['name']


def subject_readiness(conn, subject_id, grade=None, semester=None):
    units = build_units(conn, subject_id, grade=grade, semester=semester)
    return {'units': len(units), 'questions': sum(len(u['questions']) for u in units.values())}


def recent_sessions(conn, subject_id=None, limit=12, status='submitted'):
    sql = '''SELECT s.*, sub.subject_name, sub.subject_code FROM quiz_sessions s
             JOIN subjects sub ON sub.id=s.subject_id WHERE 1=1 '''
    args = []
    if status:
        sql += ' AND s.status=? '
        args.append(status)
    if subject_id:
        sql += ' AND s.subject_id=? '
        args.append(subject_id)
    sql += ' ORDER BY s.id DESC LIMIT ?'
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args).fetchall()]
