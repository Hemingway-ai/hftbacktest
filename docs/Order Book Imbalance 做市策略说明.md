# Order Book Imbalance 做市策略说明

本文档基于 `examples/Market Making with Alpha - Order Book Imbalance.ipynb`，说明该示例中订单簿失衡 `Order Book Imbalance, OBI` 的定义、alpha 构造方式、做市报价逻辑、主要参数含义，以及该策略在回测中的定位与局限。

## 1. 背景

这份示例的目标不是像 GLFT 那样从成交强度和波动率推导最优价差，而是直接把盘口结构中的失衡信息转成一个短期 alpha 信号，然后用这个 alpha 去推动做市报价中心偏移。

核心思路可以概括为：

```text
订单簿失衡 -> 标准化 alpha -> fair price
fair price + 库存约束 -> reservation price
reservation price ± half spread -> bid / ask
```

因此，这是一种“带 alpha 的做市策略”，而不是单纯的被动双边报价。

## 2. 什么是 Order Book Imbalance

订单簿失衡描述的是，在某一段盘口深度内，买盘和卖盘的数量是否明显不对称。

如果在接近中间价的区域内：

- 买盘总量显著大于卖盘总量，说明短期向上压力更强
- 卖盘总量显著大于买盘总量，说明短期向下压力更强

这类信息属于典型的市场微观结构信号，常被用来预测极短周期内的价格偏移。

## 3. notebook 开头介绍的几类盘口指标

这份 notebook 在开头列举了几种常见的盘口类指标：

- Static Order Book Imbalance
- Standardized Order Book Imbalance
- VAMP
- Weighted-Depth Order Book Price
- VAMP effective 等变体

但本 notebook 真正拿来做策略测试的，是最简单的一种：

```text
Standardized Order Book Imbalance
```

也就是把原始盘口失衡做成 rolling 标准化后的时间序列。

## 4. 这份示例中的 OBI 定义

策略每次循环时，都会读取当前盘口深度，并在中间价上下一个固定百分比范围内累计买卖盘数量。

中间价定义为：

```python
mid_price = (best_bid + best_ask) / 2.0
```

设 `looking_depth` 为观测深度比例，例如 `0.025` 表示中间价上下 `2.5%`。

那么：

- 卖盘从 `best_ask` 往上累计，到 `mid_price * (1 + looking_depth)`
- 买盘从 `best_bid` 往下累计，到 `mid_price * (1 - looking_depth)`

最后得到原始失衡值：

```python
imbalance = sum_bid_qty - sum_ask_qty
```

其直觉是：

- `imbalance > 0`，买盘更厚
- `imbalance < 0`，卖盘更厚

## 5. 为什么要做标准化

直接使用 `sum_bid_qty - sum_ask_qty` 有一个问题：不同时间段盘口总挂单量可能差异很大，原始值不可直接比较。

因此 notebook 对 `imbalance` 做了 rolling 标准化：

```python
m = np.nanmean(imbalance_timeseries[max(0, t + 1 - window):t + 1])
s = np.nanstd(imbalance_timeseries[max(0, t + 1 - window):t + 1])
alpha = (imbalance_timeseries[t] - m) / s
```

于是 `alpha` 的含义变成：

```text
当前盘口失衡相对于最近一段历史均值，偏离了多少个标准差
```

这让信号在不同时间段更可比，也更适合直接映射到价格偏移。

## 6. alpha 如何进入做市报价

这份 notebook 最关键的一行是：

```python
fair_price = mid_price + c1 * alpha
```

这意味着：

- 当 `alpha > 0` 时，公平价格高于当前中间价
- 当 `alpha < 0` 时，公平价格低于当前中间价

也就是说，策略认为盘口失衡能够预测短期价格中心的移动。

其中：

- `mid_price` 是当前市场中间价
- `alpha` 是标准化后的订单簿失衡
- `c1` 是把 alpha 转成价格偏移的系数

因此 `c1` 越大，说明策略越相信 OBI 对短期价格的预测能力。

