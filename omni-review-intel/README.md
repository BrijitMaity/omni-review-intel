# Omni Review Intel

**Enterprise Cross-Web Review Intelligence & Automated Excel Reporting System**

Given a single URL — a YouTube video, e-commerce product page (Amazon, Flipkart, Walmart), social video (Instagram, TikTok), or product image — this system automatically:
1. **Identifies the exact product/topic** using multimodal LLM entity extraction and metadata scrapers.
2. **Discovers all review sources across the internet** where the content or product is discussed (YouTube, Reddit, blogs, forums, eCommerce).
3. **Collects uncapped user reviews & comments** directly via official APIs, high-speed downloaders, and headless crawlers.
4. **Performs deep sentiment & aspect analysis** dividing comments into Good, Bad, and Neutral with key aspect tagging (Price, Quality, Battery, Comfort, etc.).
5. **Exports a clean, executive 7-sheet `.xlsx` report** with embedded interactive charts, auto-filters, frozen panes, and verified hyperlinks.

---

## 🏗️ Architecture

```
Input URL → Stage 1 (Entity ID) → Stage 2 (Web Discovery) →
Stage 3 (Uncapped Collector) → Stage 4 (Map-Reduce Sentiment) → Stage 5 (Corporate 7-Sheet Excel)
```

Each stage is independently cacheable via SQLite (`cache.db`) — reruns skip completed work instantly.

---

## ⚡ Quick Start (1-Click Windows)

Double-click any of the provided batch scripts in the project root:
- **`start.bat`** — Interactive Master Command Center (CLI analysis, status, tests, cache).
- **`run_ui.bat`** — Launch the full Amazon-style Streamlit Web UI (`http://localhost:8501`).
- **`run_cli.bat "<URL>"`** — Directly analyze a URL and auto-open the generated Excel workbook.
- **`run_tests.bat`** — Run the full automated test suite (74 unit/integration tests).

---

## 💻 Manual Setup & Execution

### 1. Prerequisites
- Python 3.11 or Python 3.12
- pip

### 2. Install Dependencies

```bash
pip install -r requirements.txt
playwright install chromium   # for general web crawling
```

### 3. Optional API Keys (`.env`)
The system runs out-of-the-box in **Smart Built-in Mode** without external API keys. To unlock cloud LLM reasoning and extended platform scrapers, copy `.env.example` to `.env`:

| Key | Purpose | Cost |
|-----|---------|------|
| `GEMINI_API_KEY` | Multimodal entity extraction & executive reasoning | Free tier available |
| `YOUTUBE_API_KEY` | Official YouTube Data API v3 collection | Free (10,000 units/day) |
| `REDDIT_CLIENT_ID` + `SECRET` | Official Reddit OAuth comment collector | Free |
| `TAVILY_API_KEY` | Deep AI search query discovery | Free tier available |
| `APIFY_API_KEY` | Amazon customer reviews, Instagram, TikTok | Optional |

---

## 📊 Corporate Excel Report Structure (Up to 9 Dedicated Tabs)

| Tab # | Sheet Name | Contents & Purpose |
|:---:|---|---|
| **1** | **All Reviews & Comments** | **Active Tab on open**: Complete uncapped master table of all scraped comments with sentiment pills, star ratings, author, date, aspect, and source hyperlinks. |
| **2** | **🎯 Target Reviews** | Comments/reviews scraped strictly from the primary target URL (unmixed). |
| **3** | **👍 Good Reviews** | Filtered positive customer reviews, praise, and satisfaction highlights. |
| **4** | **👎 Bad Reviews** | Filtered negative customer complaints, product issues, defects, and criticisms. |
| **5** | **⚖️ Neutral Reviews** | Filtered objective feedback, feature queries, and neutral community discussions. |
| **6** | **🌐 Discovered Web Reviews** | Additional context collected from web discovery sources (when available). |
| **7** | **Executive Review** | C-suite summary dashboard with AI Review Score (1–10), Pros/Cons/Verdict, and **Embedded Pie Chart**. |
| **8** | **Platform & Aspect Summary** | Platform metrics breakdown, aspect breakdown (Price, Battery, Comfort, etc.), and **Embedded Bar Chart**. |
| **9** | **Discovered Sources** | Complete verified audit log of all crawled web URLs with status indicators and hyperlinks. |

---

## 🧪 Automated Testing

```bash
python -m pytest -v
```

### CLI Usage Examples

```bash
# Product page
python main.py --url "https://www.amazon.com/dp/XXXXXXXX"

# Image URL
python main.py --url "https://example.com/product-image.jpg"

# Custom output directory
python main.py --url "https://..." --output-dir ./my_reports

# Verbose logging
python main.py --url "https://..." --verbose
```

### 5. Run — Streamlit Web UI

```bash
streamlit run app.py
```

Open `http://localhost:8501`, paste a URL, and click **Analyze**.

---

## 🧪 Running Tests

```bash
pytest tests/ -v
```

---

## 📁 Project Structure

```
omni-review-intel/
├── .env.example              # API keys template
├── requirements.txt          # Pinned dependencies
├── config.py                 # Pydantic Settings
├── main.py                   # CLI entry point
├── app.py                    # Streamlit web UI
├── core/
│   ├── schemas.py            # All Pydantic v2 models
│   ├── cache.py              # SQLite cache
│   └── pipeline.py           # Async orchestrator (Stages 1→5)
├── stages/
│   ├── _llm_client.py        # Gemini/OpenAI provider wrapper
│   ├── s1_input_analyzer.py  # URL classification + entity extraction
│   ├── s2_web_discovery.py   # Tavily/SerpApi/DDG web search
│   ├── s3_collectors/
│   │   ├── router.py         # Concurrent dispatch by platform
│   │   ├── youtube_collector.py
│   │   ├── reddit_collector.py
│   │   ├── web_collector.py  # crawl4ai + LLM extractor
│   │   └── apify_collector.py
│   ├── s4_ai_analyzer.py     # Map-Reduce sentiment analysis
│   └── s5_excel_builder.py   # openpyxl multi-sheet builder
├── prompts/
│   ├── entity_extraction.txt
│   ├── batch_comment_eval.txt
│   └── executive_review.txt
├── outputs/                  # Generated .xlsx reports
└── tests/
    ├── test_schemas.py
    ├── test_cache.py
    ├── test_router.py
    └── test_excel_builder.py
```

---

## ⚙️ Configuration Defaults

All tunable in `.env` or `config.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `MAX_DISCOVERED_URLS` | 15 | Max URLs to scrape per run |
| `MAX_COMMENTS_PER_URL` | 100 | Max comments per source |
| `LLM_BATCH_SIZE` | 30 | Comments per LLM batch |
| `MAX_CONCURRENT_SCRAPES` | 5 | Parallel scrape tasks |
| `REQUEST_TIMEOUT` | 30 | HTTP timeout (seconds) |

---

## 🛡️ Engineering Guardrails

- **Fault tolerance**: Any failed site is logged in the Sources sheet and the pipeline continues
- **Cache-first**: SQLite caching at every stage — reruns skip already-completed work
- **Token economy**: Fast `gemini-2.0-flash` for batch tagging, strong model only for executive review
- **Public data only**: Official APIs (YouTube Data API, Reddit PRAW) and publicly accessible pages
- **Concurrency limits**: `asyncio.Semaphore` prevents API rate-limit hammering

---

## 📜 License

MIT
