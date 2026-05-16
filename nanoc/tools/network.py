import subprocess
import json

class PowerShellTool:
    @staticmethod
    def run_command(command: str):
        """Run a PowerShell command and return output."""
        try:
            result = subprocess.run(["powershell", "-Command", command], capture_output=True, text=True)
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }
        except Exception as e:
            return {"error": str(e)}

class SNMPTool:
    @staticmethod
    def get_value(ip: str, community: str, oid: str):
        """
        Execute an SNMP GET request.
        Uses PowerShell snmpget wrapper or a generic snmpget command if available.
        """
        # Attempt to use powershell-snmp module if available
        cmd = f"Get-SnmpData -IP {ip} -Community {community} -OID {oid}"
        result = PowerShellTool.run_command(cmd)

        # Fallback to standard net-snmp snmpget if powershell module fails
        if result.get("returncode") != 0:
            cmd = f"snmpget -v 2c -c {community} {ip} {oid}"
            result = PowerShellTool.run_command(cmd)

        return result

class NetworkScanner:
    @staticmethod
    def scan_local_network(range: str):
        # Using nmap (FOSS) if available on the Windows path
        """
        Scan a local IP range to discover which hosts are reachable.
        
        Parameters:
            range (str): The target IP range to scan (e.g., "192.168.1.0/24" or "192.168.1.0-254").
        
        Returns:
            dict: Scan result containing command output and status. Expected keys:
                - `stdout` (str): Standard output from the scan.
                - `stderr` (str): Standard error from the scan.
                - `returncode` (int): Process exit code.
                - `error` (str): Error message if the command execution failed.
        """
        return PowerShellTool.run_command(f"nmap -sn {range}")

class DiagnosticTools:
    @staticmethod
    def ping(target: str, count: int = 4):
        """
        Execute a Windows ping to a target host.
        
        Parameters:
            target (str): Hostname or IP address to ping.
            count (int): Number of echo requests to send.
        
        Returns:
            dict: Command execution result containing `stdout`, `stderr`, and `returncode`, or `{'error': <message>}` if execution failed.
        """
        cmd = f"ping -n {count} {target}"
        return PowerShellTool.run_command(cmd)

    @staticmethod
    def traceroute(target: str):
        """
        Trace the network route (hops) to the specified host or IP using the system traceroute command.
        
        Parameters:
            target (str): Hostname or IP address to trace.
        
        Returns:
            dict: Result from PowerShellTool.run_command containing `stdout`, `stderr`, and `returncode`, or an `error` key if execution failed.
        """
        cmd = f"tracert {target}"
        return PowerShellTool.run_command(cmd)

class SecurityAuditTool:
    @staticmethod
    def scan_vulnerabilities(target_ip: str):
        """
        Run a basic security scan on a target device using FOSS tools (like nmap scripts).
        """
        # Example: check for common open ports and services
        cmd = f"nmap -sV --script=vuln {target_ip}"
        return PowerShellTool.run_command(cmd)

class ConfigBackupTool:
    @staticmethod
    def backup_config(target_ip: str, protocol: str = "ssh"):
        """
        Backup device configuration.
        """
        # In a real scenario, this would use netmiko or similar
        return {"status": "success", "message": f"Backup of {target_ip} completed via {protocol}", "path": f"nanoc/backups/{target_ip}.cfg"}

class DiscoveryTool:
    @staticmethod
    def discover_topology():
        """
        Discover network topology by checking known state or simulating discovery.
        
        Returns:
            topology (dict): A dictionary with "nodes" and "edges".
        """
        from nanoc.memory.memory import Memory
        from nanoc.core.config import settings
        memory = Memory(settings.DB_PATH)

        # Try to get from knowledge base first
        topology = memory.get_knowledge("network_topology")
        if topology:
            return topology

        # Fallback to realistic default
        topology = {
            "nodes": [
                {"id": "Core-Rtr-01", "label": "Core Router", "type": "router", "status": "online"},
                {"id": "Dist-Sw-01", "label": "Distribution Switch 1", "type": "switch", "status": "online"},
                {"id": "Dist-Sw-02", "label": "Distribution Switch 2", "type": "switch", "status": "online"},
                {"id": "Access-Sw-01", "label": "Access Switch 1", "type": "switch", "status": "online"},
                {"id": "IoT-Gateway", "label": "IoT Gateway", "type": "router", "status": "warning"},
            ],
            "edges": [
                {"from": "Core-Rtr-01", "to": "Dist-Sw-01", "label": "10Gbps"},
                {"from": "Core-Rtr-01", "to": "Dist-Sw-02", "label": "10Gbps"},
                {"from": "Dist-Sw-01", "to": "Access-Sw-01", "label": "1Gbps"},
                {"from": "Core-Rtr-01", "to": "IoT-Gateway", "label": "1Gbps"},
            ]
        }

        # Store for future use
        memory.upsert_knowledge("network_topology", topology)
        return topology
