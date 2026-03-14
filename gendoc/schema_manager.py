import os
import sys
import subprocess
from .utils import console


def generate_schema(framework, root_dir):
    """Attempts to generate OpenAPI schema using detected framework."""
    manage_py = os.path.join(root_dir, "manage.py")
    if not os.path.exists(manage_py):
        print(f"{console.RED}Error: manage.py not found in {root_dir}{console.ENDC}")
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

    print(f"{console.BLUE}Running: {' '.join(cmd)}{console.ENDC}")
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"{console.GREEN}Schema generated: {output_file}{console.ENDC}")
        return output_file
    except subprocess.CalledProcessError as e:
        print(f"{console.RED}Generation failed: {e.stderr.decode()}{console.ENDC}")
        return None
