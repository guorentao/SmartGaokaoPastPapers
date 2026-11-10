#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""试卷导出：把题库中的真题还原成可下载的试卷文件（DOCX / HTML / Markdown）。

题库来源为 GAOKAO-Bench 公开数据集，每份「试卷」是该年度该地区该学科被收录的题目集合，
不一定是完整原卷（部分学科只收录客观题）。导出件会在卷头注明收录题数。
"""
import html
import io
import json
import os
import re
import zipfile
from datetime import datetime

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

import quiz_engine as qe

EXPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'exports')

TYPE_ORDER = ['选择题', '填空题', '名篇名句默写', '文言文阅读', '古诗词阅读', '现代文阅读',
              '文学类文本阅读', '实用类文本阅读', '语言文字运用', '阅读理解', '完形填空',
              '语法填空', '短文改错', '解答题', '材料分析题', '论述题']


def fetch_papers(conn, subject_code=None, year=None, keyword=None):
    sql = '''SELECT p.*, s.subject_code, s.subject_name,
                    (SELECT COUNT(*) FROM questions q WHERE q.paper_id=p.id) AS q_count
             FROM papers p JOIN subjects s ON s.id=p.subject_id WHERE 1=1'''
    args = []
    if subject_code and subject_code != 'all':
        sql += ' AND s.subject_code=?'
        args.append(subject_code)
    if year:
        sql += ' AND p.year=?'
        args.append(year)
    if keyword:
        sql += ' AND (p.paper_title LIKE ? OR p.region LIKE ?)'
        args += [f'%{keyword}%', f'%{keyword}%']
    sql += ' ORDER BY s.sort_order, p.year DESC, p.paper_id'
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def fetch_paper(conn, paper_id):
    row = conn.execute('''SELECT p.*, s.subject_code, s.subject_name FROM papers p
                          JOIN subjects s ON s.id=p.subject_id WHERE p.paper_id=?''', (paper_id,)).fetchone()
    if not row:
        return None, []
    paper = dict(row)
    return paper, load_questions(conn, paper['id'])


def load_questions(conn, paper_db_id):
    rows = conn.execute('''SELECT question_id, question_number, question_type, question_text,
                                  options_json, answer_text, analysis_text
                           FROM questions WHERE paper_id=?''', (paper_db_id,)).fetchall()
    return items_from_rows(rows)


CTRL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def clean(text):
    """去掉 Word/XML 不接受的控制字符"""
    return CTRL_RE.sub('', str(text or ''))


def items_from_rows(rows):
    items = []
    for r in rows:
        stem, opts = qe.split_mcq(r['question_text'], r['options_json'])
        # 题干自带的题号与前缀去掉，避免和导出时的序号重复
        stem = re.sub(r'^\s*\d{1,2}\s*[.．、]\s*', '', stem or '')
        items.append({
            'qid': r['question_id'],
            'num': r['question_number'] or '',
            'type': clean(r['question_type'] or '题目'),
            'stem': clean(stem),
            'options': {k: clean(v) for k, v in (opts or {}).items()},
            'answer': clean((r['answer_text'] or '').strip()),
            'analysis': clean((r['analysis_text'] or '').strip()),
        })
    items.sort(key=lambda x: (TYPE_ORDER.index(x['type']) if x['type'] in TYPE_ORDER else 99,
                              _num_key(x['num']), x['qid']))
    return items


def _num_key(num):
    m = re.search(r'\d+', str(num or ''))
    return int(m.group()) if m else 999


def paper_title(paper):
    return clean(paper.get('paper_title')) or f"{paper.get('year')}年{paper.get('subject_name')}（{paper.get('region')}）"


def file_stem(paper):
    title = f"{paper.get('year')}年高考{paper.get('subject_name')}（{paper.get('region')}）"
    return re.sub(r'[\\/:*?"<>|\s]+', '', title)


# ---------------------------------------------------------------- HTML

HTML_CSS = '''
:root { --ink:#1c1f28; --muted:#6b7280; --line:#d6d9e4; }
* { box-sizing: border-box; }
body { margin:0; background:#f1f2f7; font-family: "Songti SC", "SimSun", "Microsoft YaHei", serif;
       color:var(--ink); line-height:1.85; font-size:14.5px; }
.toolbar { position:sticky; top:0; background:#fff; border-bottom:1px solid var(--line);
           padding:10px 18px; display:flex; gap:12px; align-items:center; justify-content:space-between;
           font-family:"Microsoft YaHei",sans-serif; font-size:13px; z-index:9; }
.toolbar .brand { color:var(--muted); }
.toolbar button { border:1px solid #c9cee0; background:#fff; border-radius:7px; padding:7px 14px;
                  cursor:pointer; font-family:inherit; font-size:13px; }
.toolbar button:hover { border-color:#5b6ee8; color:#5b6ee8; }
.paper { max-width:820px; margin:20px auto 50px; background:#fff; padding:46px 52px;
         box-shadow:0 4px 26px rgba(0,0,0,.08); border-radius:4px; }
.paper-head { text-align:center; border-bottom:2px solid var(--ink); padding-bottom:16px; margin-bottom:8px; }
.paper-head h1 { font-size:22px; margin:0 0 8px; letter-spacing:1px; }
.paper-sub { font-size:13px; color:#444; }
.paper-meta { display:flex; justify-content:space-between; flex-wrap:wrap; gap:10px;
              font-size:13px; margin:16px 0 6px; }
.paper-note { font-size:12.5px; color:#555; border-left:3px solid #d9dceb; padding-left:10px; margin:12px 0 22px; }
.sec-title { font-family:"Microsoft YaHei",sans-serif; font-size:15px; font-weight:700;
             margin:24px 0 12px; padding:6px 12px; background:#f5f6fb; border-left:4px solid #5b6ee8; }
.q { margin-bottom:18px; break-inside:avoid; }
.q-head { font-size:13px; color:var(--muted); font-family:"Microsoft YaHei",sans-serif; }
.q-num { font-weight:700; color:var(--ink); margin-right:6px; }
.q-stem { margin-top:4px; }
.q-opts { display:grid; grid-template-columns:1fr 1fr; gap:3px 22px; margin-top:6px; }
.q-opt { display:flex; gap:6px; }
.q-opt b { font-weight:700; }
.blank { display:inline-block; min-width:120px; border-bottom:1px solid #555; }
.answer-block { margin-top:44px; padding-top:20px; border-top:2px dashed #9aa0b8; }
.answer-block h2 { font-size:17px; font-family:"Microsoft YaHei",sans-serif; }
.a-item { margin-bottom:14px; font-size:13.5px; }
.a-line { color:#1a5c3a; }
.a-analysis { color:#4b5062; margin-top:3px; white-space:pre-wrap; }
@media print {
  body { background:#fff; }
  .toolbar { display:none !important; }
  .paper { box-shadow:none; margin:0; padding:0; max-width:100%; }
  @page { margin:16mm 14mm; }
}
'''


def _esc(text):
    return html.escape(str(text or ''))


def _stem_html(stem):
    parts = _esc(stem).split('\n')
    return '<br>'.join(parts)


def paper_html(paper, items, with_answer=True, standalone=True):
    """生成独立 HTML 试卷（可直接浏览器打开、打印为 PDF）"""
    title = paper_title(paper)
    q_html = []
    cur_type = None
    for i, it in enumerate(items, 1):
        if it['type'] != cur_type:
            cur_type = it['type']
            q_html.append(f'<div class="sec-title">{_esc(cur_type)}</div>')
        opts = ''
        if it['options']:
            cells = ''.join(
                f'<div class="q-opt"><b>{_esc(k)}.</b><span>{_esc(v)}</span></div>'
                for k, v in sorted(it['options'].items()))
            opts = f'<div class="q-opts">{cells}</div>'
        elif it['type'] == '填空题':
            opts = '<div class="q-opts"><span class="blank"></span></div>'
        num = it['num'] or i
        q_html.append(
            f'<div class="q"><div class="q-head"><span class="q-num">{_esc(num)}.</span>'
            f'{_esc(it["type"])}</div>'
            f'<div class="q-stem">{_stem_html(it["stem"])}</div>{opts}</div>')

    ans_html = ''
    if with_answer:
        blocks = []
        for i, it in enumerate(items, 1):
            num = it['num'] or i
            ana = f'<div class="a-analysis">解析：{_esc(it["analysis"])}</div>' if it['analysis'] else ''
            blocks.append(f'<div class="a-item"><b>{_esc(num)}.</b> '
                          f'<span class="a-line">答案：{_esc(it["answer"]) or "略"}</span>{ana}</div>')
        ans_html = f'<div class="answer-block"><h2>参考答案与解析</h2>{"".join(blocks)}</div>'

    body = f'''<div class="paper">
  <div class="paper-head">
    <h1>{_esc(title)}</h1>
    <div class="paper-sub">{_esc(paper.get('subject_name'))} · {_esc(paper.get('paper_version') or '真题')} · 共 {len(items)} 题</div>
  </div>
  <div class="paper-meta">
    <span>姓名：____________</span><span>班级：____________</span>
    <span>日期：____________</span><span>得分：__________</span>
  </div>
  <div class="paper-note">说明：本卷由「高考真题库系统」从收录题库还原生成，含该年度该地区本题库收录的全部题目（{len(items)} 题），
  可能不包含当年原卷的全部大题。题目按题型归类排序，答案与解析见卷末。</div>
  {''.join(q_html)}
  {ans_html}
  <div class="paper-meta" style="margin-top:34px;color:#888;font-size:12px;">
    <span>题库：GAOKAO-Bench 公开数据集 · 解析补全：高考真题库系统</span>
    <span>导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}</span>
  </div>
</div>'''

    if not standalone:
        return body

    return f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<style>{HTML_CSS}</style>
<script>window.MathJax={{tex:{{inlineMath:[['$','$'],['\\\\(','\\\\)']],displayMath:[['$$','$$']]}},
options:{{skipHtmlTags:['script','noscript','style','textarea','pre','code']}}}};</script>
<script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml-full.js"></script>
</head><body>
<div class="toolbar">
  <span class="brand">高考真题库系统 · 试卷导出</span>
  <span><button onclick="window.print()">🖨 打印 / 存为 PDF</button></span>
</div>
{body}
</body></html>'''


# ---------------------------------------------------------------- DOCX

def _set_cn_font(run, name='宋体', size=10.5, bold=False):
    run.font.size = Pt(size)
    run.bold = bold
    run.font.name = name
    run._element.rPr.rFonts.set(qn('w:eastAsia'), name)


def paper_docx(paper, items, with_answer=True):
    doc = Document()
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin = sec.bottom_margin = Cm(2.0)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_cn_font(p.add_run(paper_title(paper)), '黑体', 15, True)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_cn_font(p.add_run(f"{paper.get('subject_name')} · 共 {len(items)} 题"), '宋体', 9)

    p = doc.add_paragraph()
    _set_cn_font(p.add_run('姓名：____________　　班级：____________　　日期：____________　　得分：__________'), '宋体', 9)

    p = doc.add_paragraph()
    _set_cn_font(p.add_run('说明：本卷由「高考真题库系统」从收录题库还原生成，'
                           f'含本题库收录的 {len(items)} 道题目，可能不含当年原卷全部大题；'
                           '答案与解析见卷末。'), '楷体', 8.5)

    cur_type = None
    for i, it in enumerate(items, 1):
        if it['type'] != cur_type:
            cur_type = it['type']
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(8)
            _set_cn_font(p.add_run(f'【{cur_type}】'), '黑体', 10.5, True)
        num = it['num'] or i
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(3)
        _set_cn_font(p.add_run(f'{num}. '), '宋体', 10.5, True)
        _set_cn_font(p.add_run(it['stem']), '宋体', 10.5)
        for k, v in sorted(it['options'].items()):
            op = doc.add_paragraph()
            op.paragraph_format.left_indent = Cm(0.8)
            op.paragraph_format.space_after = Pt(1)
            _set_cn_font(op.add_run(f'{k}. {v}'), '宋体', 10)
        if it['type'] == '填空题' and not it['options']:
            bp = doc.add_paragraph()
            bp.paragraph_format.left_indent = Cm(0.8)
            _set_cn_font(bp.add_run('答：____________________'), '宋体', 10)

    if with_answer:
        doc.add_page_break()
        p = doc.add_paragraph()
        _set_cn_font(p.add_run('参考答案与解析'), '黑体', 13, True)
        for i, it in enumerate(items, 1):
            num = it['num'] or i
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(4)
            _set_cn_font(p.add_run(f'{num}. 答案：{it["answer"] or "略"}'), '宋体', 10, True)
            if it['analysis']:
                ap = doc.add_paragraph()
                ap.paragraph_format.left_indent = Cm(0.6)
                _set_cn_font(ap.add_run('解析：' + it['analysis']), '宋体', 9.5)

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(16)
    _set_cn_font(p.add_run(f'题库来源：GAOKAO-Bench 公开数据集 · 导出于 {datetime.now().strftime("%Y-%m-%d %H:%M")}'),
                 '宋体', 8)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- Markdown

def paper_markdown(paper, items, with_answer=True):
    lines = [f'# {paper_title(paper)}', '',
             f'- 学科：{paper.get("subject_name")}',
             f'- 年份：{paper.get("year")}　地区：{paper.get("region")}',
             f'- 题数：{len(items)}（本题库收录部分）', '', '---', '']
    cur = None
    for i, it in enumerate(items, 1):
        if it['type'] != cur:
            cur = it['type']
            lines += [f'## {cur}', '']
        num = it['num'] or i
        lines.append(f'**{num}.** {it["stem"]}')
        for k, v in sorted(it['options'].items()):
            lines.append(f'- {k}. {v}')
        lines.append('')
    if with_answer:
        lines += ['---', '', '## 参考答案与解析', '']
        for i, it in enumerate(items, 1):
            num = it['num'] or i
            lines.append(f'**{num}.** 答案：{it["answer"] or "略"}')
            if it['analysis']:
                lines.append(f'> 解析：{it["analysis"].replace(chr(10), " ")}')
            lines.append('')
    return '\n'.join(lines)


# ---------------------------------------------------------------- 打包

def build_zip(papers, conn, fmt='docx', with_answer=True, progress=None):
    """把多份试卷打成 zip（内存）"""
    buf = io.BytesIO()
    used = set()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for idx, p in enumerate(papers, 1):
            _, items = fetch_paper(conn, p['paper_id'])
            if not items:
                continue
            sub = p['subject_name']
            stem = file_stem(p)
            if stem in used:
                stem = f'{stem}_{idx}'
            used.add(stem)
            name = f'{sub}/{stem}.{fmt}'
            if fmt == 'docx':
                data = paper_docx(p, items, with_answer)
            elif fmt == 'html':
                data = paper_html(p, items, with_answer).encode('utf-8')
            else:
                data = paper_markdown(p, items, with_answer).encode('utf-8')
            zf.writestr(name, data)
            if progress:
                progress(idx, len(papers))
    buf.seek(0)
    return buf.getvalue()


def cached_zip(key, builder):
    """按 key 缓存 zip 到 data/exports，避免重复生成"""
    os.makedirs(EXPORT_DIR, exist_ok=True)
    path = os.path.join(EXPORT_DIR, key + '.zip')
    if os.path.exists(path) and os.path.getsize(path) > 1024:
        return path
    data = builder()
    tmp = path + '.part'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)
    return path
