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
from datetime import date, datetime

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


# ---------------------------------------------------------------------------
# 页面与 VIP
# ---------------------------------------------------------------------------
st.set_page_config(page_title="WZ Breaker - A股情绪动量模型", layout="wide", page_icon="🤖")

st.sidebar.title("WZ Breaker")
st.sidebar.markdown("---")
st.sidebar.subheader("🔐 VIP 用户验证")
activation_code = st.sidebar.text_input("请输入激活码解锁核心功能", type="password")

# 用户数据库：激活码 -> 到期日期
VIP_DATABASE = {
    "niuniu888": "2026-12-31",
    "test001": "2026-9-30",
}

if activation_code not in VIP_DATABASE:
    st.warning("⚠️ 欢迎来到 WZ Breaker - A股情绪动量模型。请输入有效的 VIP 激活码以解锁选股策略。")
    st.sidebar.error("无效激活码")
    st.sidebar.info("购买激活码请联系管理员微信。")
    st.sidebar.markdown("**微信：azxc139210**")
    st.markdown("购买激活码请联系管理员微信。")
    st.markdown("**微信：azxc139210**")
    st.stop()

expire_str = VIP_DATABASE[activation_code]
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

                # 核心：带校验与重试的全市场今开价快照
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
            df_spot = ak.stock_zh_a_spot_em()
            if df_spot is None or df_spot.empty:
                raise ValueError("全市场行情接口返回为空")

            required = {"代码", "名称", "最新价", "涨跌幅", "换手率", "最高"}
            missing = required - set(df_spot.columns)
            if missing:
                raise ValueError(f"行情缺少字段：{missing}")

            df = df_spot.copy()
            df["代码"] = df["代码"].astype(str).str.replace(".0", "", regex=False).str.zfill(6)
            df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
            df["换手率"] = pd.to_numeric(df["换手率"], errors="coerce")
            df["最新价"] = pd.to_numeric(df["最新价"], errors="coerce")
            df["最高"] = pd.to_numeric(df["最高"], errors="coerce")

            df = df[~df["名称"].astype(str).str.contains("ST", case=False, na=False)]
            df = df[~df["代码"].str.startswith("688")]
            df = df[~df["代码"].str.startswith("300")]
            df = df[(df["涨跌幅"] >= 3.0) & (df["涨跌幅"] <= 7.0)]
            df = df[df["换手率"] >= 5.0]
            df = df[df["最高"] > 0]
            df = df[df["最新价"] >= df["最高"] * 0.985]

            show_cols = ["代码", "名称", "最新价", "涨跌幅", "换手率", "最高"]
            df_res = df[show_cols].sort_values(by=["涨跌幅", "换手率"], ascending=False).reset_index(drop=True)

            if df_res.empty:
                st.error("今日尾盘无符合强资金抢筹特征的标的，管住手")
            else:
                st.success(f"🎉 尾盘扫描完成，发现 {len(df_res)} 只符合强资金抢筹特征的标的。")
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
                    },
                )
                st.caption("以上仅为数据筛选，不构成任何投资建议。股市有风险，入市需谨慎。")

        except Exception as e:
            st.error(f"运行报错：{e}")
            st.error("请检查网络是否正常。建议在交易日 14:50 左右、行情接口可用时再扫一次。")

st.markdown("---")

# ---------------------------------------------------------------------------
# 模块四：趋势中军·放量起爆雷达 —— 中线波段
# ---------------------------------------------------------------------------
st.header("📈 模块四：趋势中军·放量起爆雷达")
st.info("💡 操作指南：盘中随时可看。专门捕捉 50-500亿盘子、处于中期上升通道、今日突然放量突破的『趋势核心龙』。适合中线波段持有。")

