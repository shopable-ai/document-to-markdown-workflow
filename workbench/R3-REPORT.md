# R3 实施与验收报告

任务：D2MD-WORKBENCH-R3。基线：90fce65bf82d2a93d24c88db1aa854177a20dd88。状态：代码实现及隔离验证完成，生产验收未完成。

## 1. 需求与功能对账

保留 [需求说明](../项目需求/需求说明.md)、[需求树](../项目需求/需求树.md) 的 R01—R11；退役 R12 不复用。[历史功能说明](../requirements/FUNCTIONS.md) 仅作对账；WB 为专项工作包，非独立需求权威。

| 工作包 | 既有需求/功能联系 | 本轮边界 |
|---|---|---|
| WB-01 文件提交 | R01、R10；FUN-DOC-001/004 | R4 |
| WB-02 等待顺序 | R10；来源隔离与调度 | R5 |
| WB-03 追加批次 | R10；FUN-DOC-010 | 后续 |
| WB-04 观察与恢复 | R09、R10；FUN-DOC-005/010 | R3 只读，生产对账待完成 |
| WB-05 资源治理 | R06、R07；FUN-DOC-008/009 | R3 磁盘与登记容量；R6 清理 |
| WB-06 并发 | FUN-DOC-010 | R5 |
| WB-07 识别降本 | R03—R07；FUN-DOC-007/008 | R7 |

## 2. 源码事实与实现契约

- [后台管理器](../adapters/pdf_background_task.py) 的 status_task 存在生命周期写入分支，不能用于无副作用的轮询。仅从已登记状态、实际 PID/PGID/worker token 及固定证据文件读取，进程身份不确认则 Unknown，不写 failed，不发信号。
- [批量适配器](../adapters/lan_pdf_markdown_batch.py) 的 manifest/results/current 发布代次、冻结清单、来源路径、SHA-256 和严格验收统计是对账依据。禁止跨代次混合；同名不同路径分别显示。页处理完成不代表整本严格通过。
- [资源槽](../adapters/pdf_resource_slots.py) 的锁查询可能创建或获取锁。本服务不试抢锁、不新建锁；仅显示已登记容量和输出根所在磁盘实测余量。实际占用、可回收文件量和保护明细 Unknown。
- [批量运行说明](../references/lan-pdf-markdown-batch.zh.md) 定义跨路线总账路径 conversions/_consolidated/source-consolidation.json。仓库未取得其运行样本与字段契约，不能推断全局已验收数或合并单路线计数。只报告其可读取性与 Unknown。待生产取证后实现确切适配。
- [长任务契约](../contracts/long-task-interaction.yaml) 和既有生产入口不改。仅观测明确指定的 PDF collection。非集合任务、非 PDF 的状态接入不属于本轮已实现范围。
- server.py 标准库、固定 GET 路径、显式根目录和 ID 白名单、拒绝不符路径/符号链接/非普通文件、8 MiB 读取上限。仅绑定 IPv4 回环；Host/Origin 检查、无命令输入。回环不等于认证，同机有权限的其他程序仍可访问；禁止公网暴露。可信本机文件系统为前提。
- HTTP/SSE 同源，3 秒完整快照、2 秒缓存，服务 epoch/序列。断线不把旧快照标作实时。刷新重取，重连不依赖历史事件。阶段只表示最新有效检查点或终态，不保证等同当前活跃子阶段；无足够页数/耗时证据则 Unknown。
- 正式界面 index.html/app.js/style.css 沿用 R2 四页布局，使用真实 API，无内置模拟数据。R2 preview 文件不改。所有未实现写操作禁用。

## 3. 实测证据（2026-11-25）

