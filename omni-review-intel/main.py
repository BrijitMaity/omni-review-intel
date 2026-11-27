"""
main.py — Master Command Center & CLI Entry Point for omni-review-intel.

Unified single command-line interface supporting both interactive menu mode
and direct command-line arguments:

Usage:
  Interactive Menu:
    python main.py                   # Launches interactive command center
    python main.py menu

  Direct Subcommands:
    python main.py ui                # Launches Amazon-style Streamlit UI
    python main.py analyze <URL>     # Runs 5-stage pipeline on URL
    python main.py demo              # Generates sample report without API key
    python main.py test              # Runs complete 70-test suite
    python main.py test --ui         # Runs automated GUI test suite only
    python main.py status            # Displays system & API key diagnostics
    python main.py cache stats       # Shows SQLite cache metrics
    python main.py cache clear       # Wipes local cache

  Legacy / Direct Flag Mode:
    python main.py --url "https://..."
    python main.py "https://..."
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from loguru import logger


def _setup_logging(verbose: bool) -> None:
    logger.remove()
    console_level = "DEBUG" if verbose else "ERROR"
    logger.add(sys.stderr, level=console_level, format="<level>{level}</level> | {message}")
    try:
        log_file = Path("app.log")
        logger.add(
            str(log_file),
            level="DEBUG",
            rotation="10 MB",
            retention="3 days",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            encoding="utf-8",
        )
    except Exception:
        pass


def _try_rich() -> bool:
    """Check if rich is installed for pretty CLI formatting."""
    try:
        import rich  # noqa: F401
        return True
    except ImportError:
        return False


# ── Subcommand Handlers ────────────────────────────────────────────────────────

def cmd_ui(port: int = 8501, host: str = "localhost") -> None:
    """Launch the Amazon-style Streamlit Web UI."""
    print(f"\n🚀 Launching Omni Review Intel Web UI on http://{host}:{port} ...\n")
    pkg_dir = Path(__file__).resolve().parent
    app_path = pkg_dir / "app.py"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        f"--server.port={port}",
        f"--server.address={host}",
    ]
    try:
        subprocess.run(cmd, cwd=str(pkg_dir))
    except KeyboardInterrupt:
        print("\nWeb UI stopped by user.")


def cmd_demo(output_dir: str = "outputs") -> str:
    """Run instant demo analysis and generate sample report."""
    from core.demo import build_demo_state

    print("\n🧪 Running Demo Review Intelligence (Sony WH-1000XM5 Sample) ...")
    state = build_demo_state(output_dir=output_dir)
    review = state.executive_review

    use_rich = _try_rich()
    if use_rich:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="Demo Pipeline Summary", show_lines=True, border_style="gold1")
        table.add_column("Metric", style="bold cyan", width=25)
        table.add_column("Value", style="white")

        if review:
            table.add_row("Topic", review.topic_name)
            table.add_row("Overall Sentiment", f"[bold green]{review.overall_sentiment}[/bold green]")
            table.add_row("AI Score", f"{review.ai_score_out_of_10:.1f} / 10.0")
            table.add_row("Sources Scanned", str(len(state.sources)))
            table.add_row("Analyzed Comments", str(state.total_comments_valid))
            table.add_row(
                "Sentiment Split",
                f"[green]{state.positive_pct:.1f}%[/green] pos | "
                f"[yellow]{state.neutral_pct:.1f}%[/yellow] neu | "
                f"[red]{state.negative_pct:.1f}%[/red] neg",
            )
            table.add_row("Generated Excel", state.output_xlsx_path)

        console.print(table)
        console.print(f"\n[bold green]✅ Demo report saved:[/bold green] {state.output_xlsx_path}\n")
    else:
        print(f"\n=== Demo Analysis Complete ===")
        print(f"Topic:     {review.topic_name if review else 'N/A'}")
        print(f"AI Score:  {review.ai_score_out_of_10 if review else 0:.1f}/10")
        print(f"Report:    {state.output_xlsx_path}\n")

    return state.output_xlsx_path


def cmd_test(ui_only: bool = False, verbose: bool = True) -> int:
    """Run pytest suite (full tests or UI suite)."""
    pkg_dir = Path(__file__).resolve().parent
    target = "tests/test_ui.py" if ui_only else "tests"
    print(f"\n🧪 Running Test Suite ({target}) ...\n")
    cmd = [sys.executable, "-m", "pytest", target]
    if verbose:
        cmd.append("-v")
    res = subprocess.run(cmd, cwd=str(pkg_dir))
    return res.returncode


def cmd_status() -> None:
    """Display comprehensive system diagnostics, API keys, and environment status."""
    from config import settings

    use_rich = _try_rich()
    if use_rich:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="System & API Key Diagnostics", show_lines=True, border_style="cyan")
        table.add_column("Component / API", style="bold", width=30)
        table.add_column("Status", width=15)
        table.add_column("Details", style="dim")

        table.add_row("Python Executable", "✅ Valid", sys.executable)
        table.add_row("Python Version", "✅ Valid", sys.version.split()[0])
        table.add_row(
            "Gemini API (Primary)",
            "✅ Present" if settings.has_gemini else "❌ Missing",
            "Required for Live Analysis (Set in .env or via UI)",
        )
        table.add_row(
            "YouTube Data API",
            "✅ Present" if settings.has_youtube else "⚠️ Optional",
            "Enables official YouTube comment scraping",
        )
        table.add_row(
            "Reddit PRAW API",
            "✅ Present" if settings.has_reddit else "⚠️ Optional",
            "Enables official Reddit community post & comment crawling",
        )
        table.add_row(
            "Tavily Search API",
            "✅ Present" if settings.has_tavily else "⚠️ Optional",
            "Powers cross-web deep discovery (falls back to DDG)",
        )
        table.add_row(
            "SerpApi (Google Lens)",
            "✅ Present" if settings.has_serpapi else "⚠️ Optional",
            "Enables reverse-image search discovery",
        )
        table.add_row(
            "Apify Client API",
            "✅ Present" if settings.has_apify else "⚠️ Optional",
            "Optional actor plugin for Amazon/Instagram/TikTok",
        )

        # Cache & Output stats
        from core.cache import get_cache
        cache = get_cache()
        stats = cache.stats()
        table.add_row("SQLite Cache", "✅ Active", f"{stats.get('total_entries', 0)} cached items ({settings.cache_db_path})")

        out_dir = Path(settings.outputs_dir)
        xlsx_count = len(list(out_dir.glob("*.xlsx"))) if out_dir.exists() else 0
        table.add_row("Reports Directory", "✅ Active", f"{xlsx_count} .xlsx reports in '{settings.outputs_dir}'")

        console.print(table)
        print()
    else:
        print("\n=== Omni Review Intel System Status ===")
        print(f"Python:       {sys.version.split()[0]} ({sys.executable})")
        print(f"Gemini API:   {'✅ Present' if settings.has_gemini else '❌ Missing'}")
        print(f"YouTube API:  {'✅ Present' if settings.has_youtube else '⚠️ Optional'}")
        print(f"Reddit API:   {'✅ Present' if settings.has_reddit else '⚠️ Optional'}")
        print(f"Tavily API:   {'✅ Present' if settings.has_tavily else '⚠️ Optional'}")
        print(f"SerpApi:      {'✅ Present' if settings.has_serpapi else '⚠️ Optional'}")
        print(f"Apify:        {'✅ Present' if settings.has_apify else '⚠️ Optional'}\n")


def cmd_cache(action: str = "stats") -> None:
    """Manage local SQLite cache."""
    from core.cache import get_cache
    from config import settings

    cache = get_cache()
    if action == "clear":
        count = cache.clear_all()
        print(f"\n🗑️  Cleared {count} entries from cache ({settings.cache_db_path}).\n")
    else:
        stats = cache.stats()
        db_file = Path(settings.cache_db_path)
        size_kb = db_file.stat().st_size / 1024 if db_file.exists() else 0
        print(f"\n💾 Cache Status ({settings.cache_db_path}):")
        print(f"   Total Entries: {stats.get('total_entries', 0)}")
        print(f"   Database Size: {size_kb:.1f} KB\n")


async def _run_analysis(url: str, output_dir: str, use_rich: bool) -> str:
    """Internal runner for live pipeline analysis."""
    from core.pipeline import run_pipeline

    if use_rich:
        from rich.console import Console
        from rich.progress import Progress, SpinnerColumn, TextColumn
        from rich.table import Table

        console = Console()
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=False,
        ) as prog:
            task_id = prog.add_task("[cyan]Initializing pipeline...", total=5)

            async def cb(stage: str, message: str) -> None:
                prog.update(task_id, description=f"[cyan][{stage}] {message}")
                if message.startswith(("Topic identified", "Discovered", "Collected", "Analysis complete", "Report ready")):
                    prog.advance(task_id)

            state = await run_pipeline(input_url=url, output_dir=output_dir, progress_cb=cb)

        review = state.executive_review
        table = Table(title=f"Pipeline Summary — {review.topic_name if review else url}", show_lines=True, border_style="blue")
        table.add_column("Field", style="bold cyan", width=25)
        table.add_column("Value", style="white")

        if review:
            sentiment_color = {"Positive": "green", "Neutral": "yellow", "Negative": "red"}.get(
                review.overall_sentiment, "white"
            )
            table.add_row("Target URL", url)
            table.add_row("Topic", review.topic_name)
            table.add_row("Sentiment", f"[{sentiment_color}]{review.overall_sentiment}[/{sentiment_color}]")
            table.add_row("AI Score", f"{review.ai_score_out_of_10:.1f} / 10.0")
            table.add_row("Sources Scanned", str(len(state.sources)))
            table.add_row("Analyzed Comments", str(state.total_comments_valid))
            table.add_row(
                "Sentiment Split",
                f"[green]{state.positive_pct:.1f}%[/green] pos | "
                f"[yellow]{state.neutral_pct:.1f}%[/yellow] neu | "
                f"[red]{state.negative_pct:.1f}%[/red] neg",
            )
            table.add_row("Dedicated Report", state.output_xlsx_path)

        console.print(table)
        if state.stage_errors:
            console.print(f"[yellow]⚠️  Warnings:[/yellow] {'; '.join(state.stage_errors)}")

        console.print(f"\n[bold green]✅ Report generated:[/bold green] {state.output_xlsx_path}\n")

        if sys.platform == "win32" and os.path.exists(state.output_xlsx_path):
            try:
                os.startfile(str(Path(state.output_xlsx_path).resolve()))
                console.print(f"[bold cyan]📂 Automatically opened Excel report:[/bold cyan] {state.output_xlsx_path}\n")
            except Exception:
                pass
        return state.output_xlsx_path
    else:
        async def cb(stage: str, message: str) -> None:
            print(f"[{stage}] {message}", flush=True)

        state = await run_pipeline(input_url=url, output_dir=output_dir, progress_cb=cb)
        review = state.executive_review
        if review:
            print(f"\n=== Pipeline Complete for {url} ===")
            print(f"Topic:    {review.topic_name}")
            print(f"Score:    {review.ai_score_out_of_10:.1f}/10")
            print(f"Sources:  {len(state.sources)}")
            print(f"Comments: {state.total_comments_valid}")
            print(f"Output:   {state.output_xlsx_path}\n")

        if sys.platform == "win32" and os.path.exists(state.output_xlsx_path):
            try:
                os.startfile(str(Path(state.output_xlsx_path).resolve()))
                print(f"📂 Automatically opened Excel report: {state.output_xlsx_path}\n")
            except Exception:
                pass
        return state.output_xlsx_path


def _configure_key_prompt() -> None:
    """Prompt user to set or update GEMINI_API_KEY and save to .env."""
    print("\n🔑 Gemini API Key Configuration")
    print("Get your free Gemini API key at: https://aistudio.google.com/app/apikey")
    key = input("Enter your Gemini API key (or press Enter to cancel): ").strip()
    if key:
        env_path = Path(".env")
        lines = []
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("GEMINI_API_KEY="):
                    continue
                lines.append(line)
        lines.append(f"GEMINI_API_KEY={key}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.environ["GEMINI_API_KEY"] = key
        try:
            from config import settings
            settings.gemini_api_key = key
            from stages._llm_client import get_llm_client
            get_llm_client(force_refresh=True)
        except Exception:
            pass
        print(f"✅ Gemini API key saved to {env_path.resolve()}.\n")


def cmd_analyze(urls: list[str] | str, output_dir: str = "outputs", verbose: bool = False) -> list[str]:
    """Execute live 5-stage analysis pipeline on one or more URLs, generating separate reports for each."""
    _setup_logging(verbose)
    use_rich = _try_rich()

    if isinstance(urls, str):
        raw_tokens = urls.replace(",", " ").split()
        url_list = [t.strip() for t in raw_tokens if t.strip()]
    else:
        url_list = [u.strip() for u in urls if u and u.strip()]

    if not url_list:
        print("❌ Error: No target URLs provided.", file=sys.stderr)
        sys.exit(1)

    generated_reports: list[str] = []
    total_targets = len(url_list)

    for idx, target_url in enumerate(url_list, start=1):
        if total_targets > 1:
            print("\n" + "=" * 70)
            print(f"🎯 Processing Target [{idx}/{total_targets}]: {target_url}")
            print("=" * 70)
        try:
            report_path = asyncio.run(_run_analysis(target_url, output_dir, use_rich))
            if report_path:
                generated_reports.append(report_path)
        except KeyboardInterrupt:
            print("\nInterrupted by user.")
            sys.exit(130)
        except Exception as e:
            print(f"\nPipeline execution error for {target_url}: {e}", file=sys.stderr)
            if verbose:
                logger.exception(e)

    if total_targets > 1 and generated_reports:
        print("\n" + "=" * 70)
        print("✅ ALL TARGET LINKS PROCESSED — SEPARATE EXCEL REPORTS GENERATED:")
        print("=" * 70)
        for i, rep in enumerate(generated_reports, start=1):
            print(f"  {i}. {rep}")
        print("=" * 70 + "\n")

    return generated_reports


# ── Interactive Menu ──────────────────────────────────────────────────────────

def cmd_menu() -> None:
    """Display 1-click URL input and interactive command center."""
    while True:
        print("\n" + "=" * 70)
        print("  🛒  OMNI REVIEW INTEL — 1-CLICK AUTOMATED REVIEW SYSTEM")
        print("=" * 70)
        print("  Give any product link, video link, or review URL.")
        print("  The system will find all sites where it appears, collect comments,")
        print("  perform sentiment analysis, and generate your Excel workbook!")
        print("=" * 70)
        print("  [Options: 1=Web Dashboard | 2=Instant Demo | 3=API Key | 4=Tests | 0=Exit]")
        print("=" * 70)

        try:
            choice = input("\n👉 Paste URL to analyze (or enter option number) [1]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break

        if not choice or choice == "1":
            cmd_ui()
            break
        elif choice.startswith(("http://", "https://")):
            cmd_analyze(urls=choice)
            break
        elif choice == "2":
            cmd_demo()
        elif choice == "3":
            _configure_key_prompt()
        elif choice == "4":
            cmd_test(ui_only=False)
        elif choice == "5":
            cmd_test(ui_only=True)
        elif choice == "6":
            cmd_status()
        elif choice == "7":
            action = input("Type 'stats' to inspect or 'clear' to wipe cache [stats]: ").strip().lower()
            if action in ("clear", "c"):
                cmd_cache("clear")
            else:
                cmd_cache("stats")
        elif choice == "8":
            from config import settings
            out_path = Path(settings.outputs_dir).resolve()
            out_path.mkdir(parents=True, exist_ok=True)
            print(f"\n📂 Reports Directory: {out_path}")
            if sys.platform == "win32":
                os.startfile(str(out_path))
        elif choice in ("0", "exit", "quit", "q"):
            print("Goodbye!")
            break
        else:
            if "." in choice and not choice.isdigit():
                cmd_analyze(urls=choice)
                break
            else:
                print("⚠️ Invalid choice. Please paste a URL or enter an option number.")


# ── CLI Argument Parser ───────────────────────────────────────────────────────

def main() -> None:
    pkg_dir = Path(__file__).resolve().parent
    if str(pkg_dir) not in sys.path:
        sys.path.insert(0, str(pkg_dir))
    os.chdir(str(pkg_dir))

    # Quick detection for direct URL positional parameter(s) or legacy flag
    raw_args = sys.argv[1:]

    # If no arguments provided, launch interactive menu
    if not raw_args:
        cmd_menu()
        return

    # If all positional arguments look like URLs
    if all(a.startswith(("http://", "https://")) for a in raw_args):
        cmd_analyze(urls=raw_args)
        return

    parser = argparse.ArgumentParser(
        prog="omni-review-intel",
        description="Unified Command Center for Omni Review Intel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Subparsers for clean subcommands
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # ui subcommand
    ui_parser = subparsers.add_parser("ui", help="Launch Amazon-style Streamlit Web UI")
    ui_parser.add_argument("--port", type=int, default=8501, help="Port to bind Streamlit server")
    ui_parser.add_argument("--host", default="localhost", help="Host address for Streamlit server")

    # analyze subcommand (supports single or multiple URLs)
    analyze_parser = subparsers.add_parser("analyze", help="Analyze one or more product, video, or review URLs")
    analyze_parser.add_argument("urls", nargs="*", default=[], help="One or more target URLs (separated by space)")
    analyze_parser.add_argument("--url", help="Single URL to analyze (legacy flag)")
    analyze_parser.add_argument("--output-dir", default="outputs", help="Directory for generated .xlsx")
    analyze_parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")

    # demo subcommand
    demo_parser = subparsers.add_parser("demo", help="Generate sample report without requiring API keys")
    demo_parser.add_argument("--output-dir", default="outputs", help="Directory for generated .xlsx")

    # test subcommand
    test_parser = subparsers.add_parser("test", help="Run automated test suite (70 tests)")
    test_parser.add_argument("--ui", action="store_true", help="Run Streamlit AppTest GUI suite only")
    test_parser.add_argument("--verbose", "-v", action="store_true", default=True, help="Verbose test output")

    # status subcommand
    subparsers.add_parser("status", help="Display system diagnostics and API key readiness")

    # cache subcommand
    cache_parser = subparsers.add_parser("cache", help="Inspect or clear local SQLite cache")
    cache_parser.add_argument("action", choices=["stats", "clear"], nargs="?", default="stats")

    # menu subcommand
    subparsers.add_parser("menu", help="Launch interactive command center menu")

    # Also support top-level legacy flags: --url, --output-dir, --verbose
    parser.add_argument("--url", help="Legacy flag: Target URL to analyze")
    parser.add_argument("--output-dir", default="outputs", help="Directory to save .xlsx")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Route based on parsed arguments
    if args.command == "ui":
        cmd_ui(port=args.port, host=args.host)
    elif args.command == "demo":
        cmd_demo(output_dir=args.output_dir)
    elif args.command == "test":
        cmd_test(ui_only=args.ui, verbose=args.verbose)
    elif args.command == "status":
        cmd_status()
    elif args.command == "cache":
        cmd_cache(action=args.action)
    elif args.command == "menu":
        cmd_menu()
    elif args.command == "analyze":
        target_urls = args.urls or ([args.url] if getattr(args, "url", None) else [])
        if not target_urls:
            print("❌ Error: analyze command requires at least one URL. Example: python main.py analyze https://...", file=sys.stderr)
            sys.exit(1)
        cmd_analyze(urls=target_urls, output_dir=args.output_dir, verbose=args.verbose)
    elif getattr(args, "url", None):
        cmd_analyze(urls=args.url, output_dir=args.output_dir, verbose=args.verbose)
    else:
        cmd_menu()


if __name__ == "__main__":
    main()
