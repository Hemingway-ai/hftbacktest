# GLFT 做市模型深度指南（从零到一）

本文档配套 `examples/GLFT Market Making Model and Grid Trading.ipynb`，**为基础较薄弱的读者重写**：

- 假设读者只懂基础概率和高中数学
- 从订单簿、tick、随机游走这些最基础的概念开始铺
- 每一个数学概念都配数值例子和图解
- 公式推导分小步走，每一步都告诉你"为什么这一步要这样做"
- 全部读完，应该能从零理解到能自己改写 GLFT 策略

如果你只想看代码不想看推导，可以从 **第六章** 看起。如果你只想要公式速查，看 **附录 B**。

阅读节奏建议：

- 第零章（前置基础）：先把这里读懂
- 第一章到第四章：理论部分，读得慢一点，每个公式都对照数值例子
- 第五章到第八章：工程部分，对照 notebook 看
- 附录 C 是常见问题，遇到不懂的地方先去那里找答案

---

## 目录

- 阅读指南
- 第零章 — 前置基础知识
- 第一章 — 做市问题的本质
- 第二章 — 随机过程建模
- 第三章 — Avellaneda–Stoikov 框架与 HJB 方程
- 第四章 — 从 AS 到 GLFT
- 第五章 — 参数校准
- 第六章 — Notebook 代码逐段解析
- 第七章 — 网格交易的整合
- 第八章 — 回测分析、简化假设与改进方向
- 附录 A — 数学记号速查
- 附录 B — 主要公式汇总
- 附录 C — 常见问题 FAQ
- 附录 D — 参考文献

---

## 第零章 前置基础知识

如果你已经熟悉订单簿、tick、做市商、Maker/Taker 等概念，可以直接跳到第一章。否则建议先把这一章读完，后面会反复用到这些术语。

### 0.1 订单簿（Limit Order Book, LOB）

订单簿是交易所记录所有未成交挂单的数据结构。在 ETHUSDC 永续合约上，盘口看起来大概是这样（假设 tick size = 0.01 USDC）：

```text
                ETHUSDC 盘口
        ┌─────────────────────────┐
        │   卖盘（ask）            │
        │   3000.05    1.20 ETH   │← 卖五
        │   3000.04    0.80 ETH   │
        │   3000.03    0.50 ETH   │
        │   3000.02    2.10 ETH   │
        │   3000.01    3.40 ETH   │← best ask
        ├─────────────────────────┤  ← mid price = 3000.005
        │   3000.00    2.70 ETH   │← best bid
        │   2999.99    1.90 ETH   │
        │   2999.98    0.60 ETH   │
        │   2999.97    1.10 ETH   │
        │   2999.96    0.40 ETH   │← 买五
        │   买盘（bid）            │
        └─────────────────────────┘
```

要点：

- **best bid**：所有买单中价格最高的一笔，这里是 3000.00
- **best ask**：所有卖单中价格最低的一笔，这里是 3000.01
- **mid price（中间价）**：`(best bid + best ask) / 2`，这里是 3000.005
- **spread（买卖价差）**：`best ask - best bid`，这里是 0.01（一个 tick）
- **depth（深度）**：盘口某个价位上的总挂单量

### 0.2 tick 是什么

`tick` 是交易所规定的最小价格变动单位。ETHUSDC 上 tick = 0.01 USDC，意味着所有挂单价格只能是 `…, 2999.99, 3000.00, 3000.01, …` 这种 0.01 的整数倍。

在策略代码里经常把价格换算成 tick 数（"价格 tick"）：

```python
mid_price_tick = mid_price / tick_size  # 3000.005 / 0.01 = 300000.5
```

这样所有距离都用整数 tick 表示，便于处理。

**半 tick（half tick）**：notebook 里成交强度的距离被量化在 0.5 tick 的网格上。这意味着距离 "+0.5 tick"、"+1.0 tick"、"+1.5 tick" 这样的位置都会被记录。这是 notebook 里一个已知的单位小坑，第八章会展开讲。

### 0.3 Maker 和 Taker

任何一笔成交都涉及两方：

- **Maker（被动方）**：之前挂了单子等在那里，被对方主动撮合
- **Taker（主动方）**：用市价单或可成交的限价单，主动去吃 maker 的单子

为什么分这两类？

- Maker 提供流动性，交易所往往给**返佣**（手续费为负，比如 Binance USDM Liquidity Provider 计划给到 -0.005%）
- Taker 消耗流动性，要付**手续费**（比如 +0.045%）

做市商的全部目标就是**当 maker**：挂被动单等成交，赚价差 + 返佣。

notebook 里所有挂单都用 `GTX` 标志：

```python
hbt.submit_buy_order(0, oid, bid_price, qty, GTX, LIMIT, False)
#                                              ^^^
```

`GTX` = Good-Till-Crossed = "如果这单会立刻吃对手盘（即变成 taker），就拒绝挂单"。这是做市单的标准用法。

### 0.4 一笔完整做市交易的流程

假设你的做市策略某一时刻挂了：

- 买单 @ 3000.00，0.1 ETH
- 卖单 @ 3000.02，0.1 ETH

过了一会儿：

1. 某个 taker 用市价买单买入 0.05 ETH → 吃掉你的卖单 0.1 ETH 的一部分
2. 你的卖单成交 0.05 ETH @ 3000.02
3. 又过了一会，另一个 taker 市价卖出 0.08 ETH → 吃掉你的买单
4. 你的买单成交 0.05 ETH（剩余部分） @ 3000.00（实际上 0.05 ETH 因为前面卖了 0.05，库存归零）

净结果：

- 卖出 0.05 ETH @ 3000.02 = 收入 150.001
- 买入 0.05 ETH @ 3000.00 = 支出 150.000
- 毛利 = 0.001 USDC
- 加上两次成交的 maker 返佣 ≈ 0.005% × 2 × 150 ≈ 0.015 USDC
- 总利润 ≈ 0.016 USDC

这就是做市的微观盈利来源。它看起来很少，但单笔订单 1 秒可能就成交一次，一天累计能积少成多。

风险点：在第 2 步和第 3 步之间，价格可能跌到 2999.50，那时你就是单边持有 0.05 ETH 多头，账面浮亏。这就是**库存风险**。

### 0.5 做市的两个核心难题

1. **挂多远**：挂得太近，spread 太薄赚不到价差；挂得太远，根本不会成交
2. **挂多少**：挂太多，库存累积太快；挂太少，赚不到手续费

GLFT 的工作就是：根据当前市场状态，**自动算出** "挂多远" 和 "挂的中心应该怎么偏"。

### 0.6 库存（inventory）和持仓（position）

策略代码里 `position` 和数学公式里 `q` 是同一个东西：

- `q > 0`：净多头，多了几个 contract
- `q < 0`：净空头
- `q = 0`：完全平仓

举例：你挂买卖单各 1 lot。卖单先成交：`q = -1`（净空）。然后买单成交：`q = -1 + 1 = 0`（回归平仓）。

库存风险的本质：`q ≠ 0` 时，价格波动直接影响你的浮盈浮亏。

### 0.7 关于"公平价格"

策略里我们要算 bid/ask 相对于一个**公平价格** `S` 的偏移。这个公平价格代表"我们认为现在资产的真实价值"。

最简单的选择：用中间价当公平价格。这就是 notebook 的做法：

```python
mid_price_tick = (best_bid_tick + best_ask_tick) / 2.0
```

但中间价有缺点：

- 买卖盘口量极度不平衡时（比如 best bid 量 0.1，best ask 量 100），真实公平价应该更偏向 ask
- 短时间内可能有突发 alpha 信号没有反映

进阶版本可以用 micro-price、VWAP、加 alpha 信号等。这份示例为了聚焦 GLFT 主结构，直接用中间价。

---

## 第一章 做市问题的本质

### 1.1 做市商不预测方向

很多人第一次接触做市会有这个误解："做市是不是要预测涨跌？"

答：不是。

做市的核心模型是**捕获价差**。如果价格随机游走，做市商也能赚钱——因为他不押方向，只押"会有人来回买卖"。

打个比方：你在外汇兑换柜台工作，对所有客人，买入价比卖出价低一点点。只要客人**双向**都有（有人来兑欧元买美元，也有人来兑美元买欧元），你就赚每一对客人的价差。你完全不需要预测欧元会涨还是会跌。

但是，如果某天所有客人都是单边的——全都来买美元——那你的欧元会越攒越多，万一欧元真的跌了，你这堆欧元就亏了。这就是**库存风险**和**adverse selection**（被打到的总是不利方向）。

### 1.2 三组关键变量

GLFT 最终要决定的，是两个数：

```math
\delta^b,\ \delta^a \geq 0
```

分别是相对中间价的买价深度和卖价深度。报价形式为：

```math
P^b = S - \delta^b,\qquad P^a = S + \delta^a
```

`(δ^b, δ^a)` 应该由三组变量决定：

| 决定因素 | 来源 | 影响 |
| --- | --- | --- |
| 库存 `q` | 实时持仓 | 库存大 → 报价中心偏移 |
| 波动率 `σ` | 实证估计 | 波动大 → 半价差变宽 |
| 成交活跃度 `A, k` | 实证估计 | `A` 大、`k` 小 → 报价可以更紧 |

GLFT 给出的就是：

```math
(\delta^b, \delta^a) = f(q, \sigma, A, k;\ \gamma, \Delta, \xi)
```

