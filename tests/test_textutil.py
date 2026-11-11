import re

from content_workbench.textutil import split_thread, thread_report, wechat_html, x_weighted_length


def test_weighted_length_counts_cjk_double_and_urls_as_23():
    assert x_weighted_length("abc") == 3
    assert x_weighted_length("你好") == 4
    assert x_weighted_length("看 https://example.com/a/very/long/path") == 2 + 1 + 23


def test_split_thread_on_dash_lines():
    body = "1/ 第一条 🧵\n---\n2/ 第二条\n\n---\n\n3/ 第三条"
    assert split_thread(body) == ["1/ 第一条 🧵", "2/ 第二条", "3/ 第三条"]


def test_thread_report_flags_overlong():
    report = thread_report("短\n---\n" + "长" * 141)
    assert report[0]["over"] is False
    assert report[1]["weighted"] == 282 and report[1]["over"] is True


def test_wechat_html_lists_have_no_whitespace_between_tags():
    md = "开了三个号：\n\n- 双人号；\n\n![](/api/images/1)\n\n- 二创号；\n- 擂台。\n\n1. 一\n\n2. 二\n"
    html = wechat_html(md)
    assert html.count("<li") == 5
    assert not re.search(r"<(?:ul|ol|li)\b[^>]*>\s+<|>\s+</?(?:ul|ol|li)\b", html)
    assert "<p" in html.split("<ol")[1]  # loose items keep their paragraphs


def test_wechat_html_inlines_styles_and_escapes_raw_html():
    html = wechat_html("## 一、标题\n\n正文 **重点**\n\n<script>alert(1)</script>")
    assert '<h2 style="' in html and '<p style="' in html and '<strong style="' in html
    assert "<script>" not in html
