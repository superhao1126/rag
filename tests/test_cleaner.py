import pytest

from core.cleaner import CleanConfig, TextCleaner


def test_cleaner_removes_html_noise_and_repairs_cjk_spacing():
    raw = "第 1 页\nHello&nbsp;World\u200b\n<script>alert(1)</script>\n你 好\n---\n"

    cleaned, report = TextCleaner().clean(raw)

    assert cleaned == "Hello World\n你好"
    assert report.counters["html_entity:&nbsp;"] == 1
    assert report.counters["control_char"] == 1
    assert report.counters["html_block"] == 1
    assert report.counters["page_footer"] == 1
    assert report.counters["punct_only"] == 1
    assert report.counters["cjk_space"] == 1


def test_cleaner_preserves_cjk_line_breaks_while_fixing_inline_spacing():
    raw = "第一段\n第二段\n你 好"

    cleaned, report = TextCleaner().clean(raw)

    assert cleaned == "第一段\n第二段\n你好"
    assert report.counters["cjk_space"] == 1


def test_cleaner_keeps_numeric_content_lines_by_default():
    raw = "接口状态码\n200\n第 2 页\n2024\n- 3 -\n业务含义成功"

    cleaned, report = TextCleaner().clean(raw)

    assert cleaned == "接口状态码\n200\n2024\n业务含义成功"
    assert report.counters["page_footer"] == 2


def test_cleaner_does_not_strip_non_html_angle_bracket_text():
    raw = "a < b > c\nList<T> 泛型\nUse <token> value\n<code>keep</code>"

    cleaned, report = TextCleaner().clean(raw)

    assert cleaned == "a < b > c\nList<T> 泛型\nUse <token> value\nkeep"
    assert report.counters["html_tag"] == 2


def test_cleaner_duplicate_lines_only_collapse_when_threshold_reached():
    raw = "备注\n备注\n正文\n水印\n水印\n水印\n正文"
    cleaner = TextCleaner(CleanConfig(dup_line_threshold=3))

    cleaned, report = cleaner.clean(raw)

    assert cleaned == "备注\n备注\n正文\n水印\n正文"
    assert report.counters["dup_line"] == 2


def test_cleaner_is_idempotent_for_core_noise_rules():
    raw = "第 1 页\nHello&nbsp;World\u200b\n你 好\n水印\n水印\n水印\n"
    cleaner = TextCleaner()

    once, _ = cleaner.clean(raw)
    twice, _ = cleaner.clean(once)

    assert once == twice


def test_cleaner_preserves_fenced_code_blocks():
    raw = "\n".join([
        "示例:",
        "```html",
        "<div class=\"x\">a   b</div>",
        "```",
        "正文",
    ])

    cleaned, report = TextCleaner().clean(raw)

    assert cleaned == raw
    assert report.counters["protected_block"] == 1
    assert "html_tag" not in report.counters


def test_cleaner_preserves_markdown_tables():
    raw = "\n".join([
        "字段说明",
        "| 字段 | 含义 |",
        "| --- | --- |",
        "| code | 200 |",
        "结束",
    ])

    cleaned, report = TextCleaner().clean(raw)

    assert cleaned == raw
    assert report.counters["protected_block"] == 1
    assert "punct_only" not in report.counters


def test_cleaner_preserves_indented_code_blocks():
    raw = "\n".join([
        "代码:",
        "    def f():",
        "        return '<tag>'",
        "结束",
    ])

    cleaned, report = TextCleaner().clean(raw)

    assert cleaned == raw
    assert report.counters["protected_block"] == 1
    assert "html_tag" not in report.counters


def test_cleaner_preserves_formula_blocks():
    raw = "\n".join([
        "公式:",
        "$$",
        "<mi>x</mi> = 1",
        "$$",
        "结束",
    ])

    cleaned, report = TextCleaner().clean(raw)

    assert cleaned == raw
    assert report.counters["protected_block"] == 1
    assert "html_tag" not in report.counters


def test_cleaner_decodes_general_html_entities():
    raw = "A&#x26;B&#32;C&nbsp;D"

    cleaned, report = TextCleaner().clean(raw)

    assert cleaned == "A&B C D"
    assert report.counters["html_entity"] == 3
    assert report.counters["html_entity:&nbsp;"] == 1


def test_cleaner_normalizes_cjk_alnum_spacing():
    raw = "AI模型测试123"

    cleaned, report = TextCleaner().clean(raw)

    assert cleaned == "AI 模型测试 123"
    assert report.counters["cjk_alnum_space"] == 2


def test_cleaner_can_redact_privacy_tokens_when_enabled():
    raw = "联系 test@example.com，访问 https://example.com/a?token=123，电话 13800138000"
    cleaner = TextCleaner(CleanConfig(redact_privacy=True))

    cleaned, report = cleaner.clean(raw)

    assert cleaned == "联系 <EMAIL>,访问 <URL>,电话 <PHONE>"
    assert report.counters["privacy_email"] == 1
    assert report.counters["privacy_url"] == 1
    assert report.counters["privacy_phone"] == 1


def test_cleaner_removes_long_noise_tokens():
    raw = f"token {'a' * 60} done"

    cleaned, report = TextCleaner().clean(raw)

    assert cleaned == "token done"
    assert report.counters["long_noise_token"] == 1


def test_cleaner_removes_replacement_character_noise():
    raw = "正文 ��� 结束"

    cleaned, report = TextCleaner().clean(raw)

    assert cleaned == "正文结束"
    assert report.counters["replacement_noise"] == 1


def test_clean_report_tracks_deleted_lines_and_summary_details():
    raw = "第 1 页\n---\n正文"

    cleaned, report = TextCleaner().clean(raw)

    assert cleaned == "正文"
    assert report.removed_lines == 2
    assert report.removed_chars == len("第 1 页") + len("---")
    assert "删除 2 行" in report.summary()


def test_clean_report_flags_low_retention_risk():
    raw = "\n".join(f"第 {i} 页" for i in range(1, 8)) + "\n正文"

    cleaned, report = TextCleaner().clean(raw)

    assert cleaned == "正文"
    assert "low_retention" in report.warnings
    assert "风险提示" in report.summary()


def test_cleaner_accepts_none_as_empty_text():
    cleaned, report = TextCleaner().clean(None)

    assert cleaned == ""
    assert report.raw_chars == 0
    assert report.clean_chars == 0


def test_cleaner_rejects_non_string_input():
    with pytest.raises(TypeError):
        TextCleaner().clean(123)


def test_cleaner_handles_long_malformed_html_like_text():
    raw = "<" + ("notatag" * 2000) + ">"
    cleaner = TextCleaner(CleanConfig(drop_noise_tokens=False))

    cleaned, report = cleaner.clean(raw)

    assert cleaned == raw
    assert "html_tag" not in report.counters