其中 `γ, Δ, ξ` 是策略参数（风险偏好），不是从市场估出来的。

### 1.3 为什么需要一个数学模型

直接拍脑袋设定 spread 也能跑出策略，但有几个问题：

- 不同时间市场波动率差几倍，固定 spread 在低波动时太宽（不成交）、高波动时太窄（亏损）
- 库存累积没有自动机制处理，要靠人工止损
- 不同标的、不同时段都要重新调参

GLFT 给出一个**统一的公式**，把 `(σ, A, k, q)` 这些可观测量直接映射到 `(δ^b, δ^a)`。它不一定最优，但提供了一个理论指引下的自适应骨架。

---

## 第二章 随机过程建模

这一章把 GLFT 的全部建模写清楚。基础不好的读者重点看 2.1 和 2.2 的数值例子，那两节是后面所有推导的根。

### 2.1 中间价的随机过程

#### 2.1.1 从随机游走开始

想象一个人站在数轴 0 点。每 1 秒，他抛一次硬币：

- 正面 → 向右走 1 步
- 反面 → 向左走 1 步

`n` 秒后他在哪？

```text
S_n = X_1 + X_2 + ... + X_n
```

其中每个 `X_i ∈ {-1, +1}`，独立、概率各 1/2。

- 期望：`E[S_n] = 0`
- 方差：`Var(S_n) = n`（独立变量方差可加）
- 标准差：`std(S_n) = √n`

**关键观察**：`n` 秒后他的位置标准差是 `√n`，而不是 `n`。也就是说，**位置的"幅度"按 √时间 增长，不是按时间增长**。

#### 2.1.2 从离散到连续：布朗运动

如果把"每 1 秒走 ±1"改成"每 Δt 秒走 ±√Δt"，让 `Δt → 0`，得到的就是**布朗运动** `W_t`：

- `W_0 = 0`
- `W_t - W_s ~ N(0, t - s)`（增量服从均值 0、方差 t-s 的正态分布）
- 不同时间段的增量相互独立

布朗运动是连续时间里 "无方向随机游走" 的极限。

#### 2.1.3 中间价的 SDE

GLFT 假设中间价遵循：

```math
dS_t = \sigma\,dW_t
```

意思是：

- 在每一个无穷小时间 `dt` 内，价格变化 `dS_t` 是一个均值 0、方差 `σ² dt` 的随机量
- `σ` 是控制波动幅度的常数

**为什么没有漂移项**：在做市的几百毫秒到几秒尺度上，方向性收益（drift）远小于波动率带来的随机扰动，可以忽略。

#### 2.1.4 σ 的量纲

由 `Var(dS_t) = σ² dt` 可知，`σ²` 量纲是 "价格平方 / 时间"，所以 `σ` 量纲是 "**价格 / √时间**"。

**数值例子**：假设 σ = 10 tick/√s。

- 1 秒后价格变化的标准差 = 10 × √1 = 10 tick
- 10 秒后 = 10 × √10 ≈ 31.6 tick
- 1 分钟后 = 10 × √60 ≈ 77.5 tick

注意**不是** 10 秒后变化 100 tick——这是新手最常犯的错误。波动率按 √时间 缩放，不是按时间。

#### 2.1.5 从 100ms 样本估 σ

notebook 用 100ms 一个样本，记录 `mid_price_chg`。

- 单步（100ms）方差 `= σ² × 0.1`
- 单步标准差 `= σ × √0.1`

所以从单步标准差反推 σ：

```math
\sigma = \frac{\text{std}(\text{mid\_price\_chg})}{\sqrt{0.1}} = \text{std}(\text{mid\_price\_chg})\cdot\sqrt{10}
```

这就是代码里 `np.sqrt(10)` 的来源：

```python
volatility = np.nanstd(mid_price_chg) * np.sqrt(10)
```

如果换成 50ms 采样，应该是 `× √20`；500ms 采样应该是 `× √2`。

### 2.2 订单到达的随机过程：泊松过程

#### 2.2.1 泊松过程是什么

泊松过程是描述 "稀有事件随时间发生" 的标准数学模型。例子：

- 每分钟接到的电话数
- 每天发生的雷电次数
- 每秒到达某 ATM 的客户数
- **每秒打到你做市单的市场单数**

如果事件以**强度（intensity）λ 次 / 单位时间**发生，那么：

- 任意时段 `[t, t+T]` 内事件数 `~ Poisson(λT)`
- 任意两次事件间的间隔 `~ Exponential(λ)`
- **无记忆性**：不管之前是否发生过事件，下一次事件的等待时间分布不变

#### 2.2.2 直观例子

假设你做市单的成交强度是 λ = 2 次 / 秒，相当于平均每 0.5 秒被打到一次。

- 1 秒内成交 0 次的概率 = `e^{-λ} = e^{-2} ≈ 0.135`
- 1 秒内成交 1 次的概率 = `λe^{-λ} ≈ 0.271`
- 1 秒内成交 2 次的概率 = `λ²/2 × e^{-λ} ≈ 0.271`
- 1 秒内成交 ≥3 次的概率 ≈ 0.323

注意：平均 2 次 / 秒不代表"每秒一定成交 2 次"。有时候 1 秒成交 0 次，有时候连续成交 4-5 次。这种聚簇现象在 Poisson 过程中是正常的随机波动。

#### 2.2.3 为什么用 λ(δ) = A·exp(-kδ)

挂单深度越深，越不容易成交。GLFT 用指数函数描述这个衰减：

```math
\lambda(\delta) = A\cdot e^{-k\delta}
```

两个理由：

1. **经验拟合**：实证统计 limit order 的成交强度，确实大致是指数衰减
2. **解析便利**：这种形式让 HJB 方程能解出闭式解

参数意义：

- `A` 是"贴着中间价（δ=0）时"的成交强度，**单位次 / 秒**
- `k` 是衰减系数，单位 1/tick；`k` 大意味着稍微挂远就很难成交

数值例子：假设 `A = 1.08 /s`，`k = 0.04 /tick`。

| 挂单深度 δ | λ(δ) = A·e^{-kδ} |
| --- | --- |
| 0 tick | 1.08 / s |
| 10 tick | 1.08 × e^{-0.4} ≈ 0.72 / s |
| 30 tick | 1.08 × e^{-1.2} ≈ 0.33 / s |
| 50 tick | 1.08 × e^{-2.0} ≈ 0.15 / s |
| 100 tick | 1.08 × e^{-4.0} ≈ 0.020 / s |

也就是说，挂在 100 tick 远，平均 50 秒才被打到一次。

#### 2.2.4 取对数变成直线

对 `λ(δ) = A·e^{-kδ}` 取对数：

```math
\log \lambda(\delta) = \log A - k\delta
```

这是关于 `δ` 的一次线性函数。

- 截距是 `log A`
- 斜率是 `-k`

只要从市场数据中估出经验 `λ(δ)` 曲线，对 `(δ, log λ)` 做线性回归，就能拿到 `A` 和 `k`。

这是后面 `linear_regression()` 的全部数学基础。

### 2.3 买单成交计数过程 N^b 和 卖单成交计数过程 N^a

定义：

- `N^b_t`：截至时刻 `t`，你的买单累计成交次数（每次 `+1`）
- `N^a_t`：截至时刻 `t`，你的卖单累计成交次数

它们都是泊松计数过程：

- `N^b` 强度 = `λ(δ^b) = A·e^{-kδ^b}`
- `N^a` 强度 = `λ(δ^a) = A·e^{-kδ^a}`

注意一个重要事实：策略**主动控制**这两个过程的强度，因为 δ^b、δ^a 是策略选的。这正是后面"最优控制"问题的来源。

### 2.4 库存过程 q_t

每发生一次买单成交，你买入 `Δ` 个单位（库存 +Δ）；每发生一次卖单成交，库存 -Δ：

```math
q_t = \Delta\cdot N^b_t - \Delta\cdot N^a_t
```

或者写成微分形式：

```math
dq_t = \Delta\,dN^b_t - \Delta\,dN^a_t
```

`Δ` 在最简单情形下取 1。它代表"每次成交对应的库存增量"。

### 2.5 现金过程 X_t

每次买单成交，付钱：现金减少 `P^b × Δ = (S - δ^b) × Δ`。
每次卖单成交，收钱：现金增加 `P^a × Δ = (S + δ^a) × Δ`。

```math
dX_t = (S_t + \delta^a_t)\Delta\,dN^a_t - (S_t - \delta^b_t)\Delta\,dN^b_t
```

### 2.6 财富过程 V_t

账面财富 = 现金 + 库存按当前公平价计价：

```math
V_t = X_t + q_t\cdot S_t
```

对 `V_t` 求微分（用 Ito 公式 + 上面三个过程的关系）：

```math
dV_t = dX_t + q_t\,dS_t + S_t\,dq_t
```

代入各项：

```math
dV_t = (S + \delta^a)\Delta\,dN^a - (S - \delta^b)\Delta\,dN^b + q\sigma\,dW + S(\Delta\,dN^b - \Delta\,dN^a)
```

把含 `S` 的项合并：

```math
(S + \delta^a)\Delta dN^a - S\Delta dN^a = \delta^a\Delta dN^a
```

```math
- (S - \delta^b)\Delta dN^b + S\Delta dN^b = \delta^b\Delta dN^b
```

最终得到一个非常关键的公式：

