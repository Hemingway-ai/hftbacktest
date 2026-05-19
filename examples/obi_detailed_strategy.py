"""
OBI Market Making Strategy - Detailed Data Collection Version

Extended version of the Order Book Imbalance market making strategy that records
per-second snapshots, individual fill events, and second-level K-line data.
Exports all data to a JSON file for the web-based analysis tool.

Usage:
    python obi_detailed_strategy.py

Output:
    obi_detailed_output.json - Load this file in backtest_analysis.html
"""

import json
import datetime
import numpy as np
from numba import njit, uint64
from numba.typed import Dict

from hftbacktest import (
    BacktestAsset,
    ROIVectorMarketDepthBacktest,
    GTX,
    LIMIT,
    BUY,
    SELL,
    BUY_EVENT,
    SELL_EVENT,
    Recorder,
    FILLED,
)

# ============================================================================
# Custom dtypes for detailed data collection
# ============================================================================

snapshot_dtype = np.dtype([
    ('timestamp',    'i8'),   # nanosecond timestamp
    ('mid_price',    'f8'),   # (best_bid + best_ask) / 2
    ('best_bid',     'f8'),
    ('best_ask',     'f8'),
    ('spread',       'f8'),   # best_ask - best_bid
    ('position',     'f8'),
    ('balance',      'f8'),
    ('fee',          'f8'),   # cumulative fee
    ('alpha',        'f8'),   # standardized imbalance
    ('imbalance',    'f8'),   # raw imbalance (sum_bid - sum_ask)
    ('bid_price',    'f8'),   # strategy bid quote price
    ('ask_price',    'f8'),   # strategy ask quote price
], align=True)

fill_dtype = np.dtype([
    ('timestamp',       'i8'),   # loop timestamp when fill detected
    ('exch_timestamp',  'i8'),   # exchange-side timestamp
    ('side',            'i1'),   # BUY=1, SELL=-1
    ('exec_price',      'f8'),
    ('exec_qty',        'f8'),
    ('order_price',     'f8'),   # original submitted price
    ('mid_at_fill',     'f8'),
    ('spread_at_fill',  'f8'),
    ('alpha_at_fill',   'f8'),
    ('position_after',  'f8'),
    ('balance_after',   'f8'),
], align=True)

kline_dtype = np.dtype([
    ('timestamp', 'i8'),   # 1-second bar start (nanoseconds)
    ('open',      'f8'),
    ('high',      'f8'),
    ('low',       'f8'),
    ('close',     'f8'),
    ('volume',    'f8'),
    ('buy_vol',   'f8'),
    ('sell_vol',  'f8'),
], align=True)


# ============================================================================
# Core @njit strategy function with detailed data recording
# ============================================================================

