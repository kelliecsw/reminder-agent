from reminder_agent.telegram.formatting import render_markdown


def test_render_markdown_returns_telegram_rich_html():
    rich_message = render_markdown(
        "💧 **Drink water** and read [the guide](https://example.com)."
    )

    assert rich_message == {
        "html": (
            '<p>💧 <strong>Drink water</strong> and read '
            '<a href="https://example.com">the guide</a>.</p>'
        )
    }


def test_render_markdown_escapes_raw_html():
    source = "Use 2 < 3 & an unmatched * literally."

    rich_message = render_markdown(source)

    assert rich_message == {
        "html": "<p>Use 2 &lt; 3 &amp; an unmatched * literally.</p>"
    }


def test_render_markdown_preserves_currency_in_native_table():
    rich_message = render_markdown(
        "| Category | Price |\n"
        "|:--|--:|\n"
        "| Budget | $200–$400 |"
    )

    assert rich_message is not None
    html = rich_message["html"]
    assert html.startswith("<table bordered striped compact>")
    assert "<thead>" not in html
    assert "<tbody>" not in html
    assert '<th align="left">Category</th>' in html
    assert '<td align="right">$200–$400</td>' in html
    assert "tg-math" not in html


def test_render_markdown_uses_explicit_fenced_math_only():
    rich_message = render_markdown(
        "$200–$400\n\n"
        "```math\n"
        "E = mc^2 & x < y\n"
        "```"
    )

    assert rich_message == {
        "html": (
            "<p>$200–$400</p>\n"
            "<tg-math-block>E = mc^2 &amp; x &lt; y</tg-math-block>"
        )
    }