## 7. 再叠加库存惩罚

仅有 alpha 还不够，因为做市策略还要控制库存风险。

notebook 中进一步定义：

```python
normalized_position = position / order_qty
reservation_price = fair_price - skew * normalized_position
```

这里的逻辑是：

- 持仓偏多时，`reservation_price` 会被压低，鼓励卖出、抑制继续买入
- 持仓偏空时，`reservation_price` 会被抬高，鼓励买回、抑制继续卖出

值得注意的是，库存不是直接按绝对数量进入，而是先除以 `order_qty` 做了归一化，等于把仓位解释成“当前持仓相当于多少个标准下单单位”。

## 8. 最终报价怎么生成

得到 `reservation_price` 后，策略再加上固定半价差：

```python
bid_price = reservation_price - half_spread
ask_price = reservation_price + half_spread
```

然后加上不穿价约束：

```python
bid_price = min(np.round(reservation_price - half_spread), best_bid)
ask_price = max(np.round(reservation_price + half_spread), best_ask)
```

再对齐到 tick：

```python
bid_price = np.floor(bid_price / tick_size) * tick_size
ask_price = np.ceil(ask_price / tick_size) * tick_size
```

这说明该策略依然是典型的被动挂单做市：

- 买价不会高于当前 best bid
- 卖价不会低于当前 best ask

## 9. `obi_mm()` 的完整执行流程

可以把 `obi_mm()` 概括为以下步骤：

1. 每隔固定时间 `interval` 运行一次。
2. 读取当前盘口和仓位。
3. 计算中间价。
4. 在给定深度范围内累计 bid / ask 数量。
5. 计算原始失衡值 `imbalance`。
6. 对 `imbalance` 做 rolling 标准化，得到 `alpha`。
7. 用 `fair_price = mid_price + c1 * alpha` 生成带信号的公平价。
8. 用库存惩罚把公平价修正成 `reservation_price`。
9. 用固定半价差生成 bid / ask。
10. 撤掉不在新目标价格上的旧订单，提交新订单。
11. 记录状态用于回测统计。

这套流程非常直接，优点是结构清晰，便于替换信号源。

## 10. 参数含义

这份 notebook 的参数几乎全部是手动设定的交易参数，而不是在线拟合出来的。

### 10.1 `half_spread`

固定半价差。

- 越大，报价越保守
- 越小，越容易成交，但更容易被 adverse selection

### 10.2 `skew`

库存惩罚强度。

- 越大，越不愿意持有库存
- 越小，策略更愿意承受持仓波动

### 10.3 `c1`

alpha 到价格偏移的映射系数。

- 越大，fair price 对 OBI 更敏感
- 越小，策略更接近普通中间价做市

### 10.4 `looking_depth`

观测订单簿失衡的深度比例。

例如：

- `0.025` 表示看中间价上下 `2.5%`
- `0.001` 表示看中间价上下 `0.1%`

深度越浅，信号越偏近端盘口；深度越深，信号更平滑，但也更慢。

### 10.5 `interval`

策略刷新频率。

- `1_000_000_000` 表示 `1s`
- `500_000_000` 表示 `500ms`

### 10.6 `window`

rolling 标准化窗口长度。

例如：

- `1hour`
- `10min`

窗口越长，标准化更稳定；窗口越短，信号更敏感。

### 10.7 `order_qty_dollar`

每笔订单的目标美元名义规模。代码会根据当前价格换算成真实下单数量。

### 10.8 `max_position_dollar`

最大库存限制，以美元名义表示。

### 10.9 `grid_num`

每一侧挂几层单。

虽然代码支持网格，但这份 notebook 的示例大多使用：

```python
grid_num = 1
```

因此实质上更接近单层双边做市。

### 10.10 `grid_interval`

网格层间距。若 `grid_num = 1`，它基本不起作用。

## 11. 订单更新逻辑

策略在每次循环里都会生成新的目标订单集合：

- 买侧目标订单 `new_bid_orders`
- 卖侧目标订单 `new_ask_orders`

然后：

