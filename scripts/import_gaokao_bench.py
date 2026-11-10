#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 GAOKAO-Bench 公开数据集（OpenLMLab，2010-2022 高考真题 JSON）
导入题目到 SmartGaokaoPastPapers 数据库，并自动关联知识点。

用法:
  python scripts/import_gaokao_bench.py [--data-dir data/gaokao_bench] [--db database/gaokao.db]
"""

import argparse
import glob
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 文件名 -> (学科code, 题型)
FILE_TYPE_MAP = {
    'Chinese_Lang_and_Usage_MCQs': ('chinese', '选择题'),
    'Chinese_Modern_Lit': ('chinese', '现代文阅读'),
    'Chinese_Language_Ancient_Poetry_Reading': ('chinese', '古诗词阅读'),
    'Chinese_Language_Classical_Chinese_Reading': ('chinese', '文言文阅读'),
    'Chinese_Language_Famous_Passages_and_Sentences_Dictation': ('chinese', '名篇名句默写'),
    'Chinese_Language_Language_and_Writing_Skills_Open-ended_Questions': ('chinese', '语言文字运用'),
    'Chinese_Language_Literary_Text_Reading': ('chinese', '文学类文本阅读'),
    'Chinese_Language_Practical_Text_Reading': ('chinese', '实用类文本阅读'),
    'Math_I_MCQs': ('math', '选择题'),
    'Math_II_MCQs': ('math', '选择题'),
    'Math_I_Fill-in-the-Blank': ('math', '填空题'),
    'Math_II_Fill-in-the-Blank': ('math', '填空题'),
    'Math_I_Open-ended_Questions': ('math', '解答题'),
    'Math_II_Open-ended_Questions': ('math', '解答题'),
    'English_MCQs': ('english', '选择题'),
    'English_Reading_Comp': ('english', '阅读理解'),
    'English_Fill_in_Blanks': ('english', '语法填空'),
    'English_Cloze_Test': ('english', '完形填空'),
    'English_Language_Error_Correction': ('english', '短文改错'),
    'English_Language_Cloze_Passage': ('english', '完形填空'),
    'Physics_MCQs': ('physics', '选择题'),
    'Physics_Open-ended_Questions': ('physics', '解答题'),
    'Chemistry_MCQs': ('chemistry', '选择题'),
    'Chemistry_Open-ended_Questions': ('chemistry', '解答题'),
    'Biology_MCQs': ('biology', '选择题'),
    'Biology_Open-ended_Questions': ('biology', '解答题'),
    'Political_Science_MCQs': ('politics', '选择题'),
    'Political_Science_Open-ended_Questions': ('politics', '解答题'),
    'History_MCQs': ('history', '选择题'),
    'History_Open-ended_Questions': ('history', '解答题'),
    'Geography_MCQs': ('geography', '选择题'),
    'Geography_Open-ended_Questions': ('geography', '解答题'),
}

SUBJECT_CODE_MAP = {  # 题目ID前缀
    'chinese': 'CHN', 'math': 'MATH', 'english': 'ENG',
    'physics': 'PHY', 'chemistry': 'CHEM', 'biology': 'BIO',
    'politics': 'POL', 'history': 'HIS', 'geography': 'GEO',
}

# Math_I / Math_II 指定地区后缀
FILE_REGION_SUFFIX = {'Math_I': '新课标Ⅰ卷', 'Math_II': '新课标Ⅱ卷'}


def normalize_region(category: str) -> str:
    """把 GAOKAO-Bench 的 category 归一化为试卷地区名"""
    c = category.strip('（）()').strip()
    if not c:
        return '未标注'
    if '甲' in c:
        return '全国甲卷'
    if '乙' in c:
        return '全国乙卷'
    if '解析' in c:
        return '未标注'
    low = c.lower()
    if 'ⅲ' in c or 'iii' in low:
        return '新课标Ⅲ卷'
    if 'ⅱ' in c or 'ii' in low:
        return '新课标Ⅱ卷'
    if 'ⅰ' in c or 'i' in low:
        return '新课标Ⅰ卷'
    if '新课标' in c:
        return '新课标卷'
    if '全国卷' in c or '全国' in c:
        return '全国卷'
    return c + '卷' if '卷' not in c else c


def region_code(region: str) -> str:
    return {
        '新课标Ⅰ卷': 'XKB1', '新课标Ⅱ卷': 'XKB2', '新课标Ⅲ卷': 'XKB3',
        '全国甲卷': 'JIA', '全国乙卷': 'YI', '新课标卷': 'XKB',
        '全国卷': 'QG', '未标注': 'NA',
    }.get(region, re.sub(r'\W+', '', region)[:6] or 'NA')


def extract_qnum(text: str) -> str:
    m = re.match(r'\s*(\d{1,2})\s*[．.、]', text or '')
    return m.group(1) if m else ''


def extract_score(text: str):
    m = re.search(r'[（(]\s*(\d{1,2})\s*分\s*[)）]', text or '')
    return int(m.group(1)) if m else None


def extract_options(text: str):
    """从题干文本中拆出 A/B/C/D 选项"""
    opts = {}
    pattern = re.compile(r'^([A-F])\s*[．.、]\s*(.+)$')
    for line in (text or '').splitlines():
        m = pattern.match(line.strip())
        if m:
            opts[m.group(1)] = m.group(2).strip()
    return opts or None


def load_kp_names(conn, subject_id):
    rows = conn.execute(
        'SELECT id, kp_name FROM knowledge_points WHERE subject_id = ?', (subject_id,)
    ).fetchall()
    # 长名称优先匹配，避免短名覆盖
    return sorted([(r['id'], r['kp_name']) for r in rows],
                  key=lambda x: len(x[1]), reverse=True)


def match_kps(text, kp_names, max_k=3):
    hits = []
    for kp_id, name in kp_names:
        if name and len(name) >= 2 and name in text:
            hits.append((kp_id, 1.0 if name in text else 0.5))
        if len(hits) >= max_k:
            break
    return hits


def import_all(data_dir, db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')

    subjects = {r['subject_code']: r['id'] for r in
                conn.execute('SELECT id, subject_code FROM subjects')}

    stats = defaultdict(int)
    errors = []
    used_qids = set()

    # 预载每个学科的知识点名
    kp_cache = {sid: load_kp_names(conn, sid) for sid in subjects.values()}

    files = sorted(glob.glob(os.path.join(data_dir, '*.json')))
    for fp in files:
        fname = os.path.splitext(os.path.basename(fp))[0]
        key = fname.replace('2010-2022_', '').replace('2010-2013_', '').replace('2012-2022_', '').replace('2014-2022_', '')
        if key not in FILE_TYPE_MAP:
            errors.append(f'未知文件类型: {fname}')
            continue
        subject_code, qtype = FILE_TYPE_MAP[key]
        subject_id = subjects[subject_code]
        kp_names = kp_cache[subject_id]
        prefix = SUBJECT_CODE_MAP[subject_code]

        data = json.load(open(fp, encoding='utf-8'))
        examples = data.get('example', [])

        # 按 (年份, 地区) 分组建试卷
        papers = defaultdict(list)
        for ex in examples:
            year = str(ex.get('year', '')).strip()
            category = ex.get('category', '')
            region = FILE_REGION_SUFFIX.get(key.split('_')[0] if key.startswith('Math') else key,
                                            normalize_region(category))
            papers[(year, region)].append(ex)

        for (year, region), exs in papers.items():
            rc = region_code(region)
            paper_id_str = f'P-{prefix}-{year}-{rc}'
            row = conn.execute('SELECT id FROM papers WHERE paper_id = ?', (paper_id_str,)).fetchone()
            if row:
                paper_db_id = row['id']
            else:
                cur = conn.execute(
                    '''INSERT INTO papers (paper_id, subject_id, paper_title, year, region,
                       paper_type, paper_version, parsed_status)
                       VALUES (?,?,?,?,?,?,?,2)''',
                    (paper_id_str, subject_id,
                     f'{year}年高考{ {"math":"数学","chinese":"语文","english":"英语","physics":"物理","chemistry":"化学","biology":"生物","politics":"政治","history":"历史","geography":"地理"}[subject_code] }（{region}）',
                     int(year) if year.isdigit() else None, region, qtype, '真题', ))
                paper_db_id = cur.lastrowid
                stats['papers'] += 1

            for i, ex in enumerate(exs):
                text = (ex.get('question') or '').strip()
                if not text:
                    continue
                qnum = extract_qnum(text) or str(i + 1)
                qid = f'P-{prefix}-{year}-{rc}-Q{qnum}'
                # 去重/防冲突（含数据库中已存在的ID）
                base_qid, n = qid, 1
                while (qid in used_qids or
                       conn.execute('SELECT 1 FROM questions WHERE question_id = ?', (qid,)).fetchone()):
                    n += 1
                    qid = f'{base_qid}-{n}'
                content_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
                dup = conn.execute(
                    'SELECT id FROM questions WHERE content_hash = ? AND subject_id = ?',
                    (content_hash, subject_id)).fetchone()
                if dup:
                    stats['duplicates'] += 1
                    continue

                ans = ex.get('answer')
                if isinstance(ans, list):
                    answer_text = '、'.join(str(a) for a in ans)
                else:
                    answer_text = str(ans or '').strip()
                analysis = (ex.get('analysis') or '').strip()

                cur = conn.execute(
                    '''INSERT INTO questions (question_id, subject_id, paper_id, question_number,
                       question_type, difficulty_level, score_value, question_text, options_json,
                       answer_text, analysis_text, has_formula, content_hash, source_type)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'merged')''',
                    (qid, subject_id, paper_db_id, qnum, qtype,
                     2, extract_score(text), text,
                     json.dumps(extract_options(text), ensure_ascii=False) if extract_options(text) else None,
                     answer_text, analysis,
                     1 if '$' in text else 0, content_hash))
                q_db_id = cur.lastrowid
                used_qids.add(qid)
                stats['questions'] += 1

                # 自动关联知识点
                rels = match_kps(text + '\n' + analysis, kp_names)
                for kp_id, score in rels:
                    conn.execute(
                        'INSERT OR IGNORE INTO question_kp_relations (question_id, kp_id, relevance_score) VALUES (?,?,?)',
                        (q_db_id, kp_id, score))
                    stats['kp_relations'] += 1

    conn.commit()

    # 统计结果
    n_q = conn.execute('SELECT COUNT(*) c FROM questions').fetchone()['c']
    n_p = conn.execute('SELECT COUNT(*) c FROM papers').fetchone()['c']
    n_r = conn.execute('SELECT COUNT(*) c FROM question_kp_relations').fetchone()['c']
    conn.close()

    print('=' * 50)
    print(f'本次导入: 新增试卷 {stats["papers"]} 份, 新增题目 {stats["questions"]} 道, '
          f'知识点关联 {stats["kp_relations"]} 条, 跳过重复 {stats["duplicates"]} 道')
    print(f'数据库总计: 试卷 {n_p} 份, 题目 {n_q} 道, 关联 {n_r} 条')
    if errors:
        print('警告:', *errors, sep='\n  ')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', default=os.path.join(ROOT, 'data', 'gaokao_bench'))
    ap.add_argument('--db', default=os.path.join(ROOT, 'database', 'gaokao.db'))
    args = ap.parse_args()
    import_all(args.data_dir, args.db)
