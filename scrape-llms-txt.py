# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx>=0.27.0",
#     "loguru>=0.7.2",
#     "rich>=13.7.0",
# ]
# ///

"""
Scrape and download markdown files from llms.txt URLs.

This script parses llms.txt files and downloads all referenced markdown files
to an organized directory structure: downloads/MM-DD-YYYY/domain_name/<filename.md>
"""

import asyncio
import itertools
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from loguru import logger
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

# Type aliases for Python 3.13
type URLString = str
type FilePath = Path
type DomainName = str
type ParsedLLMSTxt = dict[str, str | dict[str, list[dict[str, str]]]]

# Constants
MAX_FILENAME_DISPLAY_LEN = 35
MAX_CONCURRENT_DOWNLOADS = 10


@dataclass
class DownloadContext:
    """Context for tracking download progress."""

    overall_progress: Progress
    overall_task_id: TaskID
    file_progress: Progress
    semaphore: asyncio.Semaphore


def parse_llms_txt(txt: str) -> ParsedLLMSTxt:
    """
    Parse llms.txt file contents to a dict.

    This is a robust parser that handles various llms.txt formats,
    including files without summary sections.

    Args:
        txt: Raw text content of llms.txt file

    Returns:
        Dictionary with title, summary, info, and sections

    """

    def _chunked(it: iter, chunk_sz: int) -> iter:
        it = iter(it)
        return iter(lambda: list(itertools.islice(it, chunk_sz)), [])

    def _parse_links(links: str) -> list[dict[str, str]]:
        link_pat = r"-\s*\[(?P<title>[^\]]+)\]\((?P<url>[^\)]+)\)(?::\s*(?P<desc>.*))?"
        return [
            m.groupdict()
            for line in re.split(r"\n+", links.strip())
            if line.strip() and (m := re.search(link_pat, line))
        ]

    # Split into header and sections
    start, *rest = re.split(r"^##\s*(.*?$)", txt, flags=re.MULTILINE)

    # Parse sections
    sects = {k: _parse_links(v) for k, v in dict(_chunked(rest, 2)).items()}

    # Parse header - handle both with and without summary
    # Pattern with optional summary (blockquote)
    pat_with_summary = (
        r"^#\s*(?P<title>.+?$)\n+"
        r"(?:^>\s*(?P<summary>.+?$)\n+)?"
        r"(?P<info>.*)"
    )

    match = re.search(pat_with_summary, start.strip(), re.MULTILINE | re.DOTALL)

    if match:
        d = match.groupdict()
        d["summary"] = d.get("summary") or ""
        d["info"] = (d.get("info") or "").strip()
    else:
        # Fallback: just extract title
        title_match = re.search(r"^#\s*(.+?)$", start.strip(), re.MULTILINE)
        d = {
            "title": title_match.group(1) if title_match else "Unknown",
            "summary": "",
            "info": "",
        }

    d["sections"] = sects
    return d