```math
dV_t = \delta^a_t\Delta\,dN^a_t + \delta^b_t\Delta\,dN^b_t + q_t\sigma\,dW_t
```

#### 2.6.1 这个公式的物理意义

`dV` 分成三块：

1. `δ^a Δ dN^a`：每次卖单成交贡献 `δ^a × Δ` 利润（卖在 ask，比 fair 高 δ^a）
2. `δ^b Δ dN^b`：每次买单成交贡献 `δ^b × Δ` 利润（买在 bid，比 fair 低 δ^b）
3. `q σ dW`：库存按公平价波动带来的浮盈浮亏（**唯一的风险源**）

也就是说，做市商的：

- **利润来源** = 前两项的累计成交贡献
- **风险来源** = 第三项（库存暴露 × 价格波动）

GLFT 的全部工作就是：在最大化前两项的同时控制第三项的方差。

---

## 第三章 Avellaneda–Stoikov 框架与 HJB 方程

第二章已经把"做市问题"写成数学语言。现在我们要解这个问题：找出最优的 `(δ^b, δ^a)`。

### 3.1 优化目标：CARA 效用

#### 3.1.1 为什么不直接最大化期望财富

直觉上，做市商要的不就是"赚得多"吗？为什么不直接最大化 `E[V_T]`？

考虑两个策略：

- 策略 A：100% 概率赚 100
- 策略 B：50% 概率赚 250，50% 概率亏 50

期望都是 100，但策略 B 风险更大。一个有风险厌恶的人会选 A。

最大化期望财富的"风险中性"假设，会让模型推荐策略 B 或者更激进的策略，这不符合做市商的实际偏好。

#### 3.1.2 CARA 效用函数

AS 用指数效用（Constant Absolute Risk Aversion）：

```math
U(V) = -e^{-\gamma V}
```

其中 `γ > 0` 是风险厌恶系数。

性质：

- `U` 关于 `V` 单调递增（财富越多效用越大）
- `U` 是凹函数（边际效用递减）
- 凹度由 `γ` 控制

数值例子：γ = 0.05，比较两个策略：

- 策略 A：100% 赚 100，效用 = `-e^{-5} ≈ -0.0067`
- 策略 B：50% 赚 250，50% 亏 50，期望效用 = `0.5·(-e^{-12.5}) + 0.5·(-e^{2.5}) ≈ -6.09`

策略 A 的期望效用远大于 B（-0.0067 > -6.09）。所以 CARA 偏好把策略 A。这就是"风险厌恶"在数学上的表达。

#### 3.1.3 为什么是 CARA 而不是别的效用

- CARA 在线性高斯结构下有解析解
- 最优策略与初始财富无关（很方便）
- 把"方差"和"期望"通过 γ 加权，结构清晰

`γ` 的实际意义：每多 1 单位库存，做市商心里"等价于多少不确定性补偿"。`γ` 越大越保守。

### 3.2 优化问题与价值函数

做市商在时刻 `t` 的目标：选择整段未来的 `(δ^b_s, δ^a_s)_{t≤s≤T}`，最大化：

```math
J = \mathbb{E}_t\bigl[-e^{-\gamma V_T}\bigr]
```

定义**价值函数**：

```math
u(t, x, q, s) = \sup_{(\delta^a, \delta^b)}\mathbb{E}\!\left[-e^{-\gamma V_T} \mid X_t = x, q_t = q, S_t = s\right]
```

含义：当前状态 `(t, x, q, s)`，**采用最优策略**能达到的最大期望效用。

注意 `u` 依赖四个变量：

- `t`：当前时间
- `x`：当前现金
- `q`：当前库存
- `s`：当前公平价

### 3.3 动态规划与 Bellman 原理

#### 3.3.1 国际象棋类比

下棋时，要找到"当前棋盘下的最优走法"，可以这样想：

- 我每一步可选的所有走法都试一遍
- 对每个走法，看走完之后**最优**地走下去能得到多少分
- 选其中分数最高的那一步

这就是动态规划：**当前最优 = 当前一步的收益 + 下一时刻最优**。

#### 3.3.2 Bellman 方程（离散时间版）

对做市问题离散写：

```math
u(t, x, q, s) = \sup_{\delta^b,\delta^a}\mathbb{E}\bigl[\text{下一个时间步的 } u(t+\Delta t, X_{t+\Delta t}, q_{t+\Delta t}, S_{t+\Delta t})\bigr]
```

意思就是：当前状态价值 = "我选一对 (δ^b, δ^a) 后，下一刻状态的期望价值"的最大值。

### 3.4 HJB 方程（连续时间 Bellman）

把 Bellman 方程的离散时间步取 `Δt → 0`，并用 Ito 引理展开，得到 HJB（Hamilton–Jacobi–Bellman）方程：

```math
\partial_t u + \tfrac{1}{2}\sigma^2 \partial_{ss}^2 u
+ \sup_{\delta^b}\lambda(\delta^b)\bigl[u(t, x-(s-\delta^b)\Delta, q+\Delta, s) - u\bigr]
+ \sup_{\delta^a}\lambda(\delta^a)\bigl[u(t, x+(s+\delta^a)\Delta, q-\Delta, s) - u\bigr]
= 0
```

边界条件（在终止时刻 `T`，所有效用必须等于终值效用）：

```math
u(T, x, q, s) = -e^{-\gamma(x + q s)}
```

#### 3.4.1 三块意义

这个看起来吓人的方程其实只有三块意思：

1. `∂_t u + (1/2)σ² ∂_ss u`：**没有成交时**的演化项
   - `∂_t u` 是价值函数随时间的自然演化
   - `(1/2)σ² ∂_ss u` 是公平价随机游走带来的扩散修正（来自 Ito 公式）

2. 第二个 `sup`：**有买单成交时**的瞬时变化
   - 一旦买单成交，状态从 `(x, q)` 变成 `(x - (s - δ^b)Δ, q + Δ)`
   - 价值函数对应变化 `u(new) - u(old)`
   - 这个跳跃以强度 `λ(δ^b)` 发生
   - 做市商选 `δ^b` 最大化这一项

3. 第三个 `sup`：**有卖单成交时**的瞬时变化
   - 类似，方向相反

#### 3.4.2 为什么是 sup

做市商主动选 `(δ^b, δ^a)`，所以在最优控制框架下，方程里出现 `sup` 是说"做市商会选让这一项最大的那个 δ"。

### 3.5 求解 HJB：变量替换技巧

直接解这个偏微分方程几乎不可能。AS 用一个绝妙的 ansatz（猜测形式）：

```math
u(t, x, q, s) = -e^{-\gamma(x + qs)}\cdot e^{-\gamma\theta(t, q)}
```

为什么这样猜？

- 终止条件是 `u(T) = -e^{-γ(x+qs)}`，所以 `t = T` 时 `θ(T, q) = 0`
- CARA 效用对线性变换有好性质，所以"乘上一个不依赖 x、s 的修正"是合理的
- `(x + qs)` 就是当前的"账面财富" `V_t`，所以这个 ansatz 等价于说：

```math
u = -e^{-\gamma V_t}\cdot e^{-\gamma\theta(t, q)}
```

`θ(t, q)` 可以理解为"剩余时间内、库存为 `q` 时，额外能获得的等价确定性收益"。

#### 3.5.1 代入 HJB 后会发生什么

代入 ansatz 后，所有含 `x` 和 `s` 的部分会消掉（这是 CARA 加布朗的核心好处）。剩下一个关于 `θ(t, q)` 的方程：

```math
\partial_t \theta - \tfrac{1}{2}\gamma\sigma^2 q^2
+ \sup_{\delta^b}\frac{\lambda(\delta^b)}{\gamma}\bigl[1 - e^{-\gamma(\delta^b\Delta - \theta(t,q+\Delta)+\theta(t,q))}\bigr]
+ \sup_{\delta^a}\frac{\lambda(\delta^a)}{\gamma}\bigl[1 - e^{-\gamma(\delta^a\Delta - \theta(t,q-\Delta)+\theta(t,q))}\bigr]
= 0
```

这是一个**只关于 `(t, q)` 两个变量**的方程，比原 HJB 简单很多。

### 3.6 一阶条件 → 最优深度公式

#### 3.6.1 解出最优 δ^b

固定其他变量，对 `δ^b` 求一阶最优。在 `λ(δ) = A·e^{-kδ}` 的假设下，被求导的项是：

```math
A e^{-k\delta^b}\bigl[1 - e^{-\gamma(\delta^b\Delta - \Delta\theta)}\bigr]
```

其中 `Δθ = θ(q+Δ) - θ(q)`。

对 `δ^b` 求导设为 0，经过代数整理，得到：

```math
\delta^{b*} = \frac{1}{k}\log\!\left(1 + \frac{\gamma}{k}\right) + \frac{\theta(t,q) - \theta(t, q+\Delta)}{\Delta}
```

（这里 `Δ = 1` 简化情形）

#### 3.6.2 卖单类似

```math
\delta^{a*} = \frac{1}{k}\log\!\left(1 + \frac{\gamma}{k}\right) + \frac{\theta(t,q) - \theta(t, q-\Delta)}{\Delta}
```

#### 3.6.3 直觉拆解

- 第一项 `(1/k)·log(1 + γ/k)` 与库存无关，是"基础半价差"
- 第二项是关于 `θ` 的差分，反映库存对报价的影响

如果 `θ` 关于 `q` 是凹函数（一般情况），那么：

