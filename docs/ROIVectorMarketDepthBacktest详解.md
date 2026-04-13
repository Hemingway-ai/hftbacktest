# ROIVectorMarketDepthBacktest 详解

## 一、概述

`ROIVectorMarketDepthBacktest` 是 hftbacktest 框架中**基于向量化市场深度的回测引擎**。它使用 **ROI (Range of Interest)** 机制，只在指定价格范围内维护订单簿数据，从而实现高性能的回测。

### 1.1 名称解析

- **ROI**: Range of Interest（关注范围），只维护指定价格区间内的订单簿
- **Vector**: 使用连续数组存储深度数据，而非哈希表
- **MarketDepth**: 市场深度（订单簿）数据
- **Backtest**: 回测引擎

### 1.2 与 HashMapMarketDepthBacktest 的区别

| 特性 | ROIVectorMarketDepthBacktest | HashMapMarketDepthBacktest |
|------|------------------------------|----------------------------|
| 数据结构 | 连续数组 | 哈希表 |
| 价格范围 | 限定ROI范围 | 无限制 |
| 内存访问 | 缓存友好，连续内存 | 随机访问 |
| 适用场景 | 价格波动有限的品种 | 价格波动剧烈的品种 |
| 性能 | 更高 | 较低 |

---

## 二、核心架构

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                  ROIVectorMarketDepthBacktest                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                    Market Data Flow                             │ │
│  │                                                                 │ │
│  │   Reader ──► Local Processor ──► Strategy ──► Exchange Proc.   │ │
│  │     │            │                   │              │          │ │
│  │     ▼            ▼                   ▼              ▼          │ │
│  │   Events    ROIVectorDepth      Order Logic   ROIVectorDepth   │ │
│  │                                                                 │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                 ROIVectorMarketDepth                            │ │
│  │                                                                 │ │
│  │   Price:  roi_lb ◄────────────────────────────► roi_ub         │ │
│  │            │                                        │           │ │
│  │            ▼                                        ▼           │ │
│  │   Index:     0    1    2    3   ...   N-2   N-1    N           │ │
│  │            ┌────┬────┬────┬────┬─────┬────┬────┬────┐          │ │
│  │  bid_depth │ Q0 │ Q1 │ Q2 │ Q3 │ ... │    │    │    │          │ │
│  │            └────┴────┴────┴────┴─────┴────┴────┴────┘          │ │
│  │            ┌────┬────┬────┬────┬─────┬────┬────┬────┐          │ │
│  │  ask_depth │ Q0 │ Q1 │ Q2 │ Q3 │ ... │    │    │    │          │ │
│  │            └────┴────┴────┴────┴─────┴────┴────┴────┘          │ │
│  │                                                                 │ │
│  │   Index = price_tick - roi_lb_tick                             │ │
│  │   Price = (index + roi_lb_tick) * tick_size                    │ │
│  │                                                                 │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 数据结构

```rust
pub struct ROIVectorMarketDepth {
    pub tick_size: f64,           // 最小价格变动
    pub lot_size: f64,            // 最小数量变动
    pub timestamp: i64,           // 当前时间戳
    pub ask_depth: Vec<f64>,      // 卖盘深度数组
    pub bid_depth: Vec<f64>,      // 买盘深度数组
    pub best_bid_tick: i64,       // 最优买价tick
    pub best_ask_tick: i64,       // 最优卖价tick
    pub low_bid_tick: i64,        // 最低买价tick
    pub high_ask_tick: i64,       // 最高卖价tick
    pub roi_ub: i64,              // ROI上界tick
    pub roi_lb: i64,              // ROI下界tick
    pub orders: HashMap<OrderId, L3Order>,  // 订单映射（L3支持）
}
```

---

## 三、方法详解

### 3.1 属性方法

#### `current_timestamp`

返回当前回测时间戳。

```python
@property
def current_timestamp(self) -> int64:
    """
    In backtesting, this timestamp reflects the time at which 
    the backtesting is conducted within the provided data.
    """
```

**示例：**
```python
ts = hbt.current_timestamp  # 纳秒级时间戳
```

#### `num_assets`

返回资产数量。

```python
@property
def num_assets(self) -> uint64:
    """
    Returns the number of assets.
    """
```

