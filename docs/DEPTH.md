# 深度登记 anomalyattr（指标异常归因与维度下钻）

- 规模：中型（8 个功能模块 / 8 个页面 / 126 条测试）
- 核心规则一：**稳健 z 异常检测**——按「门店 × 渠道」建日序列，取当日之前最近 28 个**同层**历史点（工作日 / 休息日分开，区间左闭右开，不含当日），`z = 0.6745 × (x − 中位数) / MAD`；`|z| ≥ 3.5` 为候选，相邻业务日同向连续 2 天为确认；同层点不足 14 不判；MAD 为 0 时退到平均绝对偏差，仍为 0 则本点不判。
- 核心规则二：**LMDI 量价分解**——`净收入 = 可售房晚 × 出租率 × 均价`，权重用对数平均，`Δrev = Δ可售 + Δ出租率 + Δ均价 + 残差`；残差显式量化到分；任一侧因子为 0 时整组记 `A_ZERO_BASE` 并计入残差。
- 核心规则三：**复盘与敏感性**——对每条确认异常重算一次基线：同层窗口先剔节假日点（占三成以上即标 `R_HOLIDAY_THIN`），MAD 为 0 退到平均绝对偏差，两者都为 0 时不做除法、记 `R_NO_DISPERSION`；因子用 LMDI 在事件窗口与其前等长窗口之间分解；再用窗口 21/28/35 × 阈值 2.5/3.5/4.5 扫九个组合，任一组合不再成立就把结论标 `unstable` 并列出翻转组合。
- 使用者与工作流：蓝湾酒店集团经营分析组 8 人，每天 120 门店的渠道日报在 11:00 前汇总；过去靠肉眼翻表，跌幅只能手工拆到量或价。现在导入当日快照即得到「哪些序列连续异常 + 掉在哪一因子 + 哪个门店贡献最多」，四条账在页面上可直接对平；复盘页再把每条异常翻一遍，给出基线是否被节假日拉偏、换窗口换阈值后结论还成不成立。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 指标接入 | 上传或选样例，看到编码、列映射、逐行诊断 | POST /api/import、POST /api/import/preview、页面 / | `app/modules/m1_ingest/handler.py` `app/modules/m1_ingest/service.py` `app/modules/m1_ingest/reader.py` `app/modules/m1_ingest/decode.py` `app/modules/m1_ingest/columns.py` | `tests/test_ingest.py` |
| M2 日历与归一 | 查看字段定义、试算单行、确认时区与分层 | GET /api/contract、POST /api/contract/preview | `app/modules/m2_calendar/handler.py` `app/modules/m2_calendar/service.py` `app/modules/m2_calendar/calendar_rules.py` `app/modules/m2_calendar/tz.py` `app/modules/m2_calendar/contract.py` | `tests/test_calendar.py` |
| M3 质量隔离 | 看原因码分布、导出隔离行、核对三账 | GET /api/quarantine、GET /api/quarantine.csv | `app/modules/m3_quality/handler.py` `app/modules/m3_quality/service.py` `app/modules/m3_quality/rules.py` `app/modules/m3_quality/dupes.py` `app/modules/m3_quality/quarantine.py` | `tests/test_quality.py` |
| M4 异常检测 | 看水位时间轴、确认点与候选点明细 | GET /api/anomalies、GET /api/anomalies/timeline、页面 /anomalies | `app/modules/m4_detect/handler.py` `app/modules/m4_detect/service.py` `app/modules/m4_detect/stratify.py` `app/modules/m4_detect/madz.py` `app/modules/m4_detect/series.py` | `tests/test_detect.py` |
| M5 归因与批次 | 看三因子分解、维度份额、同源重跑 | GET /api/attribution、POST /api/attribution/rerun、GET /api/batches、GET /api/reconcile、页面 /attribution /drilldown /batches | `app/modules/m5_attrib/handler.py` `app/modules/m5_attrib/service.py` `app/modules/m5_attrib/lmdi.py` `app/modules/m5_attrib/dimension.py` `app/modules/m5_attrib/batches.py` | `tests/test_attrib.py` |
| M6 报告与导出 | 看运行清单、导出证据文件 | GET /api/report、GET /api/export/{kind}、页面 /report | `app/modules/m6_export/handler.py` `app/modules/m6_export/service.py` `app/modules/m6_export/csv_out.py` `app/modules/m6_export/report_build.py` `app/modules/m6_export/manifest_out.py` | `tests/test_export.py` |
| M7 异常复盘 | 打开一条确认异常，看基线、因子、动作与稳定性；扫九个窗口×阈值组合 | GET /api/review、GET /api/review/{event_id}、GET /api/review/sensitivity、GET /api/review/cards.csv、页面 /review | `app/modules/m7_review/handler.py` `app/modules/m7_review/service.py` `app/modules/m7_review/baseline.py` `app/modules/m7_review/sensitiv.py` `app/modules/m7_review/corr.py` `app/modules/m7_review/cards_csv.py` | `tests/test_m7_review.py` `tests/test_m7_sensitivity.py` |
| M8 门店退网清除 | 发起不可逆清除、看四账前后差额与残留、导出无门店标识凭据、中断后续跑 | POST /api/purges、GET /api/purges、GET /api/purges/{purge_id}、GET /api/purges/{purge_id}/vouchers/{kind}.csv、页面 /purge | `app/modules/m8_purge/handler.py` `app/modules/m8_purge/service.py` `app/modules/m8_purge/csv_out.py` `app/store.py` | `tests/test_m8_purge.py` |

