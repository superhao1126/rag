from core.cleaner import TextCleaner


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
