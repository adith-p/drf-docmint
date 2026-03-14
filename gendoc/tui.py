import os
import json
import re
from pathlib import Path

import typer
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Header,
    Footer,
    Input,
    Select,
    Button,
    RichLog,
    Label,
    DirectoryTree,
    TabbedContent,
    TabPane,
    Markdown,
    Switch,
    Checkbox,
)
from textual.screen import ModalScreen
from textual.suggester import Suggester

from .parser import scan_project, parse_schema_file
from .openapi import generate_openapi_spec
from .postman import generate_postman_collection
from .renderer import generate_markdown
from .converters import convert_to_pdf, convert_to_html
from .utils import YAML_AVAILABLE

FORMATS = [
    ("Markdown (.md)", "md"),
    ("Standalone HTML (.html)", "html"),
    ("PDF Document (.pdf)", "pdf"),
    ("OpenAPI (JSON)", "json"),
    ("OpenAPI (YAML)", "yaml"),
    ("Postman Collection", "postman"),
]

ABOUT_MD = """
# drf-docmint

A zero-runtime static API documentation generator for Django REST Framework.

## Features
- **Static Analysis (AST)**: Scans your project without loading the Django environment.
- **Auto-Detection**: Intelligently detects `drf-spectacular` or `drf-yasg` schemas.
- **Multiple Formats**: Export docs to Markdown, HTML, PDF, OpenAPI, and Postman Collections.

---
*Developed for DRF engineers. TUI powered by Textual.*
"""

PAGE_LINES = 100  # lines per page
WINDOW = 3  # pages kept in DOM at once


def split_chunks(lines: list, size: int) -> list:
    return ["\n".join(lines[i : i + size]) for i in range(0, len(lines), size)]


class PathSuggester(Suggester):
    async def get_suggestion(self, value: str) -> str | None:
        if not value:
            return None
        try:
            path = Path(value).expanduser()
            if value.endswith(os.sep) or (os.altsep and value.endswith(os.altsep)):
                dir_path, prefix = path, ""
            else:
                dir_path, prefix = path.parent, path.name
            if dir_path.is_dir():
                for p in dir_path.iterdir():
                    if p.name.startswith(prefix):
                        m = str(p) + (os.sep if p.is_dir() else "")
                        if value.startswith("~"):
                            m = m.replace(str(Path.home()), "~", 1)
                        return m
        except Exception:
            pass
        return None


