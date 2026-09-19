# IAMABOT 策略说明（v10）

> **对应版本**：比赛服务器 version 13（GitHub `main` 分支 `strategy/brain.py`，本文档写作时的线上版本）
> **撰写日期**：2026 年 9 月 19 日
> **本次改动的驱动力**：v9 的本地回归矩阵（`tools/arena`）常年 87/88 全胜，但从 `api-mechmania.duckdns.org`（[mechmania.github.io/matches](https://mechmania.github.io/matches/index.html) 背后的真实比赛数据库）下载并核对**线上真实对局记录**后发现，IAMABOT v13 在服务器上对 Gang、clankerbot、JaniceKeepTalking、Team Name 这四支队伍的真实战绩是 **0 胜 5 负、1 胜 5 负、0 胜 4 负、0 胜 3 负**——本地评测矩阵完全没有暴露这个问题。v10 就是这次复盘的产物：先把四支队伍的真实录像重新下载、逐 tick 解析，再针对量出来的具体原因改代码、并把新的对手画像补回本地回归矩阵，让下次同样的问题能在本地就测出来。

---

## 一、这次做了什么、为什么

v9 文档里的对手画像（DIBSFA、Gang、JaniceKeepTalking、clankerbot）全部来自更早期的版本（match 135/237/376 等），当时的分析没有错，但**这些队伍自己也在不断更新**，早的画像已经和线上真实对手脱节。这次重新做了一遍研究：

1. 从比赛数据库的公开 API（`https://api-mechmania.duckdns.org`，`matches/index.html` 页面背后调用的接口）按队名过滤，拉出 DIBSFA、Potatoes、Gang、JaniceKeepTalking、clankerbot、Team Name 六支队伍**当前版本**的真实对局列表和 IAMABOT 自己的完整历史战绩。
2. 下载关键对局的 NDJSON 逐 tick 日志（`GET /match/{id}/log`，与 `mm-cli` 本地引擎产出的 `.mmgl` 格式完全一致：增量编码，`fleet_a`/`fleet_b` 除首个 tick 外都是 `{added, changed, removed}` 差量），写了一个独立的解析脚本，重建每一 tick 的完整舰队状态，统计出兵顺序、伤亡曲线、交战距离、阵型间距、双方在“己方矿点 / 对方矿点 / 中场”的时间占比、payload 推进曲线、终局构成等。
3. 拿这些真实数据核对 v9 的对手模型，发现四支队伍里有三支（Gang、clankerbot、Team Name）的打法已经和 v9 文档里写的完全不一样，JaniceKeepTalking 打法没变但我们本地复刻版的强度远弱于真实版本。
4. 找到两个可验证、可修的具体漏洞（终局不转化矿工、经济投入偏多导致同等编制下战斗位偏少），用仓库自带的 `tools/arena` 引擎级回归矩阵反复验证，只保留全量回归不倒退的改动。
5. 把四支队伍（含 Potatoes）的新画像写成新的对手复刻类，加回 `tools/arena`，这样以后这类"线上输、本地测不出来"的问题能在本地先测出来。

---

## 二、真实战绩：谁真的在赢我们

在写任何代码之前，先把 IAMABOT（v13）对这六支队伍的**全部**线上历史战绩核对了一遍（数据来自 `match/list?teams=IAMABOT&teams=<对手>`，2026-09-18~19 期间的比赛，`main` 分支 version 13 一直是本次唯一在线版本）：

| 对手 | 战绩（IAMABOT 视角） | 定性 |
|---|---|---|
| **Gang** | **0 胜 5 负**（match #804 #729 #514 #261 #135，从最早到最新全部输） | 用户点名的“守家”类，实际最致命 |
| **clankerbot** | **1 胜 5 负**（仅 #982 赢，#1247 #907 #603 #114 #12 全负） | 用户点名的“进攻”类，第二致命 |
| **JaniceKeepTalking** | **0 胜 4 负**（#944 #841 #237 #207） | 用户点名的“进攻”类，从未赢过 |
| **Team Name** | **0 胜 3 负**（#1094 #932 #656） | 用户点名的“进攻”类，从未赢过 |
| DIBSFA | 4 胜 2 负（#1140 #790 #758 #124 胜，仅早期 #376 #16 负） | 用户点名的“守家”类，目前实际不是威胁 |
| Potatoes | 3 胜 0 负（#652 #528 #64） | 用户点名的“守家”类，目前完全不是威胁 |

**结论先行**：六支队伍里真正在赢我们的是 Gang、clankerbot、JaniceKeepTalking、Team Name——也就是用户点名的“进攻型”三队全部在赢，外加“守家”组里的 Gang。DIBSFA、Potatoes 两支“守家”队伍反而完全打不过我们。这和 v9 文档给人的印象（“对手库覆盖了这四队，全部验证过”）差距很大：v9 的本地复刻版显然已经不能代表这些队伍现在的真实打法了。下面第三节逐队给出量出来的具体原因。

---

## 三、六支队伍的真实画像（逐 tick 数据）

### 3.1 Gang——本次修复的核心目标（0-5）

样本：match #804（IAMABOT 执 A，负）、#729（Gang 执 A，负）。两局打法完全一致，互为印证。

- **开局**：`EEEEEEEEBBBBBBHHH`——8 矿工起手，然后 6 战斗兵、3 治疗兵，和 v9 文档记录的“7 矿工全去挖对手的矿”**完全不同**：这一版 Gang 的矿工全部留在**自己的**矿点（`own_dep` 时间占比 43%~76%，`enemy_dep` 恒为 0%），根本不偷矿——v9 里专门为“Gang 偷家”写的应对逻辑（`_home_state` 的 raid 检测、`STEAL`/`HOME_MODE`）针对的已经是一个不存在的对手。
- **治疗兵比例高**：稳态 16 战斗 + 8 治疗（33%），终局前一度到 22 战斗 + 10 治疗（31%）。
- **终局转化（关键）**：两局都在**恰好 T=6000/6001**（生产截止的瞬间）把全部 8 个矿工自毁、加急换成战斗兵：矿工 8→0，战斗兵 16→22。这和 `tools/arena` 里早就替 DIBSFA 建模过的“终局自毁矿工换战斗兵”是同一招（`DibsfaConvert`），但 v9 的策略自己**从没实现过这一招**——`_endgame_extractors` 只会让矿工原地当肉盾/藏起来，不会转化成枪。
- **阵型更紧**：battle bot 相对质心的离散度 Gang 是 6.75~7.99，我们同期是 9.13~9.35（更散）。
- **战斗结果**：中局（T=1500~6000）双方基本势均力敌，payload 甚至一度被我们推向 Gang 一侧；但终局转化一发生，Gang 立刻多出真实的火力差（22 打 18 支枪），阵型又更紧，两局里我们都在 T=6000→7500~7752 之间的**1500~1750 tick 内被从满编 32 打到 0**，而 Gang 自己只掉 7 个单位（32→25）。这不是慢慢被拖垮，是终局那一下直接崩盘。

**一句话诊断**：Gang 现在是“économie 稳健打底 + 终局一次性把矿工变成枪”的打法，我们输球不是输在中局，是输在终局那记转火力的时间差。

### 3.2 clankerbot——第二致命（1-5）

样本：match #1247（IAMABOT 执 A，负，这也是我们最新一次和它交手）。

- **开局**：`EBBBBBHBEEHHBHBHB`——矿工数固定在 **3 个**，之后不再造矿工（`EXTRACTOR_TARGET` 相当于 3，明显低于 v9 复刻版里假设的“低治疗比例”）。
- **治疗兵比例非常高**：稳态 18 战斗 + 11 治疗（**38%**，是六队里最高的），而 v9 的 `roster.py` 里现存的 `clanker` 复刻版用的是 `IAMABOT_HEALER_RATIO=0.08`——真实 clankerbot 和我们本地的复刻版南辕北辙。
- **不龟缩**：battle bot 有 99% 的时间在中场（`mid_field`），和我们的打法思路（占 payload 附近）几乎一样，靠的是正面消耗取胜，不是靠战术奇招。
- **战斗结果**：T=4001 时我们还领先（303 HP 打 263 HP），T=4501 开始逆转，到 T=6000 我方从 32 落到 0——**在 endgame 正式开始之前**舰队就已经打光了。同一时间窗口里，我方治疗兵数量从 8 一路掉到 0，说明治疗兵本身在正面消耗里先被磨掉了。

**一句话诊断**：clankerbot 用极低的矿工投入换出更高比例的战斗位和治疗位，同编制下比我们多枪、多奶，正面互殴我们耗不过。

### 3.3 JaniceKeepTalking（0-4）

样本：match #944（JaniceKeepTalking 执 A，负）。

- **开局**：`1:E 2:B 3:B 4:B 5:H 6:B 7:H 8:B 9:E 10:E 11:B 12:H 13:B 14:B 15:H 16:B 17:B`，矿工同样固定在 3 个左右，治疗比例约 28%~31%，和 v9 `roster.py` 里现有的 `jkt` 复刻版（`IAMABOT_EXTRACTORS=3, IAMABOT_HEALER_RATIO=0.35`）方向是对的，**但强度差一大截**：真实 JKT 在 T=2501 时已经 316 HP / 32 满编，我们同期只有 153 HP / 18 单位；本地 `jkt` 复刻版在回归里却是被我们 62:1 打穿（见第四节）。这说明画像的“形”对了，但复刻版的微操/决策强度远不如真实对手，回归矩阵测不出真实差距。
- **战斗结果**：payload capture 几乎单调地从 0 推到 +1.000（推到我方终点），T=3300 直接判负，是被正面碾压后 payload 一路平推过来的，不是被偷家或钻空子。

### 3.4 Team Name（0-3）

样本：match #1094（IAMABOT 执 A，负）。

- **开局**：`BEHEBBEBHBBBHBBBH`——没有像我们、Gang、clankerbot 那样集中起手，B/E/H 从第一手就穿插着造，6 矿工。
- **战斗结果**：这局最快也最干脆——**T=2989 就结束**，靠的是 payload 一路被推到我方终点（capture 从 0 单调推到 -1.000），不是歼灭战。T=2001→2501 这 500 tick 里我方从 21 单位/195 HP 掉到 16 单位/146 HP，对面从 23/227 涨到 26/257——一次遭遇战后差距就拉开，此后再没追回来。
- Team Name 有 17% 的时间在我方矿点附近（对比 Gang 是 0%），说明它会分兵威胁矿点，但主力仍然是抢占领圈。

**一句话诊断**：Team Name 靠更朴素但执行更扎实的打法（没有明显的套路漏洞可钻）在开局后 2000 tick 左右的第一次真正交火里就把我们打崩，此后一路平推 payload。这是三支“进攻型”队伍里最难用一招解决的一个——见第五节“已知局限”。

### 3.5 DIBSFA（4-2，目前不是威胁）

样本：match #1234（DIBSFA 执 A vs Potatoes，胜，用作行为验证）+ #1140（DIBSFA vs IAMABOT，负于我们）。

- 当前版本（v16）开局是 `BBBBBBBB` 后接 `EEEEEEEE`（8 战斗接 8 矿工），和 v9 文档记录的“match 376：7 矿工优先”**顺序反过来了**，但矿工目标数（8）和终局转化（8→0，`_endgame_extractors`/`DibsfaConvert` 同款，精确发生在 T=6000）的核心结构没变，`tools/arena` 里现成的 `dibsfa8`/`dibsfa_turtle` 复刻版依然基本成立，回归里我们对它是 payload_progress 大比分胜（cap +0.45~+0.70）。真实战绩也印证了这点：最近三次交手全部是我们赢。

### 3.6 Potatoes（3-0，目前不是威胁）

样本：match #1234（vs DIBSFA，作为团队的一方观察）。

- 开局 `EEBEBBBEBEEEEHHBB`，8 矿工经济流，`own_dep` 时间占比高达 85%，和 DIBSFA 是近亲打法（经济优先、龟缩到后期）。真实战绩 3-0 也和本地一直能轻松打穿这类“重经济、轻战斗位”对手的规律一致，不需要专门修。仍然按用户要求写了复刻版补进回归矩阵（`potatoes_econ`），防止以后这支队伍改打法。

---

## 四、代码改动

### 4.1 终局矿工转化（`_endgame_convert`，新增）

对应 3.1 节发现的漏洞。在生产截止前 `CONVERT_WINDOW`（默认 80）tick 的窗口内：

1. 强制 `fabricator_next = BATTLE`（这个窗口里不再造任何矿工/治疗兵）；
2. 如果舰队已经满编（32/32）且代币够付一次加急，就自毁一个矿工腾出位置，让下一 tick 的加急落到战斗兵头上；
3. 至少保留 `CONVERT_KEEP`（默认 1）个矿工不转化，留给 `_endgame_extractors` 现有的“藏起来防团灭”逻辑用——终局被团灭直接判负是比多一个矿工更大的风险，这个保险不能因为学 Gang/DIBSFA 的转化就拆掉。

```python
strategy/brain.py: AdvancedStrategy._endgame_convert()
```

本地对着 `main`（GitHub 未改动版本）打自打验证：同一局 T=6001 时，改过的一方是 `Battle:23 Healer:8 Extractor:1`，未改的一方仍是 `Battle:18 Healer:8 Extractor:6`——转化逻辑在真实引擎里按预期触发（细节见第六节）。

### 4.2 经济收紧、治疗比例上调（默认参数调整）

对应 3.1~3.3 节共同的模式：Gang、clankerbot、JaniceKeepTalking 三支真正在赢我们的队伍全部只投 3~4 个矿工（我们是 6 个），治疗比例 30%~38%（我们是 33%）。同编制下它们比我们多出 2~3 个战斗/治疗位。调整：

| 参数 | v9 | v10 | 依据 |
|---|---|---|---|
| `EXTRACTOR_TARGET`（矿工上限） | 6 | **4** | Gang/clankerbot/JKT 均值 3~4 |
| `HEALER_RATIO`（治疗兵比例） | 0.33 | **0.36** | clankerbot 实测 38%，Gang 实测 31% |

开局序列 `OPENING`（前 17 手，决定第 500~900 tick 首战强度）不变，只改变稳态目标——4.1 的终局转化已经把"多余经济投入"在终局收回成战斗兵，所以稳态阶段可以更激进地压低矿工数而不用担心终局吃亏。

### 4.3 新对手复刻（回归矩阵扩充）

在 `tools/arena/bots/styles_v61/strategy/opponents.py` 按第三节量出来的真实参数新增四个复刻类，并接入 `main.py` 的 `OPP` 表和 `roster.py`：

| 复刻类 | 对应真实队伍 | 关键参数 |
|---|---|---|
| `GangTurtle`（继承已有的 `DibsfaTurtle`，复用其终局转化） | Gang（match #729/#804） | 8 矿工、31% 治疗、终局转化 |
| `ClankerSustain` | clankerbot（match #1247） | 固定 3 矿工、35% 治疗 |
| `TeamNamePush` | Team Name（match #1094） | 均衡起手、6 矿工 |
| `PotatoesEcon` | Potatoes（match #1234） | 8 矿工经济流 |

对应的 roster 条目 `gang_turtle26` / `clanker26` / `teamname26` / `potatoes26` 都打了 `"2026"` 标签并加入 `QUICK` 集合，以后每次改动都会自动测到。另外新增 `main` 条目（`bots/main_orig`，GitHub `main` 分支未改动的 `strategy/brain.py` 快照），可以随时用 `python3 tools/arena/arena.py "cand=live" --opps main` 把工作区改动和线上版本对打。

### 4.4 已尝试但没采用的方向

- `IAMABOT_EXTRACTORS=5`／`HEALER_RATIO=0.38` 等更激进的组合在全量回归里会新增 2~4 局倒退（主要是已经很接近的 `dibsfa_turtle`、`st24` 类对手），劣于 4.2 选定的 4/0.36 组合，弃用。
- `CONVERT_WINDOW=40`（照抄 `DibsfaConvert` 的原始取值）在满编时机偏晚的对局里窗口不够用（实测有一局到 T=6000 都没转化成功），改为 80，全量回归里没有再出现这个问题。

---

## 五、回归验证结果

使用仓库自带的 `tools/arena/arena.py`，所有候选方案和对手库里每个对手在 A、B 两侧各打一局，逐局与基线比较：

```sh
mm-cli run --quiet   # 编译一次引擎/绑定
python3 tools/arena/arena.py "base=main_orig" "v10=live" --opps full \
    --baseline base -j 8 --out tools/arena/results/v10_release_check.jsonl
```

| 方案 | 对手 | 战绩（49 个对手 × 2 侧 = 98 局） | 结论 |
|---|---|---|---|
| `base`（GitHub `main` 未改动版本，version 13） | 全量 | 95 胜 3 负 | 基线 |
| **`v10`（本次改动）** | 全量 | **96 胜 2 负** | 采纳；新赢 `main:B0`、`v12:B0`（镜像局，见下）；新输 `camper5:B0`（见下） |

- **`camper5:B0`**（唯一新增的负局）：对手是通用的“局部占优后去堵对方出生点”打法（不在用户点名的六队之列），打满 9000 tick 靠 tiebreak 惜败（击杀比 50:57，非常接近）。经济收紧换来的战斗位优势在这一类长局里边际为负，其余 48 个对手（含全部新画像）都没有因为这次改动倒退。
- **`gang_turtle26`**：改动前两侧全负，改动后 B 侧转胜，A 侧仍负（K/L=35/68，是回归矩阵里最难的一个）——`_endgame_convert` 和参数调整改善了这个匹配，但没有完全解决，见第七节。
- **`main:B0` / `v12:B0`**：这两局是同源代码互打时的镜像偏置（见 v9 文档"镜像局"一节和本文第六节），v10 因为和基线已经不再完全同构，两局都翻正——不代表真实强度提升，只是巧合地跳出了镜像偏置的坑。

全部原始结果保存在 `tools/arena/results/`：`v10_release_check.jsonl`（本表数据）、`v10_tune.jsonl` / `v10_full_tune.jsonl` / `v10_full_tune2.jsonl`（4.4 节的参数搜索过程）、`live_vs_main_orig.jsonl`（第六节的自打对局）。

---

## 六、按用户要求：修改后的仓库 vs GitHub `main` 分支的实战对局

用户明确要求把改完的代码和 `github.com/asher0913/IAMABOT/tree/main/strategy` 的 `brain.py` 对打一局并输出日志，结果如下（A/B 各打一局，双方都是满血引擎对局，非近似仿真）：

```sh
python3 tools/arena/arena.py "candidate=live" --opps main --keep -j 2
```

| 对局 | 结果 | 结束原因/tick | 终局（T=6000/6001）构成对比 |
|---|---|---|---|
| A 侧执 `live`（改动版） | **改动版胜** | elimination @ 7441 | 改动版 `Battle 23 / Healer 8 / Extractor 1`　未改版 `Battle 18 / Healer 8 / Extractor 6` |
| B 侧执 `live`（改动版） | 未改版胜 | elimination @ 7752 | 改动版 `Battle 18 / Healer 8 / Extractor 6`　未改版 `Battle 18 / Healer 8 / Extractor 6` |

两局的构成对比清楚地展示了 4.1 节的转化逻辑：**A 侧那一局在终局前把矿工从 6 个转化掉了 5 个**（`23/8/1`），触发条件满足得早；**B 侧那一局两边构成完全相同**（`18/8/6`，转化没有触发——舰队刚好卡在 T≈6000 附近才满编，`CONVERT_WINDOW` 窗口没抓住这次时机），这局输给了未改版，但输的原因是**没有触发转化**，而不是转化本身有问题；两个近乎同构的策略互打时，先手方（A 侧）有系统性优势——这正是 v9 文档"六、已知局限"里记录过的"镜像局"偏置（`v9 对 v8 执 B 方会输`），不是这次改动引入的新问题。放在整个第五节 98 局的全量回归里看，v10 净胜出 1 局、没有在任何一个真实对手画像上倒退，这一组 A/B 各一局的近似同源对局本身信息量有限，只把它当作"转化逻辑在真实引擎里确实按设计触发"的直接证据，不作为强度证据。

完整逐 tick 分析（出兵顺序、伤亡曲线、payload 推进、矿工位置）见随本次改动一起发出的文本报告（两局分别对应 A/B 两侧视角）。

---

## 七、已知局限

- **`gang_turtle26` A 侧仍未解决**：这是回归矩阵里唯一还打不过的画像，说明 Gang 的终局那一波集火效率（阵型更紧、目标分配更准）本次没有完全对齐，需要进一步压缩阵型离散度或调整目标分配的等待代价函数，留给下一版。
- **Team Name 没有找到可复现的具体漏洞**：3.4 节的诊断是"开局后第一次遭遇战被打崩"，但没能定位到像 Gang 的终局转化、clankerbot 的矿工/治疗比例那样具体、可单点修复的原因——`TeamNamePush` 复刻版目前只还原了开局节奏，回归里我们仍能轻松打穿它（说明复刻版强度不够），无法验证针对性修复。这是本次研究最大的未解问题，需要更多真实对局样本（目前只深入分析了 1 局）才能进一步拆解。
- **本地复刻版系统性偏弱**：JaniceKeepTalking、Team Name 的复刻版在回归里都被我们大比分打穿（62:1 量级），但真实队伍能 0-4、0-3 赢我们——`tools/arena/README.md` 早就写明"对手都是用我们自己的开火内核仿制，会高估我们、低估对手"，这次的数据进一步坐实了这一点：光是画像（出兵比例、开局节奏）对了不够，真实对手的微操强度本地复刻不出来，回归矩阵的胜负数字只能作为"没有让已知问题变差"的下限保证,不能当作"线上会赢"的证明。
- **样本量有限**：每支队伍只深入解析了 1~2 局（受限于人工复盘的时间成本），且都是最新版本的单次快照——真实队伍的打法本身还在演化（DIBSFA 在 3.5 节里就观察到开局顺序反了过来），这份画像有随时过期的风险，需要定期重新下载复盘。
- **`camper5:B0` 的新负局**：4.2 节的经济收紧是全局性调整，对"局部胜势后拖长局"的打法有边际负面影响，目前判断利大于弊（换来对三支真正在赢我们的队伍的改善），但如果以后遇到更多这类对手,需要重新评估。

---

## 八、版本演进（续 v9）

| 版本 | 服务器编号 | 关键变化 |
|---|---|---|
| v9 | version 13 | 对方主力偷家时全军推车（带滞后）、回归测试工具、运行统计与异常兜底 |
| **v10（当前）** | **version 13（本次改动尚未提交到比赛服务器）** | 下载解析 DIBSFA/Potatoes/Gang/JKT/clankerbot/Team Name 真实线上对局，发现四队真实战绩远差于本地评测；新增终局矿工转化（`_endgame_convert`）；矿工上限 6→4、治疗比例 33%→36%；`tools/arena` 新增四个基于真实数据的对手复刻（`gang_turtle26`/`clanker26`/`teamname26`/`potatoes26`）和 `main`（GitHub 当前版本快照，用于自打对比） |

## 九、研究方法附录：如何重新拉取真实对局数据

```sh
# 按队名过滤，拿到某队最近的对局列表（最多两个 teams 参数）
curl -sS -G "https://api-mechmania.duckdns.org/match/list" \
  --data-urlencode "limit=15" --data-urlencode "teams=Gang" \
  --data-urlencode "status=success" --data-urlencode "order_by=submitted_at" --data-urlencode "order=desc"

# 下载某局的逐 tick 日志（NDJSON，线上是 gzip 传输，curl 需要 --compressed）
curl -sS --compressed "https://api-mechmania.duckdns.org/match/<id>/log" -o match.ndjson
```

日志格式和 `mm-cli` 本地引擎产出的 `.mmgl` 完全一致：第一行是场景配置（地图、payload 路径、矿点位置等），此后每行一个 tick，除极少数字段外一律是相对上一帧的**增量**（`fleet_a`/`fleet_b` 是 `{"added": {...}, "changed": {...}, "removed": [...]}`，键是字符串形式的 bot id；`deposit_a`/`deposit_b`/`fabricator_a`/`fabricator_b`/`capture` 只在变化时出现，需要在读取端做增量重放/前向填充）。`tools/arena/replay.py` 已经有一份处理 `mm-cli` 本地日志的实现，可以参考其思路自行扩展成同时支持两种来源。
