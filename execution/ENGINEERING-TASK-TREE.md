# 工程任务树

状态：derived。唯一来源是 ENGINEERING-TASKS.md；本树不记录一次 Run 的状态。

    ET0 执行控制与可观测基线
      ET0-01 来源、范围、授权和运行记录
      ET0-02 逐页状态、停止、取消和 checkpoint
        ↓
    ET1 单页内容质量纵向切片
      ET1-01 内容、结构、关系和 Unknown
      ET1-02 视觉判定、资产和来源
      ET1-03 reader Markdown 和标题/追溯分层
      ET1-04 路线校准
      ET1-05 独立验收
        ↓
    ET2 单文件多页面
      ET2-01 第 2–5 页小批量
      ET2-02 第 6–total_pages 和确定性合并
        ↓
    ET3 多来源编排
      ET3-01 每个来源独立 package 和 Verdict
        ↓
    ET4 测试空间
      ET4-01 全类型测试矩阵
        ↓
    ET5 可选下游
      ET5-01 知识库工作副本清洗
      ET5-02 视觉派生

ET1 是最小可行纵向闭环；没有当前来源、内容/视觉/Markdown/来源登记/独立验收的闭合，ET2/ET3 只会放大错误。

