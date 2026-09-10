# -*- coding: utf-8 -*-
"""
A股超短线「弱转强」竞价选股脚本
================================
适合完全不会编程的新手：装好依赖后，双击「明天竞价自动选股.bat」即可。

选股逻辑（全部条件必须同时满足）：
1. 昨日复盘：上一个交易日涨停
2. 昨日换手率在 10% ~ 30% 之间（有成交、但没有过度炒作）
3. 剔除 ST、科创板(688/689)、创业板(300/301)、北交所
4. 今日集合竞价结束后，高开幅度 > 3% 且 < 9%
   （高开说明资金回流；不到 9% 是为了排除一字涨停买不到）

数据来源（东方财富，经 akshare 封装）：
- 涨停池：ak.stock_zt_pool_em(date=昨日)
- 全市场快照：ak.stock_zh_a_spot_em()   ← 一次拉取，绝不逐只查询

用法：
    python auction_picker.py          # 等到今天/明天 09:25 自动跑
    python auction_picker.py --now    # 立刻跑一遍（方便你现在试）

声明：本脚本仅供学习研究，不构成任何投资建议。股市有风险，入市需谨慎。
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# 0. 依赖检查：如果没装 akshare / pandas，自动用清华镜像安装
# ---------------------------------------------------------------------------
def _ensure_packages() -> None:
    """检测必要的第三方库，缺失时自动安装，避免新手卡在 pip。"""
    missing = []
    try:
        import pandas  # noqa: F401
    except ImportError:
        missing.append("pandas")
    try:
        import akshare  # noqa: F401
    except ImportError:
        missing.append("akshare")

    if not missing:
        return

    print("检测到缺少依赖：{}，正在自动安装（清华镜像）...".format("、".join(missing)))
    import subprocess

    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        *missing,
        "-i",
        "https://pypi.tuna.tsinghua.edu.cn/simple",
    ]
    try:
        subprocess.check_call(cmd)
    except Exception as exc:
        print("自动安装失败：{}".format(exc))
        print("请手动打开命令行，进入本文件夹后执行：")
        print("    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple")
        sys.exit(1)


_ensure_packages()

import akshare as ak  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402


# ---------------------------------------------------------------------------
# 1. 可调参数（以后想改条件，只改这里即可）
# ---------------------------------------------------------------------------
TURNOVER_MIN = 10.0          # 昨日换手率下限（%）
TURNOVER_MAX = 30.0          # 昨日换手率上限（%）
GAP_MIN = 0.03               # 今日高开下限（3%）
GAP_MAX = 0.09               # 今日高开上限（9%，排除接近一字板）
AUCTION_HOUR = 9             # 集合竞价结束：小时
AUCTION_MINUTE = 25          # 集合竞价结束：分钟
AUCTION_DELAY_SECONDS = 12   # 9:25 后再多等几秒，让行情源把「今开」刷新出来
MAX_RETRIES = 5              # 网络失败最多重试次数
RETRY_BASE_SECONDS = 2.0     # 第一次失败后等待秒数，之后按 2 倍递增
RESULT_DIR = "results"       # 结果 CSV 保存目录


# ---------------------------------------------------------------------------
# 2. 终端显示：Windows 中文乱码 + 彩色表格
# ---------------------------------------------------------------------------
def _setup_console() -> None:
    """让 Windows 终端能正确显示中文，并开启 ANSI 彩色。"""
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if sys.platform == "win32":
        try:
            # 切到 UTF-8 代码页，避免中文变成乱码
            os.system("chcp 65001 >nul")
        except Exception:
            pass
        try:
            # 开启 Windows 10+ 的虚拟终端彩色
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_uint()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass
        # Python 3.7+ 可直接重配标准输出编码
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


_setup_console()

# ANSI 颜色（不支持时也不会报错，只是没有颜色）
RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
GRAY = "\033[90m"


def log_info(msg: str) -> None:
    print("{}[信息]{} {}".format(CYAN, RESET, msg), flush=True)


def log_ok(msg: str) -> None:
    print("{}[成功]{} {}".format(GREEN, RESET, msg), flush=True)


def log_warn(msg: str) -> None:
    print("{}[提示]{} {}".format(YELLOW, RESET, msg), flush=True)


def log_err(msg: str) -> None:
    print("{}[错误]{} {}".format(RED, RESET, msg), flush=True)


# ---------------------------------------------------------------------------
# 3. 带指数退避的重试：抓取失败就等一会儿再试，避免偶发网络抖动
# ---------------------------------------------------------------------------
def retry_call(
    func: Callable,
    desc: str,
    max_retries: int = MAX_RETRIES,
    base_seconds: float = RETRY_BASE_SECONDS,
    validator: Optional[Callable] = None,
):
    """
    调用 func，失败或数据不合法时自动重试。

    validator：可选的校验函数。返回 False / 抛异常 都视为失败。
    等待时间：2秒、4秒、8秒、16秒……（指数退避），降低被限流的概率。
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            log_info("正在{}（第 {}/{} 次）...".format(desc, attempt, max_retries))
            result = func()

            # 空表视为失败，触发重试
            if result is None:
                raise ValueError("接口返回了空结果")
            if isinstance(result, pd.DataFrame) and result.empty:
                raise ValueError("接口返回了空表格")

            if validator is not None and not validator(result):
                raise ValueError("数据校验未通过（可能今开价尚未刷新）")

            if isinstance(result, pd.DataFrame):
                log_ok("{}完成，共 {} 行。".format(desc, len(result)))
            else:
                log_ok("{}完成：{}".format(desc, result))
            return result

        except Exception as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            wait = base_seconds * (2 ** (attempt - 1))
            log_warn("{}失败：{}。{:.0f} 秒后重试...".format(desc, exc, wait))
            time.sleep(wait)

    raise RuntimeError("{}连续失败 {} 次。最后一次原因：{}".format(desc, max_retries, last_error))