- `q > 0` 时，`θ(q+Δ) < θ(q)` ⇒ `θ(q) - θ(q+Δ) > 0` ⇒ `δ^b` 变大（买价更低，劝退买单）
- `q > 0` 时，`θ(q-Δ) > θ(q)` ⇒ `θ(q) - θ(q-Δ) < 0` ⇒ `δ^a` 变小（卖价更进，鼓励卖单）

也就是说，**库存大时，买价退、卖价进**，主动倾向于平仓。这就是 skew。

### 3.7 AS 的局限性

AS 的解需要知道 `θ(t, q)`，而 `θ` 本身需要解前面的方程并依赖剩余时间 `T - t`。在永续合约或现货等没有自然终止时刻的场景下，这个 `T` 怎么选？

更糟的是，越接近 `T`，AS 的最优策略会强制清仓（把库存激进甩出），这种行为在长期运行的做市中是**不合适**的。

GLFT 的工作就是把这个 `T → ∞` 的极限解出来，给出一个不依赖终止时刻的近似闭式公式。

---

## 第四章 从 AS 到 GLFT

### 4.1 GLFT 的两个推广

GLFT 论文相对 AS 增加了两个参数：

- `Δ`：每次成交的固定数量（不再要求 Δ=1）
- `ξ`：一个独立于 `γ` 的风险参数

notebook 里用简化版本：

- `Δ = 1`
- `ξ = γ`

后面 4.2-4.4 的推导都基于这个简化版本，但公式形式保留 `Δ, ξ`，以方便对照论文。

### 4.2 渐近极限 T → ∞

考虑剩余时间 `τ = T - t` 趋于无穷。直觉上：

- 库存累积总能有时间慢慢清掉
- "终值惩罚"不再重要

数学上引入贴现：考虑 `e^{-rτ}` 折扣的目标，让 `τ → ∞`，`θ(t, q)` 不再依赖 `t`，只依赖 `q`：

```math
\theta(t, q)\to \theta_\infty(q)
```

`θ_∞(q)` 满足一个**纯代数方程**（论文 (4.4) 式），不涉及时间导数。

### 4.3 二次型近似

`θ_∞(q)` 在 `q = 0` 附近用二次型展开：

```math
\theta_\infty(q) \approx \alpha_0 - \alpha_1 q^2
```

其中 `α_0`、`α_1 > 0` 是关于 `(A, k, σ, γ, Δ, ξ)` 的函数。

这种近似的精度：

- 在 `q` 不太大（比如 `|q| < 10` 左右）时很好
- 在 `q` 非常大时偏差变大（但实盘策略也不会让库存累到非常大）

### 4.4 推到公式 (4.6) 和 (4.7)

把 `θ_∞(q) ≈ α_0 - α_1 q²` 代入 3.6 节的最优深度公式：

```math
\delta^{b*}(q) = \frac{1}{k}\log\!\left(1 + \frac{\gamma}{k}\right) + \frac{\theta_\infty(q) - \theta_\infty(q+\Delta)}{\Delta}
```

```math
= \frac{1}{k}\log\!\left(1 + \frac{\gamma}{k}\right) + \frac{-\alpha_1 q^2 + \alpha_1 (q+\Delta)^2}{\Delta}
```

```math
= \frac{1}{k}\log\!\left(1 + \frac{\gamma}{k}\right) + \alpha_1(2q + \Delta)
```

把 `α_1` 的显式形式代入（论文中给出 `α_1 = (1/2)√(γσ²/(2AΔk)·(1+ξΔ/k)^{k/(ξΔ)+1})`），并把 `1/k` 改写为更一般的 `1/(ξΔ)` 形式：

```math
\delta^{b*}_{approx}(q) = \frac{1}{\xi \Delta}\log\!\left(1 + \frac{\xi \Delta}{k}\right) + \frac{2q + \Delta}{2}\sqrt{\frac{\gamma \sigma^2}{2A\Delta k}\left(1 + \frac{\xi \Delta}{k}\right)^{\frac{k}{\xi \Delta} + 1}}
```

```math
\delta^{a*}_{approx}(q) = \frac{1}{\xi \Delta}\log\!\left(1 + \frac{\xi \Delta}{k}\right) - \frac{2q - \Delta}{2}\sqrt{\frac{\gamma \sigma^2}{2A\Delta k}\left(1 + \frac{\xi \Delta}{k}\right)^{\frac{k}{\xi \Delta} + 1}}
```

这就是 notebook 用到的公式 (4.6) 和 (4.7)。

### 4.5 引入 c_1 和 c_2

公式很长，工程上很难直接调试。把"不含 `σ`、不含 `q`"的部分抽出来：

```math
c_1 = \frac{1}{\xi \Delta}\log\!\left(1 + \frac{\xi \Delta}{k}\right)
```

```math
c_2 = \sqrt{\frac{\gamma}{2A\Delta k}\left(1 + \frac{\xi \Delta}{k}\right)^{\frac{k}{\xi \Delta} + 1}}
```

注意 `c_2` 内部**没有 σ**——因为我们把根号里的 `σ²` 提到外面：

```math
\sqrt{\sigma^2\cdot \frac{\gamma}{2A\Delta k}(\cdots)} = \sigma\cdot c_2
```

于是 (4.6)(4.7) 简化为：

```math
\delta^{b*}(q) = c_1 + \frac{\Delta}{2}\sigma c_2 + q\sigma c_2
```

```math
\delta^{a*}(q) = c_1 + \frac{\Delta}{2}\sigma c_2 - q\sigma c_2
```

### 4.6 拆成 half spread 和 skew

定义：

```math
\text{half spread} = c_1 + \frac{\Delta}{2}\sigma c_2
```

```math
\text{skew} = \sigma c_2
```

那么：

```math
\delta^{b*}(q) = \text{half spread} + \text{skew}\cdot q
```

```math
\delta^{a*}(q) = \text{half spread} - \text{skew}\cdot q
```

最终报价：

```math
P^b = S - (\text{half spread} + \text{skew}\cdot q)
```

```math
P^a = S + (\text{half spread} - \text{skew}\cdot q)
```

### 4.7 各部分的直觉

| 项 | 直觉 | 让它变大的因素 |
| --- | --- | --- |
| `c_1` | 基础半价差，与库存波动率无关 | `k` 小（远端也容易成交）让 c_1 变大；`ξ` 大让 c_1 变小 |
| `(Δ/2)·σ·c_2` | 波动率额外贡献的半价差 | `σ` 大、`γ` 大、`A` 小、`k` 小都让它变大 |
| `σ·c_2·q` | 库存偏斜 | `σ` 大、`γ` 大、库存大都让它变大 |

非常关键的观察：

- `c_1` 完全由 `A, k` 决定（加上策略参数 `γ, ξ, Δ`）
- 波动率 `σ` 通过 `c_2` 进入，**同时**影响 half spread 和 skew
- 库存 `q` 只影响 skew，不改变 half spread

### 4.8 数值例子：算一个具体的报价

假设此刻：

- 中间价 S = 3000 USDC（tick = 0.01，所以 mid_price_tick = 300000）
- A = 1.08 /s
- k = 0.04 /tick
- σ = 10 tick/√s
- γ = ξ = 0.05
- Δ = 1
- q = 5（多 5 个 lot）

#### 4.8.1 算 c_1

```math
c_1 = \frac{1}{0.05 \times 1}\log\!\left(1 + \frac{0.05\times 1}{0.04}\right)
```

```math
= \frac{1}{0.05}\log(1 + 1.25) = 20 \times \log(2.25) \approx 20 \times 0.8109 \approx 16.22
```

#### 4.8.2 算 c_2

先算根号内部：

```math
\frac{\gamma}{2A\Delta k} = \frac{0.05}{2 \times 1.08 \times 1 \times 0.04} = \frac{0.05}{0.0864} \approx 0.579
```

```math
\left(1 + \frac{\xi\Delta}{k}\right)^{\frac{k}{\xi\Delta}+1} = (2.25)^{0.04/0.05 + 1} = (2.25)^{1.8}
```

```math
(2.25)^{1.8} = e^{1.8 \log 2.25} = e^{1.8 \times 0.8109} \approx e^{1.46} \approx 4.30
```

所以：

```math
c_2^2 \approx 0.579 \times 4.30 \approx 2.49
```

```math
c_2 \approx 1.58
```

#### 4.8.3 算 half spread 和 skew

```math
\text{half spread} = c_1 + \frac{\Delta}{2}\sigma c_2 = 16.22 + 0.5 \times 10 \times 1.58 = 16.22 + 7.90 = 24.12\ \text{tick}
```

```math
\text{skew} = \sigma c_2 = 10 \times 1.58 = 15.8\ \text{tick / lot}
```

#### 4.8.4 算最终报价

reservation_price_tick = mid - skew × q = 300000 - 15.8 × 5 = 300000 - 79 = 299921

```math
P^b_{tick} = 299921 - 24.12 \approx 299897
```

```math
P^a_{tick} = 299921 + 24.12 \approx 299945
```

转回美元：

- bid = 2998.97
- ask = 2999.45

相比中间价 3000：

- bid 距中间价 -1.03 USDC（103 tick）
- ask 距中间价 -0.55 USDC（55 tick）

两边都偏低，因为多头库存让 reservation price 往下移。

#### 4.8.5 如果库存为 0 呢

`q = 0` 时：

