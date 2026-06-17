import os
import time
import sys

TASK_DESCRIPTION = """Project: Autonomous Network Operating Center
Core principle: use only 100% FOSS software, tools and utilities
Multi Thread Parallel Processing capability

how can we improve and enhance it further.
analyse and identify, find and fix any gaps or blind spots or dead ends or loop holes or wrappers or loose ends or dummies in the project.
while improving or enhancing, do not remove any features or functionalities.

Continuous Codebase Improvement
repeat the above until all features and functions are added and there is nothing left to improve or enhance"""

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