端到端：`tests/test_e2e.py`（脏样例 → 隔离 → 三账 → 检测 → 归因 → 导出，含 4xx 与幂等重跑）；复盘链路 `tests/test_m7_review.py::test_cards_match_confirmed_events`（检测 → 复盘卡 → 导出）。

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 历史导出给的是 GB18030 字节，HTTP 头却声明 UTF-8 | `open()` 崩或整表乱码，列名对不上 | 声明编码只作首个候选，严格解码失败回退 GB18030 并记 `N_CHARSET_FALLBACK` | `app/modules/m1_ingest/decode.py` | `tests/test_ingest.py::test_gb18030_fallback_decodes_chinese_headers` `tests/test_ingest.py::test_naive_utf8_decode_would_fail` |
| TRAP-02 | 自家导出的 CSV 带 UTF-8 BOM | 首列名变 `\ufeffhotel_code`，按列名取值全空 | 解码前剥 BOM，留 `N_BOM_STRIPPED` 提示 | `app/modules/m1_ingest/decode.py` | `tests/test_ingest.py::test_bom_stripped_before_header_mapping` |
| TRAP-06 | `1,234.56`、`１２３４.５０`、`980元/间夜` | `float()` 抛错，或把全角串当字符串 | NFKC + 去千分位 + 剥单位后缀，再 Decimal 化 | `app/modules/m2_calendar/handler.py` `app/modules/m2_calendar/service.py` | `tests/test_calendar.py::test_thousands_and_fullwidth_numbers_normalized` `tests/test_calendar.py::test_unit_suffix_stripped` |
| TRAP-07 | 房价列混着 `N/A`、`-`、`未知`、空串 | 被当成 0 参与合计，均价被拉低 | 哨兵清单集中定义，返回三态 `None`，落 `Q_SENTINEL` 隔离 | `app/config.py`、`app/modules/m2_calendar/service.py` | `tests/test_calendar.py::test_sentinel_is_three_state_not_zero` |
| TRAP-11 | 同一快照写成 `2026-11-03T22:00:00Z` 与 `2026-11-04T06:00:00+08:00` | 两个写法落进不同业务日，序列多一个或少一个点 | 先折算 UTC，再取东八区本地日；无偏移按 +08:00 | `app/modules/m2_calendar/tz.py` | `tests/test_calendar.py::test_timezone_offsets_collapse_to_same_business_day` |
| TRAP-13 | 补报数据在 3 天后才到，业务日已落后水位 | 直接丢弃，跌幅被抹平 | 迟到点仍计入有效点，标 `late_flag`；超过补报窗口另记 `L_OUT_OF_WINDOW` | `app/modules/m3_quality/rules.py` | `tests/test_quality.py::test_late_arrival_is_kept_and_marked` |
| TRAP-14 | 同键多条快照，其中一对快照时间相同而数值不同 | 后写覆盖，冲突被悄悄藏起 | 排序键 (快照瞬时, 行号)；同快照时间数值不同整键隔离 `Q_DUP_CONFLICT` | `app/modules/m3_quality/dupes.py` | `tests/test_quality.py::test_duplicate_snapshot_later_wins` `tests/test_quality.py::test_duplicate_conflict_isolated_both_rows` `tests/test_quality.py::test_naive_last_write_wins_keeps_conflict` |
| TRAP-18 | 0 记录 / 仅表头 / 全空行三种空输入 | 都返回“成功导入 0 行”，下游除零 | 三态分开，各自返回 `E_NO_ROWS` / `E_HEADER_ONLY` / `E_ALL_BLANK`（422） | `app/modules/m1_ingest/columns.py`、`app/modules/m1_ingest/reader.py` | `tests/test_ingest.py::test_empty_header_only_state` `tests/test_ingest.py::test_empty_all_blank_state` `tests/test_ingest.py::test_empty_no_rows_state` |
| TRAP-23 | 基线窗口与对比窗口的端点归属 | 两端都算，跨度多一天，相邻期间重复计入 | 一律左闭右开 `[start, end)`；基期结束 = 当期开始 | `app/modules/m2_calendar/calendar_rules.py`、`app/modules/m4_detect/stratify.py`、`app/modules/m5_attrib/service.py` | `tests/test_calendar.py::test_half_open_span_excludes_end` `tests/test_detect.py::test_half_open_window_excludes_current_day` `tests/test_attrib.py::test_windows_are_half_open_and_touch` |
| TRAP-24 | 三因子各自四舍五入后合计与总差不符；份额凑不到 1 | 报告里差额和因子差 0.01，份额合计 100.01% | 因子用 40 位精度算，残差显式量化到分；份额尾差由「其他」或最大组吸收 | `app/modules/m5_attrib/lmdi.py`、`app/modules/m5_attrib/dimension.py` | `tests/test_attrib.py::test_lmdi_residual_is_explicit_zero` `tests/test_attrib.py::test_dimension_shares_conserve_to_one` `tests/test_attrib.py::test_zero_total_delta_marks_share_none` |
| TRAP-27 | 复盘基线剔掉节假日后全等，MAD 与平均绝对偏差都是 0 | `0.6745 × (x − median) / mad` 直接除零，接口 500，或写死一个「无异常」 | 离散度为 0 时不进入除法：`review_z` 返回 `(None, R_NO_DISPERSION)`，卡上标退化、置信降为低 | `app/modules/m7_review/baseline.py` | `tests/test_m7_review.py::test_mad_zero_returns_code_not_division` `tests/test_m7_review.py::test_flat_series_card_flagged_degenerate` |
| TRAP-28 | 基准组合下确认的事件，把阈值从 3.5 提到 4.5 就消失 | 只报基准组合的结论，把不稳定的判断当稳定的用 | 九个组合各跑一次检测，逐事件记保留/翻转；任一组合不成立就把结论标 unstable 并列出翻转组合 | `app/modules/m7_review/sensitiv.py`、`app/modules/m7_review/service.py` | `tests/test_m7_sensitivity.py::test_flip_marks_unstable_and_lists_combo` `tests/test_m7_sensitivity.py::test_threshold_monotonic_confirmed_points` |
| TRAP-31 | 门店退网后直接 `DELETE` 明细行 | 旧批次行账/点账/金额账立刻不平，财务无法复核历史收入 | 先按 run×渠道×业务日聚成不含门店标识的汇总凭据，再在同一事务删明细并按剩余数据复算四账；报告差额逐项等于凭据承接量 | `app/modules/m8_purge/service.py`、`app/store.py` | `tests/test_m8_purge.py::test_voucher_revenue_equals_removed_detail_revenue` `tests/test_m8_purge.py::test_report_differences_match_vouchers_and_balanced` |
| TRAP-32 | 只做读取隐藏，隔离记录或重导时门店又出现 | 隔离 CSV、复盘卡、同源重导仍能定位退网门店，等于没清 | 物理删除 points/quarantine 明细并扫描检测与复盘残留；退网门店在采信阶段即被剔除，同源重跑复用、新批次也不恢复 | `app/modules/m5_attrib/service.py`、`app/modules/m8_purge/service.py` | `tests/test_m8_purge.py::test_detection_and_review_have_no_purged_hotel` `tests/test_m8_purge.py::test_same_file_reimport_does_not_restore_decommissioned_hotel` `tests/test_m8_purge.py::test_new_batch_filters_decommissioned_hotel_rows` |
| TRAP-33 | 清除过程中断在删了一半的批次 | 读路径看到半清除明细，续跑时凭据被重算成 0、账目永久不平 | 凭据写入+明细删除+summary 复算放进单个 SQLite 事务整体回滚；每批次提交后落 progress，续跑读回清除前快照只补未完成批次 | `app/store.py`、`app/modules/m8_purge/service.py` | `tests/test_m8_purge.py::test_interrupted_purge_resumes_and_finishes` `tests/test_m8_purge.py::test_interrupted_after_commit_resumes_remaining_runs` |
| TRAP-34 | 清除与导入并发，导入把退网门店又写回去 | 清除白做，库里重新出现门店标识 | 进程内写锁 + `purges` 表 running 标记双重门：导入先检查再拿锁，清除在跑直接 409 E_PURGE_RUNNING | `app/modules/m5_attrib/service.py`、`app/store.py` | `tests/test_m8_purge.py::test_import_blocked_while_purge_running` `tests/test_m8_purge.py::test_api_import_conflict_during_purge` |

