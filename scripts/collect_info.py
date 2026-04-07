import os
import re
import json
import requests
from datetime import datetime, timezone, timedelta
from anthropic import Anthropic
from bs4 import BeautifulSoup

NOTION_TOKEN = os.environ["NOTION_TOKEN"]

CATEGORY_DB_IDS = {
    "SEO":    "33b8d71fe58081bb938bcdcd8638f446",
    "AI":     "33b8d71fe580812898ccdaff27a9caaf",
    "世界情勢": "33b8d71fe580810fb827f1208538b12c",
    "政治":   "33b8d71fe58081038781e339bc82c769",
}

KOSATSU_PAGE_ID = "3368d71fe58081228beae2dd4aa27035"

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
    db_id = CATEGORY_DB_IDS[category]
    iso_date = date_str.replace("/", "-")

    children = [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": summary}}]},
        }
    ]
    for point in points:
        children.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": point}}]},
        })
    children.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": f"元記事: {url}", "link": {"url": url}}}]
        },
    })

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


def create_kosatsu_page(date_str, collected_summaries):
    """収集した記事をもとに日次考察ページを生成"""

    # 各カテゴリのサマリーをまとめてClaudeに渡す
    context = ""
    for cat, items in collected_summaries.items():
        if not items:
            continue
        context += f"\n## {cat}\n"
        for item in items:
            context += f"- {item['title']}: {item['summary'][:150]}\n"

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=3000,
        messages=[{
            "role": "user",
            "content": f"""今日（{date_str}）収集した以下のニュースをもとに、日次考察レポートを作成してください。

{context}

以下のJSON形式のみで返してください（マークダウン不要）:
{{
  "sekai": "世界情勢の考察（200文字程度）",
  "seiji": "日本政治・経済の考察（200文字程度）",
  "ai": "AI・テクノロジーの考察（200文字程度）",
  "seo": "SEOトレンドの考察（200文字程度）",
  "kabuka": [
    {{"meigara": "銘柄名", "code": "証券コード", "direction": "↑", "reason": "理由（50文字）"}},
    {{"meigara": "銘柄名", "code": "証券コード", "direction": "↔", "reason": "理由（50文字）"}},
    {{"meigara": "銘柄名", "code": "証券コード", "direction": "↓", "reason": "理由（50文字）"}}
  ],
  "matome": "総合コメント（150文字程度）"
}}"""
        }]
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
    kosatsu = json.loads(raw)

    # Notionページの中身を構築
    children = []

    def h2(text):
        return {"object": "block", "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]}}

    def para(text):
        return {"object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]}}

    children.append(h2("🌍 世界情勢"))
    children.append(para(kosatsu.get("sekai", "")))
    children.append(h2("🏛️ 日本政治・経済"))
    children.append(para(kosatsu.get("seiji", "")))
    children.append(h2("🤖 AI・テクノロジー"))
    children.append(para(kosatsu.get("ai", "")))
    children.append(h2("📈 SEOトレンド"))
    children.append(para(kosatsu.get("seo", "")))
    children.append(h2("📊 株価予測"))

    # テーブル
    kabuka = kosatsu.get("kabuka", [])
    if kabuka:
        rows = [["銘柄", "コード", "方向", "理由"]]
        for k in kabuka:
            rows.append([k.get("meigara",""), k.get("code",""), k.get("direction",""), k.get("reason","")])
        table_rows = []
        for row in rows:
            table_rows.append({
                "type": "table_row",
                "table_row": {"cells": [[{"type": "text", "text": {"content": cell}}] for cell in row]}
            })
        children.append({
            "object": "block", "type": "table",
            "table": {"table_width": 4, "has_column_header": True, "has_row_header": False, "children": table_rows}
        })

    children.append(h2("💬 総合コメント"))
    children.append(para(kosatsu.get("matome", "")))

    # ページ作成
    data = {
        "parent": {"page_id": KOSATSU_PAGE_ID},
        "properties": {
            "title": {"title": [{"text": {"content": f"{date_str} 日次考察｜世界情勢×日本政治×AIから読む株価動向"}}]}
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
        "SEO": "SEO", "AI": "AI", "世界情勢": "世界情勢", "政治": "政治",
        "営業": "営業", "組織運営": "組織運営", "IT": "IT",
        "業界動向": "業界動向", "マーケティング": "マーケティング",
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

    # 収集した記事のサマリーを考察用に蓄積
    collected_summaries = {"SEO": [], "AI": [], "世界情勢": [], "政治": []}

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

            if category in collected_summaries:
                collected_summaries[category].append({
                    "title": result["title"],
                    "summary": result["summary"],
                })
        except Exception as e:
            print(f"[ERROR] {url} -> {e}")

    # 1件以上収集できた場合のみ考察ページを生成
    total_collected = sum(len(v) for v in collected_summaries.values())
    if total_collected > 0:
        print(f"\n[考察生成] {total_collected}件の記事をもとに日次考察を作成中...")
        try:
            create_kosatsu_page(today, collected_summaries)
            print("[考察完了]")
        except Exception as e:
            print(f"[考察ERROR] {e}")


if __name__ == "__main__":
    main()