- reservation = mid = 300000
- bid = 300000 - 24.12 = 299976
- ask = 300000 + 24.12 = 300024
- 即 bid = 2999.76、ask = 3000.24，对称围绕中间价

可以看到 skew 的作用：把对称的双边报价整体平移，让两边都往去库存方向靠。

---

## 第五章 参数校准

### 5.1 三个需要校准的量

- 成交强度参数 `A`, `k`
- 波动率 `σ`

策略参数 `γ`、`Δ`、`ξ` 不校准，由人定。

### 5.2 校准成交强度的思路

回顾：`λ(δ) = A·e^{-kδ}`。取对数变成直线：`log λ = log A - kδ`。

校准流程：

1. 从市场数据中估出经验 `λ(δ)` 曲线（每个 δ 桶上单位时间被成交多少次）
2. 对 `(δ, log λ)` 做线性回归 → 拿到斜率 `-k` 和截距 `log A`
3. `k = -slope`, `A = exp(intercept)`

#### 5.2.1 怎么估经验 λ(δ)

最严格的做法：把策略真的挂在每个 δ 上，统计成交。但这不实际。

替代做法：用**市场成交记录**来近似。

逻辑是：如果一个市场单成交价距中间价 3 tick，那么所有挂在 1.5 tick、2 tick、2.5 tick 的反向单都**有机会**成交。

### 5.3 arrival_depth 的精确定义

每个 100ms 时间步内，记录所有成交。对每笔成交：

- 如果是 buy event（吃卖盘）：`depth = trade_price_tick - mid_price_tick`（成交价高于中间价多少）
- 如果是 sell event（吃买盘）：`depth = mid_price_tick - trade_price_tick`（成交价低于中间价多少）

取这一步内所有 depth 的最大值，作为该步的 `arrival_depth`：

```python
depth = -np.inf
for last_trade in hbt.last_trades(0):
    trade_price_tick = last_trade.px / tick_size
    if last_trade.ev & BUY_EVENT == BUY_EVENT:
        depth = np.nanmax([trade_price_tick - mid_price_tick, depth])
    else:
        depth = np.nanmax([mid_price_tick - trade_price_tick, depth])
arrival_depth[t] = depth
```

为什么取最大？因为我们关心"最远到了多深"。如果最远成交穿到 +5 tick，那么 +1、+2、+3、+4 tick 上的单子都被打到了。

### 5.4 measure_trading_intensity 逐行解析

```python
@njit
def measure_trading_intensity(order_arrival_depth, out):
    max_tick = 0
    for depth in order_arrival_depth:
        if not np.isfinite(depth):
            continue
        tick = round(depth / .5) - 1
        if tick < 0 or tick >= len(out):
            continue
        out[:tick] += 1
        max_tick = max(max_tick, tick)
    return out[:max_tick]
```

逐行：

- `if not np.isfinite(depth): continue`：跳过 NaN（没成交的时间步）
- `tick = round(depth / .5) - 1`：把 depth 量化到 half-tick 网格
  - `depth = 0.5` → tick = 0
  - `depth = 1.0` → tick = 1
  - `depth = 1.5` → tick = 2
  - 注意这里就是已知的 half-tick 单位问题
- `if tick < 0 or tick >= len(out): continue`：边界保护
- `out[:tick] += 1`：所有不深于 `tick` 的桶都 +1（"被打到的次数 +1"）
- 最后返回 `out[:max_tick]`

#### 5.4.1 一个具体的小例子

假设观测窗口内只有 3 个 arrival_depth 样本（其他都 NaN）：

- 第 1 步：depth = 0.5 → tick = 0 → `out[:0]` 不变（无操作，因为 `out[:0]` 是空切片）
- 第 2 步：depth = 1.5 → tick = 2 → `out[:2] += 1`，即 `out[0] += 1`、`out[1] += 1`
- 第 3 步：depth = 2.0 → tick = 3 → `out[:3] += 1`，即 `out[0] += 1`、`out[1] += 1`、`out[2] += 1`

最终：

- `out[0] = 2`（被打到的次数：第 2、3 步）
- `out[1] = 2`（被打到的次数：第 2、3 步）
- `out[2] = 1`（被打到的次数：第 3 步）
- `out[3] = 0`

这就是经验 λ 曲线（"次数"）。要转为强度还需要除以观测时长。

### 5.5 转换为每秒强度

观测窗口是 10 分钟 = 600 秒，所以：

```python
lambda_ /= 600
```

如果 `out[5] = 1200`（10 分钟内被打到 1200 次），那么 `λ(5 half-ticks) = 1200/600 = 2.0` 次 / 秒。

### 5.6 线性回归拟合 A, k

```python
@njit
def linear_regression(x, y):
    sx = np.sum(x)
    sy = np.sum(y)
    sx2 = np.sum(x ** 2)
    sxy = np.sum(x * y)
    w = len(x)
    slope = (w * sxy - sx * sy) / (w * sx2 - sx**2)
    intercept = (sy - slope * sx) / w
    return slope, intercept
```

这是最小二乘的标准公式。简单回顾：对 `y = ax + b`，最优 `a` 和 `b` 满足：

```math
a = \frac{n\sum xy - \sum x\sum y}{n\sum x^2 - (\sum x)^2},\quad b = \bar{y} - a\bar{x}
```

调用：

```python
y = np.log(lambda_)
k_, logA = linear_regression(ticks, y)
A = np.exp(logA)
k = -k_
```

注意：

- `slope = -k`，所以 `k = -slope`
- `intercept = log A`，所以 `A = exp(intercept)`

### 5.7 为什么只用浅层区间

实证 `λ` 在远端有两个问题：

1. **样本少**：很少有市场单穿到 100 tick 远，统计噪音大
2. **形态偏离**：极端行情会带来"超远端"成交，这些是异常事件，不应该污染指数拟合

所以 notebook 把拟合范围限制在前 70 个 half-tick（即 35 tick 以内）：

```python
x_shallow = ticks[:70]
lambda_shallow = lambda_[:70]
y = np.log(lambda_shallow)
k_, logA = linear_regression(x_shallow, y)
A = np.exp(logA)
k = -k_
```

这等于"我只关心策略真正会挂的距离区间"。

### 5.8 波动率估计

```python
volatility = np.nanstd(mid_price_chg) * np.sqrt(10)
```

回顾 2.1.5：100ms 一个样本，乘 `√10` 转成"每秒平方根"尺度。`σ` 量纲是 `tick/√s`。

#### 5.8.1 一个数值例子

假设最近 10 分钟（6000 步）的 mid_price_chg 标准差是 3.4 tick / 100ms。

```math
\sigma = 3.4 \times \sqrt{10} \approx 10.75\ \text{tick}/\sqrt{s}
```

这就是后面 GLFT 公式里用的 σ。

### 5.9 策略参数 γ, Δ, ξ 的选取

| 参数 | 含义 | notebook 取值 | 调参方向 |
| --- | --- | --- | --- |
| `γ` | 风险厌恶系数 | 0.05 | 大 → 保守，价差更宽、skew 更强 |
| `Δ` | 每次成交的库存增量 | 1 | 通常等于 order_qty 在 contract 数上的值 |
| `ξ` | 额外风险参数 | γ（即 0.05） | 论文允许独立设置；notebook 取 ξ = γ |

`γ = 0.05` 是经验值。调小一些（如 0.01）会让策略更激进，调大（如 0.5）会让策略更保守。

### 5.10 校准频率和滚动窗口

```python
if t % 50 == 0:        # 每 5 秒
    if t >= 6_000 - 1: # 累积满 10 分钟才开始
        lambda_ = measure_trading_intensity(arrival_depth[t-5999:t+1], tmp)
        ...
```

- 每 50 步（5 秒）重新校准一次
- 用最近 6000 步（10 分钟）的数据

为什么选这两个值？

- **5 秒太短**：A, k, σ 的估计噪音大，会让策略报价频繁抖动
- **5 秒太长**：跟不上市场状态变化（突发波动率上升时反应慢）
- 10 分钟窗口在样本量和时效性之间折中

不同标的可能需要调整：

- 高波动品种：窗口缩短（如 5 分钟）
- 低波动品种：窗口延长（如 30 分钟）

### 5.11 校准的常见陷阱

#### 5.11.1 单位混乱

`measure_trading_intensity` 输出按 half-tick 桶。`linear_regression` 用 `ticks = np.arange(N) + 0.5` 作为 x 轴，看起来是 tick 单位。但实际上 `out[i]` 对应深度 `i+1` 个 half-tick，约 0.5(i+1) tick。

也就是说，notebook 里 `k` 的真实物理量纲可能是 1/(0.5 tick) = 2/tick，但代码里直接当作 1/tick 用。这是 notebook 里明确提到的 "incorrectly in half-tick units" 问题。

对于复现 notebook 结果没问题，但迁移到自己的策略时需要明确单位。

#### 5.11.2 没成交时怎么办

刚启动时还没积满 6000 步，A、k、σ 都是 NaN。`compute_coeff` 会得到 NaN 结果，半价差也是 NaN。代码用 `np.isfinite(bid_price)` 保护：

```python
if position < max_position and np.isfinite(bid_price):
    hbt.submit_buy_order(...)
```

即未完成 warmup 前不挂单。

#### 5.11.3 极端市场下的失效

