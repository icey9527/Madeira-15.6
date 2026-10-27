Microsoft YaHei fallback face
=============================

`MicrosoftYaHei.otf` is the full Noto Sans CJK SC Regular font from
https://github.com/notofonts/noto-cjk, with its internal Windows family name
changed to `Microsoft YaHei` / `微软雅黑` so Wine's GDI font matching uses it
when a game requests Microsoft YaHei.

This is a glyph fallback for compatibility, not the proprietary Microsoft
font. License: SIL Open Font License 1.1 — see `OFL.txt`.

The font is bundled as an app resource and installed into the Wine prefix's
`drive_c/windows/Fonts` directory at startup, so fresh installations have
Chinese glyphs without downloading anything.

2026-10-27: simsun.ttc (SimSun / NSimSun / 宋体), simhei.ttf (SimHei / 黑体),
simkai.ttf (KaiTi / 楷体) and msgothic.ttc (MS Gothic, Japanese) are bundled
so that a game asking for a Chinese serif, a Chinese sans, a Kai face or the
Japanese Gothic finds a real face instead of falling back to the single
bundled sans. They are Microsoft font software: not covered by this
project's licence, included for the maintainer's own devices, and a fork
that redistributes this app should decide for itself whether it may ship
them. The install step copies every font in this directory into the prefix's
C:\windows\Fonts at launch.
