# 核心 Rust 引擎模块详解

## 一、概述

`hftbacktest` crate 是整个框架的核心，用 Rust 实现了回测引擎、市场深度、实盘交易等关键功能。本文档深入解析各模块的设计与实现。

### 模块总览

```
hftbacktest/src/
├── lib.rs              # 库入口，feature flags
├── types.rs            # 核心类型定义 (~1034 行)
├── prelude.rs          # 公共 re-exports
├── utils/              # 工具模块
├── depth/              # 市场深度实现 (4 种)
├── backtest/           # 回测引擎
└── live/               # 实盘模块
```

---

## 二、types.rs — 核心类型系统

### 2.1 Event 结构 (64 字节对齐)

`Event` 是所有市场数据的统一表示，采用 C 内存布局和 64 字节缓存行对齐：

```rust
#[repr(C)]
#[derive(Clone, Copy)]
#[repr(align(64))]
pub struct Event {
    pub ev: u8,          // 事件类型标志 (位组合)
    pub exch_ts: i64,    // 交易所时间戳 (纳秒)
    pub local_ts: i64,   // 本地时间戳 (纳秒)
    pub px: f64,         // 价格
    pub qty: f64,        // 数量
    pub order_id: u64,   // 订单 ID
    pub ival: i64,       // 整数值 (用于 tick 价格等)
    pub fval: f64,       // 浮点值
}
```

**设计要点**：
- 64 字节对齐匹配 CPU 缓存行，优化内存访问
- `ev` 字段使用位标志组合，支持多达 40+ 种事件类型
- 纳秒级时间戳精度

### 2.2 事件类型常量

事件类型通过位标志组合定义：

```rust
// 位置标志
pub const LOCAL_EVENT: u8 = 0b0000_0001;   // 本地端事件
pub const EXCH_EVENT: u8  = 0b0000_0010;   // 交易所端事件

// 数据类型标志
pub const DEPTH_EVENT: u8       = 0b0000_0100;  // 深度更新
pub const TRADE_EVENT: u8       = 0b0000_1000;  // 成交事件
pub const DEPTH_CLEAR_EVENT: u8 = 0b0001_0000;  // 深度清空
pub const DEPTH_SNAPSHOT_EVENT: u8 = 0b0010_0000;  // 深度快照
pub const DEPTH_BBO_EVENT: u8   = 0b0100_0000;  // BBO 更新

// 买卖方向
pub const BUY_EVENT: u8  = 0b0000_0001;  // 买方事件
pub const SELL_EVENT: u8 = 0b0000_0010;  // 卖方事件

// 常用组合
pub const LOCAL_BID_DEPTH_EVENT: u8 = LOCAL_EVENT | DEPTH_EVENT | BUY_EVENT;
pub const LOCAL_ASK_DEPTH_EVENT: u8 = LOCAL_EVENT | DEPTH_EVENT | SELL_EVENT;
pub const EXCH_FILL_EVENT: u8 = EXCH_EVENT | FILL_EVENT;
```

### 2.3 Order 结构

```rust
pub struct Order {
    pub qty: f64,           // 订单数量
    pub leaves_qty: f64,    // 剩余数量
    pub exec_qty: f64,      // 已执行数量
    pub price_tick: i64,    // 价格 (tick 单位)
    pub order_id: u64,      // 订单 ID
    pub status: Status,     // 订单状态
    pub side: Side,         // 买卖方向
    pub time_in_force: TimeInForce,  // 有效期
    pub ord_type: OrdType,  // 订单类型
    pub q: f64,             // 队列位置信息
    pub priority: i64,      // 优先级
}
```

### 2.4 Bot trait — 统一策略接口

`Bot<MD>` trait 是回测和实盘的统一接口，策略代码通过它与引擎交互：

```rust
pub trait Bot<MD: MarketDepth> {
    // 时间控制
    fn elapse(&mut self, duration: i64) -> i64;
    fn elapse_bt(&mut self, duration: i64) -> i64;
    fn wait_next_feed(&mut self, include_order_resp: bool, timeout: i64) -> i64;
    fn wait_order_response(&mut self, asset_no: usize, order_id: u64, timeout: i64) -> i64;

    // 市场数据
    fn depth(&self, asset_no: usize) -> &MD;
    fn last_trades(&self, asset_no: usize) -> &[Event];
    fn clear_last_trades(&mut self, asset_no: usize);

    // 持仓与状态
    fn position(&self, asset_no: usize) -> f64;
    fn state_values(&self, asset_no: usize) -> &StateValues;

    // 订单管理
    fn orders(&self, asset_no: usize) -> &OrderDict;
    fn clear_inactive_orders(&mut self, asset_no: usize);

    // 订单操作
    fn submit_buy_order(&mut self, ...) -> i64;
    fn submit_sell_order(&mut self, ...) -> i64;
    fn cancel(&mut self, asset_no: usize, order_id: u64, wait: bool) -> i64;
    fn modify(&mut self, ...) -> i64;

    // 延迟信息
    fn feed_latency(&self, asset_no: usize) -> Option<(i64, i64)>;
    fn order_latency(&self, asset_no: usize) -> Option<(i64, i64, i64)>;

    // 生命周期
    fn close(&mut self) -> i64;
    fn current_timestamp(&self) -> i64;
    fn num_assets(&self) -> usize;
}
```

