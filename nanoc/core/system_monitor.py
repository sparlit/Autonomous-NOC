import os

class SystemMonitor:
    @staticmethod
    def get_cpu_usage():
        """Get CPU usage as a percentage."""
        try:
            # Read from /proc/stat
            with open('/proc/stat', 'r') as f:
                line = f.readline()
            parts = line.split()
            idle = float(parts[4])
            total = sum(float(x) for x in parts[1:])
            return (idle, total)
        except Exception:
            return (0, 0)

    @staticmethod
    def calculate_cpu_percent(prev_idle, prev_total, curr_idle, curr_total):
        idle_delta = curr_idle - prev_idle
        total_delta = curr_total - prev_total
        if total_delta == 0:
            return 0.0
        return 100.0 * (1.0 - idle_delta / total_delta)

    @staticmethod
    def get_mem_usage():
        """Get RAM usage as a percentage."""
        try:
            with open('/proc/meminfo', 'r') as f:
                lines = f.readlines()
            mem_total = 0
            mem_free = 0
            for line in lines:
                if 'MemTotal:' in line:
                    mem_total = int(line.split()[1])
                elif 'MemAvailable:' in line:
                    mem_available = int(line.split()[1])
                    return 100.0 * (1.0 - mem_available / mem_total)
            return 0.0
        except Exception:
            return 0.0
