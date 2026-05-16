import time
import subprocess
import sys
import os

def start_nanoc():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    return subprocess.Popen([sys.executable, "nanoc/main.py"], env=env)

def watchdog():
    print("Watchdog started...")
    process = start_nanoc()

    while True:
        time.sleep(30)
        # Check if process is still running
        if process.poll() is not None:
            print(f"NANOC crashed or stopped with return code {process.returncode}. Restarting...")

            # Check if we should revert from backup
            backup_dir = "nanoc_backup"
            if os.path.exists(backup_dir):
                import shutil
                print("Bad evolution suspected. Reverting from backup...")
                if os.path.exists("nanoc"):
                    shutil.rmtree("nanoc")
                shutil.copytree(backup_dir, "nanoc")

            process = start_nanoc()
        else:
            # Check for 'heartbeat' file updated by NANOC
            heartbeat_file = "nanoc/logs/heartbeat.txt"
            if os.path.exists(heartbeat_file):
                mtime = os.path.getmtime(heartbeat_file)
                if time.time() - mtime > 120: # 2 minutes without heartbeat
                    print("NANOC frozen. Killing and restarting...")
                    process.kill()
                    process = start_nanoc()

if __name__ == "__main__":
    watchdog()