# ---------------------------------------------------------------------------
# 4. 交易日历：算出「上一个交易日」（自动跳过周末、节假日）
# ---------------------------------------------------------------------------
def _normalize_trade_dates(raw: pd.DataFrame) -> list:
    """把交易日历表转成 datetime.date 列表。"""
    col = "trade_date" if "trade_date" in raw.columns else raw.columns[0]
    dates = pd.to_datetime(raw[col], errors="coerce").dropna().dt.date.tolist()
    return sorted(dates)


def get_previous_trade_date(today=None):
    """
    返回上一个 A 股交易日，格式 YYYYMMDD。

    优先用新浪交易日历；如果日历接口挂了，就倒着试最近 15 天的涨停池，
    哪个日期能查出数据，就算哪天是上一交易日。
    """
    if today is None:
        today = datetime.now().date()

    def _from_calendar():
        cal = ak.tool_trade_date_hist_sina()
        dates = _normalize_trade_dates(cal)
        prev = [d for d in dates if d < today]
        if not prev:
            raise ValueError("交易日历里找不到今天之前的日期")
        return prev[-1]

    try:
        prev_date = retry_call(_from_calendar, desc="获取A股交易日历")
        return prev_date.strftime("%Y%m%d"), prev_date
    except Exception as exc:
        log_warn("交易日历接口不可用（{}），改用涨停池倒推上一交易日。".format(exc))

    # 兜底：从昨天开始往前试，最多 15 天
    for i in range(1, 16):
        candidate = today - timedelta(days=i)
        date_str = candidate.strftime("%Y%m%d")
        try:
            df = ak.stock_zt_pool_em(date=date_str)
            if df is not None and not df.empty:
                log_ok("倒推得到上一交易日：{}".format(date_str))
                return date_str, candidate
        except Exception:
            continue

    raise RuntimeError("无法确定上一交易日，请检查网络后重试。")


# ---------------------------------------------------------------------------
# 5. 昨日涨停池 + 换手率/板块过滤
# ---------------------------------------------------------------------------
def _normalize_code_series(series: pd.Series) -> pd.Series:
    """股票代码统一成 6 位字符串，避免 000001 被当成数字 1。"""
    s = series.astype(str).str.replace(".0", "", regex=False)
    s = s.str.replace(r"^(sh|sz|bj)", "", regex=True, case=False)
    return s.str.zfill(6)


