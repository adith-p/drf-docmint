import sys
import os
import json
import re
from typing import Optional

import typer
from rich.panel import Panel
from rich.prompt import Prompt

from .parser import (
    scan_project,
    detect_schema_frameworks,
    find_generated_schemas,
    parse_schema_file,
)
from .utils import console, ProgressBar, YAML_AVAILABLE
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
        None, "--api-version", help="Filter by API version"
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

    if destination:
        output_file = destination
        output_dir = os.path.dirname(output_file)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
    else:
        output_dir = "docs"
        os.makedirs(output_dir, exist_ok=True)
        ext = "json" if format == "postman" else format
        output_file = os.path.join(output_dir, f"API_DOCS.{ext}")

    # Rich Panel Banner
    console.print(
        Panel.fit(
            "[bold cyan]🍃 drf-docmint API Doc Generator[/bold cyan]",
            border_style="cyan",
        )
    )

    specs = []
    serializers_map = {}
    used_schema = False

    if os.path.isfile(target) and target.endswith((".json", ".yaml", ".yml")):
        console.print("\n[blue]Parsing schema file...[/blue]")
        try:
            specs, serializers_map = parse_schema_file(target)
            used_schema = True
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(code=1)

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

            if schema_files:
                console.print("[bold]Found existing schema file(s).[/bold]")

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

                if choice == "s":
                    # --- NEW LOGIC: Handle multiple schemas ---
                    selected_schema = schema_files[0]
                    if len(schema_files) > 1:
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

    if not used_schema:
        console.print(f"\n[blue]Scanning {target}...[/blue]")
        pbar = ProgressBar(total_phases=6, verbose=verbose)
        specs, serializers_map = scan_project(target, callback=pbar.update)
        pbar.finish()

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

        import yaml  # Lazy import since PyYAML is optional

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
