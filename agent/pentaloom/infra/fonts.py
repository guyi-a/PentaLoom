"""CJK 字体探测.

启动时跑一次 (functools.cache), 把"系统装了哪些 LLM 认得的 CJK 字体" 注进 system
prompt + 给 fix_fonts 默认值用. 缺字体时 LLM 看到空清单, 知道该调
install_noto_sans_sc.

跨平台:
- macOS: system_profiler SPFontsDataType -json (3-5s 一次, cache 后 0)
  注: macOS 10.11+ 起 PingFang / STHeiti / HiraginoSansGB 的 family 字段是中文
  ("苹方-简" / "黑体-简" / "冬青黑体简体中文"), LLM 不认; 我们走 typeface 的
  PostScript name (e.g. "PingFangSC-Regular") 反查 latin family.
- Linux: fc-list :lang=zh -f "%{family}\\n"
- Windows: 简化, 只识常见 CJK 字体文件名 (msyh.ttc / simhei.ttf / NotoSansSC*.otf)

环境变量 PENTALOOM_FONTS_FORCE_EMPTY=1 强制返 [] — 给 e2e 测 install_noto_sans_sc 路径用.
"""

from __future__ import annotations

import functools
import json
import os
import platform
import subprocess
from pathlib import Path

from loguru import logger

# 优先级 = LLM 写 east_asian_name 时最不容易踩坑的字体名优先.
# 取交集时按这顺序输出, fix_fonts 默认值 = 列表 [0].
_CJK_PREFERENCE: tuple[str, ...] = (
    # 跨平台 — 首选
    "Noto Sans SC", "Noto Sans CJK SC",
    "Noto Serif SC", "Noto Serif CJK SC",
    "Source Han Sans SC", "Source Han Sans CN", "Source Han Sans",
    "Source Han Serif SC", "Source Han Serif",
    # macOS native
    "PingFang SC", "PingFang TC", "PingFang HK",
    "Hiragino Sans GB",
    "Heiti SC", "Heiti TC", "STHeiti", "STSong", "STKaiti", "STFangsong",
    # Windows native
    "Microsoft YaHei", "Microsoft YaHei UI",
    "SimHei", "SimSun", "NSimSun", "DengXian", "FangSong", "KaiTi",
    # Linux native
    "WenQuanYi Micro Hei", "WenQuanYi Zen Hei",
    # JP / KR fallback (能渲中文但效果差, 排末尾)
    "Noto Sans CJK TC", "Noto Sans CJK JP", "Noto Sans CJK KR",
    "Noto Sans TC", "Noto Sans JP", "Noto Sans KR",
    "Hiragino Kaku Gothic Pro", "Yu Gothic", "Meiryo",
    "MS Gothic", "MS Mincho",
    "Apple SD Gothic Neo", "Malgun Gothic",
)

# PostScript 名前缀 (-Regular / -Medium / -Light 之类后缀剥掉) → 偏好清单里的 latin family.
# 主要给 macOS 用 (system_profiler 给的 family 是中文, postscript 是 latin).
# Linux/Windows 走各自原生路径, 通常不需要这个映射.
_POSTSCRIPT_TO_FAMILY: dict[str, str] = {
    "PingFangSC": "PingFang SC",
    "PingFangTC": "PingFang TC",
    "PingFangHK": "PingFang HK",
    "STHeitiSC": "Heiti SC",
    "STHeitiTC": "Heiti TC",
    "STHeiti": "STHeiti",
    "STSong": "STSong",
    "STKaiti": "STKaiti",
    "STFangsong": "STFangsong",
    "HiraginoSansGB": "Hiragino Sans GB",
    "HiraginoKakuGothicPro": "Hiragino Kaku Gothic Pro",
    "HiraginoKakuGothicProN": "Hiragino Kaku Gothic Pro",
    "NotoSansSC": "Noto Sans SC",
    "NotoSansTC": "Noto Sans TC",
    "NotoSansJP": "Noto Sans JP",
    "NotoSansKR": "Noto Sans KR",
    "NotoSansCJKsc": "Noto Sans CJK SC",
    "NotoSansCJKtc": "Noto Sans CJK TC",
    "NotoSansCJKjp": "Noto Sans CJK JP",
    "NotoSansCJKkr": "Noto Sans CJK KR",
    "NotoSerifSC": "Noto Serif SC",
    "NotoSerifCJKsc": "Noto Serif CJK SC",
    "SourceHanSansSC": "Source Han Sans SC",
    "SourceHanSansCN": "Source Han Sans CN",
    "SourceHanSansHC": "Source Han Sans",
    "SourceHanSans": "Source Han Sans",
    "SourceHanSerifSC": "Source Han Serif SC",
    "SourceHanSerif": "Source Han Serif",
    "MicrosoftYaHei": "Microsoft YaHei",
    "MicrosoftYaHeiUI": "Microsoft YaHei UI",
    "SimHei": "SimHei",
    "SimSun": "SimSun",
    "NSimSun": "NSimSun",
    "DengXian": "DengXian",
    "FangSong": "FangSong",
    "KaiTi": "KaiTi",
    "WenQuanYiMicroHei": "WenQuanYi Micro Hei",
    "WenQuanYiZenHei": "WenQuanYi Zen Hei",
    "AppleSDGothicNeo": "Apple SD Gothic Neo",
    "MalgunGothic": "Malgun Gothic",
    "YuGothic": "Yu Gothic",
    "Meiryo": "Meiryo",
    "MSGothic": "MS Gothic",
    "MSMincho": "MS Mincho",
}