**关键设计**：回测引擎 `Backtest<MD>` 和实盘机器人 `LiveBot<MD>` 都实现了这个 trait，因此策略代码无需修改即可在两种环境运行。

---

## 三、depth/ — 市场深度模块

### 3.1 Trait 层次结构

```
MarketDepth (基础 trait)
├── L1MarketDepth (L1 更新)
├── L2MarketDepth (L2 更新)
│   └── ApplySnapshot (快照应用)
└── L3MarketDepth (L3 订单级别更新)
```

**MarketDepth 核心方法**：

| 方法 | 说明 |
|------|------|
| `best_bid()` / `best_ask()` | 最优买卖价 |
| `best_bid_tick()` / `best_ask_tick()` | 最优买卖价 (tick) |
| `best_bid_qty()` / `best_ask_qty()` | 最优买卖量 |
| `tick_size()` / `lot_size()` | 最小价格/数量变动 |
| `qty_at_tick(price_tick)` | 指定价位的数量 |
| `timestamp()` | 当前时间戳 |

### 3.2 HashMapMarketDepth

基于 `HashMap<i64, f64>` 实现，使用价格 tick 作为 key：

```rust
pub struct HashMapMarketDepth {
    pub tick_size: f64,
    pub lot_size: f64,
    pub timestamp: i64,
    pub bid_depth: HashMap<i64, f64>,
    pub ask_depth: HashMap<i64, f64>,
    pub best_bid_tick: i64,
    pub best_ask_tick: i64,
}
```

**特点**：
- 无价格范围限制
- O(1) 平均访问时间
- 适合价格波动剧烈的品种
- 内存开销相对较高

### 3.3 ROIVectorMarketDepth

基于连续数组实现，只维护 ROI (Range of Interest) 范围内的数据：

```rust
pub struct ROIVectorMarketDepth {
    pub tick_size: f64,
    pub lot_size: f64,
    pub timestamp: i64,
    pub bid_depth: Vec<f64>,      // 连续数组
    pub ask_depth: Vec<f64>,      // 连续数组
    pub best_bid_tick: i64,
    pub best_ask_tick: i64,
    pub roi_lb: i64,              // ROI 下界 (tick)
    pub roi_ub: i64,              // ROI 上界 (tick)
}
```

**索引计算**：
```
index = price_tick - roi_lb_tick
price = (index + roi_lb_tick) * tick_size
array_length = roi_ub_tick - roi_lb_tick + 1
```

**特点**：
- 缓存友好的连续内存访问
- O(1) 严格访问时间
- 内存使用可控
- 适合价格范围可预测的品种

### 3.4 BTreeMarketDepth

基于 `BTreeMap<i64, f64>` 实现：

**特点**：
- 有序遍历支持
- 适合需要按价格顺序遍历的场景
- 性能介于 HashMap 和 ROIVector 之间

### 3.5 FusedHashMapMarketDepth

融合多个数据流的深度实现，主要用于实盘：

```rust
pub struct FusedHashMapMarketDepth {
    pub depth: HashMapMarketDepth,
    // 支持合并 L1 + L2 数据流
}
```

---

## 四、backtest/ — 回测引擎模块

### 4.1 整体架构

```
Backtest<MD>
├── EventSet (事件调度器)
│   └── 管理所有资产的 4 类事件时间戳
├── Asset[] (资产数组)
│   ├── Local (本地处理器)
│   │   ├── MarketDepth (订单簿)
│   │   ├── State (持仓/余额)
│   │   └── OrderBus (订单通信)
│   ├── Exchange (交易所处理器)
│   │   ├── MarketDepth (订单簿)
│   │   ├── State (持仓/余额)
│   │   ├── QueueModel (排队模型)
│   │   └── OrderBus (订单通信)
│   └── Reader (数据读取器)
│       └── DataSource[] (数据源)
└── Recorder (记录器)
```

### 4.2 EventSet — 事件调度器

`EventSet` 管理所有资产的事件时间戳，决定下一步处理哪个事件：

