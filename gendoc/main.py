import sys
import os
import json
import re
from typing import Optional

import typer
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

from .parser import (
    scan_project,
    detect_schema_frameworks,
    find_generated_schemas,
    parse_schema_file,
)
from .utils import console, ProgressBar, YAML_AVAILABLE, interactive_select
from .openapi import generate_openapi_spec
from .postman import generate_postman_collection
from .renderer import generate_markdown
from .converters import convert_to_pdf, convert_to_html
from .schema_manager import generate_schema

app = typer.Typer(
    name="drf-docmint", help="Static API Documentation Generator", add_completion=False
)


@app.command()
def generate_docs(
    target: str = typer.Argument(".", help="Project root or schema file"),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Launch interactive wizard"
    ),
    experimental: Optional[str] = typer.Option(
        None, "--experimental", "-e", help="Run an experimental module (e.g., 'tui')"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-vb", help="Verbose output"),
    destination: Optional[str] = typer.Option(
        None, "--destination", "-d", help="Output file path"
    ),
    format: str = typer.Option(
        "md",
        "--format",
        "-f",
        help="Output format: 'md', 'pdf', 'html', 'json', 'yaml', 'postman'",
    ),
    api_version: Optional[str] = typer.Option(
        None, "--api-version", help="Filter by API version (e.g., 'v1')"
    ),
    auto_open: bool = typer.Option(
        False, "--open", "-o", help="Open the generated file automatically"
    ),
    version: bool = typer.Option(
        False, "--version", "-v", help="Show drf-docmint version and exit"
    ),
):
    if version:
        from gendoc import __version__

        console.print(__version__)
        raise typer.Exit()

    # ==========================================
    # 🧪 EXPERIMENTAL PLUGINS / MODES
    # ==========================================
    if experimental:
        plugin = experimental.lower()

        if plugin == "tui":
            try:
                from .tui import DocMintApp

                DocMintApp().run()
                raise typer.Exit()
            except ImportError:
                console.print(
                    "[red]Textual is required for TUI mode. Install with: uv add textual[/red]"
                )
                raise typer.Exit(1)

        # In the future, you can add more experimental plugins here like:
        # elif plugin == "live-server":
        #     from .plugins import start_live_server
        #     start_live_server()
        #     raise typer.Exit()

        else:
            console.print(f"[red]Unknown experimental feature: '{experimental}'[/red]")
            console.print("[yellow]Available experimental features: 'tui'[/yellow]")
            raise typer.Exit(1)

    # Rich Panel Banner
    console.print(
        Panel.fit(
            "[bold cyan]🍃 drf-docmint API Doc Generator[/bold cyan]",
            border_style="cyan",
        )
    )

    # ==========================================
    # 🧙‍♂️ INTERACTIVE WIZARD MODE
    # ==========================================
    if interactive:
        console.print("[bold yellow]Interactive Mode Activated[/bold yellow]\n")

        # 1. Target Directory / File
        target = Prompt.ask("Enter project root path or schema file", default=target)

        # 2. Output Format
        format_map = {
            "Markdown (.md)": "md",
            "Standalone HTML (.html)": "html",
            "PDF Document (.pdf)": "pdf",
            "OpenAPI (JSON)": "json",
            "OpenAPI (YAML)": "yaml",
            "Postman Collection": "postman",
        }
        format_choice_label = interactive_select(
            "Select output format:", list(format_map.keys())
        )
        format = format_map[format_choice_label]

        # 3. Output Destination (Dynamically based on target root)
        default_dest = (
            os.path.join(target, "docs")
            if os.path.isdir(target)
            else os.path.join(os.path.dirname(target) or ".", "docs")
        )

        dest_input = Prompt.ask(
            "Enter output destination path (leave blank for default)",
            default=destination or default_dest,
        )
        destination = dest_input.strip() if dest_input.strip() else default_dest

        # 4. API Version Filter
        api_ver_input = Prompt.ask(
            "Filter by API version? (e.g., 'v1', 'all', or leave blank for none)",
            default=api_version or "",
        )
        api_version = api_ver_input.strip() if api_ver_input.strip() else None

        # 5. Auto Open
        auto_open = Confirm.ask(
            "Auto-open generated file when finished?", default=auto_open
        )

    # ==========================================
    # ⚙️ CORE EXECUTION (Runs for both modes)
    # ==========================================

    # Determine output filename and location dynamically
    ext = "json" if format == "postman" else format

    if destination:
        # Check if the user passed a file name (e.g. docs/custom_api.md) or a directory (e.g. docs/)
        if os.path.splitext(destination)[1]:
            output_file = destination
            output_dir = os.path.dirname(output_file)
        else:
            output_dir = destination
            output_file = os.path.join(output_dir, f"API_DOCS.{ext}")

        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
    else:
        # Global fallback if not in interactive mode and no destination provided
        output_dir = (
            os.path.join(target, "docs")
            if os.path.isdir(target)
            else os.path.join(os.path.dirname(target) or ".", "docs")
        )
        os.path.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"API_DOCS.{ext}")

    specs = []
    serializers_map = {}
    used_schema = False

    # Schema file provided directly
    if os.path.isfile(target) and target.endswith((".json", ".yaml", ".yml")):
        console.print("\n[blue]Parsing schema file...[/blue]")
        try:
            specs, serializers_map = parse_schema_file(target)
            used_schema = True
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(code=1)

    # Directory scan & Schema Detection
    else:
        root_dir = target
        frameworks = detect_schema_frameworks(root_dir)
        schema_files = find_generated_schemas(root_dir)

        if frameworks or schema_files:
            console.print("\n[cyan]--- Schema Detection ---[/cyan]")

            if frameworks:
                console.print(
                    f"Detected frameworks: [bold]{', '.join(frameworks)}[/bold]"
                )

            if schema_files:
                console.print("Detected existing schema files:")
                for schema_file in schema_files:
                    console.print(f"  [yellow]- {schema_file}[/yellow]")

            console.print("-" * 24)

            # ---------------------------------------------------------
            # SCHEMA DECISION: Interactive Menu vs Manual Typed Prompt
            # ---------------------------------------------------------
            if schema_files:
                console.print("[bold]Found existing schema file(s).[/bold]")

                if interactive:
                    choices = ["Static Analysis (drf-docmint engine)"]
                    choices.append(
                        f"Use existing schema file ({len(schema_files)} found)"
                    )
                    if frameworks:
                        choices.append(f"Regenerate schema using {frameworks[0]}")

                    choice_label = interactive_select(
                        "How would you like to parse the API?", choices
                    )
                    if "Use existing" in choice_label:
                        choice = "s"
                    elif "Regenerate" in choice_label:
                        choice = "r"
                    else:
                        choice = "g"
                else:
                    if frameworks:
                        choice = Prompt.ask(
                            "Use existing ([green]s[/green]), regenerate ([yellow]r[/yellow]), or static analysis ([blue]g[/blue])?",
                            choices=["s", "r", "g"],
                            default="g",
                        )
                    else:
                        choice = Prompt.ask(
                            "Use existing schema ([green]s[/green]) or static analysis ([blue]g[/blue])?",
                            choices=["s", "g"],
                            default="g",
                        )

                # Process the user's choice
                if choice == "s":
                    selected_schema = schema_files[0]
                    if len(schema_files) > 1:
                        if interactive:
                            selected_schema = interactive_select(
                                "Multiple schemas detected. Which one?", schema_files
                            )
                        else:
                            console.print(
                                "\n[cyan]Multiple schema files detected. Please choose one:[/cyan]"
                            )
                            for idx, f in enumerate(schema_files, 1):
                                console.print(f"  [green]{idx}[/green]: {f}")
                            valid_choices = [
                                str(i) for i in range(1, len(schema_files) + 1)
                            ]
                            file_choice = Prompt.ask(
                                "Enter the number of the schema to use",
                                choices=valid_choices,
                                default="1",
                            )
                            selected_schema = schema_files[int(file_choice) - 1]

                    console.print(f"[green]Using:[/green] {selected_schema}")
                    try:
                        specs, serializers_map = parse_schema_file(selected_schema)
                        used_schema = True
                    except Exception as e:
                        console.print(f"[red]Error parsing schema: {e}[/red]")
                        console.print(
                            "[yellow]Falling back to static analysis...[/yellow]"
                        )

                elif choice == "r" and frameworks:
                    generated_file = generate_schema(frameworks[0], root_dir)
                    if generated_file:
                        try:
                            specs, serializers_map = parse_schema_file(generated_file)
                            used_schema = True
                        except Exception as e:
                            console.print(
                                f"[red]Error parsing generated schema: {e}[/red]"
                            )

            elif frameworks:
                console.print(
                    "[yellow]Framework detected but no schema file found.[/yellow]"
                )

                if interactive:
                    choice_label = interactive_select(
                        f"Generate schema using {frameworks[0]} or use static analysis?",
                        [
                            f"Generate schema ({frameworks[0]})",
                            "Static Analysis (drf-docmint engine)",
                        ],
                    )
                    choice = "y" if "Generate" in choice_label else "g"
                else:
                    choice = Prompt.ask(
                        f"Generate schema using {frameworks[0]} ([green]y[/green]) or static analysis ([blue]g[/blue])?",
                        choices=["y", "g"],
                        default="g",
                    )

                if choice == "y":
                    generated_file = generate_schema(frameworks[0], root_dir)
                    if generated_file:
                        try:
                            specs, serializers_map = parse_schema_file(generated_file)
                            used_schema = True
                        except Exception as e:
                            console.print(
                                f"[red]Error parsing generated schema: {e}[/red]"
                            )

    # ==========================================
    # 🔍 FALLBACK: STATIC ANALYSIS ENGINE
    # ==========================================
    if not used_schema:
        console.print(f"\n[blue]Scanning {target}...[/blue]")
        pbar = ProgressBar(total_phases=6, verbose=verbose)
        specs, serializers_map = scan_project(target, callback=pbar.update)
        pbar.finish()

    # Filter standard paths if requested
    if api_version:
        initial_count = len(specs)
        if api_version.lower() == "all":
            specs = [s for s in specs if re.search(r"/v\d+", s["path"])]
            console.print("[blue]Filtering: all versioned endpoints[/blue]")
        else:
            version_pattern = (
                api_version if api_version.startswith("v") else f"v{api_version}"
            )
            specs = [s for s in specs if f"/{version_pattern}" in s["path"].lower()]
            console.print(f"[blue]Filtering: API version {version_pattern}[/blue]")

        filtered_count = initial_count - len(specs)
        if filtered_count > 0:
            console.print(
                f"[blue]Filtered {filtered_count} endpoints (kept {len(specs)}).[/blue]"
            )

    # ==========================================
    # 💾 EXPORT LOGIC
    # ==========================================
    if format == "json":
        console.print("\n[green][+][/green] Generating JSON (OpenAPI)...")
        openapi_spec = generate_openapi_spec(specs, serializers_map)
        with open(output_file, "w") as f:
            json.dump(openapi_spec, f, indent=2)
        console.print("[bold]Successfully generated JSON spec.[/bold]")
        console.print(f"Saved to: [underline]{output_file}[/underline]")
        if auto_open:
            typer.launch(output_file)
        return

    if format == "yaml":
        console.print("\n[green][+][/green] Generating YAML (OpenAPI)...")
        if not YAML_AVAILABLE:
            console.print("[red]Error: PyYAML is required for YAML export.[/red]")
            console.print("[yellow]Run: uv add PyYAML[/yellow]")
            sys.exit(1)

        import yaml  # Lazy import

        openapi_spec = generate_openapi_spec(specs, serializers_map)
        with open(output_file, "w") as f:
            yaml.dump(openapi_spec, f, sort_keys=False)
        console.print("[bold]Successfully generated YAML spec.[/bold]")
        console.print(f"Saved to: [underline]{output_file}[/underline]")
        if auto_open:
            typer.launch(output_file)
        return

    if format == "postman":
        console.print("\n[green][+][/green] Generating Postman Collection...")
        collection = generate_postman_collection(specs, serializers_map)
        with open(output_file, "w") as f:
            json.dump(collection, f, indent=2)
        console.print("[bold]Successfully generated Postman Collection.[/bold]")
        console.print(f"Saved to: [underline]{output_file}[/underline]")
        if auto_open:
            typer.launch(output_file)
        return

    # Normal Docs Generation (MD/PDF/HTML)
    console.print("\n[green][+][/green] Generating content...")

    if format == "html":
        md = generate_markdown(specs, serializers_map, mode="html")
        success = convert_to_html(md, output_file)
        if success:
            console.print(
                f"\n[bold]Successfully generated HTML documentation for {len(specs)} endpoints[/bold]"
            )
            console.print(f"Saved to: [underline]{output_file}[/underline]")
            if auto_open:
                typer.launch(output_file)
        return

    # Generate with format-specific rendering
    md = generate_markdown(specs, serializers_map, mode=format)

    if format == "pdf":
        success = convert_to_pdf(md, output_file)
        if not success:
            md_fallback = output_file.replace(".pdf", ".md")
            with open(md_fallback, "w") as f:
                f.write(md)
            console.print(
                "\n[yellow]PDF generation failed. Saved as Markdown instead:[/yellow]"
            )
            console.print(f"Saved to: [underline]{md_fallback}[/underline]")
            if auto_open:
                typer.launch(md_fallback)
        else:
            md_version = output_file.replace(".pdf", ".md")
            md_content = generate_markdown(specs, serializers_map, mode="md")
            with open(md_version, "w") as f:
                f.write(md_content)

            console.print(
                f"\n[bold]Successfully generated documentation for {len(specs)} endpoints.[/bold]"
            )
            console.print(f"[green]PDF:[/green] [underline]{output_file}[/underline]")
            console.print(
                f"[green]Markdown:[/green] [underline]{md_version}[/underline]"
            )
            if auto_open:
                typer.launch(output_file)
    else:
        with open(output_file, "w") as f:
            f.write(md)
        console.print(
            f"\n[bold]Successfully generated documentation for {len(specs)} endpoints.[/bold]"
        )
        console.print(f"Saved to: [underline]{output_file}[/underline]")
        if auto_open:
            typer.launch(output_file)


if __name__ == "__main__":
    app()
