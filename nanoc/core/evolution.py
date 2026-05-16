import subprocess
import os
import shutil
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

    def apply_change_to_staging(self, filepath: str, content: str):
        """Apply a proposed code change to a file in staging."""
        target_path = os.path.join(self.staging, filepath)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "w") as f:
            f.write(content)

    def run_tests_in_staging(self) -> bool:
        """Run pytest in the staging directory with proper environment."""
        try:
            env = os.environ.copy()
            env["PYTHONPATH"] = f".:{self.staging}"
            # Only run nanoc tests to ensure core functionality isn't broken
            test_path = os.path.join(self.staging, "nanoc/tests")
            result = subprocess.run(["pytest", test_path], capture_output=True, text=True, env=env)
            if result.returncode != 0:
                print(f"Staging tests failed:\n{result.stdout}\n{result.stderr}")
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
