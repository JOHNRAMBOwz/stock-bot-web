# -*- coding: utf-8 -*-
"""
WZ Breaker - A股情绪动量模型 · Streamlit 网页前端
======================================
模块一：市场情绪监控（原界面保留）
模块二：9:25 弱转强狙击 —— 调用同目录 auction_picker.py 的高级选股逻辑
        （含今开价未刷新时的异常重试、全市场一次快照、指数退避）

运行（在本文件夹打开命令行）：
    streamlit run app.py
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta

# 保证无论从哪里启动，都能找到同目录的 auction_picker.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _ensure_streamlit() -> None:
    try:
        import streamlit  # noqa: F401
    except ImportError:
        import subprocess

        print("检测到缺少 streamlit，正在自动安装（清华镜像）...")
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "streamlit",
                "requests",
                "-i",
                "https://pypi.tuna.tsinghua.edu.cn/simple",
            ]
        )


_ensure_streamlit()

import akshare as ak
import pandas as pd
import streamlit as st

import auction_picker as picker


def _normalize_code_series(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.replace(".0", "", regex=False)
    s = s.str.replace(r"^(sh|sz|bj)", "", regex=True, case=False)
    return s.str.zfill(6)


def _to_numeric_cols(df: pd.DataFrame, cols) -> pd.DataFrame:
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _normalize_tencent_spot(raw: pd.DataFrame) -> pd.DataFrame:
    """把腾讯 rank_list 英文字段映射成系统统一中文列名。"""
    rename_map = {
        "code": "代码",
        "name": "名称",
        "zxj": "最新价",
        "zdf": "涨跌幅",
        "zd": "涨跌额",
        "hsl": "换手率",
        "lb": "量比",
        "zsz": "总市值_亿元",
        "ltsz": "流通市值_亿元",
        "pe_ttm": "市盈率-动态",
        "zdf_d60": "60日涨跌幅",
        "zdf_y": "年初至今涨跌幅",
        "volume": "成交量",
        "turnover": "成交额",
        "zf": "振幅",
        "zljlr": "主力净流入_快照",
        "zllr_d5": "主力流入_五日",
        "zllc_d5": "主力流出_五日",
    }
    df = raw.rename(columns={k: v for k, v in rename_map.items() if k in raw.columns}).copy()
    if "代码" not in df.columns:
        raise ValueError(f"腾讯行情缺少代码字段，实际列名：{list(raw.columns)}")
    df["代码"] = _normalize_code_series(df["代码"])
    df = _to_numeric_cols(
        df,
        [
            "最新价",
            "涨跌幅",
            "涨跌额",
            "换手率",
            "量比",
            "总市值_亿元",
            "流通市值_亿元",
            "市盈率-动态",
            "60日涨跌幅",
            "年初至今涨跌幅",
            "成交量",
            "成交额",
            "振幅",
            "主力净流入_快照",
            "主力流入_五日",
            "主力流出_五日",
        ],
    )
    if "总市值_亿元" in df.columns:
        df["总市值"] = df["总市值_亿元"] * 100000000
    if "主力流入_五日" in df.columns and "主力流出_五日" in df.columns:
        df["五日主力净流入"] = df["主力流入_五日"] - df["主力流出_五日"]
    return df


def _normalize_sina_spot(raw: pd.DataFrame) -> pd.DataFrame:
    """新浪官方接口列名已是中文：今开 / 昨收 / 最高 等。"""
    df = raw.copy()
    aliases = {
        "开盘": "今开",
        "昨收盘": "昨收",
        "收盘": "最新价",
        "涨跌幅度": "涨跌幅",
    }
    df = df.rename(columns={k: v for k, v in aliases.items() if k in df.columns and v not in df.columns})
    if "代码" not in df.columns:
        raise ValueError(f"新浪行情缺少代码字段，实际列名：{list(raw.columns)}")
    df["代码"] = _normalize_code_series(df["代码"])
    df = _to_numeric_cols(df, ["最新价", "涨跌幅", "今开", "昨收", "最高", "最低", "成交量", "成交额"])
    return df


def _fill_derived_prices(df: pd.DataFrame) -> pd.DataFrame:
    """腾讯榜单没有今开/昨收/最高时，用现价和涨跌幅尽量补齐。"""
    df = df.copy()
    df = _to_numeric_cols(df, ["最新价", "涨跌幅", "今开", "昨收", "最高"])
    if "昨收" not in df.columns:
        df["昨收"] = pd.NA
    need_prev = df["昨收"].isna() | (df["昨收"] <= 0)
    if need_prev.any():
        df.loc[need_prev, "昨收"] = df.loc[need_prev, "最新价"] / (1 + df.loc[need_prev, "涨跌幅"] / 100.0)
    if "今开" not in df.columns:
        df["今开"] = pd.NA
    need_open = df["今开"].isna() | (df["今开"] <= 0)
    if need_open.any():
        # 9:25 竞价刚结束时最新价≈今开；盘中则作为兜底，避免模块二直接崩溃
        df.loc[need_open, "今开"] = df.loc[need_open, "最新价"]
    return df


def _try_fetch_spot(fetcher, desc: str, max_retries: int, delay: float):
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            print(f"正在{desc}（第 {attempt}/{max_retries} 次）...")
            df = fetcher()
            if df is None or (hasattr(df, "empty") and df.empty):
                raise ValueError("接口返回了空表格")
            print(f"{desc}完成，共 {len(df)} 行。")
            return df
        except Exception as exc:
            last_error = exc
            print(f"警告：{desc}失败（第 {attempt}/{max_retries} 次）：{exc}")
            if attempt < max_retries:
                time.sleep(delay)
    raise RuntimeError(f"{desc}连续失败 {max_retries} 次：{last_error}")


def get_spot_data_with_retry(max_retries=3, delay=2, need_ohlc=False) -> pd.DataFrame:
    """
    全市场快照：优先腾讯 ak.stock_zh_a_spot_tx()，失败再走新浪 ak.stock_zh_a_spot()。
    不再使用东方财富 stock_zh_a_spot_em，避免 Connection aborted。
    need_ohlc=True 时会再补一次新浪，用于模块三需要的今开、昨收、最高。
    """
    tx_df = None
    sina_df = None
    errors = []

    try:
        tx_df = _try_fetch_spot(
            lambda: _normalize_tencent_spot(ak.stock_zh_a_spot_tx()),
            desc="获取腾讯全市场行情",
            max_retries=max_retries,
            delay=delay,
        )
    except Exception as exc:
        errors.append(str(exc))
        print(f"警告：腾讯接口不可用，准备切换新浪：{exc}")

    should_fetch_sina = tx_df is None or need_ohlc
    if should_fetch_sina:
        sina_retries = 1 if tx_df is not None else max_retries
        try:
            sina_df = _try_fetch_spot(
                lambda: _normalize_sina_spot(ak.stock_zh_a_spot()),
                desc="获取新浪全市场行情",
                max_retries=sina_retries,
                delay=delay,
            )
        except Exception as exc:
            errors.append(str(exc))
            print(f"警告：新浪接口不可用：{exc}")

    if tx_df is not None and sina_df is not None:
        ohlc_cols = [c for c in ["代码", "今开", "昨收", "最高", "最低"] if c in sina_df.columns]
        merged = tx_df.merge(sina_df[ohlc_cols].drop_duplicates("代码"), on="代码", how="left")
        return _fill_derived_prices(merged)
    if tx_df is not None:
        return _fill_derived_prices(tx_df)
    if sina_df is not None:
        return _fill_derived_prices(sina_df)
    raise RuntimeError("腾讯与新浪全市场行情均失败：{}".format("；".join(errors)))


def _market_of_code(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith("6"):
        return "sh"
    if code.startswith(("4", "8")):
        return "bj"
    return "sz"


def _prefixed_symbol(code: str) -> tuple[str, str]:
    """返回 (纯数字代码, 带 sh/sz/bj 前缀的代码)。"""
    raw = str(code).replace(".0", "").strip()
    raw = raw.lower()
    for pfx in ("sh", "sz", "bj"):
        if raw.startswith(pfx):
            raw = raw[len(pfx) :]
            break
    raw = raw.zfill(6)
    if raw.startswith("6"):
        return raw, "sh" + raw
    if raw.startswith(("0", "3")):
        return raw, "sz" + raw
    if raw.startswith(("4", "8")):
        return raw, "bj" + raw
    return raw, "sz" + raw


def _amount_to_yi(series: pd.Series) -> pd.Series:
    """把成交额统一成亿元。腾讯多为万元，新浪多为元。"""
    amt = pd.to_numeric(series, errors="coerce")
    med = amt.median()
    if pd.isna(med):
        return amt
    if med > 1e7:
        return (amt / 100000000).round(2)
    return (amt / 10000).round(2)


def _close_series(hist: pd.DataFrame) -> pd.Series:
    if hist is None or hist.empty:
        return pd.Series(dtype=float)
    col = "收盘" if "收盘" in hist.columns else ("close" if "close" in hist.columns else None)
    if col is None:
        return pd.Series(dtype=float)
    return pd.to_numeric(hist[col], errors="coerce").dropna()


def _calc_range_pct(close: pd.Series):
    if close is None or len(close) < 2:
        return "暂无数据", "暂无数据"
    last = float(close.iloc[-1])
    d5 = "暂无数据"
    d20 = "暂无数据"
    if len(close) >= 6 and float(close.iloc[-6]) > 0:
        d5 = round((last / float(close.iloc[-6]) - 1) * 100, 2)
    if len(close) >= 21 and float(close.iloc[-21]) > 0:
        d20 = round((last / float(close.iloc[-21]) - 1) * 100, 2)
    return d5, d20


def _fetch_main_net_inflow(code: str, symbol: str = "", days: int = 1, snapshot_row=None):
    """个股主力净流入，单位万元。days=1 取今日，days=5 取近五日合计。失败返回暂无数据。"""
    raw, prefixed = _prefixed_symbol(symbol or code)
    market = prefixed[:2]
    try:
        flow = ak.stock_individual_fund_flow(stock=raw, market=market)
        if flow is None or flow.empty or "主力净流入-净额" not in flow.columns:
            raise ValueError("资金流接口返回空表")
        if "日期" in flow.columns:
            flow = flow.sort_values("日期")
        vals = pd.to_numeric(flow["主力净流入-净额"], errors="coerce").dropna()
        if vals.empty:
            raise ValueError("主力净流入-净额为空")
        take = max(int(days), 1)
        val = float(vals.iloc[-1] if take == 1 else vals.tail(take).sum())
        return round(val / 10000.0, 2)
    except Exception as e:
        print(f"{raw} 抓取失败: {e}")

    if snapshot_row is not None:
        snap_col = "五日主力净流入" if days >= 5 else "主力净流入_快照"
        if snap_col in snapshot_row.index:
            snap = pd.to_numeric(pd.Series([snapshot_row[snap_col]]), errors="coerce").iloc[0]
            if pd.notna(snap):
                print(f"{raw} 资金流改用腾讯快照字段 {snap_col}={snap}")
                return round(float(snap), 2)
    return "暂无数据"


def _fetch_range_pct(code: str, symbol: str = ""):
    """近5日、近20日区间涨跌幅（%）。东方财富失败时改走腾讯/新浪。"""
    raw, prefixed = _prefixed_symbol(symbol or code)
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=70)).strftime("%Y%m%d")

    try:
        hist = ak.stock_zh_a_hist(symbol=raw, period="daily", start_date=start, end_date=end, adjust="qfq")
        close = _close_series(hist)
        if len(close) >= 2:
            return _calc_range_pct(close)
        raise ValueError("K线为空或收盘价不足")
    except Exception as e:
        print(f"{raw} 抓取失败: {e}")

    try:
        hist = ak.stock_zh_a_hist_tx(symbol=prefixed, start_date=start, end_date=end, adjust="qfq")
        close = _close_series(hist)
        if len(close) >= 2:
            return _calc_range_pct(close)
        raise ValueError("腾讯K线为空")
    except Exception as e:
        print(f"{raw} 抓取失败: {e}")

    try:
        hist = ak.stock_zh_a_daily(symbol=prefixed, start_date=start, end_date=end, adjust="qfq")
        close = _close_series(hist)
        if len(close) >= 2:
            return _calc_range_pct(close)
        raise ValueError("新浪K线为空")
    except Exception as e:
        print(f"{raw} 抓取失败: {e}")
    return "暂无数据", "暂无数据"


def _enrich_module3_advanced(df_res: pd.DataFrame, flow_days: int = 1) -> pd.DataFrame:
    """只对入围标的逐只补资金流和区间涨跌，中间休眠，避免封 IP。"""
    out = df_res.copy()
    n = len(out)
    progress = st.progress(0, text="正在对入围标的做二次高级数据抓取…")
    status = st.empty()
    flows, pct5s, pct20s = [], [], []

    for i, row in out.iterrows():
        code, symbol = _prefixed_symbol(row["代码"])
        name = str(row.get("名称", code))
        status.caption(f"二次抓取 {i + 1}/{n}：{name}（{symbol}）资金流与区间涨跌")
        print(f"二次抓取 {code} / {symbol}")

        flows.append(_fetch_main_net_inflow(code, symbol=symbol, days=flow_days, snapshot_row=row))
        time.sleep(1)
        d5, d20 = _fetch_range_pct(code, symbol=symbol)
        pct5s.append(d5)
        pct20s.append(d20)
        time.sleep(1)
        progress.progress((i + 1) / n, text=f"二次抓取进度 {i + 1}/{n}")

    out["主力净流入(万)"] = flows
    out["近5日涨跌幅(%)"] = pct5s
    out["近20日涨跌幅(%)"] = pct20s
    out = out.drop(columns=[c for c in ["主力净流入_快照", "五日主力净流入"] if c in out.columns], errors="ignore")
    progress.empty()
    status.empty()
    return out


# ---------------------------------------------------------------------------
# 页面与 VIP
# ---------------------------------------------------------------------------
st.set_page_config(page_title="WZ Breaker - A股情绪动量模型", layout="wide", page_icon="🤖")

st.sidebar.title("WZ Breaker")
st.sidebar.markdown("---")
st.sidebar.subheader("🔐 VIP 用户验证")
activation_code = st.sidebar.text_input(
    "请输入激活码解锁核心功能",
    type="password",
    placeholder="输入后请按回车键，或点击下方验证",
)
st.sidebar.button("🔐 验证并登录")

# 用户数据库：激活码 -> 到期日期
VIP_DATABASE = {
    "niuniu888": "2026-12-31",
    "test001": "2026-9-30",
}

code = (activation_code or "").strip()

if not code:
    st.sidebar.info("👆 请在上方输入激活码，并点击验证以解锁系统。")
    st.sidebar.info("购买激活码请联系管理员微信。")
    st.sidebar.markdown("**微信：azxc139210**")
    st.warning("⚠️ 欢迎来到 WZ Breaker - A股情绪动量模型。请输入 VIP 激活码后按回车，或点击左侧「验证并登录」。")
    st.markdown("购买激活码请联系管理员微信。")
    st.markdown("**微信：azxc139210**")
    st.stop()

if code not in VIP_DATABASE:
    st.sidebar.error("❌ 激活码错误或不存在，请检查大小写！")
    st.sidebar.info("购买激活码请联系管理员微信。")
    st.sidebar.markdown("**微信：azxc139210**")
    st.warning("⚠️ 欢迎来到 WZ Breaker - A股情绪动量模型。请输入有效的 VIP 激活码以解锁选股策略。")
    st.markdown("购买激活码请联系管理员微信。")
    st.markdown("**微信：azxc139210**")
    st.stop()

expire_str = VIP_DATABASE[code]
expire_date = datetime.strptime(expire_str, "%Y-%m-%d").date()
today = date.today()  # 等价于 datetime.date.today()

if today > expire_date:
    st.sidebar.error("您的激活码已过期，请续费")
    st.stop()

st.sidebar.success("🟢 尊贵的 VIP，验证通过！")
st.sidebar.markdown("---")
st.sidebar.write("👤 用户：顶级游资")
st.sidebar.write(f"⏳ 到期时间：{expire_str}")


st.title("WZ Breaker - A股情绪动量模型")
st.markdown("用绝对的理性，对抗人性的贪婪与恐惧。")
st.markdown("---")

# --- 价值十万的每日实战 SOP 指南 ---
with st.expander("🚨 【付费VIP必读】Stock-Bot 终极实战操作 SOP 与铁律 (点击展开)", expanded=True):
    st.markdown("""