def fetch_yesterday_limit_up(date_str: str) -> pd.DataFrame:
    """一次性拉取指定日期的全部涨停股（东方财富涨停池）。"""

    def _call():
        df = ak.stock_zt_pool_em(date=date_str)
        required = {"代码", "名称", "换手率"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError("涨停池缺少字段：{}".format(missing))
        return df

    return retry_call(_call, desc="获取{}涨停股池".format(date_str))


def is_excluded_stock(code: str, name: str) -> bool:
    """
    是否需要剔除：
    - ST / *ST / SST（风险警示股，超短线不碰）
    - 科创板 688、689（20cm 板块，规则不同）
    - 创业板 300、301（20cm 板块；301 是后来扩容的创业板号段）
    - 北交所 8 / 4 / 92 开头（流动性差，不适合这个策略）
    """
    code = str(code).zfill(6)
    name = str(name)

    if "ST" in name.upper():
        return True
    if code.startswith(("688", "689")):
        return True
    if code.startswith(("300", "301")):
        return True
    if code.startswith(("8", "4", "92")):
        return True
    return False


def filter_yesterday_candidates(zt_df: pd.DataFrame) -> pd.DataFrame:
    """从昨日涨停股里筛出：换手率 10%~30%，且不是 ST/科创/创业/北交所。"""
    df = zt_df.copy()
    df["代码"] = _normalize_code_series(df["代码"])
    df["换手率"] = pd.to_numeric(df["换手率"], errors="coerce")

    if "连板数" in df.columns:
        df["连板数"] = pd.to_numeric(df["连板数"], errors="coerce").fillna(1).astype(int)
    else:
        df["连板数"] = 1

    before = len(df)
    df = df[(df["换手率"] >= TURNOVER_MIN) & (df["换手率"] <= TURNOVER_MAX)]
    log_info("换手率 {}%~{}% 过滤：{} → {} 只。".format(TURNOVER_MIN, TURNOVER_MAX, before, len(df)))

    before = len(df)
    mask = df.apply(lambda row: not is_excluded_stock(row["代码"], row["名称"]), axis=1)
    df = df[mask]
    log_info("剔除 ST / 科创板 / 创业板 / 北交所：{} → {} 只。".format(before, len(df)))

    keep_cols = [c for c in ["代码", "名称", "换手率", "连板数", "所属行业"] if c in df.columns]
    return df[keep_cols].reset_index(drop=True)


# ---------------------------------------------------------------------------
# 6. 今日全市场快照（重点：一次拉取，绝不循环查单只股票）
# ---------------------------------------------------------------------------
def _spot_has_valid_open(df: pd.DataFrame) -> bool:
    """
    9:25 刚过时，行情源可能还没把「今开」写进去。
    如果超过一半股票的今开是空的或 0，就认为数据还没就绪，需要重试。
    """
    if "今开" not in df.columns:
        return False
    open_px = pd.to_numeric(df["今开"], errors="coerce")
    valid_ratio = float((open_px > 0).mean())
    return valid_ratio >= 0.5


# 东方财富全市场列表接口（与 ak.stock_zh_a_spot_em 同源）
# 收盘后实时 push2 经常直接断开，延时 push2delay 仍可一次拉全市场。
_EM_SPOT_URLS = (
    "https://82.push2.eastmoney.com/api/qt/clist/get",
    "https://88.push2.eastmoney.com/api/qt/clist/get",
    "https://82.push2delay.eastmoney.com/api/qt/clist/get",
    "https://72.push2delay.eastmoney.com/api/qt/clist/get",
    "https://push2delay.eastmoney.com/api/qt/clist/get",
)
_EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/center/gridlist.html",
    "Accept": "*/*",
}
_EM_FS = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"


