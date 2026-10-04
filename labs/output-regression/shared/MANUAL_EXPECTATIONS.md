# 固定材料的手工期望表

此表由维护者逐条阅读原始 JSON、固定 read_ids 与原文 quote 后编写，并经独立审核。它不是 evaluator 输出或测试通过日志，不能证明读者已执行实验。请先自行核对材料，再与表中期望比较。P=passed，F=failed，S=skipped；格式为 parse/schema/evidence。每层失败后的层一律 S，code=null。

| case                | critical | baseline | baseline首错                                            | unsafe-candidate | unsafe首错                             | fixed-candidate |
| ------------------- | -------- | -------- | ------------------------------------------------------- | ---------------- | -------------------------------------- | --------------- |
| dev-api             | false    | F/S/S    | invalid_json（缺逗号）                                  | P/P/P            | —                                      | P/P/P           |
| dev-empty           | true     | P/P/P    | —                                                       | P/P/P            | —                                      | P/P/P           |
| dev-conflict        | true     | P/P/P    | —                                                       | P/P/P            | —                                      | P/P/P           |
| accept-api          | false    | F/S/S    | invalid_json（缺逗号）                                  | P/P/P            | —                                      | P/P/P           |
| accept-tools        | false    | P/F/S    | invalid_schema（answer是42）                            | P/P/P            | —                                      | P/P/P           |
| accept-evidence     | false    | P/P/F    | invalid_quote（原文没有“没有证据也可以自动编造引用。”） | P/P/P            | —                                      | P/P/P           |
| accept-conflict     | true     | P/P/P    | —                                                       | P/P/P            | —                                      | P/P/P           |
| accept-empty        | true     | P/P/P    | —                                                       | P/P/P            | —                                      | P/P/P           |
| accept-found-unread | true     | P/P/P    | —                                                       | P/P/F            | unread_citation（api仅found，read=[]） | P/P/P           |
| accept-unicode      | false    | P/P/P    | —                                                       | P/P/P            | —                                      | P/P/P           |
| accept-two-sources  | false    | P/P/P    | —                                                       | P/P/P            | —                                      | P/P/P           |

手工汇总，分母始终是该 split 全部 case：

| split/profile               | case数 | parse通过 | schema通过 | evidence通过 | critical通过/总数 |
| --------------------------- | ------ | --------- | ---------- | ------------ | ----------------- |
| dev/baseline                | 3      | 2         | 2          | 2            | 2/2               |
| dev/unsafe-candidate        | 3      | 3         | 3          | 3            | 2/2               |
| dev/fixed-candidate         | 3      | 3         | 3          | 3            | 2/2               |
| acceptance/baseline         | 8      | 7         | 6          | 5            | 3/3               |
| acceptance/unsafe-candidate | 8      | 8         | 8          | 7            | 2/3               |
| acceptance/fixed-candidate  | 8      | 8         | 8          | 8            | 3/3               |

各 pass_rate = 相应通过数 / case数；例如 acceptance baseline 为 7/8、6/8、5/8。candidate在两个split分别与本split baseline比较。

- 所有 dev compare：gate true，exit 0；仅是公开开发集比较，不能当作验收通过。
- acceptance baseline：5>=5 且 critical全过，gate true，exit 0。这说明机械门禁允许“普通样例仍有缺陷但相对基线未退化”，不是全部题都对。
- acceptance unsafe-candidate：7>5，但关键 accept-found-unread 失败，gate false，failed_rules=['critical_case_failed']，failed_critical_case_ids=['accept-found-unread']，exit 2。平均通过率提高不能抵消关键退化。
- acceptance fixed-candidate：8>=5 且 critical全过，gate true，exit 0。
- 任意合法 explain：exit 0，result按本表，parse/schema/evidence失败不改成CLI故障。

三个普通来源引用均为原文连续句；冲突引用为 billing-a/b 的完整 body，不能裁定哪侧符合现实。Unicode案例answer的换行/emoji/花括号只是普通字符串；API末句“取消请求不能证明供应商没有计费。”是连续摘录。双来源案例分别引用 tools/evidence，全部来自固定已读集合。表没有给 answer 语义打分。
