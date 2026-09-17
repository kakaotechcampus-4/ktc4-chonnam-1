import re


def split_message(text: str):
    markdown_pattern = (
        r'\[([^\]]+)\]\((https?://[^)\s]+)\)'
    )

    markdown_links = re.findall(
        markdown_pattern,
        text
    )

    links = [
        url
        for _, url in markdown_links
    ]

    cleaned_text = re.sub(
        markdown_pattern,
        '',
        text
    )

    url_pattern = r'https?://[^\s\]\)]+'

    normal_links = re.findall(
        url_pattern,
        cleaned_text
    )

    links.extend(normal_links)

    message = re.sub(
        url_pattern,
        '',
        cleaned_text
    ).strip()

    # 중복 URL 제거
    links = list(dict.fromkeys(links))

    return links, message
