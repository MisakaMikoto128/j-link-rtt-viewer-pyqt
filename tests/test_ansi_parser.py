"""ANSI 转义序列解析为 (text, AnsiAttrs) 段。"""
from core.ansi_parser import AnsiAttrs, parse_ansi


def test_plain_text():
    assert parse_ansi("hello") == [("hello", AnsiAttrs())]


def test_single_color():
    out = parse_ansi("\x1b[31mred\x1b[0m")
    assert out == [("red", AnsiAttrs(fg="red"))]


def test_color_then_plain():
    out = parse_ansi("\x1b[31mhi\x1b[0m bye")
    assert out == [
        ("hi", AnsiAttrs(fg="red")),
        (" bye", AnsiAttrs()),
    ]


def test_multi_param():
    out = parse_ansi("\x1b[1;31;42mbold-red-bg-green\x1b[0m")
    attrs = out[0][1]
    assert attrs.bold is True
    assert attrs.fg == "red"
    assert attrs.bg == "green"


def test_nested_reset():
    out = parse_ansi("\x1b[31mA\x1b[32mB\x1b[0mC")
    assert out == [
        ("A", AnsiAttrs(fg="red")),
        ("B", AnsiAttrs(fg="green")),
        ("C", AnsiAttrs()),
    ]


def test_invalid_sequence_kept_as_literal():
    out = parse_ansi("\x1b[abcmhello")
    # 解析失败的序列应该当成字面量保留，不丢字符
    text = "".join(seg for seg, _ in out)
    assert "hello" in text


def test_unterminated_csi_at_end():
    out = parse_ansi("normal\x1b[31")
    text = "".join(seg for seg, _ in out)
    assert text.startswith("normal")


def test_bright_colors():
    out = parse_ansi("\x1b[91mbright-red\x1b[0m")
    assert out[0][1].fg == "bright_red"


def test_8bit_color_ignored_gracefully():
    out = parse_ansi("\x1b[38;5;196mfoo\x1b[0m")
    text = "".join(seg for seg, _ in out)
    assert text == "foo"


def test_incomplete_38_2_does_not_leak_params():
    """38;2 参数不全时，后续 G/B 值不应被误读为新 SGR 参数。"""
    # 完整应该是 38;2;R;G;B，这里只给到 G，且 G 取一个会被误读为前景红色的值
    out = parse_ansi("\x1b[38;2;255;31mfoo\x1b[0m")
    # 关键：31 不应被作为单独 SGR 应用变成前景红
    # 由于不完整，"38;2;..." 应整体被吞，前景应保持 None
    assert out[0][1].fg is None


def test_incomplete_38_5_does_not_leak_params():
    """38;5 缺索引参数时也不应泄漏。"""
    out = parse_ansi("\x1b[38;5mfoo\x1b[0m")
    # 只有 38;5 没有 N，整体吞掉
    assert out[0][1].fg is None


def test_adjacent_same_color_merged():
    """连续两个相同 SGR 之间的文本应被合并为一段。"""
    out = parse_ansi("\x1b[31mA\x1b[31mB\x1b[0m")
    assert out == [("AB", AnsiAttrs(fg="red"))]
