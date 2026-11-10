#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""为小学三科构建知识图谱（册 -> 章节 -> 知识点），并建立前置关系、分配真题。

小学题库原本只有 32 个平铺的「单元知识点」，没有学期/章节层级，
导致学科页的知识图谱树是空的。本脚本：

1. 按 grade+semester 生成 semesters（一年级上册 …… 六年级下册）
2. 每册拆 2 个 chapters（依据 data/primary/graph_map.json 的命名）
3. 原单元知识点挂到对应章节（kp_level=2），并升级 Markdown 解析
4. 每个单元再拆 4 个课时级知识点（kp_level=2，与原单元同章）
5. 建立 kp_relations 前置关系（同册顺序推进 + 跨册衔接）
6. 把该单元题目按要点关键词匹配，分配到课时知识点上

可重复执行：按 kp_id 幂等更新。
"""
import json
import os
import re
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, 'database', 'gaokao.db')
DATA = os.path.join(BASE, 'data', 'primary')

SUBJA = {'p_math': 'PMA', 'p_chinese': 'PCH', 'p_english': 'PEN'}
CN_NUM = '一二三四五六'

# 要点关键词：用于把题目分配到课时知识点（关键词 -> 权重）
GENERIC_HINTS = ['计算', '下面', '选择', '正确', '的是', '多少', '一个', '我们']


def load_units(code):
    path = os.path.join(DATA, f'{code}.json')
    return json.load(open(path, encoding='utf-8'))['units']


def split_points(note):
    """note 是「1. xxx\n2. xxx」格式，拆成要点列表"""
    parts = []
    for line in (note or '').split('\n'):
        line = line.strip()
        if not line:
            continue
        line = re.sub(r'^\d+\s*[.．、]\s*', '', line)
        if line:
            parts.append(line)
    return parts


def tokens(text):
    """中文按 2-gram + 英文单词切分，用于打分"""
    text = (text or '').lower()
    words = set(re.findall(r'[a-z]{3,}', text))
    han = re.sub(r'[^\u4e00-\u9fa5]', ' ', text)
    for seg in han.split():
        for i in range(len(seg) - 1):
            words.add(seg[i:i + 2])
    return {w for w in words if w not in GENERIC_HINTS}


def build_content(name, point, unit, points, grade, semester):
    """生成课时知识点的 Markdown 解析"""
    lines = [f'## {name}', '', point, '', f'**所属单元**：{unit}（{grade}年级{semester}册）', '',
             '### 本册知识清单']
    for i, p in enumerate(points, 1):
        mark = ' ← **本知识点**' if p == point else ''
        lines.append(f'{i}. {p}{mark}')
    lines += [
        '',
        '### 复习建议',
        f'- 先把上面这条结论用自己的话复述一遍，再对照课本例题走一遍推导。',
        f'- 做题时先把与「{name}」有关的条件圈出来，避免和同册其它概念混淆。',
        '- 出错后回到本页重读要点，再做 2 道同类题检验是否真的会了。',
    ]
    return '\n'.join(lines)


def build_unit_content(unit_name, points, grade, semester):
    lines = [f'## {unit_name}', '', f'{grade}年级{semester}册核心考点，共 {len(points)} 个要点。', '',
             '### 知识清单']
    for i, p in enumerate(points, 1):
        lines.append(f'{i}. {p}')
    lines += ['', '> 下方「章节知识点」按课时拆分，可逐个点击查看细讲与对应真题。']
    return '\n'.join(lines)


def ensure_kp(conn, subject_id, kp_id, name, chapter_id, level, content,
              grade, semester, sort_order, difficulty=2, importance=3):
    row = conn.execute('SELECT id FROM knowledge_points WHERE kp_id=?', (kp_id,)).fetchone()
    if row:
        conn.execute('''UPDATE knowledge_points SET kp_name=?, subject_id=?, chapter_id=?,
                        kp_level=?, content=?, grade=?, semester=?, sort_order=?, updated_at=datetime('now','localtime')
                        WHERE id=?''',
                     (name, subject_id, chapter_id, level, content, grade, semester, sort_order, row[0]))
        return row[0]
    cur = conn.execute('''INSERT INTO knowledge_points
        (kp_id, subject_id, chapter_id, parent_kp_id, kp_name, kp_level, description, content,
         difficulty_level, importance_level, exam_frequency, sort_order, grade, semester)
        VALUES (?,?,?,NULL,?,?,?,?,?,?,?,?,?,?)''',
                       (kp_id, subject_id, chapter_id, name, level, name, content,
                        difficulty, importance, 0, sort_order, grade, semester))
    return cur.lastrowid


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    gmap = json.load(open(os.path.join(DATA, 'graph_map.json'), encoding='utf-8'))

    stats = {'semesters': 0, 'chapters': 0, 'kps': 0, 'relations': 0, 'links': 0}

    for code, prefix in SUBJA.items():
        sub = conn.execute('SELECT id, subject_name FROM subjects WHERE subject_code=?', (code,)).fetchone()
        if not sub:
            print(f'!! 缺少学科 {code}')
            continue
        sid = sub['id']
        print(f'\n=== {sub["subject_name"]} (subject_id={sid}) ===')

        # 清理旧的册/章（保留知识点，重新挂载）
        old_sem = [r[0] for r in conn.execute('SELECT id FROM semesters WHERE subject_id=?', (sid,))]
        if old_sem:
            q = ','.join('?' * len(old_sem))
            conn.execute(f'DELETE FROM chapters WHERE semester_id IN ({q})', old_sem)
            conn.execute('DELETE FROM semesters WHERE id IN (%s)' % q, old_sem)
            conn.execute('DELETE FROM kp_relations WHERE from_kp_id IN (SELECT id FROM knowledge_points WHERE subject_id=?) '
                         'OR to_kp_id IN (SELECT id FROM knowledge_points WHERE subject_id=?)', (sid, sid))
            conn.execute('DELETE FROM knowledge_points WHERE subject_id=? AND kp_id LIKE ?', (sid, '%-V%'))
            conn.execute('DELETE FROM knowledge_points WHERE subject_id=? AND kp_id GLOB ?', (sid, f'{prefix}-G*-*'))
            # 清掉指向已删知识点的孤儿关联
            conn.execute('DELETE FROM question_kp_relations WHERE kp_id NOT IN (SELECT id FROM knowledge_points)')
            conn.execute('DELETE FROM kp_mastery WHERE kp_id NOT IN (SELECT id FROM knowledge_points)')
            conn.execute('DELETE FROM kp_relations WHERE from_kp_id NOT IN (SELECT id FROM knowledge_points) '
                         'OR to_kp_id NOT IN (SELECT id FROM knowledge_points)')

        units = load_units(code)
        unit_map = {f"{u['grade']}{u['semester']}": u for u in units}
        prev_last_kp = None

        for grade in range(1, 7):
            for sem in ('上', '下'):
                key = f'{grade}{sem}'
                unit = unit_map.get(key)
                if not unit:
                    continue
                naming = gmap[code][key]
                points = split_points(unit['note'])
                if len(points) != 4:
                    print(f'  !! {code} {key} 要点数 {len(points)} != 4，跳过细拆')
                    points = (points + [''] * 4)[:4]

                sem_code = f'{prefix}-V{grade}{"A" if sem == "上" else "B"}'
                sem_name = f'{CN_NUM[grade - 1]}年级{sem}册'
                cur = conn.execute('''INSERT INTO semesters
                    (subject_id, semester_code, semester_name, grade_level, description, sort_order)
                    VALUES (?,?,?,?,?,?)''',
                                   (sid, sem_code, sem_name, f'{CN_NUM[grade - 1]}年级',
                                    f'小学{CN_NUM[grade - 1]}年级{sem}学期', grade * 10 + (0 if sem == '上' else 5)))
                sem_id = cur.lastrowid
                stats['semesters'] += 1

                # 原单元知识点的 kp_id
                orig_kp_id = f'{prefix}-G{grade}{"A" if sem == "上" else "B"}{grade * 2 - (1 if sem == "上" else 0):02d}'
                if not conn.execute('SELECT 1 FROM knowledge_points WHERE kp_id=?', (orig_kp_id,)).fetchone():
                    # 兜底：按名称找
                    r = conn.execute('SELECT kp_id FROM knowledge_points WHERE subject_id=? AND kp_name=?',
                                     (sid, unit['kp'])).fetchone()
                    orig_kp_id = r[0] if r else None

                first_ch_kp = None
                for ci, ch in enumerate(naming, 1):
                    ch_code = f'{sem_code}-C{ci}'
                    cur = conn.execute('''INSERT INTO chapters
                        (semester_id, chapter_code, chapter_name, chapter_number, description, sort_order)
                        VALUES (?,?,?,?,?,?)''',
                                       (sem_id, ch_code, ch['chapter'], f'第{CN_NUM[ci - 1]}单元',
                                        f'{sem_name} · {ch["chapter"]}', ci))
                    ch_id = cur.lastrowid
                    stats['chapters'] += 1

                    # 该章对应的 2 个要点索引
                    idxs = [ci * 2 - 2, ci * 2 - 1]
                    # 章首追加原单元知识点（作为章节总览）
                    if ci == 1 and orig_kp_id:
                        row = conn.execute('SELECT id FROM knowledge_points WHERE kp_id=?', (orig_kp_id,)).fetchone()
                        if row:
                            conn.execute('''UPDATE knowledge_points SET chapter_id=?, kp_level=2, grade=?, semester=?,
                                            sort_order=0, content=? WHERE id=?''',
                                         (ch_id, grade, sem,
                                          build_unit_content(unit['kp'], points, grade, sem), row[0]))
                            first_ch_kp = row[0]
                            stats['kps'] += 1

                    for k, pi in enumerate(idxs):
                        kp_name = ch['kps'][k] if k < len(ch['kps']) else f'{ch["chapter"]}（{k + 1}）'
                        kp_code = f'{orig_kp_id}-{pi + 1}' if orig_kp_id else f'{sem_code}-C{ci}-{k + 1}'
                        kp_content = build_content(kp_name, points[pi], unit['kp'], points, grade, sem)
                        kp_db_id = ensure_kp(conn, sid, kp_code, kp_name, ch_id, 3, kp_content,
                                             grade, sem, pi + 1, difficulty=1 if grade <= 2 else 2)
                        stats['kps'] += 1

                        # 前置关系：本章内顺序推进、跨册衔接
                        if k > 0:
                            prev_code = f'{orig_kp_id}-{pi}' if orig_kp_id else None
                            if prev_code:
                                pr = conn.execute('SELECT id FROM knowledge_points WHERE kp_id=?', (prev_code,)).fetchone()
                                if pr:
                                    conn.execute('''INSERT INTO kp_relations
                                        (from_kp_id, to_kp_id, relation_type, description) VALUES (?,?,?,?)''',
                                                 (pr[0], kp_db_id, 'prerequisite', f'先学「{prev_code}」，再学本知识点'))
                                    stats['relations'] += 1
                        elif prev_last_kp:
                            conn.execute('''INSERT INTO kp_relations
                                (from_kp_id, to_kp_id, relation_type, description) VALUES (?,?,?,?)''',
                                         (prev_last_kp, kp_db_id, 'prerequisite', '承上：上一册的核心考点'))
                            stats['relations'] += 1
                        prev_last_kp = kp_db_id

                        # 题目分配
                        stats['links'] += assign_questions(conn, orig_kp_id, kp_db_id, points[pi], k, ch['kps'])

        conn.commit()

    print('\n=== 完成 ===')
    print(stats)
    print('\n各科图谱概览：')
    for r in conn.execute('''SELECT s.subject_name,
            (SELECT COUNT(*) FROM semesters WHERE subject_id=s.id) sem,
            (SELECT COUNT(*) FROM chapters c JOIN semesters m ON m.id=c.semester_id WHERE m.subject_id=s.id) ch,
            (SELECT COUNT(*) FROM knowledge_points WHERE subject_id=s.id) kp,
            (SELECT COUNT(*) FROM kp_relations r JOIN knowledge_points k ON k.id=r.from_kp_id WHERE k.subject_id=s.id) rel
        FROM subjects s WHERE s.stage='primary' ORDER BY s.id'''):
        print(f'  {r[0]}: {r[1]} 册 / {r[2]} 章 / {r[3]} 知识点 / {r[4]} 条关系')
    conn.close()


def assign_questions(conn, orig_kp_id, new_kp_db_id, point_text, k, chapter_kps):
    """把原单元下的题目按要点关键词分配到课时知识点（幂等）"""
    if not orig_kp_id:
        return 0
    orig = conn.execute('SELECT id FROM knowledge_points WHERE kp_id=?', (orig_kp_id,)).fetchone()
    if not orig:
        return 0
    rows = conn.execute('''SELECT DISTINCT q.id, q.question_text, q.options_json, q.answer_text
        FROM question_kp_relations r JOIN questions q ON q.id=r.question_id
        WHERE r.kp_id=?''', (orig[0],)).fetchall()
    if not rows:
        return 0

    pt = tokens(point_text)
    has = conn.execute('SELECT 1 FROM question_kp_relations WHERE question_id=? AND kp_id=?',
                       (rows[0]['id'], new_kp_db_id)).fetchone()
    if has:
        return 0

    added = 0
    for i, q in enumerate(rows):
        blob = (q['question_text'] or '') + ' ' + (q['options_json'] or '')
        qt = tokens(blob)
        score = len(pt & qt)
        # 该题归到自己的要点才挂；否则按序均分，保证每个细点都有题
        if score > 0 or (i % max(1, len(chapter_kps))) == k:
            conn.execute('''INSERT INTO question_kp_relations
                (question_id, kp_id, relation_type, relevance_score) VALUES (?,?,?,?)''',
                         (q['id'], new_kp_db_id, 'sub', round(score / 10, 2) if score else 0.3))
            added += 1
    return added


if __name__ == '__main__':
    main()