```rust
pub struct EventSet {
    // 每个资产有 4 类事件
    // LocalData: 本地端接收的市场数据
    // LocalOrder: 本地端的订单响应
    // ExchData: 交易所端的市场数据
    // ExchOrder: 交易所端的订单请求
    timestamps: Vec<[i64; 4]>,
}
```

**事件处理优先级**：按时间戳顺序处理，同时间戳时按优先级处理。

### 4.3 Local — 本地处理器

模拟策略运行的环境：

```rust
pub struct Local<MD: MarketDepth> {
    depth: MD,                    // 市场深度
    state: State,                 // 交易状态
    orders: OrderDict,            // 订单字典
    last_trades: Vec<Event>,      // 最近成交
    order_tx: OrderBus,           // 订单发送总线
    order_rx: OrderBus,           // 订单接收总线
    data_rx: DataReader,          // 数据接收
}
```

**职责**：
- 接收市场数据更新，维护订单簿
- 接收策略的订单请求，发送到 Exchange
- 接收 Exchange 的订单响应，更新订单状态
- 提供策略查询接口 (position, orders, depth)

### 4.4 Exchange — 交易所处理器

模拟交易所撮合引擎：

```rust
pub struct NoPartialFillExchange<MD: MarketDepth> {
    depth: MD,                    // 市场深度
    state: State,                 // 交易状态
    queue_model: Box<dyn QueueModel>,  // 排队模型
    order_rx: OrderBus,           // 订单接收
    order_tx: OrderBus,           // 订单响应发送
}
```

**职责**：
- 接收订单请求
- 根据市场数据和排队模型计算成交
- 更新订单状态
- 发送订单响应

### 4.5 Reader — 数据读取器

```rust
pub struct Reader {
    data: Vec<Data<Event>>,       // 数据源列表
    current: usize,               // 当前数据源索引
    cache: Cache,                 // 数据缓存
    preprocessor: Option<FeedLatencyAdjustment>,  // 预处理器
}
```

**数据加载流程**：
1. 从 NPZ 文件读取数据
2. 应用预处理器 (如延迟调整)
3. 按时间戳排序
4. 供 EventSet 调度

### 4.6 models/ — 模型系统

#### 延迟模型 (LatencyModel)

```rust
pub trait LatencyModel {
    fn entry_latency(&self, current_ts: i64) -> i64;
    fn resp_latency(&self, current_ts: i64) -> i64;
}
```

- `ConstantLatency`: 固定延迟
- `IntpOrderLatency`: 基于历史数据的插值延迟

#### 排队模型 (QueueModel)

```rust
pub trait QueueModel {
    fn fill_probability(&self, queue_position: f64, queue_size: f64, traded_qty: f64) -> f64;
}
```

- `ProbQueueModel`: 概率排队模型
- `RiskAdverseQueueModel`: 风险厌恶模型
- `L3FIFOQueueModel`: L3 FIFO 模型

#### 手续费模型 (FeeModel)

```rust
pub trait FeeModel {
    fn calculate(&self, price: f64, qty: f64, is_maker: bool) -> f64;
}
```

- `TradingValueFeeModel`: 按交易金额
- `TradingQtyFeeModel`: 按交易数量
- `FlatPerTradeFeeModel`: 固定费用

---

## 五、live/ — 实盘模块

### 5.1 LiveBot

```rust
pub struct LiveBot<MD: MarketDepth> {
    instruments: Vec<Instrument<MD>>,
    ipc: IpcChannel,
}
```

### 5.2 IPC 通信

基于 iceoryx2 实现零拷贝共享内存通信：

```
┌──────────┐    Shared Memory    ┌─────────────┐
│  LiveBot │ ◄─────────────────► │  Connector  │
│ (策略)   │   (iceoryx2 IPC)    │ (交易所连接) │
└──────────┘                     └─────────────┘
```

**通信类型**：
- `LiveEvent`: 交易所 → Bot (市场数据、订单响应)
- `LiveRequest`: Bot → 交易所 (订单请求、注册合约)

---

## 六、Feature Flags

| Feature | 说明 | 依赖 |
|---------|------|------|
| `backtest` | 回测功能 | zip, uuid, nom, hftbacktest-derive |
| `live` | 实盘功能 | chrono, tokio, iceoryx2, rand, toml, serde |
| `s3` | S3 数据源 | aws-config, aws-sdk-s3 |

**默认启用**: `backtest`, `live`

---

## 七、性能优化要点

### 7.1 内存布局
- Event 结构 64 字节缓存行对齐
- ROIVector 使用连续数组，缓存友好
- 预分配避免动态内存分配

### 7.2 算法优化
- 事件调度使用优先队列
- 订单簿更新 O(1) 复杂度
- 批量操作减少函数调用开销

### 7.3 并行化
- 数据加载支持并行读取
- 多资产独立处理
- 零拷贝 IPC 通信