**⚠️ 警告：系统提供的是纯粹的数据与逻辑，最终的利润来自您钢铁般的纪律！请将以下规则刻在脑子里：**

---

### 【第一步：观天象，定仓位】

- 🌪️ **全天候［模块一］**  
  每天早盘及盘中，首看情绪温度计。若跌停家数激增（>20家）或最高连板降至2板（退潮期），**管住手，强行空仓！**  
  仅在赚钱效应红盘、连板高度打开时，才可重仓出击。并从中寻找当前市场主线板块。

---

### 【第二步：选武器，准击杀】

- ⚔️ **激进型（早盘）—［模块二：9:25 弱转强］**  
  适合职业盯盘选手。9:25 刷新，淘汰非主线题材。9:30 开盘参考『建议入场价』直接扫货。  
  **【铁律】**：买入后立刻挂好『硬性止损位』条件单，跌破无条件斩仓核按钮；次日不涨停，无条件止盈！

- 🛡️ **稳健型（尾盘）—［模块三：14:50 潜伏］**  
  适合上班族。避开 T+1 日内暴跌风险，14:50 刷新抓取“尾盘光头抢筹”股。  
  **【铁律】**：买入后安心持有，次日早盘趁冲高（赚 2%-5%）果断落袋为安，绝不格局恋战！