如果某段时间市场极端冷清，`out[i]` 大多是 0 或 1，取对数会得到 `-∞` 或不稳定的负数，线性回归出问题。notebook 没有严格处理这个，但实盘需要：

- 强度过低时退化为固定价差
- 或扩大窗口
- 或拒绝下单等待市场恢复

---

## 第六章 Notebook 代码逐段解析

### 6.1 整体函数结构

| 函数 | 作用 |
| --- | --- |
| `measure_trading_intensity_and_volatility` | 回放市场，记录 `arrival_depth` 和 `mid_price_chg` |
| `measure_trading_intensity` | 把 `arrival_depth` 转成经验 λ 曲线 |
| `linear_regression` | 最小二乘拟合斜率 / 截距 |
| `compute_coeff` | 计算 c_1, c_2 |
| `glft_market_maker` | 单层报价主循环 |
| `gridtrading_glft_mm` | 网格报价主循环 |

### 6.2 measure_trading_intensity_and_volatility

```python
@njit
def measure_trading_intensity_and_volatility(hbt):
    tick_size = hbt.depth(0).tick_size
    arrival_depth = np.full(10_000_000, np.nan, np.float64)
    mid_price_chg = np.full(10_000_000, np.nan, np.float64)

    t = 0
    prev_mid_price_tick = np.nan
    mid_price_tick = np.nan
    
    while hbt.elapse(100_000_000) == 0:
        # 1. 记录成交深度（用上一步的 mid_price）
        if not np.isnan(mid_price_tick):
            depth = -np.inf
            for last_trade in hbt.last_trades(0):
                trade_price_tick = last_trade.px / tick_size
                if last_trade.ev & BUY_EVENT == BUY_EVENT:
                    depth = np.nanmax([trade_price_tick - mid_price_tick, depth])
                else:
                    depth = np.nanmax([mid_price_tick - trade_price_tick, depth])
            arrival_depth[t] = depth
        
        hbt.clear_last_trades(0)
        depth = hbt.depth(0)
        best_bid_tick = depth.best_bid_tick
        best_ask_tick = depth.best_ask_tick
        
        prev_mid_price_tick = mid_price_tick
        mid_price_tick = (best_bid_tick + best_ask_tick) / 2.0
        
        # 2. 记录中间价变化（用于波动率估计）
        mid_price_chg[t] = mid_price_tick - prev_mid_price_tick
        
        t += 1
```

关键点：

- `hbt.elapse(100_000_000)`：推进 100ms（单位 ns）
- 用**上一步**的 mid_price 来衡量成交深度，避免成交和中间价同步移动带来的偏差
- 第一步 `mid_price_tick` 是 NaN，所以第一步不记录 arrival_depth

### 6.3 compute_coeff

```python
@njit
def compute_coeff(xi, gamma, delta, A, k):
    inv_k = np.divide(1, k)
    c1 = 1 / (xi * delta) * np.log(1 + xi * delta * inv_k)
    c2 = np.sqrt(np.divide(gamma, 2 * A * delta * k) * ((1 + xi * delta * inv_k) ** (k / (xi * delta) + 1)))
    return c1, c2
```

直接对应：

```math
c_1 = \frac{1}{\xi\Delta}\log\!\left(1 + \frac{\xi\Delta}{k}\right)
```

```math
c_2 = \sqrt{\frac{\gamma}{2A\Delta k}\left(1 + \frac{\xi\Delta}{k}\right)^{\frac{k}{\xi\Delta} + 1}}
```

注意：

- 函数输入是 `xi` 和 `gamma`，但 notebook 调用时 `compute_coeff(gamma, gamma, ...)`，意味着 `ξ = γ`
- `σ` 不在这里，因为它在外面单独乘

### 6.4 glft_market_maker 主循环逐段

#### 6.4.1 初始化

```python
arrival_depth = np.full(10_000_000, np.nan, np.float64)
mid_price_chg = np.full(10_000_000, np.nan, np.float64)
out = np.zeros(10_000_000, out_dtype)

t = 0
prev_mid_price_tick = np.nan
mid_price_tick = np.nan

tmp = np.zeros(500, np.float64)
ticks = np.arange(len(tmp)) + 0.5

A = np.nan
k = np.nan
volatility = np.nan
gamma = 0.05
delta = 1

order_qty = 1
max_position = 20
```

- `arrival_depth` 和 `mid_price_chg`：用于校准的滚动数据
- `out`：每一步记录策略状态（half_spread、skew、volatility、A、k）用于事后分析
- `tmp`、`ticks`：用于强度估计的中间变量
- `gamma = 0.05`：风险厌恶系数
- `max_position = 20`：最大持仓 20 lot

#### 6.4.2 每 5 秒重估参数

```python
if t % 50 == 0:
    if t >= 6_000 - 1:
        tmp[:] = 0
        lambda_ = measure_trading_intensity(arrival_depth[t + 1 - 6_000:t + 1], tmp)
        if len(lambda_) > 2:
            lambda_ = lambda_[:70] / 600
            x = ticks[:len(lambda_)]
            y = np.log(lambda_)
            k_, logA = linear_regression(x, y)
            A = np.exp(logA)
            k = -k_
        volatility = np.nanstd(mid_price_chg[t + 1 - 6_000:t + 1]) * np.sqrt(10)
```

- 每 50 步（5s）触发一次
- 累积满 10 分钟（6000 步）才开始校准
- `lambda_[:70]`：只取前 70 个 half-tick
- σ 用同样窗口估

#### 6.4.3 计算 half_spread 和 skew

```python
c1, c2 = compute_coeff(gamma, gamma, delta, A, k)
half_spread_tick = c1 + delta / 2 * c2 * volatility
skew = c2 * volatility
reservation_price_tick = mid_price_tick - skew * position
```

注意：

- `compute_coeff(gamma, gamma, ...)` 即 `ξ = γ`
- reservation_price = mid - skew × position
  - position > 0（多）→ reservation 下移 → 鼓励卖单
  - position < 0（空）→ reservation 上移 → 鼓励买单

#### 6.4.4 计算最终 bid/ask（带保护）

```python
bid_price_tick = np.minimum(np.round(reservation_price_tick - half_spread_tick), best_bid_tick)
ask_price_tick = np.maximum(np.round(reservation_price_tick + half_spread_tick), best_ask_tick)

bid_price = bid_price_tick * tick_size
ask_price = ask_price_tick * tick_size
```

`np.minimum(..., best_bid_tick)` 和 `np.maximum(..., best_ask_tick)` 的作用：

- 保证 bid ≤ best_bid（不会成为新的最优买）
- 保证 ask ≥ best_ask（不会成为新的最优卖）

这是为了确保**永远都是 maker**（被动挂单），不会变成 taker。

#### 6.4.5 撤掉旧单

```python
order_values = orders.values()
while order_values.has_next():
    order = order_values.get()
    if order.cancellable:
        if (
            (order.side == BUY and order.price != bid_price)
            or (order.side == SELL and order.price != ask_price)
        ):
            hbt.cancel(0, order.order_id, False)
```

如果当前在场的订单价格 ≠ 新目标价，撤掉。

#### 6.4.6 挂新单

```python
if position < max_position and np.isfinite(bid_price):
    bid_price_as_order_id = round(bid_price / tick_size)
    if bid_price_as_order_id not in orders:
        hbt.submit_buy_order(0, bid_price_as_order_id, bid_price, order_qty, GTX, LIMIT, False)
if position > -max_position and np.isfinite(ask_price):
    ask_price_as_order_id = round(ask_price / tick_size)
    if ask_price_as_order_id not in orders:
        hbt.submit_sell_order(0, ask_price_as_order_id, ask_price, order_qty, GTX, LIMIT, False)
```

- 用价格的 tick 数作为 `order_id`，方便判重
- `position < max_position` 才挂买单（避免多仓继续累积）
- `position > -max_position` 才挂卖单（避免空仓继续累积）
- `GTX`：保证 maker

### 6.5 调整因子 adj1 和 adj2

notebook 第二个版本引入了缩放：

```python
half_spread_tick = (c1 + delta / 2 * c2 * volatility) * adj1
skew = c2 * volatility * adj2
```

`adj1 = 1, adj2 = 0.05`：保留 half spread 不动，把 skew 缩小 20 倍。

#### 6.5.1 为什么 skew 需要缩小

理论给出的 skew 在实盘中表现为：

- 库存累积到 2 个就让报价中心被推得很远
- 下一次反向成交立刻发生（因为对面深度更近）
- 策略变成"接一仓立刻平仓"的来回跳

这种"过强 skew"的原因：

- 理论假设 `λ(δ)` 是平稳指数衰减，实际市场有"成簇成交"
- 理论假设波动率恒定，实际有突发跳变
- 理论没考虑 adverse selection（被打到时往往不利方向）

`adj2 = 0.05` 等价于让策略"愿意接更多库存再开始平仓"，符合实盘观察。

#### 6.5.2 实战中的 adj 选择

把 adj1、adj2 当作可优化超参数。常见做法：

- 在历史数据上用网格搜索
- 把 adj2 拉满 1.0 看 PnL，再降到 0.5、0.1、0.05
- 看 Sharpe + MDD 综合最好的 adj 组合

---

## 第七章 网格交易的整合

### 7.1 思路

`glft_market_maker` 每次只挂一对 (bid, ask)。但实盘中可以同时挂多层（网格），优势是：

- 整体捕获价差更充分
- 利用排队位置：挂得早的单子 queue position 靠前
- 价格震荡时多层都能成交

