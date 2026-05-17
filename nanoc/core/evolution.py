import subprocess
import os
import shutil
import ast
from nanoc.core.config import settings

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

    def validate_change(self, content: str) -> bool:
        """Validate the proposed code for security risks."""
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                # Block eval and exec
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        if node.func.id in ["eval", "exec", "__import__"]:
                            print(f"Security violation: Use of forbidden function {node.func.id}")
                            return False
                    # Block os.system and os.popen
                    elif isinstance(node.func, ast.Attribute):
                        if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                            if node.func.attr in ["system", "popen"]:
                                print(f"Security violation: Use of forbidden attribute os.{node.func.attr}")
                                return False
            return True
        except SyntaxError:
            print("Syntax error in proposed code change.")
            return False
        except Exception as e:
            print(f"Validation error: {e}")
            return False

    def apply_change_to_staging(self, filepath: str, content: str) -> bool:
        """Apply a proposed code change to a file in staging after validation."""
        if not self.validate_change(content):
            return False

        target_path = os.path.join(self.staging, filepath)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "w") as f:
            f.write(content)
        return True

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
