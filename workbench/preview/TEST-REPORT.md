# R2 原型验收

环境：Chromium headless + Playwright；桌面 1180×850、窄屏 390×844。完整独立 HTML 通过 `set_content` 加载（环境限制 `file://` 导航）。Node JavaScript 语法检查通过。

| 检查 | 结果 |
|---|---|
| 初始渲染；顶部说明删除 | PASS |
| 真实浏览器 drag_to 等待任务重排 | PASS |
| 上下移按钮重排 | PASS |
| 运行中任务拖动手柄不可用 | PASS |
| 多文件 DataTransfer drop；拒绝非 PDF | PASS |
| 选择列表重复文件过滤 | PASS |
| 新批次登记；当前任务清单与顺序不被覆盖 | PASS |
| 暂停期间不推进、不启动下一批 | PASS |
| 当前批次执行结束后启动下一批；历史待复核数量保留 | PASS |
| 资源清理预览 | PASS |
| 无页面 JavaScript 异常 | PASS |
| 390px 窄屏无横向页面溢出 | PASS |

共 12/12 项通过，仅限原型交互。文件拖入使用合成 DataTransfer 测试，不等同于真实操作系统 Finder 拖放验收。尚未接入实际转换引擎；不证明正式并发、安全暂停、文件删除或验收已经实现。
