# BacktestAsset 详解

## 一、概述

`BacktestAsset` 是 hftbacktest 框架中用于**配置回测资产**的核心类。它采用**建造者模式(Builder Pattern)**，通过链式调用方法来配置回测参数，最终构建成一个完整的回测资产对象。

---

## 二、类定义结构

### 2.1 Rust 核心定义

```rust
#[pyclass(subclass)]
pub struct BacktestAsset {
    data: Vec<DataSource<Event>>,           // 市场数据源
    asset_type: AssetType,                   // 资产类型
    latency_model: LatencyModel,             // 延迟模型
    queue_model: QueueModel,                 // 排队模型
    exch_kind: ExchangeKind,                 // 交易所模型
    tick_size: f64,                          // 最小价格变动
    lot_size: f64,                           // 最小交易数量
    last_trades_cap: usize,                  // 最近成交记录容量
    roi_lb: f64,                             // ROI下界
    roi_ub: f64,                             // ROI上界
    initial_snapshot: Option<DataSource<Event>>, // 初始快照
    fee_model: FeeModel,                     // 手续费模型
    latency_offset: i64,                     // 延迟偏移
    parallel_load: bool,                     // 并行加载
}
```

### 2.2 默认值

| 字段 | 默认值 |
|------|--------|
| `latency_model` | ConstantLatency { entry: 0, resp: 0 } |
| `asset_type` | LinearAsset { contract_size: 1.0 } |
| `queue_model` | LogProbQueueModel2 |
| `exch_kind` | NoPartialFillExchange |
| `tick_size` | 0.0 |
| `lot_size` | 0.0 |
| `last_trades_cap` | 0 |
| `roi_lb` | 0.0 |
| `roi_ub` | 0.0 |
| `initial_snapshot` | None |
| `fee_model` | TradingValueFeeModel { maker: 0, taker: 0 } |
| `latency_offset` | 0 |
| `parallel_load` | true |

---

## 三、内部组成

`BacktestAsset` 最终会构建成一个 `Asset` 对象，包含三个核心组件：

```
┌─────────────────────────────────────────────────────────────┐
│                         Asset                                │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Local     │  │   Exchange  │  │      Reader         │  │
│  │  Processor  │  │  Processor  │  │   (数据读取器)       │  │
│  │ (本地处理器) │  │ (交易所处理器)│  │                     │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│        │                │                    │               │
│        ▼                ▼                    ▼               │
│   模拟本地端       模拟交易所端          读取市场数据         │
│   (策略侧)         (撮合引擎侧)                               │
└─────────────────────────────────────────────────────────────┘
```

### 3.1 Local Processor (本地处理器)

模拟策略运行的环境：
- 接收市场数据更新
- 管理订单状态
- 提供策略调用接口
- 维护持仓和余额状态

### 3.2 Exchange Processor (交易所处理器)

模拟交易所撮合引擎：
- 接收订单请求
- 根据排队模型计算成交概率
- 模拟订单撮合
- 返回订单响应

### 3.3 Reader (数据读取器)

负责加载和管理市场数据：
- 支持多个数据文件
- 支持并行加载
- 管理数据生命周期

---

## 四、方法详解

### 4.1 数据加载方法

#### `.data(data)`

设置市场数据源。

**参数：**
- `data`: 数据源，可以是以下类型：
  - `str`: 单个文件路径
  - `List[str]`: 多个文件路径列表
  - `np.ndarray`: NumPy数组
  - `List[np.ndarray]`: NumPy数组列表

**示例：**
```python
# 单个文件
.data('usdm/ethusdc_20260302.npz')

# 多个文件
.data(['usdm/ethusdc_20260302.npz', 'usdm/ethusdc_20260303.npz'])

# NumPy数组
.data(event_array)
```

#### `.initial_snapshot(data)`

设置初始订单簿快照。

**参数：**
- `data`: 快照数据，可以是文件路径或NumPy数组

**示例：**
```python
.initial_snapshot('usdm/ethusdc_20260301_eod.npz')
```

---

### 4.2 资产类型方法

#### `.linear_asset(contract_size)`

设置为线性资产（如USDT本位合约）。

**参数：**
- `contract_size`: 合约乘数