**示例：**
```python
n = hbt.num_assets  # 资产数量
```

---

### 3.2 市场数据方法

#### `depth(asset_no)`

获取指定资产的市场深度对象。

```python
def depth(self, asset_no: uint64) -> ROIVectorMarketDepth:
    """
    Args:
        asset_no: Asset number from which the market depth will be retrieved.

    Returns:
        The depth of market of the specific asset.
    """
```

**ROIVectorMarketDepth 属性：**

| 属性 | 类型 | 说明 |
|------|------|------|
| `best_bid` | float64 | 最优买价 |
| `best_ask` | float64 | 最优卖价 |
| `best_bid_tick` | int64 | 最优买价tick |
| `best_ask_tick` | int64 | 最优卖价tick |
| `best_bid_qty` | float64 | 最优买价数量 |
| `best_ask_qty` | float64 | 最优卖价数量 |
| `tick_size` | float64 | 最小价格变动 |
| `lot_size` | float64 | 最小数量变动 |
| `bid_depth` | ndarray | 买盘深度数组 |
| `ask_depth` | ndarray | 卖盘深度数组 |
| `roi_lb_tick` | int64 | ROI下界tick |
| `roi_ub_tick` | int64 | ROI上界tick |

**示例：**
```python
depth = hbt.depth(0)

# 获取最优价格
best_bid = depth.best_bid
best_ask = depth.best_ask
mid_price = (best_bid + best_ask) / 2.0

# 获取深度数组
bid_depth = depth.bid_depth  # NumPy数组
ask_depth = depth.ask_depth

# 通过tick获取数量
qty = depth.bid_qty_at_tick(price_tick)
```

#### `last_trades(asset_no)`

获取最近的市场成交记录。

```python
def last_trades(self, asset_no: uint64) -> EVENT_ARRAY:
    """
    Args:
        asset_no: Asset number from which the trades will be retrieved.

    Returns:
        An array of Event representing trades occurring in the market.
    """
```

**示例：**
```python
trades = hbt.last_trades(0)
for trade in trades:
    price = trade.px
    qty = trade.qty
    # 处理成交数据
```

#### `clear_last_trades(asset_no)`

清空成交记录缓冲区。

```python
def clear_last_trades(self, asset_no: uint64) -> None:
    """
    Clears the last trades occurring in the market from the buffer.
    """
```

---

### 3.3 持仓与状态方法

#### `position(asset_no)`

获取指定资产的持仓数量。

```python
def position(self, asset_no: uint64) -> float64:
    """
    Args:
        asset_no: Asset number from which the position will be retrieved.

    Returns:
        The quantity of the held position.
    """
```

**示例：**
```python
pos = hbt.position(0)  # 正数为多头，负数为空头
```

#### `state_values(asset_no)`

获取状态值对象（包含余额、手续费等信息）。

```python
def state_values(self, asset_no: uint64) -> StateValues:
    """
    Args:
        asset_no: Asset number from which the state values will be retrieved.

    Returns:
        The state's values.
    """
```

**StateValues 属性：**
- `position`: 持仓数量
- `balance`: 余额
- `fee`: 累计手续费
- `trade_qty`: 交易数量
- `trade_value`: 交易价值

---

### 3.4 订单管理方法

#### `orders(asset_no)`

获取订单字典。

```python
def orders(self, asset_no: uint64) -> OrderDict:
    """
    Returns:
        An order dictionary where the keys are order IDs and 
        the corresponding values are Order.
    """
```

**OrderDict 方法：**
- `get(order_id)`: 获取指定订单
- `values()`: 获取订单迭代器
- `__contains__(order_id)`: 检查订单是否存在
- `__len__()`: 获取订单数量

**示例：**
```python
orders = hbt.orders(0)

# 检查订单是否存在
if order_id in orders:
    order = orders.get(order_id)
    
# 遍历所有订单
values = orders.values()
while values.has_next():
    order = values.get()
    # 处理订单
```

#### `clear_inactive_orders(asset_no)`

清理非活跃订单。

```python
def clear_inactive_orders(self, asset_no: uint64) -> None:
    """
    Clears inactive orders from the local order dictionary whose 
    status is neither NEW nor PARTIALLY_FILLED.
    """
```

