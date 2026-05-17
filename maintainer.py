import time
import os
import sqlite3
from nanoc.core.config import settings
from nanoc.memory.memory import Memory

def trigger_maintenance():
    memory = Memory(settings.DB_PATH)
    print(f"[{time.ctime()}] Triggering continuous improvement cycle...")

    # Task to analyze and suggest improvements
    project_desc = """
    Analyze the current NANOC project for any gaps, blind spots, or potential enhancements.
    Suggest improvements, new features, and code refactorings to ensure 100% FOSS compliance and robustness.
    """

    # Check if a similar task is already pending to avoid duplicates
    with sqlite3.connect(settings.DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM tasks WHERE description LIKE '%Analyze the current NANOC project%' AND status = 'pending'"
        )
        if cursor.fetchone():
            print("Maintenance task already pending. Skipping.")
            return

    # Use the Leader to delegate the maintenance project
    # We'll create a file in the inbox as it's the standard entry point
    inbox_dir = "nanoc/inbox"
    os.makedirs(inbox_dir, exist_ok=True)
    with open(os.path.join(inbox_dir, f"maintenance_{int(time.time())}.txt"), "w") as f:
        f.write(project_desc)

    print("Maintenance task queued via inbox.")

def main():
    print("NANOC Continuous Maintainer started.")
    while True:
        try:
            trigger_maintenance()
        except Exception as e:
            print(f"Error in maintenance loop: {e}")

        # Wait for 30 minutes
        time.sleep(1800)

if __name__ == "__main__":
    main()