@njit
def obi_mm_detailed(
    hbt,
    stat,
    snapshots,
    fills,
    half_spread,
    skew,
    c1,
    looking_depth,
    interval,
    window,
    order_qty_dollar,
    max_position_dollar,
    grid_num,
    grid_interval,
    roi_lb,
    roi_ub,
):
    """
    OBI market making strategy with detailed data collection.

    Returns:
        (snap_count, fill_count) - number of records written
    """
    asset_no = 0
    imbalance_timeseries = np.full(30_000_000, np.nan, np.float64)

    tick_size = hbt.depth(0).tick_size
    lot_size = hbt.depth(0).lot_size

    t = 0
    snap_idx = 0
    fill_idx = 0
    prev_alpha = 0.0

    roi_lb_tick = int(round(roi_lb / tick_size))
    roi_ub_tick = int(round(roi_ub / tick_size))

    snap_len = len(snapshots)
    fill_len = len(fills)

    while hbt.elapse(interval) == 0:
        depth = hbt.depth(asset_no)
        position = hbt.position(asset_no)
        orders = hbt.orders(asset_no)

        best_bid = depth.best_bid
        best_ask = depth.best_ask
        mid_price = (best_bid + best_ask) / 2.0
        spread = best_ask - best_bid

        # === PHASE 1: Detect fills BEFORE clearing inactive orders ===
        order_values = orders.values()
        while order_values.has_next():
            order = order_values.get()
            if order.status == FILLED:
                if fill_idx < fill_len:
                    fills[fill_idx].timestamp = hbt.current_timestamp
                    fills[fill_idx].exch_timestamp = order.exch_timestamp
                    fills[fill_idx].side = order.side
                    fills[fill_idx].exec_price = order.exec_price
                    fills[fill_idx].exec_qty = order.exec_qty
                    fills[fill_idx].order_price = order.price
                    fills[fill_idx].mid_at_fill = mid_price
                    fills[fill_idx].spread_at_fill = spread
                    fills[fill_idx].alpha_at_fill = prev_alpha
                    fills[fill_idx].position_after = position
                    fills[fill_idx].balance_after = hbt.state_values(asset_no).balance
                    fill_idx += 1

        hbt.clear_inactive_orders(asset_no)

        # === PHASE 2: Compute OBI alpha (same logic as original obi_mm) ===
        sum_ask_qty = 0.0
        from_tick = max(depth.best_ask_tick, roi_lb_tick)
        upto_tick = min(int(np.floor(mid_price * (1 + looking_depth) / tick_size)), roi_ub_tick)
        for price_tick in range(from_tick, upto_tick):
            sum_ask_qty += depth.ask_depth[price_tick - roi_lb_tick]

        sum_bid_qty = 0.0
        from_tick = min(depth.best_bid_tick, roi_ub_tick)
        upto_tick = max(int(np.ceil(mid_price * (1 - looking_depth) / tick_size)), roi_lb_tick)
        for price_tick in range(from_tick, upto_tick, -1):
            sum_bid_qty += depth.bid_depth[price_tick - roi_lb_tick]

        imbalance_timeseries[t] = sum_bid_qty - sum_ask_qty

        m = np.nanmean(imbalance_timeseries[max(0, t + 1 - window):t + 1])
        s = np.nanstd(imbalance_timeseries[max(0, t + 1 - window):t + 1])
        alpha = np.divide(imbalance_timeseries[t] - m, s)
        prev_alpha = alpha

        # === PHASE 3: Compute strategy quotes ===
        order_qty = max(round((order_qty_dollar / mid_price) / lot_size) * lot_size, lot_size)
        fair_price = mid_price + c1 * alpha
        normalized_position = position / order_qty
        reservation_price = fair_price - skew * normalized_position

        bid_price = min(np.round(reservation_price - half_spread), best_bid)
        ask_price = max(np.round(reservation_price + half_spread), best_ask)

        bid_price = np.floor(bid_price / tick_size) * tick_size
        ask_price = np.ceil(ask_price / tick_size) * tick_size

        # === PHASE 4: Record snapshot ===
        if snap_idx < snap_len:
            snapshots[snap_idx].timestamp = hbt.current_timestamp
            snapshots[snap_idx].mid_price = mid_price
            snapshots[snap_idx].best_bid = best_bid
            snapshots[snap_idx].best_ask = best_ask
            snapshots[snap_idx].spread = spread
            snapshots[snap_idx].position = position
            snapshots[snap_idx].balance = hbt.state_values(asset_no).balance
            snapshots[snap_idx].fee = hbt.state_values(asset_no).fee
            snapshots[snap_idx].alpha = alpha
            snapshots[snap_idx].imbalance = imbalance_timeseries[t]
            snapshots[snap_idx].bid_price = bid_price
            snapshots[snap_idx].ask_price = ask_price
            snap_idx += 1

        # === PHASE 5: Strategy order placement (same as original) ===
        new_bid_orders = Dict.empty(np.uint64, np.float64)
        if position * mid_price < max_position_dollar and np.isfinite(bid_price):
            for i in range(grid_num):
                bid_price_tick = round(bid_price / tick_size)
                new_bid_orders[uint64(bid_price_tick)] = bid_price
                bid_price -= grid_interval

        new_ask_orders = Dict.empty(np.uint64, np.float64)
        if position * mid_price > -max_position_dollar and np.isfinite(ask_price):
            for i in range(grid_num):
                ask_price_tick = round(ask_price / tick_size)
                new_ask_orders[uint64(ask_price_tick)] = ask_price
                ask_price += grid_interval

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

        t += 1

        if t >= len(imbalance_timeseries):
            break

        stat.record(hbt)

    return snap_idx, fill_idx


# ============================================================================
# Python wrapper and JSON export
# ============================================================================

def run_detailed_backtest(hbt, recorder, params, max_snapshots=86_400 * 5, max_fills=500_000):
    """Run the detailed OBI strategy and return collected data arrays."""
    snapshots = np.zeros(max_snapshots, dtype=snapshot_dtype)
    fills = np.zeros(max_fills, dtype=fill_dtype)

    snap_count, fill_count = obi_mm_detailed(
        hbt,
        recorder.recorder,
        snapshots,
        fills,
        params['half_spread'],
        params['skew'],
        params['c1'],
        params['looking_depth'],
        params['interval'],
        params['window'],
        params['order_qty_dollar'],
        params['max_position_dollar'],
        params['grid_num'],
        params['grid_interval'],
        params['roi_lb'],
        params['roi_ub'],
    )

    snapshots = snapshots[:snap_count]
    fills = fills[:fill_count]

    print(f"Collected: {snap_count} snapshots, {fill_count} fills")
    return snapshots, fills


