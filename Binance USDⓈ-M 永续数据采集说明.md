# Binance USDⓈ-M 永续数据采集说明（以 ETHUSDC 为例）

本文档说明如何在本项目中采集一天的币安 USDⓈ-M 永续行情数据，并转换为回测可用的 NPZ。

## 适用范围

- 交易所：Binance USDⓈ-M Futures（永续）
- 采集程序：`collector`（Rust）
- 示例交易对：`ETHUSDC`

## 1. 构建采集器

在仓库根目录执行：

```bash
cargo build -p collector --release
```

可用 `./target/release/collector --help` 查看参数说明。

## 2. 启动采集

采集会实时订阅 `trade / bookTicker / depth@0ms` 三类流，并写入本地文件。

```bash
./target/release/collector ./data binancefuturesum ETHUSDC
```

参数含义：

- `./data`：输出目录（不存在请先创建）
- `binancefuturesum`：Binance USDⓈ-M Futures
- `ETHUSDC`：交易对（永续合约代码）

## 3. 运行满 24 小时并停止

采集文件按 UTC 日期自动滚动；如需完整一天，建议从 UTC 00:00 启动，运行到次日 00:00 后手动停止（`Ctrl+C`）。

输出文件示例：

```
./data/ethusdc_YYYYMMDD.gz
```

## 4. 转换为 NPZ（用于回测）

采集得到的是原始 WebSocket 流，需要用 Python 工具转换为 NPZ：

```python
from hftbacktest.data.utils import binancefutures

binancefutures.convert(
    input_filename="./data/ethusdc_YYYYMMDD.gz",
    output_filename="./data/ethusdc_YYYYMMDD.npz",
)
```

### 可选参数提示

- `opt`：控制是否额外解析 `markPrice` / `bookTicker` 等事件
- `buffer_size`：若遇到 IndexError（数组越界）可调大

## 5. 注意事项

- 数据量大，确保磁盘空间和带宽充足
- 日期切分以 UTC 为准
- 如果交易对不存在，会导致订阅失败；可通过 Binance `exchangeInfo` 查询合约列表

## 相关源码位置

- 采集入口：`collector/src/main.rs`
- Binance USDⓈ-M 实现：`collector/src/binancefuturesum/`
- 原始数据写入规则：`collector/src/file.rs`
- Python 转换工具：`py-hftbacktest/hftbacktest/data/utils/binancefutures.py`