class FilePickerModal(ModalScreen):
    CSS = """
    FilePickerModal { align: center middle; background: $background 80%; }
    #picker-dialog { width: 80%; height: 80%; background: $panel; border: thick $primary; padding: 1 2; }
    #picker-tree { height: 1fr; border: round $secondary; margin-bottom: 1; background: $surface; }
    .picker-nav { height: auto; margin-bottom: 1; margin-top: 1; }
    .picker-nav Button { margin-right: 1; min-width: 15; }
    .picker-buttons { height: auto; align: right middle; }
    .picker-buttons Button { margin-left: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Label("Browse File System")
            with Horizontal(classes="picker-nav"):
                yield Button("Current Dir", id="btn-nav-cwd", variant="default")
                yield Button("Home", id="btn-nav-home", variant="default")
                yield Button("Root", id="btn-nav-root", variant="default")
            yield DirectoryTree(Path(".").resolve(), id="picker-tree")
            with Horizontal(classes="picker-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="error")
                yield Button("Select Highlighted", id="btn-select", variant="success")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        tree = self.query_one("#picker-tree", DirectoryTree)
        if event.button.id == "btn-nav-cwd":
            tree.path = Path(".").resolve()
        elif event.button.id == "btn-nav-home":
            tree.path = Path.home()
        elif event.button.id == "btn-nav-root":
            tree.path = Path(os.path.abspath(os.sep))
        elif event.button.id == "btn-cancel":
            self.dismiss(None)
        elif event.button.id == "btn-select":
            self.dismiss(
                str(tree.cursor_node.data.path)
                if tree.cursor_node and tree.cursor_node.data
                else None
            )

    def on_directory_tree_file_selected(
        self, event: DirectoryTree.FileSelected
    ) -> None:
        self.dismiss(str(event.path))


class DocMintApp(App):
    TITLE = "drf-docmint"
    SUB_TITLE = "API Documentation Generator"

    CSS = """
    Screen { background: $background; }
    TabPane { padding: 1 2; }

    #sidebar  { width: 40%; border-right: vkey $primary; padding-right: 2; height: 100%; }
    #log-area { width: 60%; padding-left: 2; height: 100%; }
    #console-log { border: round $accent; height: 1fr; background: $surface; padding: 0 1; }

    .section-title { text-style: bold; margin-top: 1; margin-bottom: 1; }
    .input-group { height: auto; margin-bottom: 1; }
    .input-group Input { width: 1fr; }
    .icon-btn { min-width: 5; width: 5; margin-left: 1; background: $boost; }
    .icon-btn:hover { background: $secondary; }
    .checkbox-row { height: auto; margin-top: 1; }
    #theme-toggle-container { height: auto; margin-bottom: 1; align: left middle; }
    #theme-label { margin-top: 1; margin-right: 2; text-style: bold; }
    Select { margin-bottom: 1; }
    #generate-btn { width: 100%; margin-top: 2; text-style: bold; }

    /* Preview tab */
    #tab-preview    { height: 1fr; padding: 0; }

    /* Compact nav bar: prev · page x/n · next  — sits above the content */
    #preview-nav    {
        height: auto; padding: 0 1;
        background: $panel;
        align: center middle;
        display: none;
    }
    #nav-prev       { min-width: 6;  width: 6;  margin-right: 1; }
    #nav-next       { min-width: 6;  width: 6;  margin-left: 1;  }
    #nav-label      { width: 1fr; content-align: center middle; }

    #preview-scroll { height: 1fr; width: 1fr; border: round $success; background: $surface; }
    .md-chunk       { height: auto; padding: 0 2; }

    #preview-rich   { height: 1fr; width: 1fr; border: round $success; background: $surface; display: none; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("d", "toggle_dark", "Toggle Dark Mode"),
        ("ctrl+c", "quit", "Quit"),
        ("left", "prev_page", "Prev page"),
        ("right", "next_page", "Next page"),
    ]

    # ── paged state ───────────────────────────────────────────────────────
    _chunks: list = []
    _page: int = 0  # currently displayed page index
    _total_lines: int = 0
    _fmt: str = ""

    # ── compose ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with TabbedContent(initial="tab-generator"):
            with TabPane("Generator", id="tab-generator"):
                with Horizontal():
                    with VerticalScroll(id="sidebar"):
                        with Horizontal(id="theme-toggle-container"):
                            yield Label("Dark Mode", id="theme-label")
                            yield Switch(value=True, id="theme-switch")

                        yield Label("Target Project / Schema", classes="section-title")
                        with Horizontal(classes="input-group"):
                            yield Input(
                                value=".", id="target-input", suggester=PathSuggester()
                            )
                            yield Button(
                                "browse", id="browse-target", classes="icon-btn"
                            )

                        yield Label("Output Format", classes="section-title")
                        yield Select(FORMATS, value="md", id="format-select")

                        yield Label(
                            "Output Destination (Optional)", classes="section-title"
                        )
                        with Horizontal(classes="input-group"):
                            yield Input(
                                placeholder="e.g. docs/",
                                id="dest-input",
                                suggester=PathSuggester(),
                            )
                            yield Button("browse", id="browse-dest", classes="icon-btn")

                        yield Label("API Version Filter", classes="section-title")
                        yield Input(
                            placeholder="e.g. 'v1', 'all' (Leave blank for none)",
                            id="api-version-input",
                        )

                        with Horizontal(classes="checkbox-row"):
                            yield Checkbox("Auto-Open File", id="auto-open-chk")
                            yield Checkbox("Verbose Logging", id="verbose-chk")

                        yield Button(
                            "Generate Documentation",
                            variant="success",
                            id="generate-btn",
                        )

                    with Vertical(id="log-area"):
                        yield RichLog(id="console-log", highlight=True, markup=True)

            with TabPane("Preview", id="tab-preview"):
                # compact ‹ page x / n › nav bar
                with Horizontal(id="preview-nav"):
                    yield Button("‹", id="nav-prev", variant="default")
                    yield Label("", id="nav-label")
                    yield Button("›", id="nav-next", variant="default")
                # Markdown pages (md)
                with VerticalScroll(id="preview-scroll"):
                    pass
                # Rich syntax (json/yaml/postman)
                yield RichLog(id="preview-rich", highlight=True, markup=True)

            with TabPane("About", id="tab-about"):
                with VerticalScroll():
                    yield Markdown(ABOUT_MD, id="about-md")

        yield Footer()

    def on_mount(self) -> None:
        log = self.query_one("#console-log", RichLog)
        log.border_title = "Execution Log"
        log.write("[dim]System ready. Awaiting configuration...[/dim]")

    # ── page rendering ────────────────────────────────────────────────────

    def _render_page(self, page: int) -> None:
        """Replace the scroll container content with the given page and scroll to top."""
        if not self._chunks:
            return
        page = max(0, min(page, len(self._chunks) - 1))
        self._page = page

        scroll = self.query_one("#preview-scroll", VerticalScroll)

        # Remove existing chunk widgets
        for child in list(scroll.children):
            child.remove()

        # Mount just this one page
        scroll.mount(Markdown(self._chunks[page], classes="md-chunk"))

        # Scroll to top immediately
        scroll.scroll_home(animate=False)

        # Update nav bar
        self._refresh_nav()

        # Pre-fetch neighbours off-thread (both directions)
        self._prefetch(page - 1)
        self._prefetch(page + 1)

    def _refresh_nav(self) -> None:
        total = len(self._chunks)
        page = self._page
        label = self.query_one("#nav-label", Label)
        label.update(f"[dim]page {page + 1} / {total}[/dim]")

        self.query_one("#nav-prev", Button).disabled = page == 0
        self.query_one("#nav-next", Button).disabled = page >= total - 1

    # ── pre-fetcher (warms the chunk string — no-op here but slot for future cache) ──

    @work(thread=True)
    def _prefetch(self, page: int) -> None:
        if 0 <= page < len(self._chunks):
            _ = self._chunks[page]  # touch it; extend here if you add compression

    # ── init from generation result ───────────────────────────────────────

    def _init_md_preview(self, chunks: list, total_lines: int) -> None:
        self._chunks = chunks
        self._total_lines = total_lines
        self._fmt = "md"

        self.query_one("#preview-scroll").display = True
        self.query_one("#preview-rich", RichLog).display = False
        self.query_one("#preview-nav").display = True

        self._render_page(0)
        self.query_one(TabbedContent).active = "tab-preview"

    def _show_rich_preview(self, content: str, fmt: str) -> None:
        from rich.syntax import Syntax

        self._fmt = fmt

        self.query_one("#preview-scroll").display = False
        self.query_one("#preview-nav").display = False

        rich = self.query_one("#preview-rich", RichLog)
        rich.display = True
        rich.clear()
        lang = "json" if fmt in ("json", "postman") else "yaml"
        rich.write(Syntax(content, lang, theme="monokai", line_numbers=True))
        self.query_one(TabbedContent).active = "tab-preview"

    # ── nav button + keyboard actions ─────────────────────────────────────

    def action_prev_page(self) -> None:
        if self._fmt == "md" and self._page > 0:
            self._render_page(self._page - 1)

    def action_next_page(self) -> None:
        if self._fmt == "md" and self._page < len(self._chunks) - 1:
            self._render_page(self._page + 1)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id

        if bid == "nav-prev":
            self.action_prev_page()
            return
        if bid == "nav-next":
            self.action_next_page()
            return

        if bid == "browse-target":

            def cb(p):
                if p:
                    self.query_one("#target-input", Input).value = p

            self.push_screen(FilePickerModal(), cb)

        elif bid == "browse-dest":

            def cb(p):
                if p:
                    self.query_one("#dest-input", Input).value = p

            self.push_screen(FilePickerModal(), cb)

        elif bid == "generate-btn":
            target = self.query_one("#target-input", Input).value
            fmt = self.query_one("#format-select", Select).value
            dest = self.query_one("#dest-input", Input).value
            api_ver = self.query_one("#api-version-input", Input).value.strip()
            auto_open = self.query_one("#auto-open-chk", Checkbox).value
            verbose = self.query_one("#verbose-chk", Checkbox).value

            log = self.query_one("#console-log", RichLog)
            log.clear()
            log.write("[bold cyan]Starting generation...[/bold cyan]")
            self.run_generation(target, fmt, dest, api_ver, auto_open, verbose)

    # ── theme ─────────────────────────────────────────────────────────────

    def action_toggle_dark(self) -> None:
        self.theme = "textual-light" if self.theme == "textual-dark" else "textual-dark"
        try:
            sw = self.query_one("#theme-switch", Switch)
            with sw.prevent(Switch.Changed):
                sw.value = self.theme == "textual-dark"
        except Exception:
            pass

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "theme-switch":
            self.theme = "textual-dark" if event.value else "textual-light"

    # ── generation worker ─────────────────────────────────────────────────

    @work(thread=True)
    def run_generation(self, target, format, destination, api_ver, auto_open, verbose):
        log = self.query_one("#console-log", RichLog)
        try:
            ext = "json" if format == "postman" else format
            if destination:
                if os.path.splitext(destination)[1]:
                    output_file = destination
                    output_dir = os.path.dirname(output_file)
                else:
                    output_dir = destination
                    output_file = os.path.join(output_dir, f"API_DOCS.{ext}")
            else:
                output_dir = (
                    os.path.join(target, "docs")
                    if os.path.isdir(target)
                    else os.path.join(os.path.dirname(target) or ".", "docs")
                )
                output_file = os.path.join(output_dir, f"API_DOCS.{ext}")

            if output_dir:
                os.makedirs(output_dir, exist_ok=True)

            specs, serializers_map = [], {}
            if os.path.isfile(target) and target.endswith((".json", ".yaml", ".yml")):
                log.write(f"[blue]Parsing schema file: {target}...[/blue]")
                specs, serializers_map = parse_schema_file(target)
            else:
                log.write(f"[blue]Running static analysis on: {target}...[/blue]")

                def log_callback(msg):
                    if verbose:
                        log.write(f"  [green][+][/green] {msg}")
                    elif msg.startswith("Phase"):
                        log.write(f"[cyan]>> {msg}[/cyan]")

                specs, serializers_map = scan_project(target, callback=log_callback)

            if api_ver:
                initial_count = len(specs)
                if api_ver.lower() == "all":
                    specs = [s for s in specs if re.search(r"/v\d+", s["path"])]
                    log.write("[blue]Filtering: all versioned endpoints[/blue]")
                else:
                    vp = api_ver if api_ver.startswith("v") else f"v{api_ver}"
                    specs = [s for s in specs if f"/{vp}" in s["path"].lower()]
                    log.write(f"[blue]Filtering: API version {vp}[/blue]")
                diff = initial_count - len(specs)
                if diff > 0:
                    log.write(f"[yellow]Filtered out {diff} endpoints.[/yellow]")

            log.write(
                f"\n[bold green]Ready to export {len(specs)} endpoints![/bold green]"
            )

            if format == "json":
                spec = generate_openapi_spec(specs, serializers_map)
                with open(output_file, "w") as f:
                    json.dump(spec, f, indent=2)
            elif format == "yaml":
                if not YAML_AVAILABLE:
                    log.write(
                        "[bold red]PyYAML required. Run: uv add PyYAML[/bold red]"
                    )
                    return
                import yaml

                spec = generate_openapi_spec(specs, serializers_map)
                with open(output_file, "w") as f:
                    yaml.dump(spec, f, sort_keys=False)
            elif format == "postman":
                col = generate_postman_collection(specs, serializers_map)
                with open(output_file, "w") as f:
                    json.dump(col, f, indent=2)
            elif format == "html":
                md = generate_markdown(specs, serializers_map, mode="html")
                convert_to_html(md, output_file)
            elif format == "pdf":
                md = generate_markdown(specs, serializers_map, mode="pdf")
                if not convert_to_pdf(md, output_file):
                    output_file = output_file.replace(".pdf", ".md")
                    with open(output_file, "w") as f:
                        f.write(md)
            else:
                md = generate_markdown(specs, serializers_map, mode="md")
                with open(output_file, "w") as f:
                    f.write(md)

            log.write(f"\n[bold green]Success! Saved to: {output_file}[/bold green]")

            if format in ["md", "json", "yaml", "postman"]:
                try:
                    with open(output_file, "r", encoding="utf-8") as f:
                        content = f.read()
                    lines = content.splitlines()
                    log.write(f"[dim]{len(content)} bytes / {len(lines)} lines[/dim]")

                    if format == "md":
                        chunks = split_chunks(lines, PAGE_LINES)
                        log.write(
                            f"[dim]{len(chunks)} pages of {PAGE_LINES} lines[/dim]"
                        )
                        self.call_from_thread(self._init_md_preview, chunks, len(lines))
                    else:
                        self.call_from_thread(self._show_rich_preview, content, format)

                except Exception as e:
                    log.write(f"[bold red]Preview error: {e}[/bold red]")
            else:
                log.write(f"[dim]Format '{format}' opens externally.[/dim]")

            if auto_open:
                log.write(f"[cyan]Opening: {output_file}[/cyan]")
                typer.launch(output_file)

        except Exception as e:
            log.write(f"\n[bold red]Error: {str(e)}[/bold red]")
