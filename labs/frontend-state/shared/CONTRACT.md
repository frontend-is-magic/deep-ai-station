# 公共前端状态实验：冻结合同

版本 `frontend-state-v1`；包 `frontend-state-typescript.zip`，React + TypeScript + Tailwind + Jotai + 源码 Button。Node 24 / pnpm 10.32.1。无后端、fetch、API、WebSocket、模型调用或任意代码执行入口。开发依赖安装可联网；实际实验只处理固定本地事实。

## 三张卡与组件

固定顺序：

| ID       | 标题                 | 描述                                       |
| -------- | -------------------- | ------------------------------------------ |
| lesson-1 | 用 Props 展示课程卡  | 将标题、说明与状态传入可复用组件。         |
| lesson-2 | 用事件更新事实       | 父组件处理明确的完成与收藏意图。           |
| lesson-3 | 用派生 atom 计算视图 | 从事实计算统计和筛选结果，不保存重复数据。 |

LessonCard 接受 card / completed / favorite / onCompletedChange / onFavoriteChange props。父组件读取 atoms 并处理事件；卡片不直接访问 store 或 localStorage。完成与收藏使用语义 checkbox 的 checked 值传递明确意图，不用 toggle。重复设置 true 或 false 幂等。

## 状态与持久化

唯一键 `frontend-state-lab:v1`，精确 JSON `{ "version": 1, "completedIds": [], "favoriteIds": [] }`。只能持久化此形状，不含筛选、计数、百分比、卡片标题、平台学习记录或任何用户文本。

两个 ID 集合无重复；每次序列化按 lesson-1 / lesson-2 / lesson-3 排序。加载接受已知唯一 ID 的不同输入顺序并规范为卡片顺序，不自动写回。版本/精确字段/数组/已知唯一 string ID 不合法时整份不采用，先用空事实并提示。无数据也是空事实。初始化、筛选、渲染不写 storage；事实实际变化时写一次该键；重复同意图不写。明确操作可覆盖此前坏数据。

filterAtom 是内存状态 `all | pending | favorites`，刷新回 all。derived atoms：completedCountAtom、favoriteCountAtom、completionPercentAtom、visibleCardsAtom。全局分母永远3；百分比 `Math.round(completedCount / 3 * 100)`，显示 0 / 33 / 67 / 100。pending只隐藏已完成卡，favorites只显示已收藏卡，筛选不改变任何全局统计。

`src/state.ts` 导出 `cards`、`STORAGE_KEY`、`createLabState(storage)`；storage 是只需 getItem/setItem 的对象或 null。createLabState 返回真实 Jotai vanilla store 及 atoms（factsAtom、filterAtom、visibleCardsAtom、completedCountAtom、favoriteCountAtom、completionPercentAtom、storageIssueAtom、setCompletedAtom、setFavoriteAtom）。两个写 atom 输入 `{id:'lesson-1'|'lesson-2'|'lesson-3', value:boolean}`。factsAtom 对使用者只读，只有写 atom 更新私有事实。原生测试通过 store.set/get 验证真实派生关系。

独立错误变体可在临时解包 `src/state.ts` 中，将 completedCountAtom 的表达式 `get(factsAtom).completedIds.length` 改成 `get(factsAtom).completedIds.length + 1`；同一套正常 UI 统计断言应拒绝。不得把变体通过当作正常证据。

## 可访问 UI 合同

- h1：`组件与派生状态实验`。
- region：`全局统计`；三个 output 标签：`总完成数` / `总收藏数` / `全局完成率`。文本分别 `0 / 3`、`0 / 3`、`0%`（随事实更新）。
- select：`显示课程`；option value `all` / `pending` / `favorites`，文本 `全部课程` / `未完成课程` / `收藏课程`。
- region：`课程卡片`；article accessible name 为上表标题，`data-card-id=lesson-N`；标题 h2；每卡 checkbox label 固定 `已完成` / `已收藏`（在 article 内定位）。
- 空筛选文本：`当前筛选没有课程`；button `显示全部课程` 仅令 filter=all。
- region：`事实与派生值`；pre 标签 `持久事实 JSON` 显示 Facts 精确 shape；pre 标签 `派生视图 JSON` 显示 `{filter, visibleIds, completedCount, favoriteCount, completionPercent}`，其JSON不持久化。完整纯文本、可折行，较长内容可键盘滚动。
- 所有 checkbox/select/button 使用原生键盘交互；375px没有页面横向溢出。
- 无存储错误时 status：`只保存完成与收藏事实；筛选和统计由本页派生。`
- 坏数据 status：`本地记录格式无效，未采用；本页先使用空事实，操作后可覆盖。`
- 读取不可用 status：`浏览器存储不可用，本页仅使用内存；刷新可能丢失。`
- 写失败 status：`写入浏览器存储失败，已保留本页内存；刷新可能丢失。`

错误提示是可访问 role=status，不回显原始存储内容或异常。恢复后一次成功写入可清提示；不新增导入、导出、跨标签同步、后台监听或自动恢复。

## 包命令与文件

解压根目录：`pnpm install --frozen-lockfile`，`pnpm check`（Prettier + Vitest + tsc/Vite build）。`pnpm dev` 默认仅127.0.0.1:5198，`pnpm preview` 默认仅127.0.0.1:5198，strictPort，CLI可覆写随机端口。无代理或API配置。关闭只回收自己启动的进程。

源码白名单预期13文件：package.json、pnpm-lock.yaml、tsconfig.json、vite.config.ts、index.html、src/main.tsx、src/App.tsx、src/LessonCard.tsx、src/state.ts、src/state.test.ts、src/style.css、src/components/ui/button.tsx、src/lib/utils.ts。
shared5文件：README.md、CONTRACT.md、EVIDENCE.md、AGENTS.md、.gitignore。另含 manifest.json 与 .prettierrc.json，合计20 ZIP成员。构建仅包含这些文件。

README练习要求学习者自行新增“已收藏且未完成”派生统计，初始版不实现、不自动评分。EVIDENCE记录实际操作、一个错误假设、未验证项。下载、运行、勾选本地卡片都不改变平台课程完成/实践/证据记录。