- 📈 **波段型（盘中）—［模块四：趋势中军］**  
  适合中线大资金。盘中随时刷新，抓取 50-500 亿盘子、量比突增的趋势龙。  
  **【铁律】**：沿着均线（10日/20日生命线）持股让利润奔跑，收盘有效跌破生命线，立刻出局！

---

### 【第三步：做时间，机构抱团】

- 🗓️ **长线型（1-2 个月）—［模块五：价值趋势共振］**  
  适合稳健资金与不盯盘的客户。刷新后只做 **基本面优秀 + 100 亿以上中大盘 + 温和上升通道** 的机构重仓股，按 1～2 个月周期布局，做时间的朋友。  
  筛选条件请严格执行：动态市盈率 5～40、总市值 ≥ 100 亿、60 日涨幅 10%～40%、换手率 1%～8%。没有标的就等待，绝不降格买入。  
  **【铁律 · 建仓】**：底仓首抛，遇大盘暴跌再分批逢低吸纳，禁止追高一把买满。  
  **【铁律 · 持股】**：以 20 日线为强弱分界（站上偏多、跌破转弱减仓）；**收盘有效跌破 60 日线，彻底清仓，不再补仓硬扛。**

---

### 【终极心法】

**量化选股是做减法。宁可错过，绝不做错；宁可小亏，绝不深套！截断亏损，让利润奔跑！**
    """)


# ---------------------------------------------------------------------------
# 把 auction_picker 的终端日志同步到网页，方便看到「今开价重试」过程
# ---------------------------------------------------------------------------
def _bind_picker_logs(placeholder) -> None:
    lines: list[str] = []

    def _emit(tag: str, msg: str) -> None:
        lines.append(f"- **[{tag}]** {msg}")
        placeholder.markdown("\n".join(lines[-18:]))

    picker.log_info = lambda msg: _emit("信息", msg)
    picker.log_ok = lambda msg: _emit("成功", msg)
    picker.log_warn = lambda msg: _emit("提示", msg)
    picker.log_err = lambda msg: _emit("错误", msg)


def _pretty_result_table(picked: pd.DataFrame) -> pd.DataFrame:
    """把引擎返回的结果整理成网页表格。"""
    open_px = pd.to_numeric(picked["今开"], errors="coerce")
    prev_close = pd.to_numeric(picked["昨收"], errors="coerce")
    # 建议入场价 = 9:25 今开；硬性止损 = max(昨收零轴, 今开下跌3.5%)
    suggest_entry = open_px.round(2)
    hard_stop = pd.concat([prev_close, open_px * 0.965], axis=1).max(axis=1).round(2)

    show = pd.DataFrame(
        {
            "股票代码": picked["代码"].astype(str),
            "股票名称": picked["名称"].astype(str),
            "昨日连板": picked["连板数"].map(picker.format_lianban),
            "昨日换手(%)": pd.to_numeric(picked["换手率"], errors="coerce").round(2),
            "昨收": prev_close.round(2),
            "今开": open_px.round(2),
            "今日高开(%)": (pd.to_numeric(picked["高开幅度"], errors="coerce") * 100).round(2),
            "建议入场价": suggest_entry,
            "硬性止损位": hard_stop,
            "逻辑止盈纪律": "T+1卖出：次日不封死涨停，或冲高跌破分时黄线(均价线)，无条件止盈出局",
        }
    )
    if "所属行业" in picked.columns:
        show["所属行业"] = picked["所属行业"].astype(str)
    return show


# ---------------------------------------------------------------------------
# 模块一：市场情绪监控（保留原界面）
# ---------------------------------------------------------------------------
st.header("📊 模块一：全局情绪温度计")
if st.button("🔄 点击获取今日最新情绪数据"):
    with st.spinner("WZ Breaker 正在高速连接交易所服务器..."):
        try:
            today_str = datetime.now().strftime("%Y%m%d")
            used_date = today_str
            try:
                df_zt = ak.stock_zt_pool_em(date=today_str)
                if df_zt is None or df_zt.empty:
                    raise ValueError("今日涨停池暂无数据")
            except Exception:
                used_date, _ = picker.get_previous_trade_date()
                df_zt = ak.stock_zt_pool_em(date=used_date)

            try:
                df_dt = ak.stock_zt_pool_dtgc_em(date=used_date)
                if df_dt is None:
                    df_dt = pd.DataFrame()
            except Exception:
                df_dt = pd.DataFrame()

            col1, col2, col3 = st.columns(3)
            col1.metric("🔥 今日涨停家数", f"{len(df_zt)} 家", "赚钱效应")
            if df_dt.empty:
                col2.metric("🧊 今日跌停家数", "0 家", "亏钱效应")
            else:
                col2.metric("🧊 今日跌停家数", f"{len(df_dt)} 家", "-亏钱效应")

            if not df_zt.empty and "连板数" in df_zt.columns:
                max_lb = df_zt["连板数"].max()
                col3.metric("🚀 市场最高连板 (天花板)", f"{max_lb} 板", "情绪高度")

                st.caption(f"数据日期：{used_date}")
                st.subheader("🏆 核心连板梯队 (只显示2连板及以上)")
                df_lb = df_zt[df_zt["连板数"] >= 2].sort_values(by="连板数", ascending=False)
                show_cols = [c for c in ["代码", "名称", "最新价", "涨跌幅", "连板数", "所属行业"] if c in df_lb.columns]
                st.dataframe(df_lb[show_cols], use_container_width=True, hide_index=True)
            else:
                st.caption(f"数据日期：{used_date}")
                st.info("今日涨停池暂无连板数据。")

        except Exception as e:
            st.error(f"数据获取失败，可能由于非交易时间或接口限制：{e}")

st.markdown("---")


# ---------------------------------------------------------------------------
# 模块二：9:25 核心狙击 —— 调用 auction_picker 高级逻辑
# ---------------------------------------------------------------------------
st.header("⚡ 模块二：9:25 集合竞价『弱转强』狙击")
st.info("💡 操作指南：请在交易日早上 9:25 分 05秒 之后点击下方按钮，抓取竞价抢筹龙头。系统会自动重试今开价，直到行情刷新。")

st.caption(
    "策略条件：昨日涨停 · 换手 10%~30% · 剔除 ST/科创/创业/北交所 · 今日高开 3%~9%（全市场一次快照，失败自动指数退避重试）"
)

if st.button("🔫 启动 9:25 终极选股策略"):
    log_box = st.expander("📡 实时抓取日志（含今开价异常重试）", expanded=True)
    log_placeholder = log_box.empty()
    _bind_picker_logs(log_placeholder)

    with st.spinner("WZ Breaker 正在全网扫描超预期『弱转强』标的（今开未刷新会自动重试）..."):
        try:
            date_str, prev_date = picker.get_previous_trade_date()
            st.write(f"📅 上一交易日：**{date_str}**（{prev_date.strftime('%Y年%m月%d日')}）")

            zt_df = picker.fetch_yesterday_limit_up(date_str)
            st.write(f"🔥 昨日涨停股一共 **{len(zt_df)}** 只。")

            candidates = picker.filter_yesterday_candidates(zt_df)
            if candidates.empty:
                st.warning("昨日无符合换手率条件的股票，今日空仓。")
            else:
                preview = "、".join(
                    f"{r['名称']}({r['代码']})" for _, r in candidates.head(12).iterrows()
                )
                st.write(f"👀 进入今日竞价观察的候选：**{len(candidates)}** 只。{preview}")

                # 核心：模块二仍走东方财富全市场快照（该接口对竞价时段可用）
                spot = picker.fetch_today_spot()
                picked = picker.match_weak_to_strong(candidates, spot)

                if picked is None or picked.empty:
                    st.error("😭 竞价结束，未发现符合 3%-9% 高开条件的股票。执行纪律：管住手！")
                else:
                    st.success(f"🎉 狙击成功！WZ Breaker 发现 {len(picked)} 只符合黄金买点的标的！")
                    df_res = _pretty_result_table(picked)
                    st.dataframe(
                        df_res,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "股票代码": st.column_config.TextColumn("股票代码", width="small"),
                            "股票名称": st.column_config.TextColumn("股票名称", width="small"),
                            "昨日连板": st.column_config.TextColumn("昨日连板", width="small"),
                            "昨日换手(%)": st.column_config.NumberColumn("昨日换手(%)", format="%.2f"),
                            "昨收": st.column_config.NumberColumn("昨收", format="%.2f"),
                            "今开": st.column_config.NumberColumn("今开", format="%.2f"),
                            "今日高开(%)": st.column_config.NumberColumn("今日高开(%)", format="%+.2f"),
                            "建议入场价": st.column_config.NumberColumn("建议入场价", format="%.2f"),
                            "硬性止损位": st.column_config.NumberColumn("硬性止损位", format="%.2f"),
                            "逻辑止盈纪律": st.column_config.TextColumn("逻辑止盈纪律", width="large"),
                        },
                    )

                    csv_path = picker.save_csv(picked, date_str)
                    csv_bytes = df_res.to_csv(index=False).encode("utf-8-sig")
                    st.download_button(
                        "⬇️ 下载本次选股结果 CSV",
                        data=csv_bytes,
                        file_name=f"弱转强_{date_str}_{datetime.now().strftime('%H%M%S')}.csv",
                        mime="text/csv",
                    )
                    if csv_path:
                        st.caption(f"同时已保存到本地：{os.path.abspath(csv_path)}")

                    st.caption("以上仅为数据筛选，不构成任何投资建议。股市有风险，入市需谨慎。")

        except Exception as e:
            st.error(f"运行报错：{e}")
            st.error("请检查网络是否正常。若刚过 9:25，今开价可能尚未刷新，稍等几秒再点一次。")

st.markdown("---")

# ---------------------------------------------------------------------------
# 模块三：14:50 尾盘潜伏 —— 规避 T+1 日内波动
# ---------------------------------------------------------------------------
st.header("🌅 模块三：14:50 尾盘潜伏系统")
st.info("💡 操作指南：请在交易日下午 14:50 左右点击运行。尾盘买入，次日早盘冲高即卖，规避 T+1 日内波动风险。")

if st.button("🛒 启动 14:50 尾盘抢筹扫描"):
    with st.spinner("WZ Breaker 正在扫描尾盘强资金抢筹标的..."):
        try:
            df_spot = get_spot_data_with_retry(need_ohlc=True)
            if df_spot is None or df_spot.empty:
                raise ValueError("全市场行情接口返回为空")

            df = df_spot.copy()
            df["代码"] = _normalize_code_series(df["代码"])
            df = _to_numeric_cols(df, ["涨跌幅", "换手率", "最新价", "最高"])

            df = df[~df["名称"].astype(str).str.contains("ST", case=False, na=False)]
            df = df[~df["代码"].str.startswith("688")]
            df = df[~df["代码"].str.startswith("300")]
            df = df[(df["涨跌幅"] >= 3.0) & (df["涨跌幅"] <= 7.0)]
            if "换手率" in df.columns:
                df = df[df["换手率"] >= 5.0]
            if "最高" in df.columns:
                df = df[df["最高"] > 0]
                df = df[df["最新价"] >= df["最高"] * 0.985]

            if "成交额" in df.columns:
                df["成交额(亿)"] = _amount_to_yi(df["成交额"])

            show_cols = [
                c
                for c in [
                    "代码",
                    "名称",
                    "最新价",
                    "涨跌幅",
                    "换手率",
                    "最高",
                    "成交额(亿)",
                    "主力净流入_快照",
                    "五日主力净流入",
                ]
                if c in df.columns
            ]
            sort_cols = [c for c in ["涨跌幅", "换手率"] if c in df.columns]
            df_res = df[show_cols].sort_values(by=sort_cols, ascending=False).reset_index(drop=True)

            if df_res.empty:
                st.error("今日尾盘无符合强资金抢筹特征的标的，管住手")
            else:
                st.info(f"基础筛选入围 {len(df_res)} 只，开始逐只补抓大资金与区间涨跌（每只间隔约 2 秒，防止封 IP）…")
                df_res = _enrich_module3_advanced(df_res)
                if "主力净流入(万)" in df_res.columns:
                    df_res = df_res.copy()
                    df_res["sort_val"] = pd.to_numeric(df_res["主力净流入(万)"], errors="coerce")
                    df_res["sort_val"] = df_res["sort_val"].fillna(-999999)
                    df_res = df_res.sort_values(by="sort_val", ascending=False)
                    df_res = df_res.drop(columns=["sort_val"]).head(10).reset_index(drop=True)
                else:
                    df_res = df_res.head(10).reset_index(drop=True)
                st.success("🤖 Stock-Bot 已为您自动按『主力净流入』降序排列，并精选出全市场资金抢筹最凶的前 10 大核心标的！")
                st.dataframe(
                    df_res,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "代码": st.column_config.TextColumn("代码", width="small"),
                        "名称": st.column_config.TextColumn("名称", width="small"),
                        "最新价": st.column_config.NumberColumn("最新价", format="%.2f"),
                        "涨跌幅": st.column_config.NumberColumn("涨跌幅", format="%+.2f"),
                        "换手率": st.column_config.NumberColumn("换手率", format="%.2f"),
                        "最高": st.column_config.NumberColumn("最高", format="%.2f"),
                        "成交额(亿)": st.column_config.NumberColumn("成交额(亿)", format="%.2f"),
                        "主力净流入(万)": st.column_config.TextColumn("主力净流入(万)"),
                        "近5日涨跌幅(%)": st.column_config.TextColumn("近5日涨跌幅(%)"),
                        "近20日涨跌幅(%)": st.column_config.TextColumn("近20日涨跌幅(%)"),
                    },
                )
                st.caption("以上仅为数据筛选，不构成任何投资建议。股市有风险，入市需谨慎。")

                st.markdown("### 🎯 核心标的次日操盘计划 (执行表)")
                last_px = pd.to_numeric(df_res["最新价"], errors="coerce")
                df_plan = pd.DataFrame(
                    {
                        "代码": df_res["代码"],
                        "名称": df_res["名称"],
                        "建议入场价": last_px.astype(float).round(2),
                        "明日冲板阻力位": (last_px * 1.095).astype(float).round(2),
                        "硬性防守线(-3.5%)": (last_px * 0.965).astype(float).round(2),
                        "逻辑止盈纪律": "动态止盈：急拉不板遇阻卖；若平/低开，盯死分时黄线(即分时图上的均价线)，站稳格局，破位出局",
                    }
                )
                st.dataframe(
                    df_plan,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "代码": st.column_config.TextColumn("代码", width="small"),
                        "名称": st.column_config.TextColumn("名称", width="small"),
                        "建议入场价": st.column_config.NumberColumn("建议入场价", format="%.2f"),
                        "明日冲板阻力位": st.column_config.NumberColumn("明日冲板阻力位", format="%.2f"),
                        "硬性防守线(-3.5%)": st.column_config.NumberColumn("硬性防守线(-3.5%)", format="%.2f"),
                        "逻辑止盈纪律": st.column_config.TextColumn("逻辑止盈纪律", width="large"),
                    },
                )
                st.caption("注：尾盘潜伏博弈的是次日早盘溢价，无论盈亏，次日早盘10:00前建议了结，绝不恋战。")

st.markdown("---")

# ---------------------------------------------------------------------------
# 模块四：趋势中军·放量起爆雷达 —— 中线波段
# ---------------------------------------------------------------------------
st.header("📈 模块四：趋势中军·放量起爆雷达")
st.info("💡 操作指南：盘中随时可看。专门捕捉 50-500亿盘子、处于中期上升通道、今日突然放量突破的『趋势核心龙』。适合中线波段持有。")

if st.button("📡 启动趋势雷达扫描"):
    with st.spinner("WZ Breaker 正在扫描趋势中军·放量起爆标的..."):
        try:
            df_spot = get_spot_data_with_retry()
            if df_spot is None or df_spot.empty:
                raise ValueError("全市场行情接口返回为空")

            df = df_spot.copy()
            df["代码"] = _normalize_code_series(df["代码"])
            df = _to_numeric_cols(df, ["最新价", "涨跌幅", "量比", "60日涨跌幅", "总市值", "换手率"])

            df = df[~df["名称"].astype(str).str.contains("ST", case=False, na=False)]
            df = df[~df["代码"].str.startswith("688")]
            df = df[~df["代码"].str.startswith("300")]
            df = df[(df["涨跌幅"] >= 4.0) & (df["涨跌幅"] <= 8.0)]
            if "总市值" in df.columns:
                df = df[(df["总市值"] >= 5000000000) & (df["总市值"] <= 50000000000)]
            if "60日涨跌幅" in df.columns:
                df = df[(df["60日涨跌幅"] >= 20) & (df["60日涨跌幅"] <= 80)]
            if "量比" in df.columns:
                df = df[df["量比"] >= 2.0]
            elif "成交额" in df.columns:
                df["成交额"] = pd.to_numeric(df["成交额"], errors="coerce")
                cutoff = df["成交额"].quantile(0.7)
                df = df[df["成交额"] >= cutoff]
            if "换手率" in df.columns:
                df = df[df["换手率"] >= 5.0]

            if "成交额" in df.columns:
                df["成交额(亿)"] = _amount_to_yi(df["成交额"])

            show_cols = [
                c
                for c in [
                    "代码",
                    "名称",
                    "最新价",
                    "涨跌幅",
                    "量比",
                    "60日涨跌幅",
                    "总市值",
                    "成交额(亿)",
                    "主力净流入_快照",
                    "五日主力净流入",
                ]
                if c in df.columns
            ]
            sort_cols = [c for c in ["量比", "涨跌幅"] if c in df.columns]
            df_res = df[show_cols].sort_values(by=sort_cols, ascending=False).reset_index(drop=True)
            if "成交额(亿)" not in df_res.columns:
                df_res["成交额(亿)"] = "暂无数据"
            if "总市值" in df_res.columns:
                df_res["总市值"] = (df_res["总市值"] / 100000000).round(0).astype("Int64").astype(str) + " 亿元"
            df_res["买入建议"] = "今日放量跟随买入/逢均线低吸"
            df_res["卖出/止损纪律"] = "收盘跌破 10日/20日均线无条件止损"

            if df_res.empty:
                st.error("今日无符合趋势中军·放量起爆特征的标的，管住手")
            else:
                st.info(f"基础筛选入围 {len(df_res)} 只，开始逐只补抓近五日资金流与区间涨跌（每只间隔约 2 秒，防止封 IP）…")
                df_res = _enrich_module3_advanced(df_res, flow_days=5)
                st.success(f"🎉 趋势雷达扫描完成，发现 {len(df_res)} 只趋势核心龙。")
                st.dataframe(
                    df_res,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "代码": st.column_config.TextColumn("代码", width="small"),
                        "名称": st.column_config.TextColumn("名称", width="small"),
                        "最新价": st.column_config.NumberColumn("最新价", format="%.2f"),
                        "涨跌幅": st.column_config.NumberColumn("涨跌幅", format="%+.2f"),
                        "量比": st.column_config.NumberColumn("量比", format="%.2f"),
                        "60日涨跌幅": st.column_config.NumberColumn("60日涨跌幅", format="%+.2f"),
                        "总市值": st.column_config.TextColumn("总市值", width="small"),
                        "成交额(亿)": st.column_config.NumberColumn("成交额(亿)", format="%.2f"),
                        "主力净流入(万)": st.column_config.TextColumn("主力净流入(万)"),
                        "近5日涨跌幅(%)": st.column_config.TextColumn("近5日涨跌幅(%)"),
                        "近20日涨跌幅(%)": st.column_config.TextColumn("近20日涨跌幅(%)"),
                        "买入建议": st.column_config.TextColumn("买入建议", width="medium"),
                        "卖出/止损纪律": st.column_config.TextColumn("卖出/止损纪律", width="large"),
                    },
                )
                st.caption("以上仅为数据筛选，不构成任何投资建议。股市有风险，入市需谨慎。")

        except Exception as e:
            st.error(f"运行报错：{e}")
            st.error("请检查网络是否正常。建议在交易时段行情接口可用时再扫一次。")

st.markdown("---")

# ---------------------------------------------------------------------------
# 模块五：1-2月期『机构抱团』价值趋势共振
# ---------------------------------------------------------------------------
st.header("🗓️ 模块五：1-2月期『机构抱团』价值趋势共振")
st.info("💡 战略投资指南：本模块旨在挖掘【基本面优秀 + 中大市值 + 处于温和上升通道】的机构重仓股。按 1~2 个月周期布局，做时间的朋友，告别盯盘焦虑。")

if st.button("🔭 启动中长线价值雷达扫描"):
    with st.spinner("WZ Breaker 正在扫描机构抱团·价值趋势共振标的..."):
        try:
            df_spot = get_spot_data_with_retry()
            if df_spot is None or df_spot.empty:
                raise ValueError("全市场行情接口返回为空")

            df = df_spot.copy()
            df["代码"] = _normalize_code_series(df["代码"])
            df = _to_numeric_cols(df, ["最新价", "市盈率-动态", "60日涨跌幅", "总市值", "年初至今涨跌幅", "换手率"])

            df = df[~df["名称"].astype(str).str.contains("ST", case=False, na=False)]
            if "市盈率-动态" in df.columns:
                df = df[(df["市盈率-动态"] >= 5) & (df["市盈率-动态"] <= 40)]
            if "总市值" in df.columns:
                df = df[df["总市值"] >= 10000000000]
            if "60日涨跌幅" in df.columns:
                df = df[(df["60日涨跌幅"] >= 10) & (df["60日涨跌幅"] <= 40)]
            if "换手率" in df.columns:
                df = df[(df["换手率"] >= 1.0) & (df["换手率"] <= 8.0)]

            show_cols = [
                c
                for c in ["代码", "名称", "最新价", "市盈率-动态", "60日涨跌幅", "总市值", "年初至今涨跌幅"]
                if c in df.columns
            ]
            sort_cols = [c for c in ["60日涨跌幅", "总市值"] if c in df.columns]
            df_res = df[show_cols].sort_values(by=sort_cols, ascending=False).reset_index(drop=True)

            if df_res.empty:
                st.error("当前市场无符合低估值+中线走强的稳健标的，建议等待")
            else:
                df_res["市盈率-动态"] = df_res["市盈率-动态"].round(1)
                df_res["总市值"] = (df_res["总市值"] / 100000000).round(0).astype("int64").astype(str) + " 亿元"
                df_res["建仓策略"] = "底仓首抛，遇大盘暴跌分批逢低吸纳"
                df_res["持股纪律"] = "以20日线为强弱分界，跌破60日线彻底清仓"

                st.success(f"🎉 价值雷达扫描完成，发现 {len(df_res)} 只机构抱团稳健标的。")
                st.dataframe(
                    df_res,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "代码": st.column_config.TextColumn("代码", width="small"),
                        "名称": st.column_config.TextColumn("名称", width="small"),
                        "最新价": st.column_config.NumberColumn("最新价", format="%.2f"),
                        "市盈率-动态": st.column_config.NumberColumn("市盈率-动态", format="%.1f"),
                        "60日涨跌幅": st.column_config.NumberColumn("60日涨跌幅", format="%+.2f"),
                        "总市值": st.column_config.TextColumn("总市值", width="small"),
                        "年初至今涨跌幅": st.column_config.NumberColumn("年初至今涨跌幅", format="%+.2f"),
                        "建仓策略": st.column_config.TextColumn("建仓策略", width="medium"),
                        "持股纪律": st.column_config.TextColumn("持股纪律", width="large"),
                    },
                )
                st.caption("以上仅为数据筛选，不构成任何投资建议。股市有风险，入市需谨慎。")

        except Exception as e:
            st.error(f"运行报错：{e}")
            st.error("请检查网络是否正常。建议在交易时段行情接口可用时再扫一次。")
