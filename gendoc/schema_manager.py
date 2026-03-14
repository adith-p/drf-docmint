import os
import sys
import subprocess
from .utils import console


def generate_schema(framework, root_dir):
    """Attempts to generate OpenAPI schema using detected framework."""
    manage_py = os.path.join(root_dir, "manage.py")
    if not os.path.exists(manage_py):
        console.print(f"[red]Error: manage.py not found in {root_dir}[/red]")
        return None

    cmd = []
    output_file = "schema.yaml"

    if "drf-spectacular" in framework:
        cmd = [sys.executable, manage_py, "spectacular", "--file", output_file]
    elif "drf-yasg" in framework:
        output_file = "swagger.json"
        cmd = [sys.executable, manage_py, "generate_swagger", "-o", output_file]

    if not cmd:
        return None

    console.print(f"[blue]Running: {' '.join(cmd)}[/blue]")
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        console.print(f"[green]Schema generated: {output_file}[/green]")
        return output_file
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Generation failed: {e.stderr.decode()}[/red]")
        return None
