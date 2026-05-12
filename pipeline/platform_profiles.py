"""平台适配规格 — 从 config.toml 加载，缺省时兜底硬编码默认值。"""

PLATFORMS = ("douyin", "kuaishou", "bilibili")

_DEFAULTS: dict[str, dict] = {
    "douyin": {
        "title_max": 55,
        "desc_max": 1000,
        "tag_format": "hashtag",
        "tag_max": 10,
        "thumb_width": 1080,
        "thumb_height": 1920,
    },
    "kuaishou": {
        "title_max": 30,
        "desc_max": 500,
        "tag_format": "hashtag",
        "tag_max": 8,
        "thumb_width": 1080,
        "thumb_height": 1920,
    },
    "bilibili": {
        "title_max": 80,
        "desc_max": 2000,
        "tag_format": "comma",
        "tag_max": 12,
        "thumb_width": 1920,
        "thumb_height": 1080,
    },
}


def get_platform_profile(platform: str, config: dict) -> dict:
    """返回平台规格 {title_max, desc_max, tag_format, tag_max, thumb_width, thumb_height}。

    config 中无对应 [export.<platform>] section 时返回硬编码默认值。
    """
    if platform not in _DEFAULTS:
        raise ValueError(f"不支持的平台: {platform}（可选: {', '.join(PLATFORMS)}）")

    defaults = _DEFAULTS[platform]
    cfg_section = config.get("export", {}).get(platform, {})

    return {
        "title_max": cfg_section.get("title_max", defaults["title_max"]),
        "desc_max": cfg_section.get("desc_max", defaults["desc_max"]),
        "tag_format": cfg_section.get("tag_format", defaults["tag_format"]),
        "tag_max": cfg_section.get("tag_max", defaults["tag_max"]),
        "thumb_width": cfg_section.get("thumb_width", defaults["thumb_width"]),
        "thumb_height": cfg_section.get("thumb_height", defaults["thumb_height"]),
    }


def list_platforms(config: dict) -> list[str]:
    """返回 config 中定义的平台列表（无 [export] section 时返回全部默认平台）。"""
    export_cfg = config.get("export", {})
    if export_cfg:
        return [p for p in PLATFORMS if p in export_cfg]
    return list(PLATFORMS)
