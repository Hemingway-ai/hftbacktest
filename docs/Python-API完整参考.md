# Python API 完整参考

## 一、概述

本文档提供 HftBacktest Python 绑定的完整 API 参考。所有策略代码通过这些 API 与回测引擎交互。

---

## 二、核心类

### 2.1 BacktestAsset

回测资产配置类，使用建造者模式。

```python
from hftbacktest import BacktestAsset
```

#### 数据配置方法

| 方法 | 参数 | 说明 |
|------|------|------|
| `.data(data)` | `str \| List[str] \| np.ndarray` | 设置市场数据源 |
| `.initial_snapshot(data)` | `str \| np.ndarray` | 设置初始订单簿快照 |
| `.parallel_load(bool)` | `bool` | 是否并行加载数据 (默认 True) |

#### 资产类型方法

| 方法 | 参数 | 说明 |
|------|------|------|
| `.linear_asset(contract_size)` | `float` | 线性合约 (USDT 本位) |
| `.inverse_asset(contract_size)` | `float` | 反向合约 (币本位) |

#### 延迟模型方法

| 方法 | 参数 | 说明 |
|------|------|------|
| `.constant_order_latency(entry, resp)` | `int, int` | 固定延迟 (纳秒) |
| `.intp_order_latency(data, offset)` | `str/ndarray, int` | 插值延迟模型 |
| `.latency_offset(ns)` | `int` | 延迟偏移量 |

#### 排队模型方法

| 方法 | 参数 | 说明 |
|------|------|------|
| `.risk_adverse_queue_model()` | - | 风险厌恶模型 |
| `.log_prob_queue_model()` | - | 对数概率模型 |
| `.log_prob_queue_model2()` | - | 对数概率模型 v2 (默认) |
| `.power_prob_queue_model(n)` | `int` | 幂次概率模型 |
| `.power_prob_queue_model2(n)` | `int` | 幂次概率模型 v2 |
| `.power_prob_queue_model3(n)` | `int` | 幂次概率模型 v3 |
| `.l3_fifo_queue_model()` | - | L3 FIFO 模型 |

#### 交易所模型方法

| 方法 | 说明 |
|------|------|
| `.no_partial_fill_exchange()` | 不允许部分成交 |
| `.partial_fill_exchange()` | 允许部分成交 |

#### 手续费模型方法

| 方法 | 参数 | 说明 |
|------|------|------|
| `.trading_value_fee_model(maker, taker)` | `float, float` | 按交易金额 |
| `.trading_qty_fee_model(maker, taker)` | `float, float` | 按交易数量 |
| `.flat_per_trade_fee_model(maker, taker)` | `float, float` | 固定费用/笔 |

#### 其他配置方法

| 方法 | 参数 | 说明 |
|------|------|------|
| `.tick_size(size)` | `float` | 最小价格变动 |
| `.lot_size(size)` | `float` | 最小数量变动 |
| `.roi_lb(price)` | `float` | ROI 价格下界 |
| `.roi_ub(price)` | `float` | ROI 价格上界 |
| `.last_trades_capacity(n)` | `int` | 成交记录容量 |

---

### 2.2 ROIVectorMarketDepthBacktest

基于 ROI 向量的回测引擎。

```python
from hftbacktest import ROIVectorMarketDepthBacktest

hbt = ROIVectorMarketDepthBacktest([asset1, asset2])
```

---

### 2.3 HashMapMarketDepthBacktest

基于 HashMap 的回测引擎。

```python
from hftbacktest import HashMapMarketDepthBacktest

hbt = HashMapMarketDepthBacktest([asset1, asset2])
```

---

## 三、Bot API (回测引擎方法)

以下方法在 `@njit` 策略函数中通过 `hbt` 对象调用。

### 3.1 时间控制

#### `elapse(duration) -> int64`

推进指定时间。

**参数**：
- `duration` (uint64): 时间长度，纳秒单位

**返回值**：
| 值 | 含义 |
|-----|------|
| 0 | 成功推进 |
| 1 | 数据结束 |

**示例**：
```python
# 推进 1 秒
while hbt.elapse(1_000_000_000) == 0:
    # 策略逻辑
    pass
```

#### `elapse_bt(duration) -> int64`

仅在回测中推进时间（实盘忽略），用于模拟处理时间。

#### `wait_next_feed(include_order_resp, timeout) -> int64`

等待下一个市场数据或订单响应。

**参数**：
- `include_order_resp` (bool): 是否包含订单响应
- `timeout` (int64): 超时时间，纳秒

**返回值**：
| 值 | 含义 |
|-----|------|
| 0 | 超时 |
| 1 | 数据结束 |
| 2 | 收到市场数据 |
| 3 | 收到订单响应 |

#### `wait_order_response(asset_no, order_id, timeout) -> int64`

