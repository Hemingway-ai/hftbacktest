# GLFT 做市模型说明

本文档基于 `examples/GLFT Market Making Model and Grid Trading.ipynb`，说明该示例中 Guéant–Lehalle–Fernandez-Tapia，简称 GLFT，做市模型的理论含义、参数校准方式、代码实现流程，以及它与网格交易的结合方式。

## 1. 背景

在高频做市中，最核心的问题不是“看涨还是看跌”，而是：

- 我应该离中间价挂多远
- 当前波动率变大时，价差是否应该自动变宽
- 当前库存偏多或偏空时，买卖报价是否应该自动向某一侧偏移

固定价差的网格策略虽然简单，但对市场状态变化的适应性较差。GLFT 的作用，就是根据市场成交活跃度、波动率和库存，动态计算出更合理的双边报价，再把这个报价用于单层做市或扩展成网格。

## 2. GLFT 的核心思想

GLFT 可以看作 Avellaneda-Stoikov 模型的延伸版本。这个 notebook 使用的是论文 *Optimal market making* 中公式 4.6 和 4.7 的近似闭式解。

模型不强调一个强终止时刻 `T`，更适合连续运行的做市场景，例如：

- 现货
- 永续合约
- 高频双边被动挂单策略

模型直接给出相对于公平价格的最优买卖报价深度：

```math
\delta^{b*}_{approx}(q) = \frac{1}{\xi \Delta}\log\left(1 + \frac{\xi \Delta}{k}\right) + \frac{2q + \Delta}{2}\sqrt{\frac{\gamma \sigma^2}{2A\Delta k}\left(1 + \frac{\xi \Delta}{k}\right)^{\frac{k}{\xi \Delta} + 1}}
```

```math
\delta^{a*}_{approx}(q) = \frac{1}{\xi \Delta}\log\left(1 + \frac{\xi \Delta}{k}\right) - \frac{2q - \Delta}{2}\sqrt{\frac{\gamma \sigma^2}{2A\Delta k}\left(1 + \frac{\xi \Delta}{k}\right)^{\frac{k}{\xi \Delta} + 1}}
```

其中：

- `q` 是库存或持仓
- `σ` 是波动率
- `A, k` 描述成交强度函数
- `γ` 是库存风险厌恶系数
- `Δ` 是每笔下单的单位库存步长
- `ξ` 是模型中的另一个风险相关参数，本 notebook 里直接取 `ξ = γ`

这里的 `δ` 不是价格本身，而是“报价距离公平价格有多远”。

## 3. half spread 和 skew 的拆解

为了方便实现，notebook 先把上式拆成两个系数：

```math
c_1 = \frac{1}{\xi \Delta}\log\left(1 + \frac{\xi \Delta}{k}\right)
```

```math
c_2 = \sqrt{\frac{\gamma}{2A\Delta k}\left(1 + \frac{\xi \Delta}{k}\right)^{\frac{k}{\xi \Delta} + 1}}
```

于是最优报价深度可以改写为：

```math
\delta^{b*}_{approx}(q) = c_1 + \frac{\Delta}{2}\sigma c_2 + q \sigma c_2
```

```math
\delta^{a*}_{approx}(q) = c_1 + \frac{\Delta}{2}\sigma c_2 - q \sigma c_2
```

这样就能很自然地拆成两个交易上更直观的量：

```math
\text{half spread} = c_1 + \frac{\Delta}{2}\sigma c_2
```

```math
\text{skew} = \sigma c_2
```

所以：

```math
\delta_b(q) = \text{half spread} + \text{skew} \cdot q
```

```math
\delta_a(q) = \text{half spread} - \text{skew} \cdot q
```

最终报价写成价格形式：

```math
\text{bid price} = \text{fair price} - (\text{half spread} + \text{skew} \cdot q)
```

```math
\text{ask price} = \text{fair price} + (\text{half spread} - \text{skew} \cdot q)
```

直觉如下：

- `half spread` 决定双边报价离中间价有多远
- `skew` 决定库存对报价中心的偏移程度
- 库存偏多时，买价更低、卖价更积极
- 库存偏空时，买价更积极、卖价更高

## 4. 参数的交易含义

### 4.1 成交强度函数 `λ(δ)`

notebook 假设报价被成交的强度满足指数衰减：

```math
\lambda(\delta) = A e^{-k\delta}
```

含义是：

- 离中间价越近，越容易被成交
- 离中间价越远，成交强度按指数衰减

其中：

- `A` 表示靠近中间价时的基准成交活跃度
- `k` 表示成交强度随距离衰减的快慢

一般来说：

- `A` 越大，市场越活跃，报价可以更紧
- `k` 越大，离中间价稍远就很难成交，报价不宜过深

### 4.2 波动率 `σ`

波动率越大，说明短期价格跳动越剧烈。模型会自动扩大：

- 半价差
- 库存偏斜强度

也就是说，市场越不稳定，做市越保守。

### 4.3 风险厌恶参数 `γ`

