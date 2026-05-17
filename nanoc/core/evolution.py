import subprocess
import os
import shutil
import ast
from typing import List
from nanoc.core.config import settings

class SelfEvolutionValidator(ast.NodeVisitor):
    def __init__(self):
        self.errors = []
        self.dangerous_functions = {'eval', 'exec', '__import__'}
        self.dangerous_attributes = {
            'os': {'system', 'popen', 'spawn', 'kill'},
            'subprocess': {'run', 'Popen', 'call', 'check_call', 'check_output'}
        }

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            if node.func.id in self.dangerous_functions:
                self.errors.append(f"Dangerous function call: {node.func.id} at line {node.lineno}")
        elif isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                module = node.func.value.id
                attr = node.func.attr
                if module in self.dangerous_attributes and attr in self.dangerous_attributes[module]:
                    self.errors.append(f"Dangerous attribute access: {module}.{attr} at line {node.lineno}")
        self.generic_visit(node)

class SelfEvolutionManager:
    def __init__(self, workspace: str, staging: str):
        self.workspace = workspace
        self.staging = staging

    def prepare_staging(self):
        """Clone current source code to staging for testing."""
        if os.path.exists(self.staging):
            shutil.rmtree(self.staging)
        # Copy nanoc directory to staging
        shutil.copytree("nanoc", os.path.join(self.staging, "nanoc"))

    def validate_code(self, content: str) -> List[str]:
        """Validate code for dangerous patterns using AST."""
        try:
            tree = ast.parse(content)
            validator = SelfEvolutionValidator()
            validator.visit(tree)
            return validator.errors
        except Exception as e:
            return [f"AST Parsing Error: {e}"]

    def apply_change_to_staging(self, filepath: str, content: str):
        """Apply a proposed code change to a file in staging after validation."""
        errors = self.validate_code(content)
        if errors:
            raise ValueError(f"Validation failed for {filepath}: {', '.join(errors)}")

        target_path = os.path.join(self.staging, filepath)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "w") as f:
            f.write(content)

    def run_tests_in_staging(self) -> bool:
        """Run pytest in the staging directory."""
        try:
            # This assumes tests are written and discoverable
            result = subprocess.run(["pytest", self.staging], capture_output=True, text=True)
            return result.returncode == 0
        except Exception as e:
            print(f"Testing failed: {e}")
            return False

    def promote_staging_to_production(self):
        """Overwrite the live code with the verified staging code using an atomic-ish approach."""
        # Create a backup first
        backup_dir = "nanoc_backup"
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir)
        shutil.copytree("nanoc", backup_dir)

        try:
            # Overwrite files individually to avoid deleting the whole nanoc/ dir if something fails
            for root, dirs, files in os.walk(os.path.join(self.staging, "nanoc")):
                relative_path = os.path.relpath(root, os.path.join(self.staging, "nanoc"))
                target_root = os.path.join("nanoc", relative_path)

                os.makedirs(target_root, exist_ok=True)

                for f in files:
                    s_file = os.path.join(root, f)
                    d_file = os.path.join(target_root, f)
                    shutil.copy2(s_file, d_file)
            print("System evolved successfully. Reload required.")
        except Exception as e:
            print(f"Promotion failed: {e}. Reverting from backup...")
            shutil.rmtree("nanoc")
            shutil.copytree(backup_dir, "nanoc")
