---
name: prune-low-value-ebook-pages
description: 对电子书知识库的页面级 Markdown 工作副本执行可审计的整页筛选与删除；用于清理目录、版权/出版说明、推广领取、二维码引流、书目推荐、出版社目录、空白装饰页等与检索目标无关或低相关的 page-units。适用于“电子书知识库清洗”“删除低价值页面”“目录页不进入 AI 知识库”“合并多组页面后再清理”以及知识库导入前的可选后置步骤。
---

# 电子书低价值页清洗

## 执行边界

把本 Skill 作为可选的读者/知识库后置步骤：等待全部页面生成、分组复制或并发结果汇合完成，再清理用于知识库导入的 `page-units/` 工作副本。优先清理复制后的工作副本；不要提前修改仍在生成页面的目录。

遵守 [`contracts/ebook-page-pruning.yaml`](../../contracts/ebook-page-pruning.yaml)，使用 [`scripts/prune_ebook_pages.py`](scripts/prune_ebook_pages.py)。本 Skill 删除整个 `pNNN.md`，不改写保留页，不删除原 PDF、页面图、OCR、bbox、Vision JSON、manifest 或其他来源证据。

## 判定流程

1. 确认预期页数，并证明所有生产者、复制分组或并发任务已经汇合。目录仍在增长、页码缺失或上游仍在写入时停止。
2. 只读取候选页，不把整本正文全部加载进分类输入。先查看书首/书尾边界和标题，再按需逐页核对候选 Markdown；Markdown 无法确认时保留为 `needs-review`。
3. 将候选分为：
   - 可删除：纯目录、版权/印刷/CIP 说明、推广/评价/领取/加联系方式、出版社书目推荐、重复封面/标题页、空白或纯装饰页；
   - 默认复核：前言、序言、导读、致谢、术语表、附录、参考文献、主题索引；这些内容可能承载范围、定义或来源价值；
   - 必须保留：正文、章节内容页、承载分类边界的章节页、案例、定义、公式、图表关系和任何无法确定价值的页面。
4. 记录显式页码与每段页码的理由。不能把某一本书的“前 10 页”固化成跨书规则，也不能仅因页码靠前或文字较少就删除。
5. 先运行 `preview`。检查计划中的页数、输入哈希、理由、保留数量和完整目录快照；计划不保存被删正文。
6. 用户已经明确批准精确页码时，可直接用同一计划运行 `apply`。脚本会在删除任何文件前复核整个目录快照；发现新增、缺失或改写时不删除。
7. 从保留的 page-units 重新生成知识库索引、`total.md` 或集合包。删除页面单元不会自动改写旧的合并文件，旧 `index.md`/`total.md` 不得继续作为已清洗结果。

## 命令

预览示例：

```bash
python3 skills/prune-low-value-ebook-pages/scripts/prune_ebook_pages.py preview \
  --page-units "/absolute/path/page-units" \
  --pages 1-10 \
  --expected-page-count 354 \
  --reason 1=promotion-or-cta \
  --reason 2-10=table-of-contents \
  --plan "/absolute/path/evidence/ebook-page-pruning.json"
```

确认计划后执行：

```bash
python3 skills/prune-low-value-ebook-pages/scripts/prune_ebook_pages.py apply \
  --plan "/absolute/path/evidence/ebook-page-pruning.json"
```

允许的理由代码：

- `promotion-or-cta`
- `table-of-contents`
- `copyright-or-publication`
- `publisher-catalog-or-recommendation`
- `duplicate-cover-or-title`
- `blank-or-decoration`
- `user-approved-low-relevance`

## 安全规则

- 默认只预览；没有精确页码与理由时不删除。
- 必须提供完整合并后的 `expected_page_count`，并至少保留一个页面。
- `apply` 只接受已有计划，不重新解释自然语言、不重新分类页面。
- 任一候选哈希或完整目录快照漂移时，删除数量必须为 0。
- 计划只记录文件名、页码、哈希、字节数、行数和理由，不复制低价值正文。
- 不删除 `assets/page-NNN.*` 或任何工程证据；后续读者导出只复制保留页仍引用的资产。
- 若直接清理 engineering package 的 `page-units/`，该目录不再代表全页工程输出；不能继续用它声称完整包验收通过。

## 完成条件

只有计划指定的页面单元已删除、所有保留文件哈希不变、删除页不存在、目录快照符合预期且下游合并文件将从保留页重建时，才报告清洗动作 `applied`。该状态只证明知识库工作副本的整页筛选完成，不证明原 Workflow、页面语义或视觉关系已通过验收。
