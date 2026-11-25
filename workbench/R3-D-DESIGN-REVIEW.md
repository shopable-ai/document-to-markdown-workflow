# R3-D · 工作台需求发现与候选设计审查

- 任务：D2MD-WORKBENCH-R3-D
- 审查基准：`48ba7e9ad692f0128f6cb4f521ce128b3ae65579`（main，2026-11-25）
- 状态：candidate / PARTIAL；不是 C10–C22 的正式业务正文、架构基线或机器投影。
- 来源：本轮人工 GOAL（T01–T06），现有中文需求与治理、R2/R3 工作台、Workflow A、生产契约及定位源码。代码只作事实证据，不反向定义需求。历史 44 项候选完整原表未找回；下表为基于可核材料的重构清单，不声明逐项继承。没有运行本机生产任务、独立专家评审或实测技术 Demo。

## 0. 决策摘要

把工作台建设为现有转换系统的管理入口，不建设另一套执行引擎。用户要一次拖入/选择多本书、设置参数、持续追加、可视化进度、可恢复的低成本转换，并获得独立验收的可携带 Markdown。原有质量目标 G01–G06、R01–R11 继续有效，R12 退役不复用。运行管理是新的目标维度而非替换保真目标。正式输入与视觉授权保留 U-B01/U-B02。当前不进入 R4、不改生产任务或引擎。项目治理停留在 C01–C07；C08 必须人工决定，C09/C18/C22 不自动建立。本文件是需求发现附带的方案比较材料，不是新增权威；将来按唯一中文权威迁移，经批准再投影到英文参考/契约。

## 1. 证据与冲突

