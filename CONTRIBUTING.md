# 贡献指南

感谢你愿意花时间。这个项目最需要的贡献是**数据校对**——一道错答案、一段空缺的知识点解析，比一个新功能更有价值。

---

## 0. 环境准备

```bash
git clone https://github.com/<你的用户名>/SmartGaokaoPastPapers.git
cd SmartGaokaoPastPapers
pip install -r requirements.txt
python scripts/bootstrap.py --force     # 从 data/ 重建数据库，约 5 秒
python -m uvicorn src.web.app:app --reload --port 8000
```

改动前请确认 `python scripts/bootstrap.py --check` 全部通过，这样出问题一定是你的改动引起的。

---

## 1. 报错题（零代码贡献）

发现答案错了、选项缺了、公式渲染成乱码、题目串题？

**开一个 [数据问题 Issue](https://github.com/hbxt008/SmartGaokaoPastPapers/issues/new?template=data_issue.yml)**，填三样东西即可：

1. 页面链接（例如 `/question/MATH-2022-...`）或「2021 年全国乙卷 理科数学 第 12 题」
2. 错在哪：答案错 / 选项缺失 / 题干残缺 / 公式乱码 / 解析与题目不符
3. 你确认的正确内容（附来源更好：答案解析、教材页码）

不需要改代码。维护者会改 `data/` 下的源文件，PR 里会带你一起署名。

---

## 2. 完善知识点解析

`data/kp_content/*.json` 是「知识点 ID → Markdown 正文」的字典，按学科分文件（`math_a.json`、`chinese.json`……）。

```json
{
  "MATH-SET-001": "## 集合的概念\n\n**定义**：……\n\n**要点**\n- ……\n\n**常考题型**：……\n\n**易错**：……"
}
```

**写作格式**（现有 538 条都遵循这个结构，保持一致便于阅读）：

| 段落 | 内容 |
|---|---|
| `## 标题` | 知识点名，与 `knowledge_points.kp_name` 一致 |
| `**定义 / 核心结论**` | 概念本身或必背公式，用 `-` 列表，一行一条 |
| `**常考题型**` | 高考里怎么考，1–2 行 |
| `**易错**` | 学生最常踩的坑，1–2 条 |

要求：

- 数学公式用 LaTeX：行内 `$a^2+b^2=c^2$`，行间 `$$...$$`（前端用 MathJax 渲染）
- 不要写「暂无解析」「待补充」这类占位
- 单条控制在 200–600 字，宁精不滥

改完执行：

```bash
python scripts/import_kp_content.py    # 写入数据库
python scripts/bootstrap.py --check    # 覆盖率必须仍是 100%
```

---

## 3. 扩充题库

### 3.1 小学题（`data/primary/p_math.json`）

```json
{
  "subject_code": "p_math",
  "subject_name": "小学数学",
  "units": [
    {
      "grade": 1,
      "semester": "上",
      "kp": "10以内数的认识与加减法",
      "note": "1. 认识 0~10 各数……\n2. 用数的组成算加减……",
      "questions": [
        {"stem": "3+4=（ ）", "options": ["6","7","8","5"], "answer": "B", "analysis": "3 和 4 合起来是 7。"}
      ]
    }
  ]
}
```

- `grade` 1–6，`semester` 只能是 `"上"` 或 `"下"`
- `options` 固定 4 个，`answer` 是 `A`/`B`/`C`/`D`
- `note` 会被写成该单元的知识点解析，用编号句子写要点

导入：`python scripts/import_primary.py && python scripts/build_primary_graph.py`

### 3.2 高考题（大工程）

现有数据来自 GAOKAO-Bench 的 JSON。想加 2023 年后的新卷，两条路：

- **有结构化数据**：按 `data/gaokao_bench/2010-2022_Math_MCQs.json` 的字段（`year`、`category`、`question`、`options`、`answer`、`analysis`）整理成新文件，然后在 `scripts/import_gaokao_bench.py` 的 `FILE_TYPE_MAP` 里登记文件名
- **只有 docx / pdf**：用 `src/paper_parser/` 的解析器跑一遍，再人工校对。这类 PR 请先开 Issue 说明数据来源，避免重复劳动

导入后**务必**重跑关联：

```bash
python scripts/import_gaokao_bench.py
python scripts/relink_kp.py            # 先看 dry-run 统计
python scripts/relink_kp.py --apply    # 确认合理再写库
```

---

## 4. 改进知识点匹配

`scripts/relink_kp.py` 是纯启发式（关键词打分），阈值和停用词表都在文件顶部。调参类 PR 请附上：

- 改前的统计（`--apply` 前的 dry-run 输出）
- 改后的统计
- 至少 3 个「改前错、改后对」的具体例子

---

## 5. 代码贡献

- 分支：`feat/xxx`、`fix/xxx`、`data/xxx`
- 提交信息：一句话说清改了什么，例如 `fix: 地理材料题组无法进入测评池`
- 新增路由请同步在 `README.md` 的目录结构说明里登记
- 前端改动请附截图（浅色即可），涉及打印样式的请附 A4 预览
- CI 会执行：依赖安装 → 从零重建数据库 → 启动服务 → 探测 `/`、`/papers`、`/graph`、`/quiz` 四个页面返回 200。**重建必须能从干净仓库跑通**，不要在代码里依赖本地已有的 `database/gaokao.db`

---

## 6. 提交 PR 前检查清单

- [ ] `python scripts/bootstrap.py --force` 能从零跑通，校验全绿
- [ ] 没有把 `database/*.db`、`data/exports/` 等构建产物提交上来
- [ ] 新增的 JSON 是 UTF-8、无 BOM、能 `json.load` 解析
- [ ] 涉及页面改动附了截图
- [ ] PR 描述里写了「改了什么 / 为什么 / 怎么验证」

---

## 行为准则

讨论对事不对人。数据争议以官方答案、教材、课程标准为准，谁引用的一手材料更硬就跟谁走。
