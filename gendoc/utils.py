import sys
import os
import importlib.util
from rich.console import Console, Group
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
)
from rich.live import Live
from rich.text import Text

console = Console()

# --- Dependency Checks ---
MARKDOWN_AVAILABLE = importlib.util.find_spec("markdown") is not None
WEASYPRINT_AVAILABLE = importlib.util.find_spec("weasyprint") is not None
YAML_AVAILABLE = importlib.util.find_spec("yaml") is not None


# --- CROSS-PLATFORM INTERACTIVE MENU ---
def interactive_select(title: str, choices: list[str]) -> str:
    """A lightweight, zero-dependency cross-platform arrow-key menu."""

    def get_key():
        if os.name == "nt":  # Windows
            import msvcrt

            while True:
                key = msvcrt.getch()
                if key in (b"\x00", b"\xe0"):  # Arrow keys trigger two bytes in Windows
                    char = msvcrt.getch()
                    if char == b"H":
                        return "up"
                    if char == b"P":
                        return "down"
                if key in (b"\r", b"\n"):
                    return "enter"
                if key == b"\x03":
                    raise KeyboardInterrupt
                return key.decode("utf-8", "ignore")
        else:  # Mac/Linux
            import tty, termios

            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ch = sys.stdin.read(1)
                if ch == "\x1b":  # Escape sequence for arrows
                    ch2 = sys.stdin.read(2)
                    if ch2 == "[A":
                        return "up"
                    if ch2 == "[B":
                        return "down"
                if ch in ("\r", "\n"):
                    return "enter"
                if ch == "\x03":
                    raise KeyboardInterrupt
                return ch
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    idx = 0

    def render_menu():
        lines = [Text(f"? {title}", style="bold cyan")]
        for i, c in enumerate(choices):
            if i == idx:
                lines.append(Text(f" ❯ {c}", style="bold green"))
            else:
                lines.append(Text(f"   {c}", style="dim"))
        return Group(*lines)

    try:
        # Live updates the console in-place without scrolling
        with Live(
            render_menu(), console=console, auto_refresh=False, transient=True
        ) as live:
            while True:
                key = get_key()
                if key == "up":
                    idx = max(0, idx - 1)
                elif key == "down":
                    idx = min(len(choices) - 1, idx + 1)
                elif key == "enter":
                    break
                live.update(render_menu(), refresh=True)

        # Print the final selection so it stays in the terminal history
        console.print(f"[bold cyan]? {title}[/bold cyan] [green]{choices[idx]}[/green]")
        return choices[idx]
    except KeyboardInterrupt:
        console.print("[red]Aborted by user.[/red]")
        sys.exit(1)


# --- Phase Loader ---
class ProgressBar:
    def __init__(self, total_phases=6, verbose=False):
        self.verbose = verbose
        if not self.verbose:
            self.progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
                transient=True,
            )
            self.task = self.progress.add_task(
                "[cyan]Scanning project...", total=total_phases
            )
            self.progress.start()
        else:
            self.progress = None

    def update(self, msg):
        if self.verbose:
            console.print(f"  [green][+][/green] {msg}")
            return
        if msg.startswith("Phase"):
            parts = msg.split(":", 1)
            description = parts[1].strip() if len(parts) > 1 else msg
            self.progress.update(
                self.task, advance=1, description=f"[cyan]{description}"
            )
        else:
            self.progress.update(self.task, description=f"[cyan]{msg[:70]}")

    def finish(self):
        if not self.verbose and self.progress:
            self.progress.stop()
            console.print("[bold green]✨ Scan Complete![/bold green]")


# --- JSON Mock Data Generator ---
def get_mock_value(ftype):
    ftype = ftype.lower()
    if "int" in ftype:
        return 0
    if "float" in ftype or "decimal" in ftype:
        return 0.0
    if "bool" in ftype:
        return True
    if "uuid" in ftype:
        return "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    if "datetime" in ftype:
        return "2024-02-15T12:00:00Z"
    if "date" in ftype:
        return "2024-02-15"
    if "email" in ftype:
        return "user@example.com"
    if "url" in ftype:
        return "https://example.com"
    if "json" in ftype or "dict" in ftype:
        return {"key": "value"}
    if "list" in ftype:
        return ["string"]
    return "string"


def generate_json_example(serializer_name, serializers_map, visited=None):
    from .utils import get_base_type

    if visited is None:
        visited = set()
    base_name = get_base_type(serializer_name)
    is_list = serializer_name.startswith("List[")

    if base_name in visited:
        return [{"...recursive..."}] if is_list else {"...recursive...": True}
    if base_name not in serializers_map:
        val = get_mock_value(base_name)
        return [val] if is_list else val

    fields = serializers_map[base_name]["fields"]
    if not fields:
        return [{}] if is_list else {}

    new_visited = visited.copy()
    new_visited.add(base_name)
    example_obj = {}

    for fname, details in fields.items():
        ftype = details["type"]
        child_is_list = ftype.startswith("List[")
        child_base = get_base_type(ftype)
        if child_base in serializers_map:
            val = generate_json_example(ftype, serializers_map, new_visited)
            example_obj[fname] = val
        else:
            val = get_mock_value(child_base)
            if child_is_list:
                val = [val]
            example_obj[fname] = val

    return [example_obj] if is_list else example_obj


def get_base_type(type_str):
    if type_str.startswith("List[") and type_str.endswith("]"):
        return type_str[5:-1]
    return type_str