等待指定订单的响应。

---

### 3.2 市场数据

#### `depth(asset_no) -> MarketDepth`

获取指定资产的市场深度对象。

**MarketDepth 属性**：

| 属性 | 类型 | 说明 |
|------|------|------|
| `best_bid` | float64 | 最优买价 |
| `best_ask` | float64 | 最优卖价 |
| `best_bid_tick` | int64 | 最优买价 tick |
| `best_ask_tick` | int64 | 最优卖价 tick |
| `best_bid_qty` | float64 | 最优买量 |
| `best_ask_qty` | float64 | 最优卖量 |
| `tick_size` | float64 | 最小价格变动 |
| `lot_size` | float64 | 最小数量变动 |
| `bid_depth` | ndarray | 买盘深度数组 (ROIVector) |
| `ask_depth` | ndarray | 卖盘深度数组 (ROIVector) |
| `roi_lb_tick` | int64 | ROI 下界 tick |
| `roi_ub_tick` | int64 | ROI 上界 tick |
| `current_timestamp` | int64 | 当前时间戳 |

**MarketDepth 方法**：

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `qty_at_tick(tick)` | int64 | float64 | 指定价位数量 |
| `bid_qty_at_tick(tick)` | int64 | float64 | 买方指定价位数量 |
| `ask_qty_at_tick(tick)` | int64 | float64 | 卖方指定价位数量 |

#### `last_trades(asset_no) -> EVENT_ARRAY`

获取最近的市场成交记录。

#### `clear_last_trades(asset_no)`

清空成交记录缓冲区。

---

### 3.3 持仓与状态

#### `position(asset_no) -> float64`

获取指定资产的持仓数量。正数为多头，负数为空头。

#### `state_values(asset_no) -> StateValues`

获取状态值对象。

**StateValues 属性**：
- `position`: 持仓数量
- `balance`: 余额
- `fee`: 累计手续费
- `trade_qty`: 交易数量
- `trade_value`: 交易价值

#### `num_assets -> int`

获取资产数量。

#### `current_timestamp -> int`

获取当前时间戳。

---

### 3.4 订单管理

#### `orders(asset_no) -> OrderDict`

获取订单字典。

**OrderDict 方法**：

| 方法 | 说明 |
|------|------|
| `get(order_id)` | 获取指定订单 |
| `values()` | 获取订单迭代器 |
| `__contains__(order_id)` | 检查订单是否存在 |
| `__len__()` | 获取订单数量 |

**Order 属性**：

| 属性 | 类型 | 说明 |
|------|------|------|
| `order_id` | uint64 | 订单 ID |
| `price_tick` | int64 | 价格 (tick) |
| `qty` | float64 | 订单数量 |
| `leaves_qty` | float64 | 剩余数量 |
| `exec_qty` | float64 | 已执行数量 |
| `side` | int | 买卖方向 |
| `status` | int | 订单状态 |
| `cancellable` | bool | 是否可取消 |

#### `clear_inactive_orders(asset_no)`

清理非活跃订单 (状态不是 NEW 或 PARTIALLY_FILLED)。

---

### 3.5 订单操作

#### `submit_buy_order(...) -> int64`

```python
hbt.submit_buy_order(
    asset_no,      # uint64: 资产编号
    order_id,      # uint64: 订单 ID (必须唯一)
    price,         # float64: 价格
    qty,           # float64: 数量
    time_in_force, # uint8: 有效期 (GTC/GTX/FOK/IOC)
    order_type,    # uint8: 类型 (LIMIT/MARKET)
    wait           # bool: 是否等待响应
)
```

**返回值**：
| 值 | 含义 |
|-----|------|
| 0 | 成功 |
| 1 | 数据结束 |
| 10 | 订单 ID 已存在 |
| 11 | 请求处理中 |

#### `submit_sell_order(...) -> int64`

参数同 `submit_buy_order`。

#### `cancel(asset_no, order_id, wait) -> int64`

取消订单。

#### `modify(asset_no, order_id, price, qty, wait) -> int64`

修改订单。

---

### 3.6 延迟信息

#### `feed_latency(asset_no) -> Tuple[int64, int64] | None`

返回 `(exch_ts, local_ts)`，即交易所时间戳和本地接收时间戳。

#### `order_latency(asset_no) -> Tuple[int64, int64, int64] | None`

返回 `(req_ts, exch_ts, resp_ts)`，即请求、交易所接收、响应时间戳。

---

### 3.7 生命周期

#### `close() -> int64`

关闭回测引擎。

---

## 四、常量定义

### 4.1 买卖方向

```python
from hftbacktest import BUY, SELL
```

### 4.2 订单状态

```python
from hftbacktest import NONE, NEW, EXPIRED, FILLED, CANCELED
```