---

### 3.5 订单提交方法

#### `submit_buy_order(...)`

提交买入订单。

```python
def submit_buy_order(
    self,
    asset_no: uint64,      # 资产编号
    order_id: uint64,      # 订单ID（唯一）
    price: float64,        # 价格
    qty: float64,          # 数量
    time_in_force: uint8,  # 有效期类型
    order_type: uint8,     # 订单类型
    wait: bool             # 是否等待响应
) -> int64:
```

#### `submit_sell_order(...)`

提交卖出订单。

```python
def submit_sell_order(
    self,
    asset_no: uint64,
    order_id: uint64,
    price: float64,
    qty: float64,
    time_in_force: uint8,
    order_type: uint8,
    wait: bool
) -> int64:
```

**参数详解：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `asset_no` | uint64 | 资产编号（多资产时使用） |
| `order_id` | uint64 | 订单ID，必须唯一 |
| `price` | float64 | 订单价格 |
| `qty` | float64 | 订单数量 |
| `time_in_force` | uint8 | 有效期类型 |
| `order_type` | uint8 | 订单类型 |
| `wait` | bool | 是否等待响应 |

**Time In Force 类型：**

| 常量 | 值 | 说明 |
|------|-----|------|
| `GTC` | 0 | Good Till Cancel，直到取消 |
| `GTX` | 1 | Good Till Crossing，只做Maker |
| `FOK` | 2 | Fill Or Kill，全部成交或取消 |
| `IOC` | 3 | Immediate Or Cancel，立即成交或取消 |

**Order Type 类型：**

| 常量 | 值 | 说明 |
|------|-----|------|
| `LIMIT` | 0 | 限价单 |
| `MARKET` | 1 | 市价单 |

**返回值：**

| 值 | 含义 |
|-----|------|
| 0 | 成功提交 |
| 1 | 数据结束（wait=True时） |
| 10 | 订单ID已存在 |
| 11 | 订单请求处理中 |
| 其他 | 错误 |

**示例：**
```python
from hftbacktest import GTX, LIMIT

# 提交买入订单
order_id = 1001
price = 100.5
qty = 1.0

result = hbt.submit_buy_order(
    asset_no=0,
    order_id=order_id,
    price=price,
    qty=qty,
    time_in_force=GTX,  # 只做Maker
    order_type=LIMIT,   # 限价单
    wait=False
)

if result == 0:
    print("订单提交成功")
```

#### `cancel(asset_no, order_id, wait)`

取消订单。

```python
def cancel(self, asset_no: uint64, order_id: uint64, wait: bool) -> int64:
    """
    Cancels the specified order.

    Returns:
        0: success
        1: end of data (if wait=True)
        other: error
    """
```

**示例：**
```python
result = hbt.cancel(0, order_id, wait=False)
```

#### `modify(asset_no, order_id, price, qty, wait)`

修改订单。

```python
def modify(
    self, 
    asset_no: uint64, 
    order_id: uint64, 
    price: float, 
    qty: float, 
    wait: bool
) -> int64:
```

---

### 3.6 时间控制方法

#### `elapse(duration)`

推进指定时间。

```python
def elapse(self, duration: uint64) -> int64:
    """
    Elapses the specified duration.

    Args:
        duration: Duration to elapse. Nanoseconds is the default unit.

    Returns:
        0: success
        1: end of data
        other: error
    """
```

**示例：**
```python
# 推进1秒
result = hbt.elapse(1_000_000_000)

# 在循环中使用
while hbt.elapse(1_000_000_000) == 0:
    # 每秒执行一次策略逻辑
    pass
```

#### `elapse_bt(duration)`

仅在回测中推进时间（实盘忽略）。

```python
def elapse_bt(self, duration: int64) -> int64:
    """
    Elapses time only in backtesting. In live mode, it is ignored.
    
    This can be utilized to simulate processing times.
    """
```

#### `wait_order_response(asset_no, order_id, timeout)`

等待订单响应。

```python
def wait_order_response(
    self, 
    asset_no: uint64, 
    order_id: uint64, 
    timeout: int64
) -> int64:
    """
    Waits for the response of the order with the given order ID until timeout.
    """
```

#### `wait_next_feed(include_order_resp, timeout)`

