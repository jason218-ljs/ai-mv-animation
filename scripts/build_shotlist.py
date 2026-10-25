#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_shotlist.py — 歌词时间轴 → 分镜表骨架 + 时间轴校验

两种用法
--------
1) 生成骨架
   python build_shotlist.py lyrics.txt --fps 24 -o 分镜表.md

2) 校验已写好的分镜表
   python build_shotlist.py --check 分镜表.md --fps 24

输入格式（每行一句，支持三种写法）
----------------------------------
   0:21 | 0:24 | 我们都是渺小真实存在
   0:21 | 我们都是渺小真实存在
   0:21-0:24 我们都是渺小真实存在
   0:21 我们都是渺小真实存在

- 只给起始时间时，结束时间自动取下一句的起始时间
- 最后一句必须给结束时间，或用 --duration 指定全片总时长
- 以 # 或 // 开头的行视为注释
"""

import argparse
import re
import sys
from pathlib import Path

TIME_RE = r"(?:\d+:){0,2}\d+(?:\.\d+)?"
TIME_TOKEN_RE = re.compile(rf"^({TIME_RE})$")
LINE_RE = re.compile(rf"^\s*({TIME_RE})\s*(?:[-–—~至]\s*({TIME_RE})\s*)?\s*(.*)$")
LEAD_TIME_RE = re.compile(rf"^({TIME_RE})\s+(.*)$")
SHOT_RE = re.compile(
    rf"^\s*(?:#{{1,6}}\s*)?S(\d+)\s*[｜|:：]\s*({TIME_RE})\s*[–\-—~至]\s*({TIME_RE})"
)

SHORT_S = 2.5
LONG_S = 5.0
SWEET = (3.0, 4.0)


# ---------------------------------------------------------------- 时间处理

def parse_time(s):
    """'0:21' / '00:01:08' / '21.5' → 秒。失败返回 None。"""
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return None
    return None


def fmt_time(t):
    t = max(0.0, round(t, 3))
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - h * 3600 - m * 60
    if abs(s - round(s)) < 1e-6:
        s_str = "{:02d}".format(int(round(s)))
    else:
        s_str = "{:05.2f}".format(s)
    return "{}:{:02d}:{}".format(h, m, s_str) if h else "{}:{}".format(m, s_str)


# ---------------------------------------------------------------- 解析输入

def parse_lyrics(path):
    """返回 [(start, end_or_None, lyric), ...]"""
    rows = []
    text = Path(path).read_text(encoding="utf-8-sig")
    for lineno, line in enumerate(text.splitlines(), 1):
        raw = line.strip()
        if not raw or raw.startswith("#") or raw.startswith("//"):
            continue

        start = end = None
        lyric = ""

        if "|" in raw or "｜" in raw:
            parts = [p.strip() for p in raw.replace("｜", "|").split("|")]
            parts = [p for p in parts if p != ""]
            if len(parts) >= 3:
                start = parse_time(parts[0])
                end = parse_time(parts[1])
                lyric = " ".join(parts[2:]).strip()
            elif len(parts) == 2:
                start = parse_time(parts[0])
                if TIME_TOKEN_RE.match(parts[1]):
                    end = parse_time(parts[1])
                else:
                    lyric = parts[1]
            else:
                sys.stderr.write("[跳过] 第 {} 行格式无法识别：{}\n".format(lineno, raw))
                continue
        else:
            m = LINE_RE.match(raw)
            if not m:
                sys.stderr.write("[跳过] 第 {} 行格式无法识别：{}\n".format(lineno, raw))
                continue
            start = parse_time(m.group(1))
            end = parse_time(m.group(2)) if m.group(2) else None
            lyric = (m.group(3) or "").strip()

        # 兼容「0:21 0:24 歌词」这种空格分隔
        if end is None and lyric:
            m2 = LEAD_TIME_RE.match(lyric)
            if m2:
                end = parse_time(m2.group(1))
                lyric = m2.group(2).strip()

        if start is None:
            sys.stderr.write("[跳过] 第 {} 行起始时间无法解析：{}\n".format(lineno, raw))
            continue
        rows.append((start, end, lyric))
    return rows


def resolve_ends(rows, total_duration=None):
    """补全缺失的结束时间，返回 (shots, errors, warnings)"""
    errors, warnings = [], []
    if not rows:
        return [], ["没有解析到任何歌词行"], []

    shots = []
    for i, (start, end, lyric) in enumerate(rows):
        if end is None:
            if i + 1 < len(rows):
                end = rows[i + 1][0]
            elif total_duration is not None:
                end = total_duration
            else:
                errors.append(
                    "第 {} 句「{}」缺少结束时间，且没有下一句可推——"
                    "请补结束时间或加 --duration".format(i + 1, lyric or "(无歌词)")
                )
                end = start
        shots.append({"start": start, "end": end, "lyric": lyric})

    # 单调性与重叠
    for i, s in enumerate(shots):
        if s["end"] <= s["start"]:
            errors.append(
                "S{:02d} 结束时间 {} 不晚于起始时间 {}".format(
                    i + 1, fmt_time(s["end"]), fmt_time(s["start"])
                )
            )
    for i in range(len(shots) - 1):
        a, b = shots[i], shots[i + 1]
        if b["start"] < a["end"] - 0.001:
            errors.append(
                "S{:02d} 与 S{:02d} 重叠 {:.2f}s（{} > {}）".format(
                    i + 1, i + 2, a["end"] - b["start"], fmt_time(a["end"]), fmt_time(b["start"])
                )
            )
        elif b["start"] > a["end"] + 0.001:
            warnings.append(
                "S{:02d} 与 S{:02d} 之间有 {:.2f}s 空洞".format(
                    i + 1, i + 2, b["start"] - a["end"]
                )
            )

    if total_duration is not None:
        tail = total_duration - shots[-1]["end"]
        if abs(tail) > 0.001:
            warnings.append(
                "最后一句结束于 {}，与总时长 {} 相差 {:+.2f}s".format(
                    fmt_time(shots[-1]["end"]), fmt_time(total_duration), tail
                )
            )
    return shots, errors, warnings


# ---------------------------------------------------------------- 生成骨架

SHOT_TMPL = """### S{n:02d}｜{t0}–{t1}｜{lyric}