### 4.3 有效期类型

```python
from hftbacktest import GTC, GTX, FOK, IOC
```

| 常量 | 值 | 说明 |
|------|-----|------|
| `GTC` | 0 | Good Till Cancel |
| `GTX` | 1 | Good Till Crossing (只做 Maker) |
| `FOK` | 2 | Fill Or Kill |
| `IOC` | 3 | Immediate Or Cancel |

### 4.4 订单类型

```python
from hftbacktest import LIMIT, MARKET
```

### 4.5 事件类型

```python
from hftbacktest import (
    ALL_ASSETS,
    DEPTH_EVENT,
    TRADE_EVENT,
    DEPTH_CLEAR_EVENT,
    DEPTH_SNAPSHOT_EVENT,
    DEPTH_BBO_EVENT,
    ADD_ORDER_EVENT,
    CANCEL_ORDER_EVENT,
    MODIFY_ORDER_EVENT,
    FILL_EVENT,
    EXCH_EVENT,
    LOCAL_EVENT,
    BUY_EVENT,
    SELL_EVENT,
)
```

---

## 五、统计分析 API

### 5.1 LinearAssetRecord

用于线性合约的回测结果分析。

```python
from hftbacktest.stats import LinearAssetRecord

# 加载回测结果
data = np.load('result.npz')['0']

# 创建分析对象
record = LinearAssetRecord(data)

# 重采样
resampled = record.resample('5m')

# 计算统计指标
stats = resampled.stats(book_size=1_000_000)
stats.summary()
```

### 5.2 InverseAssetRecord

用于反向合约的回测结果分析。

```python
from hftbacktest.stats import InverseAssetRecord
```

### 5.3 Stats 类

```python
stats = record.resample('5m').stats(
    book_size=1_000_000,           # 资金规模
    trading_days_per_year=365      # 加密货币 365 天
)

# 获取特定指标
sharpe = stats['SR']
sortino = stats['Sortino']
max_dd = stats['MaxDrawdown']
```

### 5.4 自定义指标

```python
from hftbacktest.stats.metrics import Metric

class CustomMetric(Metric):
    def compute(self, df, context):
        # 自定义计算逻辑
        return {'CustomMetric': value}
```

---

## 六、Recorder API

### 6.1 Recorder

记录回测过程中的状态数据。

```python
from hftbacktest import Recorder

recorder = Recorder()

# 在策略中记录
@njit
def strategy(hbt, stat):
    while hbt.elapse(1_000_000_000) == 0:
        stat.record(hbt)
```

---

## 七、数据工具 API

### 7.1 数据验证

```python
from hftbacktest.data.validation import validate

result = validate('data.npz')
```

### 7.2 数据转换

```python
# Binance Futures
from hftbacktest.data.utils.binancefutures import convert

# Bybit
from hftbacktest.data.utils.bybit import convert

# Tardis
from hftbacktest.data.utils.tardis import convert

# Databento
from hftbacktest.data.utils.databento import convert
```

---

## 八、完整示例

### 8.1 最小回测示例

```python
from numba import njit
from hftbacktest import (
    BacktestAsset,
    ROIVectorMarketDepthBacktest,
    GTX, LIMIT, BUY, SELL,
)

@njit
def strategy(hbt):
    while hbt.elapse(1_000_000_000) == 0:
        hbt.clear_inactive_orders(0)
        depth = hbt.depth(0)
        mid = (depth.best_bid + depth.best_ask) / 2.0

        # 简单做市
        hbt.submit_buy_order(0, 1, mid - 1.0, 1.0, GTX, LIMIT, False)
        hbt.submit_sell_order(0, 2, mid + 1.0, 1.0, GTX, LIMIT, False)

        if not hbt.wait_order_response(0, -1, 5_000_000_000):
            return False
    return True

asset = (
    BacktestAsset()
    .data(['data.npz'])
    .linear_asset(1.0)
    .tick_size(0.1)
    .lot_size(0.001)
)

hbt = ROIVectorMarketDepthBacktest([asset])
strategy(hbt)
hbt.close()
```

### 8.2 多资产回测

```python
asset1 = (
    BacktestAsset()
    .data(['btcusdt.npz'])
    .tick_size(0.1)
    .lot_size(0.001)
)

asset2 = (
    BacktestAsset()
    .data(['ethusdt.npz'])
    .tick_size(0.01)
    .lot_size(0.01)
)

hbt = ROIVectorMarketDepthBacktest([asset1, asset2])

@njit
def multi_asset_strategy(hbt):
    while hbt.elapse(1_000_000_000) == 0:
        # 处理资产 0 (BTC)
        depth0 = hbt.depth(0)
        # ...

        # 处理资产 1 (ETH)
        depth1 = hbt.depth(1)
        # ...
```
