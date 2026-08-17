---
title: "Markdown 内容修复"
description: "修复已提取文字在 Markdown 中的断词、断句和结构问题。"
order: 10
---

# Markdown 内容修复

本目录的机器入口是 [SKILL.md](SKILL.md)，稳定 Skill ID 为 `markdown-content-repair`。

它在 `page-units/pNNN.md` 生成后运行，优先通过局域网 `mac96` 的语言模型修复当前页 Markdown 的排版和局部文字结构。
局域网成功后不再调用在线模型；只有明确授权且局域网失败时，才允许当前在线模型回退一次。修复只读取当前页
Markdown 和显式提供的当前片段，不默认读取 manifest、bbox、OCR/视觉 JSON、坐标、页面图或其他页面。
通过结构守卫的确定性结果直接写回当前 `page-units/pNNN.md`，并将输入/输出哈希和逐项差异记录到
`reviews/markdown-content-repair.yaml`；无法确定的候选保持 `deferred`，不写入正文。图片资产的保留和筛选由独立的视觉资产流程负责。