if st.button("📡 启动趋势雷达扫描"):
    with st.spinner("WZ Breaker 正在扫描趋势中军·放量起爆标的..."):
        try:
            df_spot = ak.stock_zh_a_spot_em()
            if df_spot is None or df_spot.empty:
                raise ValueError("全市场行情接口返回为空")

            required = {"代码", "名称", "最新价", "涨跌幅", "量比", "60日涨跌幅", "总市值", "换手率"}
            missing = required - set(df_spot.columns)
            if missing:
                raise ValueError(f"行情缺少字段：{missing}")

            df = df_spot.copy()
            df["代码"] = df["代码"].astype(str).str.replace(".0", "", regex=False).str.zfill(6)
            df["最新价"] = pd.to_numeric(df["最新价"], errors="coerce")
            df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
            df["量比"] = pd.to_numeric(df["量比"], errors="coerce")
            df["60日涨跌幅"] = pd.to_numeric(df["60日涨跌幅"], errors="coerce")
            df["总市值"] = pd.to_numeric(df["总市值"], errors="coerce")
            df["换手率"] = pd.to_numeric(df["换手率"], errors="coerce")

            df = df[~df["名称"].astype(str).str.contains("ST", case=False, na=False)]
            df = df[~df["代码"].str.startswith("688")]
            df = df[~df["代码"].str.startswith("300")]
            df = df[(df["总市值"] >= 5000000000) & (df["总市值"] <= 50000000000)]
            df = df[(df["60日涨跌幅"] >= 20) & (df["60日涨跌幅"] <= 80)]
            df = df[(df["量比"] >= 2.0) & (df["涨跌幅"] >= 4.0) & (df["涨跌幅"] <= 8.0)]
            df = df[df["换手率"] >= 5.0]

            show_cols = ["代码", "名称", "最新价", "涨跌幅", "量比", "60日涨跌幅", "总市值"]
            df_res = df[show_cols].sort_values(by=["量比", "涨跌幅"], ascending=False).reset_index(drop=True)
            df_res["总市值"] = (df_res["总市值"] / 100000000).round(0).astype("int64").astype(str) + " 亿元"
            df_res["买入建议"] = "今日放量跟随买入/逢均线低吸"
            df_res["卖出/止损纪律"] = "收盘跌破 10日/20日均线无条件止损"

            if df_res.empty:
                st.error("今日无符合趋势中军·放量起爆特征的标的，管住手")
            else:
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
            df_spot = ak.stock_zh_a_spot_em()
            if df_spot is None or df_spot.empty:
                raise ValueError("全市场行情接口返回为空")

            required = {
                "代码",
                "名称",
                "最新价",
                "市盈率-动态",
                "60日涨跌幅",
                "总市值",
                "年初至今涨跌幅",
                "换手率",
            }
            missing = required - set(df_spot.columns)
            if missing:
                raise ValueError(f"行情缺少字段：{missing}")

            df = df_spot.copy()
            df["代码"] = df["代码"].astype(str).str.replace(".0", "", regex=False).str.zfill(6)
            df["最新价"] = pd.to_numeric(df["最新价"], errors="coerce")
            df["市盈率-动态"] = pd.to_numeric(df["市盈率-动态"], errors="coerce")
            df["60日涨跌幅"] = pd.to_numeric(df["60日涨跌幅"], errors="coerce")
            df["总市值"] = pd.to_numeric(df["总市值"], errors="coerce")
            df["年初至今涨跌幅"] = pd.to_numeric(df["年初至今涨跌幅"], errors="coerce")
            df["换手率"] = pd.to_numeric(df["换手率"], errors="coerce")

            df = df[~df["名称"].astype(str).str.contains("ST", case=False, na=False)]
            df = df[(df["市盈率-动态"] >= 5) & (df["市盈率-动态"] <= 40)]
            df = df[df["总市值"] >= 10000000000]
            df = df[(df["60日涨跌幅"] >= 10) & (df["60日涨跌幅"] <= 40)]
            df = df[(df["换手率"] >= 1.0) & (df["换手率"] <= 8.0)]

            show_cols = ["代码", "名称", "最新价", "市盈率-动态", "60日涨跌幅", "总市值", "年初至今涨跌幅"]
            df_res = (
                df[show_cols]
                .sort_values(by=["60日涨跌幅", "总市值"], ascending=[False, False])
                .reset_index(drop=True)
            )

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