`γ` 控制你对库存风险有多敏感：

- `γ` 小，做市更激进，价差更窄
- `γ` 大，做市更保守，更快把报价向去库存方向偏移

在这个 notebook 中，`γ = 0.05`，属于示例里人为设定的策略参数，不是从数据中估计出来的。

### 4.4 `Δ` 和 `ξ`

这个 notebook 中明确采用：

- `Δ = 1`
- `ξ = γ`

这也是后续 `compute_coeff()` 的输入方式。

## 5. notebook 如何校准 `A`、`k` 和 `σ`

这份示例不是直接给出一组固定参数，而是在市场回放过程中动态校准。

### 5.1 采样频率

策略每 `100ms` 处理一次市场状态：

- 收集最近一段时间内的成交信息
- 计算中间价变化
- 定期重估参数

### 5.2 记录 market order 到达深度

示例先记录一个量：`arrival_depth`。

定义方式是：

- 对于主动买成交，看成交价高于中间价多少
- 对于主动卖成交，看成交价低于中间价多少

如果某个市场单“穿过”了你挂单所在的距离，就近似认为你的挂单有机会成交。

这是一个简化假设，忽略了真实撮合中的排队位置。

### 5.3 从 `arrival_depth` 估计成交强度

notebook 使用 `measure_trading_intensity()` 来统计：

- 如果我的单挂在距中间价 `0.5 tick`
- `1.0 tick`
- `1.5 tick`
- 更远的位置

在观测窗口内大概会被多少次市场成交打到。

这个统计结果构成经验上的 `λ(δ)` 曲线。

### 5.4 用线性回归拟合 `A` 和 `k`

对成交强度函数取对数：

```math
\log \lambda = -k\delta + \log A
```

就变成一条直线，因此可以用线性回归估计：

- 斜率 `-k`
- 截距 `log A`

从而得到：

- `k = -slope`
- `A = exp(intercept)`

### 5.5 为什么只拟合浅层区间

notebook 明确指出，全区间拟合并不理想：

- 靠近中间价的位置会被高估
- 远离中间价的位置会被低估

而做市单通常挂在接近中间价的位置，因此示例最终只取最近 `70 ticks` 的浅层区间重新拟合。这一点很重要，因为它意味着策略不是在拟合整条远端尾部，而是在拟合“真正关心的成交区域”。

### 5.6 波动率的估计方式

策略记录中间价变动 `mid_price_chg`，然后用最近窗口的标准差估计波动率：

```python
volatility = np.nanstd(mid_price_chg[window]) * np.sqrt(10)
```

因为采样周期是 `100ms`，每秒有 `10` 个采样点，所以需要乘 `sqrt(10)`，把标准差转换为“每秒平方根尺度”的波动率。

## 6. `compute_coeff()` 在做什么

`compute_coeff(xi, gamma, delta, A, k)` 就是把前面的理论公式变成代码：

```python
c1 = 1 / (xi * delta) * np.log(1 + xi * delta / k)
c2 = np.sqrt(gamma / (2 * A * delta * k) * ((1 + xi * delta / k) ** (k / (xi * delta) + 1)))
```

后续所有报价只依赖这两个系数加上当前波动率。

这一步相当于把“市场状态”压缩成两个最关键的报价控制量。

## 7. `glft_market_maker()` 的策略循环

notebook 中的 `glft_market_maker()` 是最核心的实现。其逻辑可以概括为以下步骤。

### 7.1 每 100ms 更新一次状态

策略在每次循环中：

- 读取盘口深度
- 获取当前持仓
- 获取最近成交
- 更新中间价
- 清理失效订单

### 7.2 每 5 秒重估参数

代码中用：

```python
if t % 50 == 0:
```

因为每步是 `100ms`，`50` 步就是 `5s`。

每隔 `5s`，策略用最近 `10` 分钟窗口重新估计：

- `A`
- `k`
- `volatility`

这使得做市参数会随着市场状态缓慢自适应，而不是每个 tick 都剧烈变化。

### 7.3 计算 half spread 和 skew

原始版本使用：

```python
half_spread_tick = c1 + delta / 2 * c2 * volatility
skew = c2 * volatility
```

然后用库存修正报价中心：

```python
reservation_price_tick = mid_price_tick - skew * position
```

含义是：

- 库存越多，`reservation_price_tick` 越往下
- 库存越空，`reservation_price_tick` 越往上

### 7.4 生成最终买卖报价

策略使用：

```python
bid_price_tick = np.minimum(np.round(reservation_price_tick - half_spread_tick), best_bid_tick)
ask_price_tick = np.maximum(np.round(reservation_price_tick + half_spread_tick), best_ask_tick)
```

这代表：

- 买价不会高于当前最优买
- 卖价不会低于当前最优卖

因此它偏向做被动挂单，而不是主动吃单。

### 7.5 只保留最新目标报价

如果当前工作订单的价格已经不等于新计算出的目标价：

- 原订单撤掉
- 新订单补上

同时策略限制最大持仓：

- `max_position = 20`

