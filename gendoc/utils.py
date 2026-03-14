import sys
import importlib.util
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
)

# Export a global console for the whole app
console = Console()

# --- Dependency Checks ---
# Using find_spec is faster as it doesn't execute the module's init code
MARKDOWN_AVAILABLE = importlib.util.find_spec("markdown") is not None
WEASYPRINT_AVAILABLE = importlib.util.find_spec("weasyprint") is not None
YAML_AVAILABLE = importlib.util.find_spec("yaml") is not None


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
                transient=True,  # Disappears when done to keep terminal clean
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