`gridtrading_glft_mm` 在 GLFT 输出的基础上铺网格。

### 7.2 网格间距由 GLFT 决定

```python
grid_interval = max(np.round(half_spread_tick) * tick_size, tick_size)
```

- 网格间距 = round(half_spread) × tick_size
- 至少 1 个 tick

含义：

- 半价差 30 tick → 网格间距 30 tick → 网格稀疏
- 半价差 5 tick → 网格间距 5 tick → 网格密集

网格密度**自动随波动率/活跃度变化**。

### 7.3 报价对齐到网格

```python
bid_price = np.floor(bid_price / grid_interval) * grid_interval
ask_price = np.ceil(ask_price / grid_interval) * grid_interval
```

把 GLFT 输出的 bid/ask 向下/向上**对齐**到网格点。这是避免每次循环都因为微小价格变化而频繁撤补——只要 bid 还落在同一个网格格子里，单子不动。

### 7.4 展开多层

```python
new_bid_orders = Dict.empty(np.uint64, np.float64)
if position < max_position and np.isfinite(bid_price):
    for i in range(grid_num):
        bid_price_tick = round(bid_price / tick_size)
        new_bid_orders[uint64(bid_price_tick)] = bid_price
        bid_price -= grid_interval
```

- 从 bid_price 开始，向下铺 grid_num 层（每层间距 grid_interval）
- 卖单类似向上铺

`grid_num = 20`，所以一侧有 20 层。

### 7.5 增量撤补

```python
order_values = orders.values()
while order_values.has_next():
    order = order_values.get()
    if order.cancellable:
        if (
            (order.side == BUY and order.order_id not in new_bid_orders)
            or (order.side == SELL and order.order_id not in new_ask_orders)
        ):
            hbt.cancel(asset_no, order.order_id, False)
            
for order_id, order_price in new_bid_orders.items():
    if order_id not in orders:
        hbt.submit_buy_order(asset_no, order_id, order_price, order_qty, GTX, LIMIT, False)
        
for order_id, order_price in new_ask_orders.items():
    if order_id not in orders:
        hbt.submit_sell_order(asset_no, order_id, order_price, order_qty, GTX, LIMIT, False)
```

只撤"不在新网格里"的旧单，只挂"新网格里没有的"新单。这样旧单的 queue position 得以保留。

### 7.6 网格 + GLFT 相比固定网格的好处

| 维度 | 固定网格 | GLFT + 网格 |
| --- | --- | --- |
| 网格中心 | 固定 | 库存大时自动偏移 |
| 网格宽度 | 固定 | 波动率/活跃度变化时自动调整 |
| 单边行情应对 | 容易爆仓 | skew 主动倾斜 |
| 参数化 | 全手动 | 大部分自动 |

---

## 第八章 回测分析、简化假设与改进方向

### 8.1 回测结果解读

notebook 给出的几组回测结果（基于 -0.005% maker 返佣）：

| 配置 | SR | ReturnOverMDD |
| --- | --- | --- |
| 原始 GLFT（ETHUSDC, 1d） | -132.77 | -0.998 |
| GLFT + adj 缩放（ETHUSDC, 1d） | -40.43 | -0.961 |
| GLFT + adj（ETHUSDC, 3d） | -28.42 | -0.985 |
| GLFT + Grid（ETHUSDC, 3d） | -17.35 | -0.916 |
| GLFT + Grid（LTCUSDT, 5d, 旧数据） | +17.18 | +3.72 |

要点：

- ETHUSDC 上几乎全亏，原因可能是 spread 太薄、活跃度太高（高频做市拥挤）
- LTCUSDT 上能盈利，spread 更宽、竞争少
- 同一个策略在不同标的差距巨大，**不要把单一回测当作"GLFT 不能用"**

### 8.2 回测应该看哪些指标

| 指标 | 含义 |
| --- | --- |
| SR | 年化夏普比率 |
| Sortino | 只对下行偏差敏感的夏普 |
| ReturnOverMDD | 总收益 / 最大回撤 |
| DailyTurnover | 日均换手率（流动性消耗） |
| MaxPositionValue | 最大持仓价值 |

只看 PnL 不够，要综合看风险调整后收益和库存暴露。

### 8.3 主要简化假设

#### 8.3.1 忽略排队位置

`measure_trading_intensity` 假设"市场单穿过深度 = 你的单成交"，没考虑你在 queue 中的位置。实盘中你可能排在很多人后面，市场单到了也不一定成交你。

影响：**高估**成交概率 → A 偏大 → 算出的 half_spread 偏小。

#### 8.3.2 用次数不用成交量

经验 λ 只统计"打到的次数"，不分大小。如果你的 order_qty 不是 1 lot 而是 10 lot，那么实际成交概率应该按"是否打满 10 lot"算，而不是"是否到这个价"。

#### 8.3.3 half-tick 单位问题

如 5.11.1 所述，notebook 里 measure_trading_intensity 输出是按 half-tick 桶，但后面公式当 tick 单位用。

#### 8.3.4 公平价格 = 中间价

中间价有噪音。更精细的策略会用 micro-price、加 OBI 信号修正、跨市场 fair value 等。

#### 8.3.5 σ 假设为常数

GLFT 推导假设 σ 是常数。实际市场有波动率聚集，单点 σ 估计在波动率爆发时严重滞后。

#### 8.3.6 没有 adverse selection 建模

理论假设：成交是泊松事件，与价格走势独立。实际：被成交的瞬间往往价格正在反向运动（毒性流）。

### 8.4 从回测到实盘的差距

1. **延迟分布**：notebook 用 `intp_order_latency`，实盘的真实延迟分布可能不同
2. **撮合假设**：`power_prob_queue_model(2.0)` 是近似，实盘队列动态更复杂
3. **QPS 限制**：交易所对 cancel/submit 有频率限制，100ms 撤补节奏可能触发
4. **资金费率**：永续合约的资金费、现货的借贷利息没有建模
5. **手续费阶梯**：实盘 maker 返佣率随 30 天交易量阶梯变化

### 8.5 改进方向（按优先级）

1. **加 alpha 到 fair price**：用 OBI、micro-price、跨市场信号修正中间价
2. **adverse selection 防护**：检测"被成交后短时间内价格反向"模式，及时撤单
3. **优化 adj1, adj2**：用历史数据搜索最优缩放
4. **改进强度估计**：加入排队位置，用 fill probability 而不是 λ(δ)
5. **加权波动率**：用 GARCH 或 EWMA 估 σ，对突发更敏感
6. **多市场对冲**：永续 + 现货 + 期权组合做相对价值

---

## 附录 A 数学记号速查

| 记号 | 含义 | 单位 |
| --- | --- | --- |
| `t` | 当前时刻 | 秒 |
| `T` | 终止时刻（AS 框架） | 秒 |
| `τ = T - t` | 剩余时间 | 秒 |
| `S_t` | 公平价格 / 中间价 | 价格 |
| `σ` | 波动率 | tick / √秒 |
| `W_t` | 标准布朗运动 | - |
| `δ^b, δ^a` | 报价深度（距中间价） | tick |
| `P^b = S - δ^b` | 买价 | 价格 |
| `P^a = S + δ^a` | 卖价 | 价格 |
| `q` | 库存（持仓） | 单位（lot） |
| `Δ` | 每次成交的库存增量 | 单位（lot） |
| `N^b_t` | 买单累计成交次数 | 次 |
| `N^a_t` | 卖单累计成交次数 | 次 |
| `λ(δ) = A·e^{-kδ}` | 成交强度函数 | 次 / 秒 |
| `A` | 强度常数 | 次 / 秒 |
| `k` | 衰减系数 | 1 / tick |
| `γ` | 风险厌恶系数 | - |
| `ξ` | 风险调整参数 | - |
| `X_t` | 现金 | 货币 |
| `V_t = X_t + q·S_t` | 财富 | 货币 |
| `u(t, x, q, s)` | 价值函数 | - |
| `θ(t, q)` | 价值函数的库存依赖项 | - |
| `c_1, c_2` | GLFT 提取出的系数 | 见正文 |
| `adj_1, adj_2` | half spread / skew 的缩放因子 | - |

---

## 附录 B 主要公式汇总

中间价随机过程：

```math
dS_t = \sigma\,dW_t
```

成交强度（指数衰减假设）：

```math
\lambda(\delta) = A\cdot e^{-k\delta}
```

库存过程：

```math
dq_t = \Delta\,dN^b_t - \Delta\,dN^a_t
```

现金过程：

```math
dX_t = (S_t + \delta^a_t)\Delta\,dN^a_t - (S_t - \delta^b_t)\Delta\,dN^b_t
```

财富分解：

```math
dV_t = \delta^a\Delta\,dN^a + \delta^b\Delta\,dN^b + q\sigma\,dW
```

效用目标：

```math
\max_{\delta^b, \delta^a}\mathbb{E}\bigl[-e^{-\gamma V_T}\bigr]
```

价值函数：

```math
u(t, x, q, s) = \sup_{\delta^a, \delta^b}\mathbb{E}_{t,x,q,s}\bigl[-e^{-\gamma V_T}\bigr]
```

HJB 方程（AS）：

```math
\partial_t u + \tfrac{1}{2}\sigma^2 \partial_{ss}^2 u
+ \sup_{\delta^b}\lambda(\delta^b)[\Delta_b u]
+ \sup_{\delta^a}\lambda(\delta^a)[\Delta_a u] = 0
```

