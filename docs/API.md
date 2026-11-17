# 接口 anomalyattr

服务端口 `18209`，容器内 `8000`。所有金额与因子以字符串返回，避免 JSON 浮点误差。

## 页面

| 路径 | 内容 |
|---|---|
| `/` | 指标接入、四账、原因码、批次刻度 |
| `/anomalies` | 水位时间轴、确认点与候选点、事件清单 |
| `/review` | 复盘卡、敏感性矩阵、同批异常、逐日证据 |
| `/attribution` | 三因子分解、门店贡献、窗口覆盖 |
| `/drilldown` | 渠道分解与门店明细 |
| `/batches` | 批次刻度、运行清单、同源重跑 |
| `/report` | 运行报告与导出口 |
| `/purge` | 门店退网清除：发起/续跑、任务清单、差额与残留核对、凭据导出 |

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/healthz` | 存活与库大小 |
| GET | `/api/import/samples` | 样例清单（名称、字节、sha256） |
| POST | `/api/import/preview` | 只采信：编码、列映射、诊断前 20 条 |
| POST | `/api/import` | 整批导入：采信 → 归一 → 质量 → 落库 → 清单 |
| GET | `/api/contract` | 字段定义、派生字段、规则、阈值、节假日 |
| POST | `/api/contract/preview` | 单行试算，返回归一后字段或原因码 |
| GET | `/api/quarantine` | 隔离明细、原因分布、三账 |
| GET | `/api/quarantine.csv` | 隔离行 CSV（行号 + 原因码 + 原值） |
| GET | `/api/anomalies` | 检测结果；`status` / `channel` / `hotel` 可筛选 |
| GET | `/api/anomalies/timeline` | 日期刻度 + 异常标记 + 水位 |
| GET | `/api/attribution` | `dimension=hotel_code\|channel`、`cur_days`、`base_days` |
| POST | `/api/attribution/rerun` | 同源重跑，命中同一批次时 `reused=true` |
| GET | `/api/batches` | 批次历史 |
| GET | `/api/batches/{run_id}` | 运行清单、参数摘要、账目备注 |
| GET | `/api/reconcile` | 四账与逐条检查 |
| GET | `/api/report` | 报告结构与结论 |
| GET | `/api/export/{kind}` | `points.csv` / `anomalies.csv` / `attribution.csv` / `manifest.json` |
| GET | `/api/review` | 复盘卡、敏感性九组合、同批异常；`run_id` 可指定批次 |
| GET | `/api/review/{event_id}` | 单张复盘卡（含逐日证据与同向对照） |
| GET | `/api/review/sensitivity` | 单组合明细：`window` 14-42、`threshold` 1-10 |
| GET | `/api/review/cards.csv` | 复盘卡 CSV（BOM + 标记码） |
| GET | `/api/purges` | 退网清除任务清单、进行中任务与已退网门店 |
| POST | `/api/purges` | 发起或续跑门店不可逆清除（body：`{"hotel_code": "H04"}`） |
| GET | `/api/purges/{purge_id}` | 清除报告：四账前后差额、凭据守恒、残留核对 |
| GET | `/api/purges/{purge_id}/vouchers/points.csv` | 指标汇总凭据 CSV（无门店标识） |
| GET | `/api/purges/{purge_id}/vouchers/quarantine.csv` | 隔离汇总凭据 CSV（无门店标识） |

## 门店退网清除（M8）

清除是不可逆操作，流程为「凭据承接 → 明细删除 → 四账复算」：

- 先按 `run × channel × biz_date` 把退网门店的有效点与隔离行聚成**不含门店标识**的汇总凭据（`purge_point_vouchers` / `purge_quarantine_vouchers`），再在同一 SQLite 事务内删除明细并复算批次 summary；事务失败整体回滚，读路径看不到半清除明细。
- 任务状态只有 `running` / `done`，中断后对同一门店重发 `POST /api/purges` 即续跑：已提交批次读回进度跳过，未完成批次继续处理。
- 清除优先于导入：存在 `running` 任务时 `POST /api/import` 直接返回 409 `E_PURGE_RUNNING`。
- 已退网门店 (`state=done`) 随同源文件重导不会恢复：命中同源批次直接复用；新批次在采信阶段剔除该门店全部行并记 `N_HOTEL_PURGED`。
- 报告 `variances` 逐项核对「清除前后差额 = 凭据承接量」（有效点、隔离表条目含被覆盖行、净收入、行账），`residuals` 扫描采信、隔离、异常检测、复盘四处残留；任一不平返回 `E_PURGE_UNBALANCED` 且任务不置完成。
- 门店标识只保留在 `purges` 任务头供审计；凭据表与凭据导出均无 `hotel_code` / `hotel_name` 列。

## 失败返回

| 场景 | 状态码 | `error` |
|---|---|---|
| 样例不存在 | 404 | `E_SAMPLE_NOT_FOUND` |
| 空文件 | 400 | `E_EMPTY_FILE` |
| 缺必需列 | 400 | `E_MISSING_COLUMNS` |
| 末行截断 | 400 | `E_TRUNCATED` |
| 仅表头 / 全空行 / 0 记录 | 422 | `E_HEADER_ONLY` / `E_ALL_BLANK` / `E_NO_ROWS` |
| 维度非法 / 窗口越界 | 400 | `E_BAD_DIMENSION` / `E_BAD_WINDOW` |
| 批次不存在 | 404 | `E_BATCH_NOT_FOUND` |
| 还没有批次 | 404 | `E_NO_RUN` |
| 导出类型未知 | 400 | `E_BAD_EXPORT` |
| 复盘窗口或阈值越界 | 400 | `E_BAD_AXIS` |
| 复盘卡不存在 | 404 | `E_CARD_NOT_FOUND` |
| 当前批次没有有效点 | 400 | `E_NO_POINTS` |
| 退网清除进行中导入冲突 | 409 | `E_PURGE_RUNNING` |
| 清除任务不存在 | 404 | `E_PURGE_NOT_FOUND` |
| 门店编码为空 | 400 | `E_BAD_HOTEL` |
| 清除后对账不平 | 400 | `E_PURGE_UNBALANCED` |

错误体固定为 `{"error": "...", "message": "..."}`，空态额外带 `empty_state`。
