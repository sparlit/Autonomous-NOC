import os
import time
import sys

TASK_DESCRIPTION = """Project: Autonomous Network Operating Center
Core principle: use only 100% FOSS software, tools and utilities
Rules:
1. Multi Thread Parallel Processing capability
2. while improving or enhancing, do not remove or discontinue any features or functionalities.

Step 1: analyse and identify any gaps or blind spots or dead ends or loop holes or wrappers or loose ends or dummies in the project.
Step 2: fix all gaps and all blind spots and dead ends and loop holes and wrappers and loose ends and dummies in the project.
Step 3: Continuous improving and enhancing the project
Step 4: do not remove any features or functionalities, only add.
Step 5: analyse and find how can we improve and enhance further.
step 6: suggest improvements, enhancements, functions and features
step 7: Continuous Codebase Improvement
step 8: repeat the above steps from step 1 to step 7 until all features and functions are added and there is nothing left to improve or enhance"""

def main(count=100):
    inbox_dir = "nanoc/inbox"
    os.makedirs(inbox_dir, exist_ok=True)

    print(f"Injecting {count} maintainer tasks into {inbox_dir}...")

    for i in range(count):
        timestamp = int(time.time())
        # Use i to ensure unique filenames if they happen in the same second
        filename = f"{inbox_dir}/bulk_task_{timestamp}_{i}.txt"
        with open(filename, "w") as f:
            f.write(TASK_DESCRIPTION)
        print(f"Created task {i+1}/{count}: {filename}")
        # Small sleep to prevent exact same timestamp and spread out load slightly
        time.sleep(0.01)

if __name__ == "__main__":
    count = 100
    if len(sys.argv) > 1:
        count = int(sys.argv[1])
    main(count)