**盈亏计算：**
$$PnL = (P_{sell} - P_{buy}) \times Q \times contract\_size$$

**示例：**
```python
.linear_asset(1.0)  # ETHUSDT合约，乘数为1
```

#### `.inverse_asset(contract_size)`

设置为反向资产（如币本位合约）。

**参数：**
- `contract_size`: 合约乘数

**盈亏计算：**
$$PnL = Q \times contract\_size \times (\frac{1}{P_{buy}} - \frac{1}{P_{sell}})$$

**示例：**
```python
.inverse_asset(100)  # BTCUSD反向合约，乘数为100
```

---

### 4.3 延迟模型方法

#### `.constant_order_latency(entry_latency, resp_latency)`

使用固定延迟模型。

**参数：**
- `entry_latency`: 订单提交延迟（纳秒）
- `resp_latency`: 订单响应延迟（纳秒）

**示例：**
```python
.constant_order_latency(100_000, 100_000)  # 100μs双向延迟
```

#### `.intp_order_latency(data, latency_offset=0)`

使用插值延迟模型，基于历史延迟数据模拟真实延迟。

**参数：**
- `data`: 历史延迟数据，可以是文件路径或NumPy数组
- `latency_offset`: 延迟偏移量（纳秒），用于跨交易所回测

**延迟数据格式：**
```python
# 延迟数据结构
dtype = [
    ('req_ts', 'i8'),    # 请求时间戳
    ('exch_ts', 'i8'),   # 交易所接收时间戳
    ('resp_ts', 'i8'),   # 响应时间戳
]
```

**示例：**
```python
.intp_order_latency(latency_data, latency_offset=0)
```

**延迟模型工作原理：**
```
策略时间线:
├── t0: 策略提交订单
│      │
│      ├── entry_latency ──► t1: 订单到达交易所
│      │
│      ├── 撮合处理
│      │
│      ├── resp_latency ──► t2: 响应返回策略
│
└── 策略收到确认
```

---

### 4.4 排队模型方法

排队模型用于模拟订单在价格队列中的成交概率。

#### `.risk_adverse_queue_model()`

风险厌恶排队模型，保守估计成交概率。

#### `.log_prob_queue_model()`

对数概率排队模型。

#### `.log_prob_queue_model2()`

对数概率排队模型v2（默认）。

#### `.power_prob_queue_model(n)`

幂次概率排队模型。

**参数：**
- `n`: 幂次参数，通常为2-5

#### `.power_prob_queue_model2(n)`

幂次概率排队模型v2。

#### `.power_prob_queue_model3(n)`

幂次概率排队模型v3。

#### `.l3_fifo_queue_model()`

L3级别FIFO排队模型，需要L3数据支持。

**排队模型原理：**
```
订单队列示意:
价格档位: 100.0
├── 订单1 (你的订单，位置=1)
├── 订单2 (位置=2)  
├── 订单3 (位置=3)
└── ...

成交概率计算:
P(fill) = f(queue_position, queue_size, traded_qty)

幂次模型: P = 1 - (position/size)^n
```

**示例：**
```python
.power_prob_queue_model(3)  # 使用幂次为3的概率模型
```

---

### 4.5 交易所模型方法

#### `.no_partial_fill_exchange()`

不允许部分成交，订单要么全部成交，要么不成交。

#### `.partial_fill_exchange()`

允许部分成交，大订单可能分多次成交。

**示例：**
```python
.no_partial_fill_exchange()  # 做市策略常用
```

---

### 4.6 手续费模型方法

#### `.trading_value_fee_model(maker_fee, taker_fee)`

按交易金额计算手续费。

**参数：**
- `maker_fee`: Maker费率（负值表示返点）
- `taker_fee`: Taker费率

**费用计算：**
$$Fee = Price \times Qty \times fee\_rate$$

**示例：**
```python
.trading_value_fee_model(-0.00005, 0.0007)
# Maker返点0.005%，Taker费率0.07%
```

#### `.trading_qty_fee_model(maker_fee, taker_fee)`

按交易数量计算手续费。

**费用计算：**
$$Fee = Qty \times fee\_rate$$

#### `.flat_per_trade_fee_model(maker_fee, taker_fee)`