def build_klines_from_data(data_files):
    """Build 1-second K-lines directly from raw market data files (Python-side).

    This avoids the njit last_trades limitation and captures all market trades.
    """
    from hftbacktest import TRADE_EVENT, BUY_EVENT, SELL_EVENT

    all_bars = []  # list of (bar_ts_ns, open, high, low, close, volume, buy_vol, sell_vol)
    current_bar = None

    for fpath in data_files:
        data = np.load(fpath)['data']
        for row in data:
            ev = int(row['ev'])
            # Only process trade events (events with non-zero quantity are trades)
            if row['qty'] <= 0:
                continue
            is_buy = (ev & BUY_EVENT) != 0
            is_sell = (ev & SELL_EVENT) != 0
            if not (is_buy or is_sell):
                continue

            ts = int(row['exch_ts'])
            px = float(row['px'])
            qty = float(row['qty'])
            bar_ts = (ts // 1_000_000_000) * 1_000_000_000

            if current_bar is not None and current_bar[0] == bar_ts:
                _, o, h, l, c, v, bv, sv = current_bar
                current_bar = (bar_ts, o, max(h, px), min(l, px), px, v + qty,
                               bv + qty if is_buy else bv,
                               sv + qty if is_sell else sv)
            else:
                if current_bar is not None:
                    all_bars.append(current_bar)
                current_bar = (bar_ts, px, px, px, px, qty,
                               qty if is_buy else 0.0,
                               qty if is_sell else 0.0)

        if current_bar is not None:
            all_bars.append(current_bar)

    klines = np.zeros(len(all_bars), dtype=kline_dtype)
    for i, (ts, o, h, l, c, v, bv, sv) in enumerate(all_bars):
        klines[i] = (ts, o, h, l, c, v, bv, sv)

    print(f"Built {len(klines)} K-lines from raw market data")
    return klines


def compute_fill_pnl(fills):
    """Compute per-fill PnL from balance changes."""
    pnl_data = []
    cumulative_pnl = 0.0
    for i in range(len(fills)):
        if i == 0:
            pnl = 0.0
        else:
            pnl = float(fills[i]['balance_after'] - fills[i - 1]['balance_after'])
        cumulative_pnl += pnl
        pnl_data.append((round(pnl, 4), round(cumulative_pnl, 4)))
    return pnl_data


def _safe_val(v):
    """Convert NaN/Inf to None for valid JSON."""
    if np.isnan(v) or np.isinf(v):
        return None
    return v


def export_to_json(snapshots, fills, klines, recorder_data, params, output_path, asset_name='ETHUSDC'):
    """Export all collected data to a JSON file for the web visualization tool."""
    pnl_data = compute_fill_pnl(fills)

    # Subsample recorder data (every ~60 seconds for display)
    rec_step = max(1, len(recorder_data) // 1440)
    rec_subsampled = recorder_data[::rec_step]
    rec_export = []
    for r in rec_subsampled:
        rec_export.append({
            "ts": int(r['timestamp']),
            "px": _safe_val(round(float(r['price']), 4)),
            "pos": _safe_val(round(float(r['position']), 6)),
            "bal": _safe_val(round(float(r['balance']), 4)),
            "fee": _safe_val(round(float(r['fee']), 4)),
            "trades": int(r['num_trades']),
            "vol": _safe_val(round(float(r['trading_volume']), 6)),
            "value": _safe_val(round(float(r['trading_value']), 2)),
        })

    # 5-minute resampled data with equity (matches stats.plot() formula)
    import polars as pl
    rec_df = pl.DataFrame(recorder_data)
    rec_5m = rec_df.group_by_dynamic(
        pl.from_epoch('timestamp', time_unit='ns'), every='5m'
    ).agg([
        pl.col('price').last().alias('px'),
        pl.col('position').last().alias('pos'),
        pl.col('balance').last().alias('bal'),
        pl.col('fee').last().alias('fee'),
        pl.col('num_trades').last().alias('trades'),
        pl.col('trading_volume').last().alias('vol'),
        pl.col('trading_value').last().alias('value'),
    ])
    # Compute equity exactly as LinearAssetRecord.prepare() does:
    #   equity_wo_fee = balance + position * price * contract_size
    #   equity = equity_wo_fee - fee
    rec_5m = rec_5m.with_columns(
        (pl.col('bal') + pl.col('pos') * pl.col('px') * 1.0).alias('equity_wo_fee')
    )
    rec_5m_export = []
    from datetime import timezone as _tz
    for row in rec_5m.iter_rows():
        ts_dt = row[0]
        # pl.from_epoch produces naive UTC datetimes; .timestamp() on naive
        # datetime treats it as local time, which shifts the result by the
        # system timezone offset (e.g. +8h on UTC+8).  Force UTC explicitly.
        if hasattr(ts_dt, 'timestamp'):
            ts_ns = int(ts_dt.replace(tzinfo=_tz.utc).timestamp() * 1e9)
        else:
            ts_ns = int(ts_dt)
        rec_5m_export.append({
            "ts": ts_ns,
            "px": _safe_val(round(float(row[1]), 4)),
            "pos": _safe_val(round(float(row[2]), 6)),
            "bal": _safe_val(round(float(row[3]), 4)),
            "fee": _safe_val(round(float(row[4]), 4)),
            "trades": int(row[5]),
            "vol": _safe_val(round(float(row[6]), 6)),
            "value": _safe_val(round(float(row[7]), 2)),
            "equity_wo_fee": _safe_val(round(float(row[8]), 4)),
        })

    data = {
        "meta": {
            "strategy": "OBI_MM",
            "asset": asset_name,
            "generated_at": datetime.datetime.now().isoformat(),
            "params": {
                "half_spread": params['half_spread'],
                "skew": params['skew'],
                "c1": params['c1'],
                "interval_ns": params['interval'],
                "tick_size": params.get('tick_size', 0.1),
                "lot_size": params.get('lot_size', 0.001),
            },
            "num_snapshots": len(snapshots),
            "num_fills": len(fills),
            "num_klines": len(klines),
        },
        "snapshots": [],
        "fills": [],
        "klines": [],
        "recorder": rec_export,
        "recorder_5m": rec_5m_export,
    }

    for s in snapshots:
        data["snapshots"].append({
            "ts": int(s['timestamp']),
            "mid": _safe_val(round(float(s['mid_price']), 4)),
            "bid": _safe_val(round(float(s['best_bid']), 4)),
            "ask": _safe_val(round(float(s['best_ask']), 4)),
            "spr": _safe_val(round(float(s['spread']), 4)),
            "pos": _safe_val(round(float(s['position']), 6)),
            "bal": _safe_val(round(float(s['balance']), 4)),
            "fee": _safe_val(round(float(s['fee']), 4)),
            "alpha": _safe_val(round(float(s['alpha']), 6)),
            "imba": _safe_val(round(float(s['imbalance']), 4)),
            "bp": _safe_val(round(float(s['bid_price']), 4)),
            "ap": _safe_val(round(float(s['ask_price']), 4)),
        })

    for i, f in enumerate(fills):
        pnl, cum_pnl = pnl_data[i]
        data["fills"].append({
            "ts": int(f['timestamp']),
            "ets": int(f['exch_timestamp']),
            "side": int(f['side']),
            "ep": _safe_val(round(float(f['exec_price']), 4)),
            "eq": _safe_val(round(float(f['exec_qty']), 6)),
            "op": _safe_val(round(float(f['order_price']), 4)),
            "mid": _safe_val(round(float(f['mid_at_fill']), 4)),
            "spr": _safe_val(round(float(f['spread_at_fill']), 4)),
            "alpha": _safe_val(round(float(f['alpha_at_fill']), 6)),
            "pos": _safe_val(round(float(f['position_after']), 6)),
            "bal": _safe_val(round(float(f['balance_after']), 4)),
            "pnl": _safe_val(pnl),
            "cum_pnl": _safe_val(cum_pnl),
        })

    for k in klines:
        data["klines"].append({
            "ts": int(k['timestamp']),
            "o": _safe_val(round(float(k['open']), 4)),
            "h": _safe_val(round(float(k['high']), 4)),
            "l": _safe_val(round(float(k['low']), 4)),
            "c": _safe_val(round(float(k['close']), 4)),
            "v": _safe_val(round(float(k['volume']), 6)),
            "bv": _safe_val(round(float(k['buy_vol']), 6)),
            "sv": _safe_val(round(float(k['sell_vol']), 6)),
        })

    with open(output_path, 'w') as f:
        json.dump(data, f, separators=(',', ':'))

    size_mb = len(json.dumps(data, separators=(',', ':'))) / 1024 / 1024
    print(f"Exported to {output_path} ({size_mb:.1f} MB)")
    print(f"  Snapshots: {len(data['snapshots'])}")
    print(f"  Fills: {len(data['fills'])}")
    print(f"  K-lines: {len(data['klines'])}")


# ============================================================================
# Feed latency generation (if not already present)
# ============================================================================

from numba import njit as njit_py
from hftbacktest import LOCAL_EVENT, EXCH_EVENT as EXCH_EVENT_PY

@njit_py
def _build_order_latency_nb(feed_data_1s, out):
    for i in range(len(feed_data_1s)):
        feed_lat = feed_data_1s[i].local_ts - feed_data_1s[i].exch_ts
        out[i].req_ts = feed_data_1s[i].local_ts
        out[i].exch_ts = out[i].req_ts + feed_lat * 4
        out[i].resp_ts = out[i].exch_ts + feed_lat * 3
        out[i]._padding = 0

def generate_feed_latency(feed_file, output_file):
    import polars as pl
    import os
    if os.path.exists(output_file):
        print(f"Latency file already exists: {output_file}")
        return
    print(f"Generating latency data from {feed_file} ...")
    raw = np.load(feed_file)['data']
    df = pl.DataFrame(raw)
    df = df.filter(
        (pl.col('ev') & EXCH_EVENT_PY == EXCH_EVENT_PY) &
        (pl.col('ev') & LOCAL_EVENT == LOCAL_EVENT)
    ).with_columns(
        pl.col('local_ts').alias('ts')
    ).group_by_dynamic(
        'ts', every='1000000000i'
    ).agg(
        pl.col('exch_ts').last(),
        pl.col('local_ts').last()
    ).drop('ts')
    feed_1s = df.to_numpy(structured=True)
    latency = np.zeros(len(feed_1s), dtype=[('req_ts', 'i8'), ('exch_ts', 'i8'), ('resp_ts', 'i8'), ('_padding', 'i8')])
    _build_order_latency_nb(feed_1s, latency)
    np.savez_compressed(output_file, data=latency)
    print(f"Saved {output_file} ({len(latency)} records)")

# ============================================================================
# Main: standalone execution
# ============================================================================

if __name__ == '__main__':
    import os
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # Configuration: single-day backtest on 20260302 using 20260301 EOD snapshot
    test_dates = [20260302]
    initial_snapshot = 'usdm/ethusdc_20260301_eod.npz'
    output_json = 'obi_detailed_output.json'

    roi_lb = 0
    roi_ub = 3000

    # Generate latency files if needed
    latency_data = []
    data_files = []
    for d in test_dates:
        ds = str(d)
        lat_file = f'usdm/feed_latency_ethusdc_{ds}.npz'
        feed_file = f'usdm/ethusdc_{ds}.npz'
        generate_feed_latency(feed_file, lat_file)
        latency_data.append(lat_file)
        data_files.append(feed_file)

    asset = (
        BacktestAsset()
        .data(data_files)
        .initial_snapshot(initial_snapshot)
        .linear_asset(1.0)
        .intp_order_latency(latency_data)
        .power_prob_queue_model(3)
        .no_partial_fill_exchange()
        .trading_value_fee_model(-0.00005, 0.0007)
        .tick_size(0.1)
        .lot_size(0.001)
        .roi_lb(roi_lb)
        .roi_ub(roi_ub)
    )

    hbt = ROIVectorMarketDepthBacktest([asset])
    recorder = Recorder(1, 30_000_000)

    tick_sz = hbt.depth(0).tick_size
    params = {
        'half_spread': 80,
        'skew': 3.5,
        'c1': 160,
        'looking_depth': 0.025,
        'interval': 1_000_000_000,
        'window': 3_600_000_000_000 // 1_000_000_000,
        'order_qty_dollar': 50_000,
        'max_position_dollar': 50_000 * 50,
        'grid_num': 1,
        'grid_interval': tick_sz,
        'roi_lb': roi_lb,
        'roi_ub': roi_ub,
        'tick_size': 0.1,
        'lot_size': 0.001,
    }

    snapshots, fills = run_detailed_backtest(hbt, recorder, params)

    hbt.close()

    # Build K-lines from raw market data (bypass njit last_trades limitation)
    print("Building K-lines from raw data...")
    klines = build_klines_from_data(data_files)

    recorder.to_npz('usdm/obi_ethusdc_detailed.npz')

    # Load recorder data for classic backtest curve
    rec_data = np.load('usdm/obi_ethusdc_detailed.npz')['0']

    export_to_json(snapshots, fills, klines, rec_data, params, output_json)
