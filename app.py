# -*- coding: utf-8 -*-
"""
小牛牛量化狙击系统 · Streamlit 网页前端
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
st.set_page_config(page_title="小牛牛量化狙击系统", layout="wide", page_icon="🤖")

st.sidebar.title("🤖 Stock-Bot 系统")
st.sidebar.markdown("---")
st.sidebar.subheader("🔐 VIP 用户验证")
activation_code = st.sidebar.text_input("请输入激活码解锁核心功能", type="password")

# 用户数据库：激活码 -> 到期日期
VIP_DATABASE = {
    "niuniu888": "2027-12-31",
    "test001": "2026-9-30",
}

if activation_code not in VIP_DATABASE:
    st.warning("⚠️ 欢迎来到 Stock-Bot 量化系统。请输入有效的 VIP 激活码以解锁选股策略。")
    st.sidebar.error("无效激活码")
    st.sidebar.info("购买激活码请联系管理员微信。")
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


st.title("🎯 Stock-Bot 核心打板/竞价系统")
st.markdown("用绝对的理性，对抗人性的贪婪与恐惧。")
st.markdown("---")


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
    show = pd.DataFrame(
        {
            "股票代码": picked["代码"].astype(str),
            "股票名称": picked["名称"].astype(str),
            "昨日连板": picked["连板数"].map(picker.format_lianban),
            "昨日换手(%)": pd.to_numeric(picked["换手率"], errors="coerce").round(2),
            "昨收": pd.to_numeric(picked["昨收"], errors="coerce").round(2),
            "今开": pd.to_numeric(picked["今开"], errors="coerce").round(2),
            "今日高开(%)": (pd.to_numeric(picked["高开幅度"], errors="coerce") * 100).round(2),
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
    with st.spinner("Stock-Bot 正在高速连接交易所服务器..."):
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

    with st.spinner("Stock-Bot 正在全网扫描超预期『弱转强』标的（今开未刷新会自动重试）..."):
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
                    st.success(f"🎉 狙击成功！Stock-Bot 发现 {len(picked)} 只符合黄金买点的标的！")
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
