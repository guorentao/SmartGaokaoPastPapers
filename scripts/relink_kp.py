#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""重建题目 <-> 知识点 关联（更严格的关键词打分匹配）

旧逻辑：知识点全名直接 substring，命中即停，导致「不等式」「函数」等泛化词
大量误关联（数学仅 733 题中 303 题有链接，且集中在少数泛化知识点）。

新逻辑：
1. 知识点名拆成关键词（去掉「的/与/和/、」等结构词、去掉括号注释），
   只保留区分度足够的关键词（中文 >=3 字，或 2 字且不在停用词表）。
2. 每题按「题干 + 选项 + 解析」统计关键词出现次数，得分 = Σ len(kw)^2 * count。
3. 取分数最高且超过阈值的一个作为 main（主要考点）；若第二名分数 >= 主考点 55%，
   再记一条 related（次要考点）。每題最多 2 条，宁缺毋滥。

用法：
  python scripts/relink_kp.py            # 只统计，不写库（dry-run）
  python scripts/relink_kp.py --apply    # 清空旧关联并写入新关联
"""
import os
import re
import sys
import json
import shutil
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'database', 'gaokao.db')

# 泛化词：单独出现不能说明考的是这个知识点
GENERIC = {
    '函数', '集合', '方程', '图形', '结构', '功能', '概念', '性质', '应用', '方法',
    '思想', '文化', '经济', '政治', '制度', '国家', '社会', '历史', '世界', '中国',
    '发展', '变化', '关系', '影响', '意义', '作用', '特点', '规律', '原理', '定律',
    '实验', '计算', '分析', '综合', '评价', '理解', '认识', '内容', '形式', '运动',
    '物质', '能量', '细胞', '遗传', '环境', '人口', '城市', '工业', '农业', '贸易',
    '市场', '价格', '企业', '政府', '公民', '民主', '法治', '道德', '哲学', '逻辑',
    '思维', '创新', '实践', '真理', '价值', '人生', '民族', '国际', '区域', '地理',
    '自然', '气候', '地形', '水文', '土壤', '生物', '资源', '灾害', '旅游', '交通',
    '语言', '阅读', '写作', '表达', '分析', '鉴赏', '文言', '诗歌', '散文', '小说',
    '语法', '词汇', '句型', '时态', '语态', '从句', '非谓语', '听力', '口语', '翻译',
    '力学', '热学', '光学', '电学', '磁学', '原子', '物理', '化学', '数学', '语文',
    '英语', '专题', '复习', '考点', '知识', '能力', '素养', '考查', '题型',
}

# 名称中的结构连接词，拆词时丢弃
SPLIT_CHARS = '的与和及或、，,/'
TRIM_PREFIX = ('运用', '理解', '掌握', '了解', '认识', '学会')
TRIM_SUFFIX = ('的概念', '的定义', '的性质', '的应用', '的运用', '的意义', '的影响',
               '的分析', '的计算', '的判断', '的规律', '的原理', '的方法', '及其应用')


def norm(text: str) -> str:
    """全角转半角、去空白与常见标点，统一小写（英文）"""
    if not text:
        return ''
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            code = 32
        elif 0xFF01 <= code <= 0xFF5E:
            code -= 0xFEE0
        out.append(chr(code))
    s = ''.join(out)
    s = re.sub(r'[\s\u00a0]+', '', s)
    return s


def keywords_of(name: str):
    """把知识点名拆成有区分度的关键词列表"""
    base = norm(name)
    base = re.sub(r'[（(][^）)]*[）)]', '', base)      # 去括号注释
    kws = set()
    if base:
        kws.add(base)
    # 去掉「XX的概念/的应用」等后缀，得到核心词
    for suf in TRIM_SUFFIX:
        if base.endswith(suf) and len(base) - len(suf) >= 2:
            kws.add(base[:-len(suf)])
    # 按连接词切分（如「正弦定理与余弦定理」）
    for part in re.split('[' + re.escape(SPLIT_CHARS) + ']', base):
        if len(part) >= 3:
            kws.add(part)
    # 过滤区分度不足的词
    out = []
    for kw in kws:
        if re.fullmatch(r'[a-zA-Z0-9]+', kw):
            if len(kw) >= 4 and kw.lower() not in GENERIC:
                out.append(kw.lower())
            continue
        if len(kw) >= 3:
            out.append(kw)
        elif len(kw) == 2 and kw not in GENERIC:
            out.append(kw)
    return out


def keywords_from_content(content: str):
    """从知识点解析正文里提取术语：加粗词 + 以特定后缀结尾的中文术语"""
    if not content:
        return set()
    kws = set()
    for m in re.findall(r'\*\*([^*\n]{2,14})\*\*', content):
        m = norm(m)
        m = re.sub(r'[（(][^）)]*[）)]', '', m)
        if 2 <= len(m) <= 12 and m not in GENERIC:
            kws.add(m)
    # 「XX定律 / XX定理 / XX系统 …」这类固定后缀术语
    for m in re.findall(r'[\u4e00-\u9fa5]{2,8}(?:定律|定理|公式|效应|规律|模型|系统|循环|反应|结构|模式|区位|机制|理论|法则|假说)', content):
        if m not in GENERIC:
            kws.add(m)
    return kws


def load_kps(conn, subject_id):
    rows = conn.execute(
        'SELECT id, kp_id, kp_name, kp_level, content FROM knowledge_points WHERE subject_id=?',
        (subject_id,)
    ).fetchall()
    items = []
    for r in rows:
        kws = keywords_of(r['kp_name'])
        # 解析正文里的术語只在名称关键词较少时补充，避免关键词爆炸导致误命中
        extra = keywords_from_content(r['content'] or '')
        kws = list(dict.fromkeys(kws + sorted(extra, key=len, reverse=True)[:12]))
        if not kws:
            continue
        items.append({
            'id': r['id'], 'kp_id': r['kp_id'], 'name': r['kp_name'],
            'level': r['kp_level'] or 1, 'kws': kws,
        })
    return items


def score_question(haystack: str, items):
    scored = []
    for it in items:
        s = 0
        for kw in it['kws']:
            c = haystack.count(kw)
            if c:
                s += c * (len(kw) ** 2)
        if s:
            # 三级知识点优先（更具体），一级/二级降权
            weight = 1.0 if it['level'] >= 3 else (0.75 if it['level'] == 2 else 0.55)
            scored.append((s * weight, it))
    scored.sort(key=lambda x: -x[0])
    return scored


def main():
    apply_mode = '--apply' in sys.argv
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if apply_mode:
        bak = DB_PATH + '.bak_relink'
        if not os.path.exists(bak):
            shutil.copy2(DB_PATH, bak)
            print(f'已备份数据库 -> {os.path.basename(bak)}')

    subjects = conn.execute('SELECT id, subject_code, subject_name FROM subjects').fetchall()
    total_linked = total_q = 0
    plan = {}

    for sb in subjects:
        items = load_kps(conn, sb['id'])
        qs = conn.execute(
            'SELECT id, question_text, options_json, analysis_text FROM questions WHERE subject_id=?',
            (sb['id'],)
        ).fetchall()
        linked = 0
        kp_hit = {}
        rels = []
        for q in qs:
            hay = norm(' '.join(filter(None, [q['question_text'], q['options_json'], q['analysis_text']])))
            total_q += 1
            scored = score_question(hay, items)
            if not scored:
                continue
            top_score, top_item = scored[0]
            # 阈值：得分过低说明只是偶然命中泛化词
            if top_score < 12:
                continue
            rels.append((q['id'], top_item['id'], 'main', 1.0))
            kp_hit[top_item['name']] = kp_hit.get(top_item['name'], 0) + 1
            linked += 1
            if len(scored) > 1 and scored[1][0] >= top_score * 0.55:
                rels.append((q['id'], scored[1][1]['id'], 'related', round(scored[1][0] / top_score, 2)))
        total_linked += linked
        plan[sb['id']] = rels
        print(f"{sb['subject_name']}: 题 {len(qs)} -> 可关联 {linked} "
              f"({linked / max(len(qs), 1) * 100:.0f}%), 涉及知识点 {len(kp_hit)}/{len(items)}")

    print(f'\n合计: {total_linked}/{total_q} 道题可关联 ({total_linked / max(total_q,1) * 100:.0f}%)')

    if apply_mode:
        conn.execute('DELETE FROM question_kp_relations')
        n = 0
        for ris in plan.values():
            for qid, kpid, rtype, score in ris:
                conn.execute(
                    'INSERT OR IGNORE INTO question_kp_relations (question_id, kp_id, relation_type, relevance_score) '
                    'VALUES (?,?,?,?)', (qid, kpid, rtype, score))
                n += 1
        conn.commit()
        print(f'已写入 {n} 条关联')
    else:
        print('（dry-run，未写库；加 --apply 生效）')
    conn.close()


if __name__ == '__main__':
    main()