- 如果当前工作订单不在新的目标集合中，就撤单
- 如果某个目标价格上还没有订单，就挂新单

这是一种典型的“目标状态驱动”更新方式，而不是在原订单基础上做局部修改。

## 12. notebook 中的几组实验

### 12.1 常规做市场景

示例中有一组较稳健的参数，例如：

- `half_spread = 80`
- `skew = 3.5`
- `c1 = 160`
- `depth = 0.025`
- `interval = 1s`
- `window = 1h`
- `order_qty_dollar = 50_000`

这类参数更像是在较大尺度下，用较慢频率做信号驱动的双边报价。

### 12.2 细 tick 版本

另一个实验场景中参数变成：

- `half_spread = 5`
- `skew = 0.2`
- `c1 = 10`
- `tick_size = 0.01`

说明这些参数并不是通用常数，而要和：

- tick size
- 标的价格尺度
- 市场流动性

一起匹配。

### 12.3 “刷量获取做市商资格”场景

notebook 明确提到一种不同目标的策略：通过提高交易量获得 market maker rebate 或资格。

对应参数更激进，例如：

- `half_spread = 10`
- `skew = 2`
- `c1 = 20`
- `depth = 0.001`
- `interval = 500ms`
- `window = 10min`
- `order_qty_dollar = 25_000`

作者也明确指出，这种策略未必本身盈利，但可能有助于达成做市商量化门槛。

## 13. 这份 notebook 想说明什么

它想说明的是：

- 订单簿失衡可以作为稳定的高频 alpha
- 把 OBI 直接加进 fair price 是一种简单有效的实现方式
- 做市策略的盈利对 rebate 和 fee structure 非常敏感

这份 notebook 后面还给出了更新后的回测结果，并指出：

- OBI 信号仍然有效
- 但单笔收益下降
- 回扣在总收益中占很重要的位置

这对高频做市非常现实：很多策略的 edge 很薄，手续费结构往往决定策略能否长期成立。

## 14. 与 GLFT 的差别

如果和前一个 GLFT notebook 对比，这份 OBI notebook 的区别很清楚。

GLFT 更关注：

- 成交强度
- 波动率
- 理论最优 half spread 和 skew

而 OBI 更关注：

- 盘口结构带来的短期 alpha
- 公平价格的动态偏移

可以简单理解为：

- GLFT 是“报价优化模型”
- OBI 是“信号驱动做市模型”

两者并不冲突，实际上完全可以结合：

- 用 OBI 调 fair price
- 用 GLFT 调 half spread 和 skew

## 15. 这份示例的局限

这份 notebook 很有启发，但也有明显简化。

### 15.1 `half_spread`、`skew`、`c1` 都是手工调参

它们没有像 GLFT 那样根据市场状态自动校准。

### 15.2 只用了单一 alpha

fair price 只依赖 OBI，没有结合：

- trade flow
- micro-price
- short-term return
- order book imbalance 的更多变体

### 15.3 OBI 的观测深度是固定的

`looking_depth` 不随波动率、流动性或 spread 动态变化。

### 15.4 标准化窗口固定

在 regime 切换明显时，固定窗口的均值和标准差未必稳定。

### 15.5 回测结果对撮合假设和 rebate 很敏感

例如：

- queue model 参数改变
- rebate 降低
- fill 难度上升

都会显著影响结果。

## 16. 一句话总结

这份 notebook 的本质是：

```text
用标准化订单簿失衡预测短期价格偏移，
再把这个偏移写进 fair price，
并结合库存惩罚生成做市报价。
```

它是一个很典型、很实用的“微观结构 alpha + 被动做市”示例。

## 17. 相关文件

- 示例 notebook：`examples/Market Making with Alpha - Order Book Imbalance.ipynb`
- 主要函数：
  - `obi_mm`

## 18. 参考资料

- *The Micro-Price: A High Frequency Estimator of Future Prices*
- *Mind the Gaps: Short-Term Crypto Price Prediction*
- Headlands Tech 的 *Market microstructure signals*