| ID | 源码/文档（相对仓库根） | 事实与限制 |
|---|---|---|
| E01 | [项目目标与范围](../项目需求/项目目标与范围.md)、[需求说明](../项目需求/需求说明.md)、[需求树](../项目需求/需求树.md)、[需求发现](../项目需求/需求发现/需求发现记录.md) | R01 完整转换、R02 连贯结构、R03 文字数字结构、R04 关系、R05 复杂视觉、R06 简单图不留、R07 最小充分视觉、R08 携带、R09 Unknown、R10 来源隔离、R11 轻度整理。R12 退役，两个 U-B 未决。 |
| E02 | [阶段门](../项目治理/项目导航与阶段门.md)、[权威治理](../项目治理/权威与变更治理.md) | C08 人工决定→C09 业务范围；C10–17→C18 完整需求；C19–21→C22 架构；当前只允许需求发现、只读对账。 |
| E03 | [历史功能](../requirements/FUNCTIONS.md)、[历史场景](../requirements/BUSINESS-SCENARIOS-AND-CHAINS.md) | FUN-DOC-001–011、SCN-DOC-001–006 仅 Brownfield 参考；FUN-DOC-012、SCN-DOC-007/008 是已排除的下游，不复活。FUN-010 有长任务观察/停止/恢复概念，FUN-007/008 有校准和必要资产概念。 |
| E04 | [R2](preview/README.md)、[R3](R3-REPORT.md)、[工作台入口](README.md)、[server.py](server.py) | R2 12 项原型测试非生产；R3 报告 16/16 unittest、HTTP 客户端 SSE 和 DOM harness PASS，真实浏览器联网和生产对账 BLOCKED，历史自评 75。server ledger_info 仅报告可读性，slot_occupancy Unknown；只读、固定 GET、白名单、3 秒快照、2 秒缓存、最多 16 个明确 run ID。尚未独立重跑。 |
| E05 | [后台管理器](../adapters/pdf_background_task.py#L1106-L1262)、[生产契约](../contracts/workflow-a-production-runtime-context.yaml) | 集合要求至少两个不同绝对 PDF 路径，batch-size=1；精确 run_root 哈希生成 run_id；不同选集/页范围同根拒绝；父目录 .pdf-collection-queue.lock，启动前持锁后传递给 worker；默认 conversions/route，--run-root 文档用途是采纳已登记批次。状态轮询有副作用（status_task L866–895）。 |
| E06 | [批量适配器](../adapters/lan_pdf_markdown_batch.py#L1686-L1909) | 单循环逐本；冻结 manifest/注册根、重验 completion、残留进程拦截、已有严格成果跳过；发布 generation 状态。全局验收不能由某路线计数相加。已有页级复用由源 hash、页码、route record、marker 校验。 |
| E07 | [资源槽](../adapters/pdf_resource_slots.py#L53-L92)、[磁盘守卫](../adapters/pdf_disk_guard.py)、[resume](../adapters/pdf_background_task.py#L1021-L1099) | local-pdf 和 visual-model capacity=1；锁查询可实际获取锁。生产契约的 min-free-bytes 默认 2 GiB，仅下一本前；代码另有 hybrid 已登记集合 resume 的 --disk-floor-bytes、批量传递与暂停分支；是否覆盖所有写点/正常新启动仍 Unknown，不宣称全流程保护。 |
| E08 | [页路由](../adapters/pdf_route_detection.py#L427-L530)、[benchmark](../adapters/recognition_route_benchmark.py)、[质量验证](../adapters/recognition_route_quality_validator.py)、[Workflow](../workflows/document-to-markdown-assets/workflow.yaml#L715-L848) | 原生优先、PDF 三路由；渲染/表格/bbox/图像信号辅助。缺失特征有默认零/false 风险；cue 无对象在精简/完整契约与代码分支存在表述差异，实际运行需对账，不先改默认。benchmark 原始执行记录 quality_assessment=not-run，必须人审。 |
| E09 | [AGENTS](../AGENTS.md)、[长任务契约](../contracts/long-task-interaction.yaml) | 长任务 detached-silent，关闭 UI 不影响运行，禁 ad-hoc 队列；跨路线 _current 和 _consolidated 总账，100% strict acceptance 才允许最终读者集合。 |
| E10 | [MDN file input](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/input/file)、[File picker](https://developer.mozilla.org/en-US/docs/Web/API/Window/showOpenFilePicker)、[file handle](https://developer.mozilla.org/en-US/docs/Web/API/FileSystemFileHandle)、[SQLite WAL](https://www.sqlite.org/wal.html) | 普通 Web File API 不暴露原始绝对路径；picker 句柄非本机路径且权限可失效；SQLite 同机事务/WAL 单 writer 可做候选持久登记，不放网络盘，实际主机文件系统及恢复尚待测。 |

Fact（人工已表达，非确认）：批量本地提交/追加、进度、恢复、磁盘、分级识别、独立交付、低成本；不动运行任务/第二队列/提前重构。Inference：D01–D08、功能矩阵、身份、SQLite、目录及并发方案是本轮推导。Conflict：历史英文下游能力与当前非目标冲突；DOCX 有技术入口但 U-B01 尚未确认正式产品输入；工作台拖入字节不等于原始路径；当前固定批次根和动态新增冲突；缺失特征不能当作无视觉。旧 UI 原型并发/资源数不是实测。

## 2. T01 候选需求树（讨论编号，非正式 R 编号）

根：高质量、高效率、低成本、安全可持续的本地文档到 Markdown。主干 A：现 R01–R11，保真和证据优先；主干 B：运行管理。操作者是拟补充的角色（同一个人可兼读者/复核者），不引入知识库管理者。

| 域 | 用户为什么需要 | 原要求/已有覆盖 | 候选新增/扩展 |
|---|---|---|---|
| D01 来源与提交 | 多书无需手工命令 | R01,R10；A1 | 新：文件取得、来源持久化、参数冻结、去重与登记 |
| D02 批次队列 | 活动时继续添加、安排优先级 | R10；FUN-010、WB-02/03 | 新：未提交排序、不可变批次、后续批次准入 |
| D03 实时观察 | 不反复询问 AI | R01,R09；FUN-005/010、R3 | 扩展状态、阶段、页数、耗时、证据新鲜度、全局总账 |
| D04 异常恢复 | 失败、断电、重启不重做 | R01,R09,R10；FUN-010 | 扩展失败分类、停止/恢复、重验与重启对账 |
| D05 成品 | 找到可阅读、可核验成果 | R01–R11、FUN-001–009 | 新：历史浏览、结果分层、版本和导出管理；质量复用 |
| D06 存储 | 避免磁盘满、无用截图堆积 | R06–R08、FUN-008/009 | 新：预算/占用/生命周期/安全清理 |
| D07 效率成本 | 提高吞吐、减少模型调用 | R03–R07,R09；FUN-007/008 | 新：分层资源、统计、质量约束下的校准 |
| D08 安全持续运行 | 关闭 UI 或重启仍安全 | R09,R10、FUN-010 | 新：本地授权、幂等、安全控制、健康/审计 |

范围：初期真实 Demo 仅正式 PDF，PPTX 走既有入口但工作台接入另证；其他类型保持 U-B01，视觉处理授权 U-B02；不将候选编号写入现需求树正文。新增运行管理是手段，不能降低质量门。保留 reader 与 engineering 边界，不做知识库、总结、翻译、重绘或自动候选合并。

## 3. T02 功能树候选与逐项追溯

P0 = 安全/正确性或最小闭环必需，不代表现在实现；P1 = 核心增量；P2 = 待验证优化。复用栏：E 已有底座（未必 UI 或生产验收），P 部分/接口候选，N 新增，U 证据不足。共 47 项（7+6+6+6+5+6+6+5）；并非机械恢复旧 44 项。每行承接所在 D 域与上表 R 映射，S 为业务场景、V 为验收/最小验证。输入→输出；异常与恢复包含禁止副作用。不要把 WB 工作包当功能。

| ID/功能/优先级 | 输入 → 正常输出 | 异常 → 恢复 | 复用 | S/V |
|---|---|---|---|---|
| D01.1 多文件拖入 P0 | File bytes→流式接收/摘要/回执 | 中断/大小越界→仅暂存未登记、续传或清理 | N;R2 模拟 | S1/V1 |
| D01.2 本机选择/路径引用 P0 | 可信本机选择→授权路径/来源记录 | 路径不存在/权限失效→重选，不猜路径 | N | S1/V1 |
| D01.3 输入/页范围预检 P0 | 类型/bytes/selector→A1 诊断 | 加密/损坏/未知类型/非法范围→拒绝或待决定 | E A1 | S1/V1 |
| D01.4 参数配置及预设 P0 | route/pages/output/授权→有效配置快照 | 不支持值→字段级错误、不暗改路线 | P 现 CLI/R2 | S1/V1 |
| D01.5 来源登记/精确指纹 P0 | 真实路径/大小/流式 SHA→source occurrence/version | 同名、内容变化→新版本或阻止 | P A1/A6 | S2/V2 |
| D01.6 重复提交与复用提示 P0 | work key+请求 ID→既有任务或新候选 | 同键不同参数/跨路径同 hash→冲突或明确授权复用 | P 严格 skip | S2/V2 |
| D01.7 容量预检与提交摘要 P0 | 来源/目标卷/估算→逐项接受或拒绝回执 | 部分失败→成功项持久、失败项明确 | P 磁盘 guard | S1/V1,V6 |
| D02.1 持久待提交清单 P0 | 已预检来源→pending intent | 崩溃→事务恢复、无 worker 时不算已启动 | N | S3/V2 |
| D02.2 等待项排序/取消 P1 | 待提交项+revision→新顺序 | 已冻结/过期 revision→409+刷新，不改清单 | N;R2 模拟 | S3/V2,V3 |
| D02.3 冻结批次及参数 P0 | 已选意图→不可变清单/manifest digest | 内容变更→新候选，不覆盖 | E/P 管理器冻结 | S3/V3 |
| D02.4 当前批次期间追加 P0 | 新来源→后续待提交批 | 集合忙→等待；既有队列不变 | N | S3/V3 |
| D02.5 官方准入/调度 P0 | 冻结批+锁+配置→官方 run ID/状态回执 | busy/已登记/超时→核对同根、禁止盲重启 | P 管理器 | S3/V3 |
| D02.6 按类型/参数分组 P0 | 混合输入→同路线/页选择组或单来源入口 | 只有一个 PDF、非正式类型→正确入口/拒绝，不伪造 2 本 | E/P 生产契约 | S1,S3/V3 |
| D03.1 批次和逐本列表 P0 | manifest/results/current→同代次状态表 | 缺/损坏→Unknown+原因 | E R3 部分 | S4/V4 |
| D03.2 实时阶段与页数 P0 | 有效 checkpoint→当前阶段/覆盖页/失败页 | 不同源/代次→不拼接，Unknown | E R3 部分 | S4/V4 |
| D03.3 耗时、更新时间与新鲜度 P1 | timestamps+worker identity→已知时长/陈旧提示 | 缺样本→无 ETA/Unknown | P R3 | S4/V4 |
| D03.4 连接恢复/刷新 P0 | epoch/seq+snapshot→当前全量 | SSE 断线→旧态标记+重连全量 | E R3 隔离 | S4/V4 |
| D03.5 错误/日志/证据定位 P0 | task/page id→限量诊断+建议 | 证据不可读→Unknown/脱敏，不暴露任意路径 | P | S4,S5/V4,V11 |
| D03.6 验收/资源真实统计 P0 | 官方总账、容量、可信 lease→独立计数/占用 | 无 schema/过期 owner→Unknown 不估算 | U R3 | S4,S8/V4,V5 |
| D04.1 用户停止 P0 | 已授权 run+identity→official cancel 回执 | 身份失效/残留→禁止发信号、阻断新执行 | E/P cancel | S5/V7 |
| D04.2 暂停与恢复 P1 | 安全边界/原因→paused，原 run resume | 不支持任意暂停→显式禁用；磁盘暂停另分 | P 已有 paused/resume，手动安全 pause 未证 | S5,S6/V6,V7 |
| D04.3 可恢复失败重试 P0 | 错误分类+原配置→新 attempt | 非重试错误→需处理，不无限循环 | P resume | S5/V7 |
| D04.4 书级成果重验复用 P0 | 源 hash/范围/独立有效证据→skip | 缺证据/修改→needs-review/重验 | E _reusable_completion | S5,S7/V7,V8 |
| D04.5 页级检查点恢复 P0 | 同源页+route+marker+hash→复用页 | 未完成/损坏/变更→只重做受影响单元并重验 | E/P graph | S5/V7 |
| D04.6 重启后对账 P0 | 登记/官方状态/身份/lease→恢复视图和允许操作 | stale PID/未决 launch→人工或 reconcile，禁双启动 | P manager/R3 | S5/V2,V7 |
| D05.1 成品与中间包分层 P0 | A7 verdict+package→结果状态/位置 | 页 100% 但未验收→非成品 | E A7/P UI | S7/V8 |
| D05.2 结果阅读/定位 P1 | 允许 package ID→MD/资产预览或系统打开 | 断链/路径越界→拒绝、重验 | P output/export | S7/V8,V11 |
| D05.3 历史与版本 P1 | source/task/run/attempt→历史只读关联 | 新运行不能覆盖旧证据；删除另走治理 | P 注册证据 | S7/V8 |
| D05.4 跨路线去重总账 P0 | _current + _consolidated→唯一来源 strict verdict | schema 不明/冲突→Unknown，不累加 | U 生产总账 | S7,V8 |
| D05.5 交付/导出 P0 | 有效 strict 全量总账+授权→可携带包/集合 | 未达 100%→正式集合禁导出；部分范围显式标记 | E/P export | S7/V8 |
| D06.1 磁盘/容量展示 P0 | 目标/暂存/状态卷实测→free/reserved | 设备断开/无法读取→Unknown/阻断写 | P R3 guard | S6/V6 |
| D06.2 资产分类盘点 P0 | 注册 manifest+引用→source/necessary/evidence/cache/unknown | 分类或 owner 不明→保护 | P A6 | S6/V9 |
| D06.3 预留/低磁盘安全暂停 P0 | 每卷预算+下一写估算→准入/暂停 | ENOSPC/估算失准→原子写失败保留 checkpoint | P guard, 覆盖未知 | S6/V6 |
| D06.4 清理预览与审批 P1 | 可再生且无引用、非活动资产→计划+原因 | 依赖不明/计划失效→拒绝 | N | S6/V9 |
| D06.5 隔离/物理清理 P1 | 批准 plan+复验锁/hash/依赖→quarantine/delete receipt | TOCTOU/占用→拒绝，隔离可恢复 | N | S6/V9 |
| D06.6 缓存/图片保留策略 P1 | 资产类别、授权/验收→策略与有效期 | 无授权/关键证据→隔离保留或阻断，不能自动删 | P A3/A6 | S6,V9 |
| D07.1 原生优先/分级路由 P0 | native evidence+局部特征→当前三路线与证据 | 特征 Unknown→保守升级/复核 | E/P detector | S8/V10 |
| D07.2 传统图像算法检测 P1 | 已授权渲染/bbox/对象/像素→可疑区域 | 无法测/低信→Unknown，非零证明不存在 | E/P detector | S8/V10 |
| D07.3 OCR/视觉模型受控升级 P0 | 低价路线质量不足/复杂关系→已校准授权 route | 模型不可用→独立 OCR attempt 或 needs-review，不跳验收 | E/P A4 | S8/V10 |
| D07.4 多维资源调度 P1 | 请求/允许/实际并发与 leases→有界书/页/model 任务 | 槽不足→等待、释放和公平策略，不旁路集合锁 | P 固定槽 | S9/V12 |
| D07.5 性能及成本统计 P1 | 每阶段耗时/调用/bytes→吞吐与真实或估算成本 | 缺费率/用量→Unknown，不报 0 | P benchmark | S8,S9/V10,V12 |
| D07.6 同页面校准及回退 P0 | 同源同页 ground truth+多路线→分层质量/成本对照 | 任何 P0 缺陷→不晋升路线、回退 | E/P quality validator | S8/V10 |
| D08.1 本地服务认证与授权 P0 | 本地 session、来源/输出范围→允许动作 | 非回环/非法 Host/Origin/CSRF→拒绝；无远程控制 | P R3 仅回环 | S10/V11 |
| D08.2 路径/进程安全 P0 | 规范路径/受控 argv/身份→受限操作 | traversal/symlink/PID 复用→拒绝 | P manager/R3 | S10/V7,V11 |
| D08.3 持续运行与界面解耦 P0 | 关闭 UI/重启服务→后台不受影响/状态重建 | 主机重启→身份复核后用户恢复，不冒充仍在运行 | E/P detached | S5,S10/V4,V7 |
| D08.4 审计与权限事件 P0 | 操作、request id、结果→不可静默覆盖的审计记录 | 日志失败→写操作 fail-closed 或明确阻断；不泄漏内容 | P progress log/N 管理审计 | S10/V11 |
| D08.5 配置、版本及故障自诊断 P1 | runtime/schema/route version→兼容报告 | schema drift/依赖缺失→只读降级/人工确认 | P | S10/V4,V11 |

注：D05.4、D06.6 的 S/V 分隔分别为 S7/V8、S6/V9。D01.3 的 input-type 能力与业务授权分离；不是批准 DOCX/EPUB。D01.6 与 D05.4 不重复：前者提交幂等，后者独立验收的跨路线证据聚合。D02.5 与 D07.4 不重复：前者集合准入，后者单集合资源调度。D06.3 与 D07.4 共用资源预算而非两个调度器。D03.4 与 D08.3 分别是连接恢复和服务/进程生命周期。FUN-DOC-011 仍服从既有单页→2–5页→全量人工门；并发不绕过。P2 优化项不独立发明功能，例如 ETA/自动调节需建立在 D03.3/D07.4 已有测量上。

## 4. T03 业务场景及运行工作流

S1 多文件提交：入口=用户明确选择/拖入及输出根；接收（引用或流式暂存）→校验格式/页范围/授权/容量→SHA→参数预检→逐项持久回执→待提交。拒绝未授权、加密/损坏/类型不支持、部分上传；不生成空成功。证据：source inventory、接收 hash、请求回执、异常原因。
S2 重复与同名：同请求同 payload 返回原回执；同路径同 hash 同 scope/profile 不重复派发；同名不同 hash 独立；同 hash 不同路径保留 occurrence 且提示明确复用，不把它们自动设为候选版本。原文件被替换需重预检并分版本；不复用旧 verdict。
S3 追加/排队：进入 pending intent→冻结按相同 route/pages 等分组→取得正式集合锁→启动官方入口→核对登记回执→运行；已有活动只持久 pending，允许重排 pending 但不能改 frozen。结束后先核对残留、严格总账、锁与新批次根；失败/未明继续等待或明确人工处理。只有明确批准的后续批次准入机制可自动启动；初版手动开始。证据：冻结 list digest、root、official run ID、lock owner、receipt。
S4 只读观察：输入明确登记 root/run ID→同代次读 manifest/results/current→核对进程身份、源 hash/检查点时戳→投影→SSE；断线保留 stale，重连全量；缺数据 Unknown。不要调用有副作用 status_task 或抢锁。统计分 queued/processing/processed/strict accepted；不能用 processed 100% 或历史状态推断 passed。证据：读源路径、generation、observed_at、epoch/seq、数据原因。
S5 失败/退出/恢复：故障分类→可重试/需人/安全阻断；持久 checkpoint 与已确认结果→确认旧 worker/token/PGID 无残留→同 run resume、attempt+1→原清单/参数/源 hash 重验→重用已严格通过书/已验证页→只做缺项→重新独立验收。用户 cancel 用官方控制，任意手动 pause 未证则禁用；浏览器/服务退出不影响后台，主机重启绝不自动把旧 PID 当活跃。证据：attempt、failure、checkpoint、resume receipt、revalidation。
S6 磁盘与资产：同一卷按实测 free−reserved−floor≥预测下一步写入量才准入，失败前保存已完成 checkpoint；ENOSPC 走原子写失败、安全停止、释放空间后重新对账。盘点→分类→只生成可清理计划→用户批准→锁内重验 hash/引用/路径/owner/再生性→先同卷 quarantine（可回滚，暂不释放空间）→独立确认后 purge；低盘时不自动误删。证据：盘点/保护原因/plan digest/receipt。源文件、必要图、有效验收证据、活动文件和 Unknown 不删。
S7 成品/独立验收：A7 重读当前来源范围、页单元、资产、Unknown 和内容质量→passed/needs-review/failed→书级严格核验→跨路线按来源版本和覆盖去重→有效总账。只有 100% 来源 strict accepted 的冻结目标集合允许既有正式集合导出。尚未通过时可看工程预览但显式非成品；部分页合格只代表选定范围，不能计 full-book strict。成品复制移动后断链检查；后续人工编辑另版本，不覆盖 canonical index/验收证据。
S8 页级识别：A2 原生提取+文字层完整性→对象/bbox/表格/候选渲染探测→native-only（确认无复杂视觉且质量门通过）；否则 native-plus-visual（保留正文）或 visual-only（原生不足）。扫描页先校准的 local OCR/结构重建，必要再升级小视觉/强视觉；不是强制 OCR 覆盖原生。不确定的空间关系、数字、箭头必须保留 Unknown；已授权且必要时留最小充分图。模型未授权不调用，失败/低信独立 attempt；一页失败登记并继续其他页，最终覆盖校验不能绕过。输出 raw evidence、路由原因、质量门、页面单元。
S9 并发：先单集合单书，固定资源容量；测得允许范围后同一批量适配器内部选择有界并发，等待与实际执行分离；书、页、模型、内存、磁盘各自限额。每书隔离输出与证据；聚合器单写发布代次；取消/残留隔离先于下一次派发。证据：lease、峰值资源、完成吞吐及失败日志。
S10 安全运行：只读观察与写操作权限隔离；本机用户通过明示授权的根和操作 ID 使用服务；未授权来源/网络模型/文件写入拒绝；每次管理写操作可审计。守护服务只恢复管理视图或按批准策略对账，不自动恢复生产。版本不兼容则降级只读。

### 4.1 单本 Workflow A（复用，不改阶段）

| 阶段 | 进入/执行 | 退出证据/异常 |
|---|---|---|
| A1 来源预检 | 已授权 source/范围→媒体/指纹/可读性 | source inventory，source-invalid/unsupported/needs-review 停 |
| A2 原生内容提取 | 对当前文档提取文字/结构/定位 | native model；extract-timeout/字段 Unknown |
| A3 视觉来源资产保留 | 授权且需要渲染/复杂区域时；已有可读图复用 | 资产引用、hash、geometry；缺图失败，授权不足不默认保留 |
| A4 页面视觉理解 | 原生优先、局部证据→已校准 OCR/视觉路线 | OCR raw、结构/冲突/Unknown；不得凭非空输出通过 |
| A5 Markdown 组装 | 页面单元守卫、原文顺序、跨页衔接、轻度整理 | index、页面覆盖与必要图；原始证据不改；保留规定修复阶段人工门 |
| A6 命名与来源登记 | 确定性命名、来源 occurrence、hash/关系与引用 | manifest/reader projection；冲突拒绝覆盖 |
| A7 资产包验收 | 独立重读当前包及适用 gate，检查页/结构/图/Unknown | verdict+检查项；失败或待复核返回最早故障阶段，不自判 |

真实 A1–A7 由已有 Workflow、Skill、Contract 约束，工作台只展示可证明的检查点，不假定每个媒体有相同页码或实时阶段。技术失败与语义验收分离；扫描文字、结构、广告策略分层，现生产 preserve-only 默认不自动清洗广告或翻译。将外部单本电子书修复规范用于 Workflow 之前必须经治理确认（例如图片守恒与生产必要图裁剪、简繁转换与 R11 非翻译等差异）；本轮不合并规范。

## 5. T04 多方案论证与选择

下列分数是方案适配的定性顺序，不是实测评分。成本低/中/高是相对增量；所有推荐均为 candidate。

| 问题 | 备选与对比 | 推荐及验证 |
|---|---|---|
| 真本机文件 | A 普通浏览器 File stream：兼容高、需复制 bytes/额外空间、断网可恢复但不获原路径；B native picker 本机服务：可得真实路径、低复制、需 UI 线程/平台授权；C File System Access：便捷但 handle 非绝对路径、权限/兼容不稳；D Electron/Tauri：原生拖路径好、打包/权限维护高 | B 为大文件引用入口，A 为 Web 真拖入补充（显式暂存/估算）。不可用假 path/文件名猜测。若真拖入必须零复制，另做原生壳可行性 V1；不提前迁移 R3。 |
| 持久登记 | JSON 原子文件简单但多项/并发冲突和查询弱；SQLite 事务/唯一键、局部查询佳但需本机卷、schema/backup；外部 DB 运维高 | SQLite 候选，仅管理意图和官方关联索引；现官方 manifest/状态/验收仍权威。短事务、唯一约束、busy timeout、本机卷、备份/recovery V2。无需部署服务 DB。 |
| 动态追加 | 改活动 source-list（破坏冻结/续跑，否决）；独立 pending intent+后续官方批次（小改、可恢复）；同批动态 manifest（大改、需要重建契约） | 选择独立意图，非第二套生产队列。只在未冻结时排序；冻结后不能改，初版手动准入。V3。 |
| 根/ID 冲突 | 重用 conversions/hybrid（同根冲突，否决）；不同 output root（隔离强但集合锁不同、总账语义需确认）；同父目录不同固定 batch root（共享官方锁，保留旧根但契约对新根/登记与总账需扩展） | 候选：同父目录 sibling conversions/hybrid--b-<stable-id>，旧根不迁移，route 不从 dirname 猜；不要把它当当前受支持新批次入口，不传任意 --run-id。先验证 root ID、共同锁、_current/_consolidated 范围 V3，再通过人工门修改正式契约。若不支持，先使用人工独立根且不得并行、须正式批准与总账定义。 |
| 调度 | Web 多 CLI 并发绕锁（否决）；官方 runner 内部有界书级并发（复用锁/证据，需改 serial 聚合、资源/故障）；多 worker 服务+消息队列（维护高/双权威风险） | 先保持串行；后在同一正式 runner 内增量，单 admission、单汇聚者，验证 V12。 |
| 状态 | 直接 status_task 高频轮询（副作用，否决）；读文件快照+SSE（R3 复用，代次/缓存/总账待证）；改引擎主动事件（更细但侵入大） | 先 R3.1 真数据对账；GET snapshot/SSE，Unknown；只有缺证据才候选增补正式只读观测契约。V4/V5。 |
| 恢复 | 重开新 root（重复、旧证据混淆）；原 root resume+证据重验（已有实现）；从头强制重转（仅用户明确新版本，成本高） | 同 run/新 attempt，严格验证完成内容、不自动改参数；V7/V8。 |
| 临时资产 | 按年龄/后缀清除（否决）；manifest/引用/生命周期可再生性+plan/执行（复杂但安全）；保留全部（磁盘不可持续） | 先只读盘点，再计划+隔离+批准 purge；持锁重验，默认保守。V6/V9。 |
| 识别成本 | 全视觉（质量不保证且贵，否决默认）；全 OCR（图关系失真）；原生/像素特征/OCR/视觉按证据与校准 | 复用现有三路线与 quality validator；不按模型大小决定；先校准后改变默认。V10。 |

### 5.1 身份和锁的关键边界

- source_occurrence_id = 本机授权来源登记 ID（路径是 locator，不等于身份）；source_version = sha256+size+immutable bytes/snapshot 指纹。多路径同 bytes 不自动合并；候选逻辑文档关系必须人工声明。
- request_id = 客户端幂等键，绑定 canonical payload digest；同键异 payload→409。work_key = occurrence/source_version+标准化页 selector+影响输出/授权的配置摘要+workflow/profile/version+目标结果作用域。不同配置是新 work 版本，不是原 attempt。
- intent_id 在持久登记时分配；冻结 batch_id 不可变，list digest、配置、scope、root 唯一约束；官方 run_id 仍由后台管理器按真实根生成。attempt_id 记录每次 launch/resume 的官方 resume_count 或可靠关联，不伪造生产 ID；page_unit_key=(source version, selected unit/page, route+relevant stage/version), evidence hash。
- reference 模式预检和执行前后重新 hash/stat；源变动拒绝、保留证据并新版本。对要求绝对不变的输入用明确授权的本机托管不可变副本或文件系统快照，引用模式无法证明恶意边读边改后复原。上传 stream 计算 hash，临时 .part 同卷原子 rename 后才登记。源路径含空格、中文、换行（现每行路径格式无法表达，拒绝或用托管安全名并记录 occurrence）、符号链接、移动、同名都要测。
- 本机 SQLite 只管 pending 意图、请求回执、批次关联和管理审计；不会从 DB 状态派生真实完成。事务不能覆盖外部进程启动：先 durable launch-intent+预期 root，调用官方入口，收到回执事务关联；崩溃/超时→按 root/registry/身份对账，若未明确未启动则禁止重发。禁止在 DB 事务中等待长 worker。freeze 后不得更新 source-list，已验收工作只读取/复核证据。
- 先持有官方准入锁再占资源，资源申请固定顺序且不持上游槽无限等待；实际 owner/slot 不明则拒绝扩容。不在 UI 查询中抢锁。单台所有共享模型/CPU/内存/磁盘预算需统一核验。队列锁范围为父目录，未来 sibling 根要在同一父目录，不能加子父目录绕开；非 PDF 入口也必须受相应资源和全局工作台 admission 约束，现状未知。

## 6. T05 架构、数据、状态与 API 候选

```
Local Web UI / optional native file chooser
  -> authenticated loopback control API (not a converter)
  -> intent registry / admission adapter (SQLite candidate)
  -> existing pdf_background_task + official collection lock
  -> existing lan_pdf_markdown_batch + A1–A7 single-source workers
  -> independent validator and official cross-route ledger
  -> reader packages (read-only)
Read-only observer -> official registered state + immutable evidence -> snapshot/SSE -> UI
Resource protection -> same official leases/manifest + authorized plans, never separate runner
```

数据唯一权威：
| 对象 | 权威 | UI/管理记录 |
|---|---|---|
| 来源选择、pending、请求幂等、批次意图 | 候选登记库 | 显示及待提交编辑 |
| 批次清单、实际 run、attempt lifecycle | 后台管理器/正式 manifest | 只存关联/快照，不改写 |
| 页级 route、checkpoint、资源证据 | 正式 producer、资源槽 | 只读投影；没有就 Unknown |
| 质量/严格验收、选中 run | 独立 validator + _consolidated | 不从进度、退出码或 DB 推断 |
| 成品 | 受验证的 canonical index / reader package | 用户编辑另工作副本 |
| UI 连接 | observer epoch/sequence | stale/connected 独立于任务 |

候选状态分轴（不是改动现有状态枚举）：
- 提交轴：draft→preflight→pending→frozen→admission_pending→linked；失败为 rejected 或 admission_unknown（对账后 linked/pending，禁止未知时重发）。未冻结可撤回，冻结只能经官方控制/重新建新意图。
- 执行轴：queued/waiting_resource→running→processing_finished；paused/cancelled/failed/interrupted 由可信正式状态/证据映射；resume 产生新 attempt、原 run 不变；旧进程身份不可验证在 UI 为 Unknown，不主动写 failed。原生字段/映射版本都保留。
- 验收轴：not_run→validating→passed | needs-review | failed；evidence/version drift→stale（停止作为有效交付依据，不能覆盖历史 verdict）；选页 passed 不等于 full-source strict acceptance。
- 交付轴：unavailable→engineering_preview→eligible→delivered（复制/链接验证、授权且严格通过）；集合需全部 strict，不允许部分冒充全量。连接断开不是执行失败。
- 进度：requested units、observed processed units、accepted units、full-book strict source count 分开；百分比仅分母已知且属于同一 source/range 时展示。真实并发区分 requested/allowed/active，资源等待与模型等待单独展示。ETA/费用需样本/费率，没有就是 Unknown。源版本不变不等于证据仍有效。

资源：book slots、page/CPU workers、model requests、RAM 上限及每 volume reserved bytes 分别约束；初始正式 book=1、local-pdf=1、visual-model=1；现有 reader scan 可达 4 local page workers 不等于 4 本。先测 1→2→4（达到条件才扩），模型租约、限流、故障释放/继承、死锁及 starvation 要复验；速度与成本必须以严格通过书/页为分母。

API 候选（R3 现有 GET /api/snapshot,/api/events 不变；以下写接口均未实现、未批准）：
| 动作 | 输入/输出 | 错误/约束 |
|---|---|---|
| POST /api/sources/import 或 native pick | request id, stream/受信选择回执→source ids/hash/preflight | 413 quota、422 invalid、403 not-authorized、partial 明示；不接受任意路径执行 |
| POST /api/intents | source ids, normalized config, idempotency→intent ids | 409 key-conflict；授权不足 403，未知类型 422 |
| PATCH /api/intents/{id} | expected revision, order/withdraw→new revision | frozen/revision conflict 409 |
| POST /api/batches/freeze | selected intents/revision→immutable id/list digest | 冲突 409，无有效输入 422 |
| POST /api/batches/{id}/admit | expected version, request id→official run/receipt or pending | collection-busy 409 或明确等待，ambiguous 503/admission_unknown，不重发 |
| POST /api/runs/{id}/cancel 或 /resume | official id, expected attempt/version, request id→receipt | unverified identity/residual 409，unsupported 422 |
| GET /api/snapshot、/api/events | 已登记范围→schema/epoch/sequence/observed_at/rows/evidence/reason | stale/Unknown，非 200 即失败，200 不代表质量通过 |
| GET /api/results/{id}、/api/resources | allowlisted IDs→package/validator/volume/asset summary | unsafe/not-found 拒绝，无任意 filesystem GET |
| POST /api/cleanup/plans、/api/cleanup/plans/{id}/apply | 策略、plan digest、用户确认→plan/receipt | expired/dependency/active lock 409；无授权 403；默认仅预览 |

API 安全：只绑定 127.0.0.1，Host/Origin/CSRF/session token 强校验，默认无跨域；本机仍非天然可信。请求大小、流式上传、速率、并发读取与日志脱敏；禁止 shell 字符串/任意命令/用户提交 CLI argv，使用受控参数。所有写路径限定授权 root，resolve+no-follow/文件身份检查（目标主机验证），拒绝 symlink、路径穿越、越界、网络盘 SQLite；读操作不触发状态变更/资源锁/总账重算。管理日志记录请求/操作者/作用域/结果，不写模型密钥和书全文。密钥存于现有可信配置，不进 UI/Git。UI 重启可由官方状态重建；不自动重启生产服务或重算总账，恢复需人工许可或确认的策略。

## 7. T06 关键可行性与验收矩阵

目前以下 V 均是未执行的测试计划；已有 R3 fixture 结果只是 E04 的历史证据。本轮没有伪造 PASS。真实只读生产取证与隔离故障测试严格分开；后者使用独立 state/output/slot root、测试材料且禁止命中生产锁/任务。每次记录基准 commit、主机/OS/工具版本、输入 hash、命令/结果、真实证据路径、预期/实际、PASS/FAIL/BLOCKED、影响及回滚；不能用测试副本证明生产。

| V / 关联域和场景 | 最小 Demo / 反方攻击 | 通过硬门、证据 |
|---|---|---|
| V1 D01/S1 | 本机实际 Web 拖 2–3 PDF（含中文/空格、同名、较大文件）、native picker、权限取消、断流、低盘、加密/错误类型/页范围 | 不猜绝对路径；上传 hash 等于源、partial 不登记、reference 有授权与源变动验证；真实磁盘测量；未授权拒绝。 |
| V2 D01/02/04 S2/3/5 | 10k 合成登记项压力；同 request 并发/重试、同名不同内容/同 hash 不同路径、崩溃点（commit 前后、launch 前后）、路径替换；SQLite busy/backup | 不重登/不双启，同键异参数冲突；所有请求可对账；有效来源隔离，未决 launch 不自动重发；吞吐、内存、p95、完整性报告。 |
| V3 D02 S3 | 在隔离同父根建立 A/B 候选根，运行 A 时追加 B，排序待提交，反复启动，尝试篡改 frozen，单 PDF 和混合输入；同 route 不同批次及 _current/_consolidated 登记 | A 清单 hash 不变，B busy/待提交，只有锁持有者执行；稳定 run ID 不冲突；根在同锁域；结束后 B 正确准入；无第二生产队列。若现入口不支持则 BLOCKED，明确需要正式契约扩展。 |
| V4 D03/08 S4/10 | R3.1 真机只读，已有已登记 run；实际浏览器 SSE、刷新、断连、stale、代次错配、同名、非活跃 PID；隔离测试 16/100/1000 行、多个客户端 | 真实 UI 与同代次官方证据一致；未测 Unknown；冻结文件及不可变报告 hash 不变，区分自然推进；不调用 status_task，不重启任务；记录 p95/CPU/IO/响应与更新延迟。 |
| V5 D03 S4 | 只读真实 _current/_consolidated 样本、跨路线同源及冲突、resource owner/PID 过期 | 计数和证据一一对应，不相加路线计数；lease 可靠才显示 active；只读获取，未取得 schema/真实占用则 BLOCKED，不写回。 |
| V6 D01/04/06 S6 | 隔离小容量测试卷：下一本前低盘、页渲染/模型写/组装/报告写中途 ENOSPC、多个路径同卷/异卷，释放空间后恢复 | 受保护 floor/reserve、无损坏/假 passed、已验收成果未变、暂停原因和原 run 恢复；若仅 hybrid resume 有 guard，记录缺口。无真盘可用则 BLOCKED，不填估值。 |
| V7 D04/08 S5/10 | 受控 worker kill、主机重启模拟/实测、cancel residual、PID 复用、进度写一半、源修改、模型超时 | 只在验证身份后取消；残留阻断；同根新 attempt；已验收书/page 0 次重复执行（有效证据不变），受影响页重做并重验；无旧 PID 误杀。 |
| V8 D04/05 S7 | 选页 passed、整书处理 100% 但 A7 failed、缺页/重复页/错序、坏图/断链、旧 index/篡改 validator、跨路线冲突、完整包复制移动 | 假通过 0；只依据当前独立 verdict，整书严格/选页分开，最终集合仅总账 100% strict，所有实际引用可读；source/hash/范围/版本一致。 |
| V9 D06 S6 | 混合目录含 source/required/evidence/cache/active/unknown、shared asset、symlink、清理计划生成后文件替换、执行中断 | 保护项误删 0；只删除已证明可再生、无引用、非活动、授权的计划对象；锁内 hash/owner 重验，隔离可恢复，实际释放 bytes 与计划区分。 |
| V10 D07 S8 | 同一批真实页面按原生纯文、扫描纯文、全页背景+text、表格、多栏、向量图/流程图、复杂图片/模糊、图 cue 无对象等分层；每类候选至少 10 页，跨书取样、独立 holdout，人工标注真值；baseline 与各级路线用同输入和权限；记录阈值版本、CER/WER（按语言）、数字/公式精确率、阅读顺序、关系和必要图召回、P0 缺陷、延迟 p50/p95、模型调用/实际成本 | 任一漏关键文字/数字/关系、未授权调用、虚构结构、必要图丢失均否决；全层质量不得低于经确认基准/预先批准非劣阈值；建议候选成本/调用下降≥30% 且无 P0（阈值待人工确认）；无计费数据不宣称成本下降。80 页只作发现/校准起点，不证明全库质量；扩大独立样本直到统计置信和业务风险满足。 |
| V11 D03/05/08 S10 | 恶意 Origin/Host、CSRF、目录穿越、软链接替换、超大上传、错误 ID、argv 注入、未授权外部模型、日志含书内容/密钥 | 未授权写/读/调用/信号 0；只读快照不获锁、不更新总账；日志与拒绝原因可审计且脱敏。 |
| V12 D07 S9 | 仅隔离的同一官方 collection，先 1 再 2/4（若允许）；相同样本/硬件/路线，测队列等待、峰值 RSS/磁盘、槽、CPU/GPU、吞吐、调用/成本和重复恢复；取消一书、模型故障、残留、聚合并发写 | 资源不越限、锁域唯一、代次一致、无交叉污染/死锁/重复成果，质量与严格通过不降；以 strict accepted books/hour 衡量收益。未实测容量就不开放 UI 扩容。 |

优先顺序：本轮只读设计。相应人工门后 R3.1 先做 V4/V5 的已登记真实只读验收；随后 V1/V2/V3 验证文件/身份/冻结/准入，再 V7/V8；V6/V9 资源，V10 校准，V12 并发。风险 P0：重复启动、状态污染、未授权文件/模型、误清理、伪造通过、跨源污染、未确认基线。P0 或 BLOCKED 不能靠综合评分抵消。

## 8. 差量与后续仓库维护

| WB | 复用 | 真正新增/未知 | 阶段（不提前执行） |
|---|---|---|---|
| 01 | A1、CLI 参数 | native picker/stream、登记、幂等 | R4 |
| 02/03 | official collection/manifest/lock | pending intent、冻结、批次根登记、排序准入 | R4（串行）/R5 |
| 04 | R3 observer、resume/validator | 真总账/资源、非集合接入、动作权限与恢复 | R3.1/R4 |
| 05 | disk guard、asset manifest | 分类/预算、计划/隔离/清理 | R6 |
| 06 | 固定 resource slots、页内并行基础 | 同队列书级并发、资源 leases/聚合 | R5 |
| 07 | detector、OCR/vision、benchmark | 缺测特征、分层非劣校准、真实成本 | R7 |
| 交付 | A7、export、跨路线总账 | 历史视图、真实 100% 总账、可携带实测 | R8 |

本轮只新增本候选审查报告；不修改 R3/R2、原始需求、机器契约、代码、测试、基线或生产运行。建议后续：
- C01–07：在现有 项目需求/需求发现/原始请求.md 按治理保存人工原话；需求发现记录.md 增量写 Fact/Inference/Conflict/Unknown 并链接本报告；项目目标与范围.md、需求说明.md 待人工讨论修订，需求树只同步已存在正文。不要另建平行中文/英文权威。
- C08：在既有 项目治理/问题与决定记录.md 明确人工决定；满足 U-B01/U-B02 与其他阻断范围后才 C09。
- C10–18：在唯一中文需求体系按批准承载功能、场景、行为、验收与追溯；英文 requirements/FUNCTIONS.md 等仅旧参考，禁止先将其升级。C19–22 才确立架构。
- R3.1：已有 workbench/R3-REPORT.md 追加真实只读证据，workbench/README.md 同步硬门；必要观测修复在批准后进行。R4–R8 才按批准增量修改后台入口、批量适配器、资源/路由等，不重复 R2/R3。
- 生产契约、Workflow、Skill、测试和 registry 只能在相应人工门之后变更。当前报告不进入生产 runtime 上下文。

人工待决定：
1. U-B01 输入范围（PDF/PPTX 以外 DOCX/EPUB/独立图，已有实现不等于批准）。
2. U-B02 源图/截图、技术临时渲染、传输到 LAN/在线模型、持久保存及替代交付权限。
3. 是否接受 native picker+Web 本地暂存双入口；引用原文件 vs 复制的默认值、容量及权限。
4. 是否同一工作台管理多个集合/输出根；后续批次手动/自动；跨批全局验收分母定义；同 bytes 不同 occurrence 如何人工复用。
5. 停止/手动暂停/自动恢复/重试预算和可接受证据；默认不自动重启生产。
6. 磁盘 floor/上限/缓存期限、隔离与永久删除权限、模型/资源/费用预算、非劣阈值。
7. 历史 44 项原表缺失：已核回上轮八域及工作包，但未获得逐项原文。若找回只做差量交叉映射，不重复整树。
8. 交互/主机与生产真实验收未证；总账 schema/资源实际占用/新批次登记/安全并发/降本收益均 Unknown。

## 9. 分项评分（自审，不是假装外部专家）

每维按五项 20 分（充分设计且有适用证据 20；部分 10–15；未证 0–5；具体按证据折减）。此为当前可实施审查成熟度，不是生产验收；七维等权。无实际独立专家或真实主机 Demo。关键硬门未过。

| 维度 | 分数 | 五项分值与扣分依据 |
|---|---:|---|
| 原始目标/需求 | 90 | 20 目标、20 保真边界、20 八域映射、15 新旧冲突、15 人工范围；U-B 未决 |
| 功能覆盖/去重 | 80 | 20 八域、20 逐项输入输出、15 异常恢复、15 追溯、10 旧44逐项未找回 |
| 工作流/恢复 | 85 | 20 A1–A7、20 入队/验收、20 异常、15 恢复边界、10 真实故障未测 |
| 技术可行性 | 65 | 15 文件平台证据、15 登记方案、15 锁/根源码、10 资源/总账 Unknown、10 Demo 未执行 |
| 架构/数据 | 80 | 20 权威、20 身份、15 状态、15 API/安全边界、10 外部启动一致性未证 |
| 质量/成本 | 75 | 20 保真门、20 复用路线、15 校准设计、10 实际样本缺失、10 实际收益缺失 |
| 安全/验收 | 85 | 20 保护、20 反方矩阵、20 证据门、15 授权、10 真实故障/安全未证 |
| **综合** | **80.0** | (90+80+85+65+80+75+85)/7=80。未达95，不宣布通过。 |

已完成：基准源码/文档定位对账、8 域候选、47 功能重构、10 场景、6 类流程及 A1–A7、方案/身份/状态/API、12 个最小验证场景及风险、差量与后续顺序。未完成：完整历史44逐项继承、人工 C08/C09/C18/C22、真实 V1–V12、R3.1 生产验收、任何正式开发。既有 R3 报告 75 分与本轮 80 分评价对象不同，不能相加。

## 10. 下一轮唯一任务（人工门前）

D2MD-WORKBENCH-R3-DG：完成 C08 待决定包与候选差量核对。先读本报告，基准检查只看新提交差异；如可得，补旧44逐项对账；只针对八项待决定事项请求明确决定，记录 Fact/Inference/Conflict/Unknown，更新既有中文发现文档（不复制功能树）。没有明确人工决定时保持 draft；不生成 C09，不启动 R4、不运行或修改生产。不允许重新做 R2/R3 或本报告的完整推理。C08 明确后按治理建立 C09，补全 C10–18 和 C19–22；相应许可后优先 R3.1 的本机只读 V4/V5，再进入隔离 V1–V3。只有正式硬门通过且独立证据支持，才允许下一阶段。输出：决定表、未决项、影响、唯一后续任务、证据和分项等权评分（仅新证据调整）。