def _fetch_eastmoney_spot_robust() -> pd.DataFrame:
    """
    用东方财富「全市场列表」一次拉完所有 A 股的今开/昨收。

    仍然是整表快照，绝不是循环查单只股票。
    每页失败会单独重试，换线路继续，避免整次抓取因某一页网络抖动而报废。
    """
    params = {
        "pn": 1,
        "pz": 100,
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": _EM_FS,
        "fields": "f12,f14,f17,f18",
    }
    last_error: Optional[Exception] = None

    for url in _EM_SPOT_URLS:
        try:
            session = requests.Session()
            session.headers.update(_EM_HEADERS)
            first = None
            for attempt in range(1, 4):
                try:
                    resp = session.get(url, params=params, timeout=20)
                    resp.raise_for_status()
                    first = resp.json()
                    break
                except Exception as exc:
                    last_error = exc
                    time.sleep(0.8 * attempt)
            if not first or not first.get("data") or not first["data"].get("diff"):
                continue

            total = int(first["data"]["total"])
            rows = list(first["data"]["diff"])
            pages = max(math.ceil(total / 100), 1)
            log_info("已连通 {} ，共 {} 只，分 {} 页拉取（整表快照）。".format(url.split("//")[1].split("/")[0], total, pages))

            for page in range(2, pages + 1):
                page_params = dict(params)
                page_params["pn"] = page
                page_ok = False
                for attempt in range(1, 4):
                    try:
                        resp = session.get(url, params=page_params, timeout=20)
                        resp.raise_for_status()
                        chunk = resp.json()["data"]["diff"]
                        rows.extend(chunk)
                        page_ok = True
                        break
                    except Exception as exc:
                        last_error = exc
                        time.sleep(0.6 * attempt)
                if not page_ok:
                    raise RuntimeError("第 {} 页连续失败：{}".format(page, last_error))
                time.sleep(0.05)

            df = pd.DataFrame(rows)
            df = df.rename(columns={"f12": "代码", "f14": "名称", "f17": "今开", "f18": "昨收"})
            return df[["代码", "名称", "今开", "昨收"]]
        except Exception as exc:
            last_error = exc
            log_warn("线路 {} 失败：{}，尝试下一条。".format(url, exc))
            continue

    raise RuntimeError("东方财富全市场快照全部线路失败：{}".format(last_error))