class LLMSTxtScraper:
    """Scraper for downloading markdown files from llms.txt sources."""

    def __init__(
        self,
        llms_txt_url: URLString,
        base_output_dir: FilePath = Path("downloads"),
    ) -> None:
        """
        Initialize the scraper.

        Args:
            llms_txt_url: URL or file path to the llms.txt file
            base_output_dir: Base directory for downloads

        """
        self.llms_txt_url = llms_txt_url
        self.base_output_dir = base_output_dir
        self.console = Console()
        self.session: httpx.AsyncClient | None = None

        # Configure loguru
        logger.remove()
        logger.add(
            sys.stderr,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
            level="INFO",
        )

    def _get_output_directory(self, domain: DomainName) -> FilePath:
        """
        Create and return the output directory path.

        Args:
            domain: Domain name for organizing downloads

        Returns:
            Path object for the output directory

        """
        date_str = datetime.now(UTC).strftime("%m-%d-%Y")
        output_dir = self.base_output_dir / date_str / domain
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _extract_domain(self, url: URLString) -> DomainName:
        """
        Extract domain name from URL.

        Args:
            url: URL string

        Returns:
            Domain name without protocol and www prefix

        """
        parsed = urlparse(url)
        domain = parsed.netloc or "local"
        # Remove www. prefix if present
        domain = domain.removeprefix("www.")
        return domain.replace(":", "_")  # Replace colons for filesystem compatibility

    def _extract_filename(self, url: URLString) -> str:
        """
        Extract filename from URL.

        Args:
            url: URL string

        Returns:
            Filename with .md extension

        """
        parsed = urlparse(url)
        path = parsed.path
        filename = Path(path).name

        if not filename:
            filename = "index.md"
        elif not filename.endswith(".md"):
            filename = f"{filename}.md"

        # Sanitize filename
        filename = filename.replace("/", "_").replace("\\", "_")
        return filename

    async def _fetch_llms_txt(self) -> str:
        """
        Fetch the llms.txt content from URL or file.

        Returns:
            Content of the llms.txt file

        Raises:
            Exception: If fetching fails

        """
        if self.llms_txt_url.startswith(("http://", "https://")):
            logger.info(f"Fetching llms.txt from URL: {self.llms_txt_url}")
            try:
                response = await self.session.get(
                    self.llms_txt_url,
                    follow_redirects=True,
                )
                response.raise_for_status()
            except httpx.HTTPError as e:
                logger.error(f"Failed to fetch llms.txt: {e}")
                raise
            else:
                return response.text
        else:
            logger.info(f"Reading llms.txt from file: {self.llms_txt_url}")
            try:
                return Path(self.llms_txt_url).read_text(encoding="utf-8")
            except OSError as e:
                logger.error(f"Failed to read llms.txt file: {e}")
                raise

    def _collect_urls(
        self,
        parsed_data: dict,
    ) -> list[tuple[URLString, DomainName, str]]:
        """
        Collect all markdown URLs from parsed llms.txt data.

        Args:
            parsed_data: Parsed llms.txt data structure

        Returns:
            List of tuples (url, domain, filename)

        """
        urls: list[tuple[URLString, DomainName, str]] = []

        sections = parsed_data.get("sections", {})
        for section_name, links in sections.items():
            # Skip optional section if desired (can be made configurable)
            # if section_name.lower() == "optional":
            #     continue

            for link in links:
                url = link.get("url", "")
                if url:
                    domain = self._extract_domain(url)
                    filename = self._extract_filename(url)
                    urls.append((url, domain, filename))
                    logger.debug(f"Found URL in section '{section_name}': {url}")

        return urls

    async def _download_file(
        self,
        url: URLString,
        domain: DomainName,
        filename: str,
        ctx: DownloadContext,
    ) -> tuple[bool, URLString]:
        """
        Download a single file with nested progress tracking.

        Args:
            url: URL to download
            domain: Domain name for organizing
            filename: Output filename
            ctx: Download context with progress bars and semaphore

        Returns:
            Tuple of (success, url)

        """
        async with ctx.semaphore:
            output_dir = self._get_output_directory(domain)
            output_path = output_dir / filename

            # Create task for this file download
            display_name = (
                filename[:MAX_FILENAME_DISPLAY_LEN] + "..."
                if len(filename) > MAX_FILENAME_DISPLAY_LEN
                else filename
            )
            file_task_id = ctx.file_progress.add_task(
                f"[cyan]{display_name}",
                total=None,
                visible=True,
            )

            try:
                async with self.session.stream(
                    "GET",
                    url,
                    follow_redirects=True,
                ) as response:
                    response.raise_for_status()

                    total_size = int(response.headers.get("content-length", 0))
                    ctx.file_progress.update(
                        file_task_id,
                        total=total_size if total_size > 0 else None,
                    )

                    with output_path.open("wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            f.write(chunk)
                            ctx.file_progress.update(file_task_id, advance=len(chunk))

            except httpx.HTTPError as e:
                logger.error(f"✗ Failed to download {url}: {e}")
                ctx.file_progress.remove_task(file_task_id)
                ctx.overall_progress.advance(ctx.overall_task_id)
                return (False, url)
            except OSError as e:
                logger.error(f"✗ Failed to write {output_path}: {e}")
                ctx.file_progress.remove_task(file_task_id)
                ctx.overall_progress.advance(ctx.overall_task_id)
                return (False, url)
            else:
                # Remove the file task once complete
                ctx.file_progress.remove_task(file_task_id)
                ctx.overall_progress.advance(ctx.overall_task_id)
                return (True, url)

    def _create_progress_group(
        self,
        overall_progress: Progress,
        file_progress: Progress,
    ) -> Panel:
        """Create a panel containing both progress bars."""
        return Panel(
            Group(overall_progress, file_progress),
            title="[bold blue]Download Progress",
            border_style="blue",
        )

    async def _download_all(
        self,
        urls: list[tuple[URLString, DomainName, str]],
    ) -> tuple[int, int]:
        """
        Download all files concurrently with nested progress tracking.

        Args:
            urls: List of (url, domain, filename) tuples

        Returns:
            Tuple of (successful_count, failed_count)

        """
        if not urls:
            logger.warning("No URLs to download")
            return (0, 0)

        # Overall progress bar
        overall_progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold green]Overall Progress"),
            BarColumn(bar_width=40),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
        )

        # Individual file progress bars
        file_progress = Progress(
            TextColumn("  "),
            SpinnerColumn(),
            TextColumn("[cyan]{task.description}"),
            BarColumn(bar_width=30),
            DownloadColumn(),
            TransferSpeedColumn(),
        )

        # Limit concurrent downloads for cleaner visual
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

        # Add overall task
        overall_task_id = overall_progress.add_task(
            "Downloading",
            total=len(urls),
        )

        # Create download context
        ctx = DownloadContext(
            overall_progress=overall_progress,
            overall_task_id=overall_task_id,
            file_progress=file_progress,
            semaphore=semaphore,
        )

        progress_group = self._create_progress_group(overall_progress, file_progress)

        with Live(progress_group, console=self.console, refresh_per_second=10):
            tasks = [
                self._download_file(url, domain, filename, ctx)
                for url, domain, filename in urls
            ]

            results = await asyncio.gather(*tasks, return_exceptions=False)

        successful = sum(1 for success, _ in results if success)
        failed = len(results) - successful

        return (successful, failed)

    async def scrape(self) -> None:
        """Main scraping workflow."""
        try:
            # Create async HTTP client
            async with httpx.AsyncClient(timeout=30.0) as client:
                self.session = client

                # Fetch and parse llms.txt
                self.console.print(
                    "\n[bold green]Starting llms.txt scraper...[/bold green]\n",
                )
                llms_content = await self._fetch_llms_txt()

                logger.info("Parsing llms.txt content")
                parsed_data = parse_llms_txt(llms_content)

                title = parsed_data.get("title", "Unknown")
                self.console.print(f"[bold]Project:[/bold] {title}\n")

                # Collect URLs
                urls = self._collect_urls(parsed_data)
                logger.info(f"Found {len(urls)} markdown files to download")

                if not urls:
                    self.console.print(
                        "[yellow]No markdown URLs found in llms.txt[/yellow]",
                    )
                    return

                # Download files
                self.console.print(f"[bold]Downloading {len(urls)} files...[/bold]\n")
                successful, failed = await self._download_all(urls)

                # Summary
                self.console.print(
                    f"\n[bold green]✓ Successfully downloaded: {successful}[/bold green]",
                )
                if failed > 0:
                    self.console.print(f"[bold red]✗ Failed: {failed}[/bold red]")

                self.console.print(
                    f"\n[bold]Files saved to:[/bold] {self.base_output_dir.absolute()}\n",
                )

        except (httpx.HTTPError, OSError, ValueError) as e:
            logger.exception(f"Scraping failed: {e}")
            self.console.print(f"\n[bold red]Error: {e}[/bold red]\n")
            sys.exit(1)


async def main() -> None:
    """Entry point for the scraper."""
    min_args = 2
    if len(sys.argv) < min_args:
        print("Usage: python scrape-llms-txt.py <llms.txt URL or file path>")
        print("\nExample:")
        print("  python scrape-llms-txt.py https://www.fastht.ml/docs/llms.txt")
        print("  python scrape-llms-txt.py ./llms.txt")
        sys.exit(1)

    llms_txt_source = sys.argv[1]
    scraper = LLMSTxtScraper(llms_txt_source)
    await scraper.scrape()


if __name__ == "__main__":
    asyncio.run(main())