等待下一个市场数据或订单响应。

```python
def wait_next_feed(self, include_order_resp: bool, timeout: int64) -> int64:
    """
    Waits until the next feed is received, or until timeout.

    Returns:
        0: timeout
        1: end of data
        2: market feed received
        3: order response received (if include_order_resp=True)
    """
```

---

### 3.7 延迟信息方法

#### `feed_latency(asset_no)`

获取最近的市场数据延迟。

```python
def feed_latency(self, asset_no: uint64) -> Tuple[int64, int64] | None:
    """
    Returns:
        (exch_ts, local_ts) if a feed has been received; otherwise, None.
    """
```

**示例：**
```python
latency = hbt.feed_latency(0)
if latency:
    exch_ts, local_ts = latency
    network_latency = local_ts - exch_ts
```

#### `order_latency(asset_no)`

获取最近的订单延迟。

```python
def order_latency(self, asset_no: uint64) -> Tuple[int64, int64, int64] | None:
    """
    Returns:
        (req_ts, exch_ts, resp_ts) if there has been an order submission; 
        otherwise, None.
    """
```

**示例：**
```python
latency = hbt.order_latency(0)
if latency:
    req_ts, exch_ts, resp_ts = latency
    entry_latency = exch_ts - req_ts
    resp_latency = resp_ts - exch_ts
```

---

### 3.8 生命周期方法

#### `close()`

关闭回测引擎。

```python
def close(self) -> int64:
    """
    Closes this backtester or bot.

    Returns:
        0: success
        other: error
    """
```

---

## 四、ROI 机制详解

### 4.1 工作原理

ROI (Range of Interest) 机制只维护指定价格范围内的订单簿数据：

```
价格轴:
     roi_lb                    roi_ub
       │                         │
       ▼                         ▼
───────┬────┬────┬────┬────┬────┬────┬───────
       │ P0 │ P1 │ P2 │ P3 │ P4 │ P5 │
       └────┴────┴────┴────┴────┴────┘
         ↑                      ↑
         │                      │
      Index 0               Index 5

数组长度 = roi_ub_tick - roi_lb_tick + 1
索引计算: index = price_tick - roi_lb_tick
价格计算: price = (index + roi_lb_tick) * tick_size
```

### 4.2 配置方式

在 `BacktestAsset` 中设置：

```python
asset = (
    BacktestAsset()
    .roi_lb(0)       # 价格下界
    .roi_ub(3000)    # 价格上界
    # ...
)
```

### 4.3 性能优势

1. **内存效率**: 只分配有限大小的数组
2. **缓存友好**: 连续内存访问
3. **快速计算**: O(1) 时间复杂度访问任意价位

### 4.4 使用注意事项

- ROI 范围应覆盖策略可能交易的价格区间
- 价格超出 ROI 范围时，相关数据会被忽略
- 对于价格波动较大的品种，需要设置更大的范围

---

## 五、完整示例

### 5.1 基础做市策略

```python
from numba import njit
from hftbacktest import (
    BacktestAsset,
    ROIVectorMarketDepthBacktest,
    GTX, LIMIT, BUY, SELL
)

@njit
def simple_mm(hbt, half_spread, order_qty):
    asset_no = 0
    
    while hbt.elapse(1_000_000_000) == 0:  # 每秒更新
        hbt.clear_inactive_orders(asset_no)
        
        depth = hbt.depth(asset_no)
        position = hbt.position(asset_no)
        orders = hbt.orders(asset_no)
        
        best_bid = depth.best_bid
        best_ask = depth.best_ask
        mid_price = (best_bid + best_ask) / 2.0
        
        # 计算报价
        bid_price = mid_price - half_spread
        ask_price = mid_price + half_spread
        
        # 提交订单
        bid_order_id = int(bid_price * 10)
        ask_order_id = int(ask_price * 10) + 1000000
        
        if bid_order_id not in orders:
            hbt.submit_buy_order(
                asset_no, bid_order_id, bid_price, 
                order_qty, GTX, LIMIT, False
            )
        
        if ask_order_id not in orders:
            hbt.submit_sell_order(
                asset_no, ask_order_id, ask_price,
                order_qty, GTX, LIMIT, False
            )

# 配置回测
asset = (
    BacktestAsset()
    .data(['market_data.npz'])
    .linear_asset(1.0)
    .tick_size(0.1)
    .lot_size(0.001)
    .roi_lb(0)
    .roi_ub(3000)
)

hbt = ROIVectorMarketDepthBacktest([asset])
simple_mm(hbt, half_spread=0.5, order_qty=1.0)
hbt.close()
```