def _postscript_to_family(ps_name: str) -> str | None:
    """把 PostScript name (PingFangSC-Regular) 反查 latin family (PingFang SC).

    内部 (.前缀) 系统字体跳过 — 那些不是给应用用的.
    """
    if not ps_name or ps_name.startswith("."):
        return None
    stem = ps_name.split("-", 1)[0]
    return _POSTSCRIPT_TO_FAMILY.get(stem)


def _detect_macos() -> set[str]:
    try:
        out = subprocess.run(
            ["system_profiler", "SPFontsDataType", "-json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug(f"[fonts] macOS system_profiler failed: {e}")
        return set()
    if out.returncode != 0:
        return set()
    try:
        data = json.loads(out.stdout or "{}")
    except json.JSONDecodeError as e:
        logger.debug(f"[fonts] macOS json parse failed: {e}")
        return set()
    items = data.get("SPFontsDataType") or []
    names: set[str] = set()
    for item in items:
        for tf in item.get("typefaces") or []:
            # 优先按 PostScript name 反查 latin family — 因为 family 字段在中文系统是中文
            ps = tf.get("_name")
            if isinstance(ps, str):
                fam = _postscript_to_family(ps)
                if fam:
                    names.add(fam)
            # 兜底 family / fullname (个别 latin-only 字体走这条)
            for key in ("family", "typeface_family"):
                v = tf.get(key)
                if isinstance(v, str) and v.strip() and not v.startswith("."):
                    names.add(v.strip())
    return names


def _detect_linux() -> set[str]:
    try:
        out = subprocess.run(
            ["fc-list", ":lang=zh", "-f", "%{family}\n"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug(f"[fonts] Linux fc-list failed: {e}")
        return set()
    if out.returncode != 0:
        return set()
    names: set[str] = set()
    for line in (out.stdout or "").splitlines():
        # fc-list 有时返 "Family Name,FamilyAlias", 拆开都收
        for part in line.split(","):
            p = part.strip()
            if p:
                names.add(p)
    return names


# Windows 简化: 已知 CJK 字体文件 → family 名映射. 探到文件即认作装了.
_WINDOWS_FONT_FILES: dict[str, str] = {
    "NotoSansSC-Regular.otf": "Noto Sans SC",
    "NotoSansSC-Regular.ttf": "Noto Sans SC",
    "NotoSansCJKsc-Regular.otf": "Noto Sans CJK SC",
    "msyh.ttc": "Microsoft YaHei",
    "msyh.ttf": "Microsoft YaHei",
    "msyhbd.ttc": "Microsoft YaHei",
    "msyhl.ttc": "Microsoft YaHei",
    "simhei.ttf": "SimHei",
    "simsun.ttc": "SimSun",
    "simsunb.ttf": "SimSun",
    "Deng.ttf": "DengXian",
    "Dengb.ttf": "DengXian",
    "FZSTK.TTF": "FangSong",
    "STKAITI.TTF": "KaiTi",
}


def _detect_windows() -> set[str]:
    names: set[str] = set()
    candidates = [Path(r"C:\Windows\Fonts")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    for d in candidates:
        if not d.exists():
            continue
        try:
            for f in d.iterdir():
                fam = _WINDOWS_FONT_FILES.get(f.name)
                if fam:
                    names.add(fam)
        except OSError as e:
            logger.debug(f"[fonts] Windows iter {d} failed: {e}")
    return names


@functools.cache
def detect_cjk_fonts() -> list[str]:
    """系统装了的 CJK 字体清单 (按 LLM 友好度排). 进程内 cache 一次.

    返回 _CJK_PREFERENCE ∩ 系统实际装了的字体, 顺序按 _CJK_PREFERENCE 定义.
    探测失败 / 系统没装时返 [] — agent 看到空会主动调 install_noto_sans_sc.
    """
    if os.environ.get("PENTALOOM_FONTS_FORCE_EMPTY") == "1":
        logger.info("[fonts] PENTALOOM_FONTS_FORCE_EMPTY=1, returning []")
        return []

    sys_name = platform.system()
    if sys_name == "Darwin":
        installed = _detect_macos()
    elif sys_name == "Linux":
        installed = _detect_linux()
    elif sys_name == "Windows":
        installed = _detect_windows()
    else:
        installed = set()

    out: list[str] = []
    for name in _CJK_PREFERENCE:
        if name in installed and name not in out:
            out.append(name)
    logger.info(
        f"[fonts] detected CJK fonts on {sys_name}: count={len(out)} "
        f"first={out[0] if out else None}"
    )
    return out


def invalidate() -> None:
    """install_noto_sans_sc 装完调; 让下次 detect_cjk_fonts() 重探."""
    detect_cjk_fonts.cache_clear()