- **画面**：
- **运镜**：
- **转场**：
- **卡点**：
- **道具档位**：
- **尾帧**：

"""


def build(shots, fps, title, total_duration):
    out = []
    out.append("# 分镜表｜{}".format(title))
    out.append("")
    out.append("| 项 | 值 |")
    out.append("|---|---|")
    out.append("| 歌曲 | {} |".format(title))
    out.append("| 成片时长 | {}（{:.2f}s） |".format(fmt_time(shots[-1]["end"]), shots[-1]["end"]))
    out.append("| 歌词句数 | {} |".format(len(shots)))
    out.append("| 镜头数 | {} |".format(len(shots)))
    out.append("| 帧率 | {} fps |".format(fps))
    out.append("")
    out.append("> 每镜固定五要素：画面 / 运镜 / 转场 / 卡点，另加道具档位与尾帧。")
    out.append("> 一句一镜，句内不切；若必须拆镜，在此说明拆点。")
    out.append("")
    out.append("---")
    out.append("")
    for i, s in enumerate(shots, 1):
        out.append(
            SHOT_TMPL.format(
                n=i,
                t0=fmt_time(s["start"]),
                t1=fmt_time(s["end"]),
                lyric=s["lyric"] or "（待填）",
            ).rstrip()
        )
        out.append("")
    out.append("---")
    out.append("")
    out.append("## 时间轴速查")
    out.append("")
    out.append("| 镜号 | 起 | 止 | 时长 | 帧数 | 状态 |")
    out.append("|---|---|---|---|---|---|")
    for i, s in enumerate(shots, 1):
        d = s["end"] - s["start"]
        out.append(
            "| S{:02d} | {} | {} | {:.2f}s | {} | {} |".format(
                i, fmt_time(s["start"]), fmt_time(s["end"]), d,
                int(round(d * fps)), duration_flag(d)
            )
        )
    return "\n".join(out) + "\n"


def duration_flag(d):
    if d < SHORT_S:
        return "偏短"
    if d > LONG_S:
        return "偏长"
    if SWEET[0] <= d <= SWEET[1]:
        return "舒适"
    return "可用"


# ---------------------------------------------------------------- 校验模式

REQUIRED = ["画面", "运镜", "转场", "卡点"]


def check(path, fps):
    text = Path(path).read_text(encoding="utf-8-sig")
    lines = text.splitlines()

    marks = []
    for idx, line in enumerate(lines):
        m = SHOT_RE.match(line)
        if m:
            marks.append((idx, int(m.group(1)), parse_time(m.group(2)), parse_time(m.group(3)), line.strip()))

    if not marks:
        print("[错误] 没有找到任何以 `S01｜0:00–0:03` 格式开头的镜头行。")
        return 1

    errors, warnings = [], []

    # 1. 时间码连续
    for i in range(len(marks) - 1):
        _, n1, _, e1, _ = marks[i]
        _, n2, s2, _, _ = marks[i + 1]
        if s2 < e1 - 0.001:
            errors.append("S{:02d} 与 S{:02d} 重叠 {:.2f}s".format(n1, n2, e1 - s2))
        elif s2 > e1 + 0.001:
            warnings.append("S{:02d} 与 S{:02d} 之间有 {:.2f}s 空洞".format(n1, n2, s2 - e1))

    # 2. 镜号连续
    for i in range(len(marks) - 1):
        if marks[i + 1][1] != marks[i][1] + 1:
            errors.append("镜号不连续：S{:02d} 之后是 S{:02d}".format(marks[i][1], marks[i + 1][1]))

    # 3. 每镜五要素 + 时长
    for k, (idx, n, s, e, raw) in enumerate(marks):
        end_idx = marks[k + 1][0] if k + 1 < len(marks) else len(lines)
        block = "\n".join(lines[idx:end_idx])
        for field in REQUIRED:
            if field not in block:
                errors.append("S{:02d} 缺少「{}」字段".format(n, field))
        d = e - s
        if d < SHORT_S:
            warnings.append("S{:02d} 时长 {:.2f}s，偏短".format(n, d))
        elif d > LONG_S:
            warnings.append("S{:02d} 时长 {:.2f}s，偏长，考虑拆分".format(n, d))

    total = marks[-1][3] - marks[0][2]
    print("=" * 56)
    print("分镜表校验：{}".format(Path(path).name))
    print("=" * 56)
    print("镜头数：{}".format(len(marks)))
    print("总时长：{}（{:.2f}s / {} 帧 @ {}fps）".format(fmt_time(total), total, int(round(total * fps)), fps))
    print("平均镜长：{:.2f}s".format(total / len(marks)))
    print("-" * 56)
    for i, (_, n, s, e, _) in enumerate(marks):
        d = e - s
        print("S{:02d}  {} → {}  {:.2f}s  {:>3} 帧  {}".format(
            n, fmt_time(s), fmt_time(e), d, int(round(d * fps)), duration_flag(d)))
    print("-" * 56)
    if errors:
        print("错误 {} 项：".format(len(errors)))
        for x in errors:
            print("  ✗ " + x)
    if warnings:
        print("提醒 {} 项：".format(len(warnings)))
        for x in warnings:
            print("  ! " + x)
    if not errors and not warnings:
        print("全部通过。")
    print("=" * 56)
    return 1 if errors else 0


# ---------------------------------------------------------------- 主入口

def main():
    ap = argparse.ArgumentParser(
        description="歌词时间轴 → 分镜表骨架 + 时间轴校验",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("input", nargs="?", help="歌词时间轴文件（生成模式），或分镜表 .md（配合 --check）")
    ap.add_argument("--check", action="store_true", help="校验模式：检查已写好的分镜表")
    ap.add_argument("--fps", type=int, default=24, help="帧率，默认 24")
    ap.add_argument("--duration", help="全片总时长，如 0:47；用于补全最后一句的结束时间")
    ap.add_argument("--title", default="未命名", help="歌曲名，用于标题")
    ap.add_argument("-o", "--out", help="输出 .md 路径，默认打印到 stdout")
    args = ap.parse_args()

    if not args.input:
        ap.print_help()
        return 2

    if args.check:
        return check(args.input, args.fps)

    total = parse_time(args.duration) if args.duration else None
    rows = parse_lyrics(args.input)
    shots, errors, warnings = resolve_ends(rows, total)

    if errors:
        print("发现 {} 项错误，已停止：".format(len(errors)), file=sys.stderr)
        for x in errors:
            print("  ✗ " + x, file=sys.stderr)
        return 1

    md = build(shots, args.fps, args.title, total)

    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print("已写入：{}".format(args.out))
    else:
        print(md)

    # 控制台摘要
    print("-" * 56)
    print("共 {} 镜，总时长 {}".format(len(shots), fmt_time(shots[-1]["end"])))
    for i, s in enumerate(shots, 1):
        d = s["end"] - s["start"]
        flag = duration_flag(d)
        if flag in ("偏短", "偏长"):
            print("  ! S{:02d} {:.2f}s {}｜{}".format(i, d, flag, s["lyric"] or "(无歌词)"))
    for x in warnings:
        print("  ! " + x)
    return 0


if __name__ == "__main__":
    sys.exit(main())
