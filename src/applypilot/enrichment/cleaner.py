"""Job Description Cleaner & Sanitizer.

Strips ugly inline Tailwind CSS styles, raw attributes (class, style, id, tw-*),
unwanted scripts, and dirty HTML markups while preserving clean, semantic structure:
- Section Headers -> <h4> (bold section titles)
- Lists -> <ul><li> / <ol><li>
- Bold / Strong -> <strong>
- Paragraphs -> <p>
- Links -> <a href="..." target="_blank" rel="noopener">
"""

import html
import re

ALLOWED_TAGS = {"p", "ul", "ol", "li", "strong", "em", "h4", "br", "a", "b", "i", "blockquote"}


def clean_job_description(raw: str | None) -> str:
    """Sanitize and format raw job description strings into clean semantic HTML/Markdown."""
    if not raw or str(raw).strip().lower() in ("none", "null", ""):
        return ""

    text = html.unescape(str(raw))

    # 1. Remove dangerous or non-content tags completely
    text = re.sub(r"<(script|style|svg|meta|link|iframe|input|button|form)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)

    # 2. Standardize headers (h1, h2, h3, h4, h5, h6) to clean <h4>
    text = re.sub(r"<h[1-6][^>]*>", "<h4>", text, flags=re.IGNORECASE)
    text = re.sub(r"</h[1-6]>", "</h4>", text, flags=re.IGNORECASE)

    # 3. Standardize b/strong/i/em
    text = re.sub(r"<b[^>]*>", "<strong>", text, flags=re.IGNORECASE)
    text = re.sub(r"</b>", "</strong>", text, flags=re.IGNORECASE)
    text = re.sub(r"<i[^>]*>", "<em>", text, flags=re.IGNORECASE)
    text = re.sub(r"</i>", "</em>", text, flags=re.IGNORECASE)

    # 4. Standardize div and p to clean <p>
    text = re.sub(r"<(div|p)[^>]*>", "<p>", text, flags=re.IGNORECASE)
    text = re.sub(r"</(div|p)>", "</p>", text, flags=re.IGNORECASE)

    # 5. Clean all inline attributes (class, style, id, tw-*, figma-*, etc.)
    def _filter_tag(match):
        is_closing = match.group(1) == "/"
        tag_name = match.group(2).lower()
        if tag_name not in ALLOWED_TAGS:
            return ""
        if is_closing:
            return f"</{tag_name}>"
        if tag_name == "a":
            href_m = re.search(r'href=["\'](.*?)["\']', match.group(0), re.IGNORECASE)
            href = href_m.group(1) if href_m else "#"
            if href.startswith("javascript:") or href.startswith("data:"):
                href = "#"
            return f'<a href="{href}" target="_blank" rel="noopener">'
        return f"<{tag_name}>"

    text = re.sub(r"<(/?)([a-zA-Z0-9]+)[^>]*>", _filter_tag, text)

    # 6. Convert Markdown syntax headers (### Title) and bold (**text**) if plain text
    text = re.sub(r"^\s*###+\s*(.*?)$", r"<h4>\1</h4>", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", text)

    # 7. Convert bullet lines (* item or - item or • item) into <ul><li> list items
    lines = text.split("\n")
    in_list = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        bullet_m = re.match(r"^[*\-•]\s+(.*)$", stripped)
        if bullet_m and not stripped.startswith("<li>"):
            if not in_list:
                new_lines.append("<ul>")
                in_list = True
            new_lines.append(f"<li>{bullet_m.group(1)}</li>")
        else:
            if in_list and not stripped.startswith("<li>") and not stripped.startswith("</ul>"):
                new_lines.append("</ul>")
                in_list = False
            new_lines.append(line)
    if in_list:
        new_lines.append("</ul>")
    text = "\n".join(new_lines)

    # 8. Clean redundant or empty tags
    text = re.sub(r"<p>\s*</p>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<li>\s*</li>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<ul>\s*</ul>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<strong>\s*</strong>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(<br\s*/?>\s*){3,}", "<br><br>", text, flags=re.IGNORECASE)

    # 9. Auto-close any unclosed HTML tags to prevent bleeding bold/italic styling
    open_tags = []
    for match in re.finditer(r"<(/?)(strong|em|h4|p|ul|ol|li|a)\b[^>]*>", text, re.IGNORECASE):
        is_close = match.group(1) == "/"
        tag_name = match.group(2).lower()
        if is_close:
            if open_tags and open_tags[-1] == tag_name:
                open_tags.pop()
            elif tag_name in open_tags:
                open_tags.remove(tag_name)
        else:
            open_tags.append(tag_name)

    for tag_name in reversed(open_tags):
        text += f"</{tag_name}>"

    # Normalize double linebreaks
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()
