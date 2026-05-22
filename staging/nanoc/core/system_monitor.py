import os

class SystemMonitor:
    @staticmethod
    def get_cpu_usage():
        """Get CPU usage as a percentage."""
        try:
            # Read from /proc/stat
            if not os.path.exists('/proc/stat'):
                return (0, 0)
            with open('/proc/stat', 'r') as f:
                line = f.readline()
            if not line.startswith('cpu'):
                return (0, 0)
            parts = line.split()
            # /proc/stat cpu line: user nice system idle iowait irq softirq steal guest guest_nice
            # idle is at index 4, iowait is at index 5
            idle = float(parts[4])
            total = sum(float(x) for x in parts[1:])
            return (idle, total)
        except (IOError, ValueError, IndexError):
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
            if not os.path.exists('/proc/meminfo'):
                return 0.0
            with open('/proc/meminfo', 'r') as f:
                lines = f.readlines()
            mem_total = 0
            mem_available = 0
            for line in lines:
                if 'MemTotal:' in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        mem_total = int(parts[1])
                elif 'MemAvailable:' in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        mem_available = int(parts[1])

            if mem_total > 0 and mem_available > 0:
                return 100.0 * (1.0 - mem_available / mem_total)
            return 0.0
        except (IOError, ValueError):
            return 0.0