def fetch_today_spot() -> pd.DataFrame:
    """
    一次性获取沪深京 A 股全部实时行情。

    优先使用你指定的 ak.stock_zh_a_spot_em()（开盘时段最稳）。
    收盘后该实时接口经常直接断开，这时自动改走同一家的延时全市场快照，
    仍然一次拉全表，绝不循环查询单只股票。
    """

    def _official():
        df = ak.stock_zh_a_spot_em()
        required = {"代码", "名称", "今开", "昨收"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError("行情快照缺少字段：{}".format(missing))
        return df

    now = datetime.now()
    hhmm = now.hour * 100 + now.minute
    in_session = 915 <= hhmm <= 1505

    if in_session:
        # 开盘时段：优先走你指定的官方实时接口
        attempt_list = [
            (_official, "一次性获取全市场实时行情(ak.stock_zh_a_spot_em)"),
            (_fetch_eastmoney_spot_robust, "一次性获取全市场行情快照（东方财富列表）"),
        ]
    else:
        # 收盘后实时 push2 经常断开，先走同一家的延时全市场快照
        attempt_list = [
            (_fetch_eastmoney_spot_robust, "一次性获取全市场行情快照（东方财富列表）"),
            (_official, "一次性获取全市场实时行情(ak.stock_zh_a_spot_em)"),
        ]

    last_error = None
    for func, desc in attempt_list:
        try:
            return retry_call(
                func,
                desc=desc,
                max_retries=3,
                base_seconds=1.5,
                validator=_spot_has_valid_open,
            )
        except Exception as exc:
            last_error = exc
            log_warn("{} 暂不可用：{}".format(desc, exc))
            log_warn("切换备用全市场快照线路（仍然一次拉完全表，不查单只）。")

    raise RuntimeError("全市场快照获取失败：{}".format(last_error))


# ---------------------------------------------------------------------------
# 7. 弱转强匹配：昨日候选 ∩ 今日高开 3%~9%
# ---------------------------------------------------------------------------
def match_weak_to_strong(candidates: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    """
    用昨日筛选出的股票代码，去今日全市场快照里取今开、昨收。
    高开幅度 = (今开 - 昨收) / 昨收
    只保留 3% < 高开 < 9% 的股票。
    """
    left = candidates.copy()
    left["代码"] = _normalize_code_series(left["代码"])

    right = spot.copy()
    right["代码"] = _normalize_code_series(right["代码"])
    right["今开"] = pd.to_numeric(right["今开"], errors="coerce")
    right["昨收"] = pd.to_numeric(right["昨收"], errors="coerce")

    merged = left.merge(right[["代码", "今开", "昨收"]], on="代码", how="left")

    unmatched = merged["今开"].isna() | (merged["今开"] <= 0) | (merged["昨收"] <= 0)
    if unmatched.any():
        names = "、".join(merged.loc[unmatched, "名称"].astype(str).tolist()[:8])
        log_warn("有 {} 只未能匹配到有效今开（停牌或数据未出），例如：{}".format(int(unmatched.sum()), names))

    merged = merged[~unmatched].copy()
    merged["高开幅度"] = (merged["今开"] - merged["昨收"]) / merged["昨收"]

    before = len(merged)
    picked = merged[(merged["高开幅度"] > GAP_MIN) & (merged["高开幅度"] < GAP_MAX)].copy()
    log_info("高开幅度 {:.0f}% ~ {:.0f}% 过滤：{} → {} 只。".format(GAP_MIN * 100, GAP_MAX * 100, before, len(picked)))

    # 连板多的排前面；相同连板再按高开幅度从高到低
    picked = picked.sort_values(by=["连板数", "高开幅度"], ascending=[False, False])
    return picked.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 8. 漂亮地打印表格（按中文实际显示宽度对齐）
# ---------------------------------------------------------------------------
def _display_width(text: str) -> int:
    """中文占 2 列，英文/数字占 1 列，用来把表格对齐。"""
    width = 0
    for ch in str(text):
        width += 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
    return width


def _pad(text: str, width: int, align: str = "left") -> str:
    text = str(text)
    pad_len = max(width - _display_width(text), 0)
    if align == "right":
        return " " * pad_len + text
    if align == "center":
        left = pad_len // 2
        return " " * left + text + " " * (pad_len - left)
    return text + " " * pad_len


def format_lianban(n) -> str:
    """1 显示为首板，2 显示为 2连板，缺失显示为 -。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "-"
    if n <= 0:
        return "-"
    if n == 1:
        return "首板"
    return "{}连板".format(n)


def print_result_table(df: pd.DataFrame, prev_date_str: str) -> None:
    """在终端打印对齐的结果表。"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = "弱转强 · 竞价选股结果"
    print()
    print(BOLD + CYAN + "=" * 72 + RESET)
    print(BOLD + CYAN + _pad(title, 72, "center") + RESET)
    print(CYAN + "  上一交易日 {}    运行时间 {}".format(prev_date_str, now_str) + RESET)
    print(CYAN + "=" * 72 + RESET)

    if df is None or df.empty:
        print()
        log_warn("今天没有同时满足全部条件的股票。")
        log_warn("可能原因：昨日涨停里换手率合适的不多，或今天高开没落在 3%~9%。")
        print()
        return

    headers = ["序号", "代码", "名称", "昨日连板", "昨日换手", "昨收", "今开", "今日高开"]
    rows = []
    for i, row in df.iterrows():
        rows.append(
            [
                str(i + 1),
                str(row["代码"]),
                str(row["名称"]),
                format_lianban(row.get("连板数")),
                "{:.1f}%".format(float(row["换手率"])),
                "{:.2f}".format(float(row["昨收"])),
                "{:.2f}".format(float(row["今开"])),
                "{:+.2f}%".format(float(row["高开幅度"]) * 100),
            ]
        )

    widths = [_display_width(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _display_width(cell))
    widths = [w + 2 for w in widths]

    def _hline(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * w for w in widths) + right

    def _fmt_row(cells, color: str = "") -> str:
        parts = []
        aligns = ["center", "center", "left", "center", "right", "right", "right", "right"]
        for i, cell in enumerate(cells):
            parts.append(_pad(cell, widths[i], aligns[i]))
        body = "│".join(parts)
        return "{}│{}│{}".format(color, body, RESET if color else "")

    print(_hline("┌", "┬", "┐"))
    print(_fmt_row(headers, BOLD + CYAN))
    print(_hline("├", "┼", "┤"))
    for row in rows:
        print(_fmt_row(row, GREEN))
    print(_hline("└", "┴", "┘"))
    print()
    log_ok("共选出 {} 只弱转强候选。以上仅为数据筛选，不构成买卖建议。".format(len(df)))
    print()


def save_csv(df: pd.DataFrame, prev_date_str: str) -> Optional[str]:
    """把结果存成 CSV，方便用 Excel 打开。没有结果就不存。"""
    if df is None or df.empty:
        return None

    os.makedirs(RESULT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(RESULT_DIR, "弱转强_{}_{}.csv".format(prev_date_str, stamp))

    out = df.copy()
    out["昨日连板"] = out["连板数"].map(format_lianban)
    out["昨日换手率"] = out["换手率"].map(lambda x: "{:.2f}%".format(float(x)))
    out["今日高开幅度"] = out["高开幅度"].map(lambda x: "{:.2f}%".format(float(x) * 100))
    cols = ["代码", "名称", "昨日连板", "昨日换手率", "昨收", "今开", "今日高开幅度"]
    if "所属行业" in out.columns:
        cols.append("所属行业")
    out[cols].to_csv(path, index=False, encoding="utf-8-sig")
    return path


# ---------------------------------------------------------------------------
# 9. 等到集合竞价结束（默认 09:25:12）
# ---------------------------------------------------------------------------
def wait_until_auction_end() -> None:
    """
    等到最近一次「该跑脚本」的时间点：
    - 现在还没到今天 9:25 → 等到今天 9:25
    - 已经过了今天 9:25，但还在交易时段（15:00 前）→ 马上跑（你来晚了也还能看）
    - 已经收盘 → 等到明天 9:25（适合今晚挂机，明天早上自动出结果）
    """
    now = datetime.now()
    today_target = now.replace(
        hour=AUCTION_HOUR,
        minute=AUCTION_MINUTE,
        second=AUCTION_DELAY_SECONDS,
        microsecond=0,
    )

    if now < today_target:
        target = today_target
        log_info("现在是 {}，将等到今天 {} 再选股。".format(now.strftime("%H:%M:%S"), target.strftime("%H:%M:%S")))
    elif now.hour < 15:
        log_warn("今天集合竞价已经结束，立即按当前行情运行（今开价仍然有效）。")
        return
    else:
        target = today_target + timedelta(days=1)
        log_info("今天已经收盘。将等到 {} 再选股，请保持电脑开机、不要关这个窗口。".format(target.strftime("%Y-%m-%d %H:%M:%S")))

    while True:
        now = datetime.now()
        remain = (target - now).total_seconds()
        if remain <= 0:
            print()
            log_ok("时间到，开始选股。")
            return

        hours, rem = divmod(int(remain), 3600)
        minutes, seconds = divmod(rem, 60)
        print(
            "\r{}[等待]{} 距离开跑还有 {:02d}:{:02d}:{:02d}   ".format(
                GRAY, RESET, hours, minutes, seconds
            ),
            end="",
            flush=True,
        )
        time.sleep(1 if remain < 120 else 5)


# ---------------------------------------------------------------------------
# 10. 主流程
# ---------------------------------------------------------------------------
def run_strategy() -> pd.DataFrame:
    print()
    print(BOLD + "A股超短线 · 弱转强竞价选股" + RESET)
    print(GRAY + "昨日涨停 + 换手 10%~30% + 今日高开 3%~9%  |  全市场一次快照，不逐只查询" + RESET)
    print()

    date_str, prev_date = get_previous_trade_date()
    log_info("上一交易日：{}（{}）".format(date_str, prev_date.strftime("%Y年%m月%d日")))

    zt_df = fetch_yesterday_limit_up(date_str)
    log_info("昨日涨停股一共 {} 只。".format(len(zt_df)))

    candidates = filter_yesterday_candidates(zt_df)
    if candidates.empty:
        log_warn("过滤后没有候选股，后面的实时匹配无需进行。")
        print_result_table(candidates, date_str)
        return candidates

    preview = "、".join("{}({})".format(r["名称"], r["代码"]) for _, r in candidates.head(12).iterrows())
    log_ok("进入今日竞价观察的候选：{} 只。{}".format(len(candidates), preview))

    spot = fetch_today_spot()
    picked = match_weak_to_strong(candidates, spot)

    print_result_table(picked, date_str)

    csv_path = save_csv(picked, date_str)
    if csv_path:
        log_ok("结果已保存到：{}".format(os.path.abspath(csv_path)))
        log_info("可以用 Excel 打开这份 CSV 文件。")

    return picked


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A股弱转强竞价选股")
    parser.add_argument(
        "--now",
        action="store_true",
        help="立刻运行，不等 9:25（用来测试，或盘中补跑）",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if not args.now:
            wait_until_auction_end()
        else:
            log_info("已指定 --now，立即开始选股（不等待 9:25）。")
        run_strategy()
    except KeyboardInterrupt:
        print()
        log_warn("已手动停止。")
        sys.exit(0)
    except Exception as exc:
        log_err("选股过程出错：{}".format(exc))
        log_err("请检查网络是否正常，或稍后再试。")
        sys.exit(1)


if __name__ == "__main__":
    main()