## 对账规则

- 行账：读入记录 = 采信行 + 被覆盖行 + 隔离行（样例 1016 = 1001 + 4 + 11）
- 点账：网格点 = 有效点 + 隔离点 + 缺失点（样例 1008 = 1001 + 5 + 2）
- 金额账：采信行净收入合计 = 有效点净收入合计（Decimal，2 位）
- 分解账：净收入差 = 可售房晚差 + 出租率差 + 均价差 + 残差
- 四账在 /api/reconcile、/api/report、/batches 页面与 `/api/export/manifest.json` 导出中逐条给出，差额非 0 显式为「不平」。
- 复盘账：确认事件数 = 复盘卡数；每张卡在九个敏感性组合里记一次「保留」或「翻转」，保留 + 翻转 = 9 × 卡数。
- 清除账（M8）：清除前后差额 = 凭据承接量——有效点差 = 凭据点数；隔离表差（隔离行 + 被覆盖行）= 凭据条目数；净收入差 = 凭据净收入；行账差 = 采信差 + 隔离表差。完成前采信、隔离、检测、复盘四类残留必须全部为 0。

## 反例与不变量

- 反例（朴素实现会挂）：`tests/test_quality.py::test_naive_last_write_wins_keeps_conflict`（按行序覆盖把冲突藏掉）、`tests/test_ingest.py::test_naive_utf8_decode_would_fail`、`tests/test_calendar.py::test_naive_float_parse_would_raise`、`tests/test_m7_review.py::test_mad_zero_returns_code_not_division`（朴素除法直接抛 DivisionByZero）。
- 边界：`tests/test_ingest.py::test_full_last_line_without_newline_is_accepted`（末行无换行但列齐不算截断）、`tests/test_quality.py::test_late_window_boundary_exactly_three_days`（晚 3 天在窗内、第 4 天才超窗）、`tests/test_detect.py::test_baseline_minimum_boundary_thirteen_points`（13 点不判、14 点才判）、`tests/test_attrib.py::test_zero_total_delta_marks_share_none`（总差为 0 不除零）。
- 不变量：`tests/test_m7_sensitivity.py::test_threshold_monotonic_confirmed_points`（阈值单调：确认点随阈值只减不增）、`tests/test_m7_sensitivity.py::test_combos_account_for_every_event`（守恒：保留 + 翻转 = 9 × 卡数）、`tests/test_attrib.py::test_rerun_is_idempotent`（幂等）、`tests/test_quality.py::test_rows_identity_holds` `tests/test_quality.py::test_points_identity_holds` `tests/test_quality.py::test_revenue_identity_holds`（守恒）、`tests/test_quality.py::test_shuffled_input_gives_same_accounts`（顺序无关）。
- 端到端：`tests/test_e2e.py::test_full_pipeline_end_to_end`。