| 测试 | 结果 | 边界 |
|---|---|---|
| Python unittest 隔离回归 | 16/16 PASS，4.121s | 临时夹具，非生产 |
| 同名来源、冻结清单、代次错配、缺失/损坏/越界证据、身份失效、计数冲突 | PASS | 单元与隔离服务 |
| native/hybrid 检查点，旧/错源进度，页数与严格验收分离 | PASS | 真实源码契约构造夹具 |
| HTTP/SSE 全量快照、断开重连、新代次，3/10→10/10→needs-review | PASS | 实际 HTTP 客户端和独立测试进程；严格计数保持 1 |
| 文件 SHA-256/mtime 不变、隔离测试 worker 未受影响 | PASS | 不外推为生产安全已验收 |
| Node app.js 语法 | PASS | 静态检查 |
| Chromium DOM 交互 | PASS | 实际 UI；测试 harness 注入 EventSource；同名来源、筛选、展开、资源、390px、断开/恢复交互，无 JS 错误 |
| Chromium 实际 HTTP/SSE、刷新与自动重连 | BLOCKED | 导航 net::ERR_BLOCKED_BY_ADMINISTRATOR；保留 browser_check.py |
| 当前生产任务/总账/资源对账与真实浏览器 | BLOCKED | CodexPro 本机连接不可用，OpenDesk 本机连接返回 429；未读取生产运行证据 |

测试命令（仓库根目录）：

```bash
python3 -m unittest discover -s workbench/tests -p 'test_*.py' -v
node --check workbench/app.js
python3 workbench/tests/dom_check.py
python3 workbench/tests/browser_check.py
```

浏览器测试需要已安装的 Playwright 与 Chromium（可用 CHROMIUM_PATH）。DOM 测试替身仅存在 tests/，不是正式模式。尚未实测长时间运行、实际集合规模、多浏览器客户端负载及 Mac 主机兼容性。历史 R2 的 12 项测试不计入本轮。

## 4. 多维自审与反方检查

评分是工程自评，不是独立专家评审。每维四项，每项 25 分：通过 25、部分 12.5、阻塞 0。等权平均；生产硬门优先于分数。

| 维度 | 通过/部分/阻塞 | 分数 |
|---|---|---:|
| 界面 | 渲染、筛选/详情、响应式及禁用操作；真实联网阻塞 | 75 |
| 状态准确 | 代次/来源、严格计数区分；阶段/资源部分；生产总账对账阻塞 | 62.5 |
| SSE | 实际 HTTP 更新、重连全量、DOM 断线标识；真实浏览器刷新重连阻塞 | 75 |
| 生产隔离 | 只读代码、入口限制、夹具证据与 worker；真实生产观察阻塞 | 75 |
| 需求一致 | 既有编号、R2 保留、范围；WB-04 真实阶段与总账部分 | 87.5 |
| 可维护 | 标准库、分层、可复现测试与说明；实际主机负载阻塞 | 75 |

综合 75/100，未达 95，不宣告 R3 生产验收完成。反方审计：状态文件不能证明进程存在；PID 单独不足；代次冲突不能拼接；100% 页数不等于验收；路线计数不等于总账；锁探测不能偷偷写；旧快照不得伪装实时；夹具/DOM 结果不得冒充生产或浏览器联网验收。

## 5. 下一阶段：R3.1 生产只读验收

使用本机已登记任务、实际 state root/output root/run ID，核验冻结清单、官方快照、最新有效检查点、独立来源总账和真实资源信息。补总账字段映射、状态/阶段依据与必要的只读资源证据；数据不可得就保持 Unknown。禁止调用有写入分支的 status_task、禁止重启/暂停/恢复生产任务、禁止重算或写回验收总账。实际浏览器验证刷新、断网重连、同名来源、错误和资源展示；记录前后不应改变的登记文件/历史证据哈希，区分生产自行推进的状态更新。实测观测开销并核验任务未受干扰。对每个验收门记录 PASS/FAIL/BLOCKED 和证据路径。所有硬门通过且有真实证据后才考虑 R4。保持 R2 原型、生产队列、验收规则不变。