每笔交易固定费用。

**费用计算：**
$$Fee = fee\_rate \text{ (per trade)}$$

---

### 4.7 其他配置方法

#### `.tick_size(size)`

设置最小价格变动单位。

```python
.tick_size(0.1)  # 价格最小变动0.1
```

#### `.lot_size(size)`

设置最小交易数量单位。

```python
.lot_size(0.001)  # 数量最小变动0.001
```

#### `.roi_lb(price)` / `.roi_ub(price)`

设置ROI (Region of Interest) 价格范围，用于优化内存使用。

```python
.roi_lb(0)      # 价格下界
.roi_ub(3000)   # 价格上界
```

**原理：** 只在指定价格范围内维护订单簿深度数据，超出范围的数据被忽略。

#### `.latency_offset(ns)`

设置延迟偏移，用于跨交易所回测。

```python
.latency_offset(10_000_000)  # 偏移10ms
```

#### `.last_trades_capacity(n)`

设置最近成交记录的容量。

```python
.last_trades_capacity(100)  # 保存最近100笔成交
```

#### `.parallel_load(bool)`

设置是否并行加载数据。

```python
.parallel_load(True)  # 并行加载（默认）
```

---

## 五、构建流程

当调用 `ROIVectorMarketDepthBacktest([asset])` 时，内部执行以下流程：

```rust
// 简化的构建流程
fn build(asset: BacktestAsset) -> Asset {
    // 1. 创建数据读取器
    let reader = Reader::builder()
        .parallel_load(asset.parallel_load)
        .data(asset.data)
        .preprocessor(FeedLatencyAdjustment::new(asset.latency_offset))
        .build();
    
    // 2. 创建订单总线（连接Local和Exchange）
    let (order_e2l, order_l2e) = order_bus(asset.latency_model);
    
    // 3. 创建本地处理器（模拟策略端）
    let local = Local::new(
        create_depth(),
        State::new(asset.asset_type, asset.fee_model),
        asset.last_trades_cap,
        order_l2e,  // 接收交易所响应
    );
    
    // 4. 创建交易所处理器（模拟撮合引擎）
    let exch = NoPartialFillExchange::new(
        create_depth(),
        State::new(asset.asset_type, asset.fee_model),
        asset.queue_model,
        order_e2l,  // 接收订单请求
    );
    
    Asset { local, exch, reader }
}
```

---

## 六、架构图

```
┌────────────────────────────────────────────────────────────────────┐
│                          BacktestAsset                              │
│                     (配置收集阶段 - Python)                          │
└────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼ build()
┌────────────────────────────────────────────────────────────────────┐
│                             Asset                                   │
│                      (运行时 - Rust)                                 │
├────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                    Reader (数据读取器)                         │  │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐              │  │
│  │  │  Data 1    │  │  Data 2    │  │  Data 3    │  ...         │  │
│  │  │ (npz文件)  │  │ (npz文件)  │  │ (npz文件)  │              │  │
│  │  └────────────┘  └────────────┘  └────────────┘              │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                         │                                          │
│                         ▼ 市场数据事件                              │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              Local Processor (本地处理器)                      │  │
│  │  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐  │  │
│  │  │ MarketDepth    │  │ State          │  │ OrderBus       │  │  │
│  │  │ (订单簿深度)    │  │ (持仓/余额)     │  │ (订单通信)      │  │  │
│  │  └────────────────┘  └────────────────┘  └────────────────┘  │  │
│  │                                                              │  │
│  │  功能: 接收市场数据、管理订单状态、提供策略接口                  │  │
│  └──────────────────────────────────────────────────────────────┘  │
│          │                                    ▲                     │
│          │ 订单请求                            │ 订单响应            │
│          ▼                                    │                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │             Exchange Processor (交易所处理器)                  │  │
│  │  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐  │  │
│  │  │ MarketDepth    │  │ QueueModel     │  │ OrderBus       │  │  │
│  │  │ (订单簿深度)    │  │ (排队模型)      │  │ (订单通信)      │  │  │
│  │  └────────────────┘  └────────────────┘  └────────────────┘  │  │
│  │                                                              │  │
│  │  功能: 模拟撮合、计算成交概率、更新订单状态                      │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
└────────────────────────────────────────────────────────────────────┘
```