## 已知不覆盖

- 只支持单文件导入；一次导入多个门店的多份文件需要重复调用 POST /api/import，跨文件合并留到下一期。
- 分层只分工作日 / 休息日两类；春节这类连续长假没有单独建层，节假日表只维护 2026 年。
- 异常检测只在 `revenue_net` 上跑；`occ`、`adr` 直接作为分解因子，不单独做异常判定。
- 补报窗口固定 3 天，没有按门店或渠道分别配置；配置项在 `app/config.py` 里。
- 未做权限与多租户；内部 8 人共用一套服务与一个 SQLite 卷。
- 复盘卡只对已确认事件生成；候选点不上卡，只在 /anomalies 看。
- 敏感性轴固定九组合（窗口 21/28/35 × 阈值 2.5/3.5/4.5），不在页面上自由扩展；单组合明细另有接口，范围 14-42 天与 1-10。
- 同批异常只按「同一天、同方向、不同门店」判定；跨天不同长度的事件不合并，也不做因果推断。
- 复盘基线只剔除节假日点，未按门店单独配置节假日表；节假日表只到 2026 年。
- 退网清除的并发控制是单进程内的写锁 + `purges` 表标记，部署形态本来就是内部 8 人共用一个 SQLite 卷、单 uvicorn 进程；多进程水平扩展时需要换外部锁。
- 退网清除不可逆：只提供按 `hotel_code` 整店清除，不支持按日期或渠道部分清除，也没有“撤销清除”；恢复门店只能凭外部原始文件重新走导入，且退网名单仍会过滤它。
- 凭据按 run×渠道×业务日聚合，同一渠道同一业务日若有多条退网门店点会合并计数，无法从凭据回溯到单日以下的单行快照。