变量替换 ansatz：

```math
u(t, x, q, s) = -e^{-\gamma(x + qs)}\cdot e^{-\gamma\theta(t, q)}
```

θ 满足的方程：

```math
\partial_t \theta - \tfrac{1}{2}\gamma\sigma^2 q^2 + \cdots = 0
```

AS 最优深度（Δ = 1）：

```math
\delta^{b*}(t, q) = \frac{1}{k}\log\!\left(1 + \frac{\gamma}{k}\right) + \frac{\theta(t,q) - \theta(t, q+\Delta)}{\Delta}
```

二次型近似：

```math
\theta_\infty(q) \approx \alpha_0 - \alpha_1 q^2
```

GLFT 公式 (4.6) / (4.7)：

```math
\delta^{b*}_{approx}(q) = \frac{1}{\xi\Delta}\log\!\left(1 + \frac{\xi\Delta}{k}\right) + \frac{2q+\Delta}{2}\sqrt{\frac{\gamma\sigma^2}{2A\Delta k}\left(1+\frac{\xi\Delta}{k}\right)^{\frac{k}{\xi\Delta}+1}}
```

```math
\delta^{a*}_{approx}(q) = \frac{1}{\xi\Delta}\log\!\left(1 + \frac{\xi\Delta}{k}\right) - \frac{2q-\Delta}{2}\sqrt{\frac{\gamma\sigma^2}{2A\Delta k}\left(1+\frac{\xi\Delta}{k}\right)^{\frac{k}{\xi\Delta}+1}}
```

系数定义：

```math
c_1 = \frac{1}{\xi\Delta}\log\!\left(1+\frac{\xi\Delta}{k}\right)
```

```math
c_2 = \sqrt{\frac{\gamma}{2A\Delta k}\left(1+\frac{\xi\Delta}{k}\right)^{\frac{k}{\xi\Delta}+1}}
```

工程拆解：

```math
\text{half spread} = c_1 + \frac{\Delta}{2}\sigma c_2
```

```math
\text{skew} = \sigma c_2
```

最终报价：

```math
P^b = S - (\text{half spread} + \text{skew}\cdot q)
```

```math
P^a = S + (\text{half spread} - \text{skew}\cdot q)
```

带调整因子的工程版：

```math
\text{half spread}_{adj} = (c_1 + \tfrac{\Delta}{2}\sigma c_2)\cdot adj_1
```

```math
\text{skew}_{adj} = \sigma c_2 \cdot adj_2
```

---

## 附录 C 常见问题 FAQ

**Q1：我对随机过程完全不懂，能直接用 GLFT 吗？**

可以。从工程角度，你只要把以下几件事做对：

1. 估出 A, k（线性回归）
2. 估出 σ（标准差 × √10）
3. 选好 γ（先试 0.05）
4. 调好 adj1, adj2（先试 1, 0.05）
5. 跑回测，迭代

理论是用来回答"为什么这样"的，但策略本身用代码 + 经验调参就能跑起来。

**Q2：为什么我估出来的 k 是负数？**

`k = -slope`。如果你估出来 `slope > 0`，意味着深度越深成交越多，这不符合直觉。可能原因：

- 数据有问题（成交方向标错）
- 浅层桶样本极少导致回归被远端拉偏
- 中间价定义不对

第一步先用 plot 看经验 λ(δ) 曲线，确认它是单调递减的。

**Q3：half_spread 算出来是负数怎么办？**

如果 `c_1` 或 `σ` 或 `c_2` 出问题，half_spread 可能算出负值。代码里要做边界保护：

```python
half_spread_tick = max(half_spread_tick, 1.0)
```

或者退回到固定 spread。

**Q4：γ = 0.05 是怎么来的？我能改吗？**

`0.05` 是经验值，可以改。建议：

- 先用 0.05 跑
- 再用 0.01、0.1、0.5 各跑一次
- 比较 Sharpe 和 MDD

γ 越大，半价差越宽 + skew 越强，整体越保守。

**Q5：notebook 里 σ 用了两个不同值，27.57 和 10.69，为什么？**

第一次是从全量数据估的（27.57），第二次是手动改了一个值（10.69）做演示。生产里用滚动估计：

```python
volatility = np.nanstd(mid_price_chg[window]) * np.sqrt(10)
```

**Q6：为什么 ξ = γ？**

论文里 ξ 可以是独立参数。notebook 简化为 ξ = γ 让公式更紧凑。如果你想区分二者：

- γ 控制库存惩罚的强度（影响 skew 大小）
- ξ 控制 c_1 中的 log 部分（影响基础 spread）

实战中分别调通常没必要，直接 ξ = γ 即可。

**Q7：网格的 grid_num = 20 怎么定？**

网格数量决定一次性挂多少层。grid_num 越大：

- 流动性提供更厚
- 但占用更多 order slot（交易所有限制）
- 需要更多撤补操作

实战上 grid_num 5-30 都常见。

**Q8：实盘和回测差多少？**

视实盘环境而定。常见的实盘衰减：

- 延迟模型不准：回测 1ms 延迟，实盘 50-100ms → 成交概率大幅下降
- 排队位置：回测不考虑，实盘可能让你慢 200ms 才成交
- adverse selection：回测中"成交"是随机的，实盘里有方向相关性

一般实盘 PnL 比回测低 30-70%。要在回测里加各种摩擦做保守估计。

**Q9：为什么 reservation price 用减法，库存越多它越低？**

代码里：

```python
reservation_price_tick = mid_price_tick - skew * position
```

- `position > 0`（多）→ reservation < mid → 整个报价向下偏移
- bid 变低（减少买入概率），ask 也变低（增加卖出概率）
- 净效果：库存往 0 拉

**Q10：如果交易所给负的 maker fee 我能稳定赚钱吗？**

不一定。Maker rebate 只是其中一个因素。如果：

- spread 极窄
- 竞争极激烈（很多机构在同一价位排队）
- 你的 queue position 永远在后面

那么 rebate 也覆盖不了 adverse selection 损失。Binance USDM 0.005% rebate 是一个很弱的盈利来源，能 breakeven 已经不错。

**Q11：我怎么知道我的 A, k 估计是不是合理的？**

几个检查：

- plot 经验 λ(δ) 曲线，看是否单调递减
- plot 拟合的 `A·exp(-k·δ)` 曲线，看是否覆盖经验曲线
- 检查 A 在 0.1-10 量级、k 在 0.01-0.5 量级（视品种）
- 看 A、k 随时间变化是否平滑（突跳说明窗口太短）

**Q12：σ 用 ATR 或者 Garman-Klass 估更好吗？**

GLFT 公式要求"瞬时波动率"，最直接的就是标准差。其他估计方法（ATR、GK、RV）也可以，但要注意单位换算。

简单标准差最容易出错的地方是采样间隔：100ms 样本要乘 √10，1 秒样本不用乘，1 分钟样本要除以 √60。

**Q13：我能把 GLFT 用在股票上吗？**

可以。理论上 GLFT 对任何有连续报价的市场都适用。需要调整：

- tick_size 改成股票的最小价位（如 0.01 美元）
- 校准窗口可能要拉长（股票成交频率低）
- γ 可能要更大（股票的隔夜风险）

**Q14：GLFT 公式里如果 q 很大，半价差会爆炸吗？**

不会。半价差 = c_1 + (Δ/2)σc_2，与 q 无关。q 只影响 skew·q 这一项。

所以 q 越大，bid 越远、ask 越近，但 spread 本身宽度不变。

**Q15：如果 A = 0 或 k = 0 怎么办？**

数学上：

- A = 0：完全没成交 → 公式分母为 0，c_2 出问题
- k = 0：完全平的强度曲线 → log(1 + ξΔ/0) = ∞ → c_1 爆炸

实战中要保护：

- 检测 A < 阈值（如 0.01）时跳过下单
- 检测 k 接近 0 时退回固定 spread

---

## 附录 D 参考文献

### 理论

- Guéant, O., Lehalle, C.-A., Fernandez-Tapia, J. *Optimal market making.* arXiv:1605.01862
- Avellaneda, M., Stoikov, S. *High-frequency trading in a limit order book.* Quantitative Finance, 2008
- Avellaneda, M., Stoikov, S. *Dealing with the Inventory Risk: A Solution to the Market Making Problem.* arXiv:1105.3115
- Cartea, Á., Jaikumar, S., Penalva, J. *Algorithmic and High-Frequency Trading.* Cambridge University Press, 2015

### 实战参考

- BitMEX, *How to Market Make Bitcoin Derivatives, Lesson 1/2*
- Borden, D. *Stochastic Control Theory and High Frequency Trading.* Columbia FE Seminar slides

### 参数校准

- *How does one calibrate lambda in a Avellaneda-Stoikov market making problem?* — Quant StackExchange

### 本仓库相关文档

- `docs/GLFT 做市模型说明.md`（精简版）
- `docs/Order Book Imbalance 做市策略说明.md`（带 alpha 的做市）
- `docs/订单簿失衡做市策略详解.md`
- `docs/延迟模型与队列模型详解.md`
- `docs/统计分析与绩效指标详解.md`
- `docs/策略开发实战指南.md`

### 示例代码

- `examples/GLFT Market Making Model and Grid Trading.ipynb`
- `examples/Market Making with Alpha - Order Book Imbalance.ipynb`