超出限制后，某一侧就不再继续挂单。

## 8. 为什么要引入 `adj1` 和 `adj2`

notebook 后面又给出了一个“调整版” GLFT：

```python
half_spread_tick = (c1 + delta / 2 * c2 * volatility) * adj1
skew = c2 * volatility * adj2
```

其中示例参数大致是：

- `adj1 = 1`
- `adj2 = 0.05`

作者这样做的原因很直接：原始 GLFT 算出来的 `skew` 太强。

表现为：

- 只积累少量库存
- 报价中心就被大幅推向去库存一侧
- 策略变得不愿意继续接仓

因此 notebook 保留理论结构，但对强度做经验上的缩放。这在实盘和回测中都很常见：理论公式给方向，实际强度靠数据和回测再调。

## 9. GLFT 与网格交易的结合

在 `gridtrading_glft_mm()` 中，GLFT 不再只是输出一对 bid/ask，而是作为网格的锚点。

流程如下：

### 9.1 先算动态基准买卖价

和单层做市一样，先基于：

- `A`
- `k`
- `σ`
- `position`

得到一对动态 bid/ask。

### 9.2 再计算网格间距

示例代码中：

```python
grid_interval = max(np.round(half_spread_tick) * tick_size, tick_size)
```

这意味着：

- 网格间距直接和 GLFT 估计出的半价差关联
- 半价差越大，网格越疏
- 半价差越小，网格越密

因此网格不再是固定步长，而是随着市场状态变化。

### 9.3 对齐到网格并展开多层订单

策略会先把动态 bid/ask 对齐到网格：

```python
bid_price = np.floor(bid_price / grid_interval) * grid_interval
ask_price = np.ceil(ask_price / grid_interval) * grid_interval
```

然后分别向下和向上扩展出多层价格：

- 买侧向下铺 `grid_num` 层
- 卖侧向上铺 `grid_num` 层

示例里：

- `grid_num = 20`

所以本质上是：

- GLFT 决定网格中心和大致疏密
- Grid 决定具体挂多少层

这比固定网格更合理，因为市场变快、波动变大、库存变化时，整个网格都会自动重心偏移和宽度调整。

## 10. 这份示例的重要简化假设

理解这部分很重要，否则容易把示例当成完全真实的交易模型。

### 10.1 忽略排队位置

示例在估计成交强度时，不考虑订单在队列中的排队位置，只要市场成交打到了那个深度，就近似认为你的单可能成交。

这通常会高估成交概率。

### 10.2 按成交次数，不按成交量

`measure_trading_intensity()` 统计的是会不会被打到的次数，而不是被打到多少成交量。

所以它更接近“成交机会强度”，而不是“成交量强度”。

### 10.3 half-tick 单位问题

notebook 里明确提示，`measure_trading_intensity` 的输出存在 half-tick 与 tick 单位的历史问题。为了保持示例结果一致，文档和代码都沿用了旧写法。

这意味着：

- 示例结果可用于理解流程
- 但如果你要据此做严谨研究或迁移到实盘，最好先统一单位定义

### 10.4 公平价格直接取中间价

这里的 `fair price` 使用的是：

```python
mid_price_tick = (best_bid_tick + best_ask_tick) / 2.0
```

这是一种简化。更复杂的策略可能会把以下信息加入公平价格估计：

- 盘口不平衡
- 短期 alpha
- 成交方向偏置
- 跨市场信息

## 11. 用一句话概括这份 notebook 的 GLFT

这份示例中的 GLFT 不是一个“预测涨跌”的模型，而是一个“根据成交活跃度、波动率和库存，自适应计算最合适挂单距离与报价偏移”的模型。

如果只看交易行为，它做了三件事：

1. 根据历史成交深度估计挂多远更容易成交
2. 根据当前波动率决定价差该有多宽
3. 根据当前库存决定报价中心该向哪边偏

然后再把这个动态报价用于：

- 单层双边做市
- 或者扩展成动态网格交易

## 12. 对策略开发的启发

如果你准备基于这个 notebook 继续开发，可以优先考虑以下几个方向：

- 把公平价格从单纯中间价升级成带 alpha 的 reservation price
- 把成交强度估计从“次数”升级成“按量加权”
- 把排队位置纳入 fill probability 模型
- 对不同市场状态分别拟合 `A` 和 `k`
- 将 `adj1`、`adj2` 作为待优化超参数，而不是固定手调

## 13. 相关文件

- 示例 notebook：`examples/GLFT Market Making Model and Grid Trading.ipynb`
- 主要函数：
  - `measure_trading_intensity_and_volatility`
  - `measure_trading_intensity`
  - `linear_regression`
  - `compute_coeff`
  - `glft_market_maker`
  - `gridtrading_glft_mm`

## 14. 参考资料

- Guéant, Lehalle, Fernandez-Tapia, *Optimal market making*
- Avellaneda, Stoikov, *Dealing with the Inventory Risk: A Solution to the Market Making Problem*
- BitMEX Market Making 系列文章