### 5.2 订单簿失衡策略

```python
@njit
def obi_strategy(hbt, looking_depth, window):
    asset_no = 0
    imbalance_ts = np.full(10000, np.nan)
    t = 0
    
    while hbt.elapse(1_000_000_000) == 0:
        depth = hbt.depth(asset_no)
        
        # 计算订单簿失衡
        mid_price = (depth.best_bid + depth.best_ask) / 2.0
        tick_size = depth.tick_size
        roi_lb_tick = depth.roi_lb_tick
        
        # 累加深度
        sum_bid_qty = 0.0
        sum_ask_qty = 0.0
        
        for i in range(len(depth.bid_depth)):
            sum_bid_qty += depth.bid_depth[i]
            sum_ask_qty += depth.ask_depth[i]
        
        imbalance_ts[t] = sum_bid_qty - sum_ask_qty
        
        # 标准化
        m = np.nanmean(imbalance_ts[max(0, t-window):t+1])
        s = np.nanstd(imbalance_ts[max(0, t-window):t+1])
        alpha = (imbalance_ts[t] - m) / s if s > 0 else 0
        
        # 根据alpha调整报价...
        
        t += 1
```

---

## 六、返回值对照表

### 6.1 elapse 返回值

| 值 | 含义 |
|-----|------|
| 0 | 成功推进 |
| 1 | 数据结束 |
| 2 | 收到市场数据 |
| 3 | 收到订单响应 |

### 6.2 订单操作返回值

| 值 | 含义 |
|-----|------|
| 0 | 成功 |
| 1 | 数据结束（wait=True时） |
| 10 | 订单ID已存在 |
| 11 | 订单请求处理中 |
| 12 | 订单未找到 |
| 13 | 无效订单请求 |
| 14 | 无效订单状态 |
| 100 | 数据错误 |

---

## 七、性能优化建议

### 7.1 ROI 范围设置

```python
# 根据品种波动性设置合适的ROI范围
# ETH: 价格约3000，波动约5%
asset.roi_lb(2500).roi_ub(3500)

# BTC: 价格约60000，波动约3%
asset.roi_lb(55000).roi_ub(65000)
```

### 7.2 使用 Numba JIT

```python
from numba import njit

@njit
def strategy(hbt):
    # 策略逻辑
    pass
```

### 7.3 批量操作

```python
# 批量取消旧订单
hbt.clear_inactive_orders(ALL_ASSETS)

# 批量清理成交
hbt.clear_last_trades(ALL_ASSETS)
```

---

## 八、常见问题

### Q1: 何时使用 ROIVectorMarketDepthBacktest vs HashMapMarketDepthBacktest？

**使用 ROIVectorMarketDepthBacktest 当：**
- 价格波动相对有限
- 需要频繁访问深度数组
- 追求最高性能

**使用 HashMapMarketDepthBacktest 当：**
- 价格波动剧烈
- 需要完整订单簿数据
- 不确定价格范围

### Q2: 如何处理价格超出 ROI 范围？

超出 ROI 范围的价格会被忽略，相关方法返回 NaN 或 0：

```python
qty = depth.bid_qty_at_tick(price_tick)
if np.isnan(qty):
    # 价格超出ROI范围
    pass
```

### Q3: order_id 如何设计？

建议使用价格tick作为order_id，便于管理：

```python
bid_order_id = uint64(round(bid_price / tick_size))
ask_order_id = uint64(round(ask_price / tick_size)) + 1_000_000_000
```

---

## 九、参考资料

- [hftbacktest 官方文档](https://hftbacktest.readthedocs.io/)
- [ROIVectorMarketDepth Rust API](https://docs.rs/hftbacktest/latest/hftbacktest/depth/struct.ROIVectorMarketDepth.html)
- [回测器文档](https://hftbacktest.readthedocs.io/en/latest/backtester.html)
