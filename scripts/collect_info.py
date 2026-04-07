import os
import re
import json
import requests
from datetime import datetime, timezone, timedelta
from anthropic import Anthropic
from bs4 import BeautifulSoup

NOTION_TOKEN = os.environ["NOTION_TOKEN"]

# カテゴリごとのNotionデータベースID（日付降順で最新が上に表示）
CATEGORY_DB_IDS = {
    "SEO":    "33b8d71fe58081bb938bcdcd8638f446",
    "AI":     "33b8d71fe580812898ccdaff27a9caaf",
    "世界情勢": "33b8d71fe580810fb827f1208538b12c",
    "政治":   "33b8d71fe58081038781e339bc82c769",
}

client = Anthropic()
JST = timezone(timedelta(hours=9))


def notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }


def fetch_article(url):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; InfoCollector/1.0)"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return text[:8000]


def summarize(url, content, category):
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": f"""以下の記事を日本語で要約してください。

カテゴリ: {category}
URL: {url}

記事内容:
{content}

以下のJSON形式のみで返してください（マークダウン不要）:
{{"title": "タイトル（30文字以内）", "oneliner": "この記事のひとこと（50文字以内）", "summary": "要約（300文字程度）", "points": ["ポイント1", "ポイント2", "ポイント3"]}}"""
        }]
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
    return json.loads(raw)


def create_notion_page(title, oneliner, summary, points, url, category, date_str):
    """DBエントリとして記事を作成（日付プロパティ付き）"""
    db_id = CATEGORY_DB_IDS[category]

    children = [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": summary}}]
            },
        }
    ]
    for point in points:
        children.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": point}}]
            },
        })
    children.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{
                "type": "text",
                "text": {"content": f"元記事: {url}", "link": {"url": url}},
            }]
        },
    })

    # date_str は "YYYY/MM/DD" 形式 → "YYYY-MM-DD" に変換
    iso_date = date_str.replace("/", "-")

    data = {
        "parent": {"database_id": db_id},
        "properties": {
            "名前": {"title": [{"text": {"content": f"{date_str} {title}"}}]},
            "日付": {"date": {"start": iso_date}},
            "ひとこと": {"rich_text": [{"text": {"content": oneliner}}]},
        },
        "children": children,
    }

    resp = requests.post("https://api.notion.com/v1/pages", headers=notion_headers(), json=data)
    resp.raise_for_status()
    return resp.json()


def load_processed_urls():
    path = "data/processed_urls.txt"
    if os.path.exists(path):
        with open(path) as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def save_processed_url(url):
    with open("data/processed_urls.txt", "a") as f:
        f.write(url + "\n")


def parse_articles(md_path):
    articles = []
    current_category = None
    category_keywords = {
        "SEO": "SEO",
        "AI": "AI",
        "世界情勢": "世界情勢",
        "政治": "政治",
        "営業": "営業",
        "組織運営": "組織運営",
        "IT": "IT",
        "業界動向": "業界動向",
        "マーケティング": "マーケティング",
    }

    with open(md_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#"):
                heading = line.lstrip("#").strip()
                for key, label in category_keywords.items():
                    if key in heading:
                        current_category = label
                        break
            elif line.startswith("- [") and current_category:
                match = re.match(r"- \[(.+?)\]\((.+?)\)", line)
                if match:
                    _, url = match.groups()
                    if url and not url.startswith("URL"):
                        articles.append({"url": url, "category": current_category})
    return articles


def main():
    today = datetime.now(JST).strftime("%Y/%m/%d")
    processed = load_processed_urls()
    articles = parse_articles("data/articles.md")

    for article in articles:
        url = article["url"]
        category = article["category"]

        if url in processed:
            print(f"[SKIP] {url}")
            continue

        if category not in CATEGORY_DB_IDS:
            print(f"[SKIP] カテゴリ未対応: {category}")
            continue

        print(f"[START] {url}")
        try:
            content = fetch_article(url)
            result = summarize(url, content, category)
            create_notion_page(
                result["title"],
                result.get("oneliner", ""),
                result["summary"],
                result["points"],
                url,
                category,
                today,
            )
            save_processed_url(url)
            print(f"[DONE] {result['title']}")
        except Exception as e:
            print(f"[ERROR] {url} -> {e}")


if __name__ == "__main__":
    main()