---

## 七、完整示例

### 7.1 基础配置

```python
from hftbacktest import BacktestAsset, ROIVectorMarketDepthBacktest

asset = (
    BacktestAsset()
    .data(['usdm/ethusdc_20260302.npz'])
    .initial_snapshot('usdm/ethusdc_20260301_eod.npz')
    .linear_asset(1.0)
    .intp_order_latency(latency_data)
    .power_prob_queue_model(3)
    .no_partial_fill_exchange()
    .trading_value_fee_model(-0.00005, 0.0007)
    .tick_size(0.1)
    .lot_size(0.001)
    .roi_lb(0)
    .roi_ub(3000)
)

hbt = ROIVectorMarketDepthBacktest([asset])
```

### 7.2 多资产配置

```python
asset1 = (
    BacktestAsset()
    .data(['usdm/btcusdt_20260302.npz'])
    .linear_asset(1.0)
    .tick_size(0.1)
    .lot_size(0.001)
    # ... 其他配置
)

asset2 = (
    BacktestAsset()
    .data(['usdm/ethusdt_20260302.npz'])
    .linear_asset(1.0)
    .tick_size(0.01)
    .lot_size(0.01)
    # ... 其他配置
)

hbt = ROIVectorMarketDepthBacktest([asset1, asset2])
```

### 7.3 反向合约配置

```python
asset = (
    BacktestAsset()
    .data(['btcusd_perp.npz'])
    .inverse_asset(100)  # BTCUSD反向合约，乘数100
    .tick_size(0.5)
    .lot_size(1)
    # ... 其他配置
)
```

---

## 八、关键设计理念

### 8.1 双处理器架构

Local 和 Exchange 分离，模拟真实交易环境中的网络延迟和异步处理：

```
真实交易环境:
┌──────────┐    网络    ┌──────────┐
│  策略端   │ ◄──────► │  交易所   │
└──────────┘           └──────────┘

回测模拟:
┌──────────┐   OrderBus   ┌──────────┐
│  Local   │ ◄─────────► │ Exchange │
└──────────┘              └──────────┘
```

### 8.2 模块化设计

延迟、排队、手续费等模型可独立配置和替换：

```python
# 不同的延迟模型
.constant_order_latency(100_000, 100_000)
.intp_order_latency(latency_data)

# 不同的排队模型
.risk_adverse_queue_model()
.power_prob_queue_model(3)
.l3_fifo_queue_model()

# 不同的手续费模型
.trading_value_fee_model(-0.00005, 0.0007)
.trading_qty_fee_model(0.01, 0.01)
```

### 8.3 高性能

- 核心逻辑用 Rust 实现
- Python 层仅做配置
- 支持 JIT 编译 (Numba)
- 并行数据加载

### 8.4 内存优化

ROI (Region of Interest) 机制限制价格范围：

```python
# 只维护 0-3000 价格范围内的订单簿
.roi_lb(0)
.roi_ub(3000)
```

---

## 九、常见问题

### Q1: 如何选择排队模型？

| 模型 | 特点 | 适用场景 |
|------|------|----------|
| `risk_adverse_queue_model` | 保守估计 | 需要严格风控 |
| `log_prob_queue_model2` | 平衡 | 通用场景（默认） |
| `power_prob_queue_model(3)` | 可调参数 | 需要精确模拟 |
| `l3_fifo_queue_model` | 精确FIFO | 有L3数据 |

### Q2: 何时使用 `latency_offset`？

跨交易所回测时，如果数据采集地点与策略运行地点不同：

```python
# 数据从美国采集，策略在亚洲运行
.latency_offset(50_000_000)  # 增加50ms延迟
```

### Q3: Maker返点如何设置？

负值表示返点：

```python
.trading_value_fee_model(-0.00005, 0.0007)
# Maker: -0.005% (返点)
# Taker: +0.07% (费用)
```

---

## 十、参考资料

- [hftbacktest 官方文档](https://hftbacktest.readthedocs.io/)
- [Rust API 文档](https://docs.rs/hftbacktest/)
- [订单成交模型详解](https://hftbacktest.readthedocs.io/en/latest/order_fill.html)
