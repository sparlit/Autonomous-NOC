import subprocess
import json
import os
import platform

class ShellTool:
    @staticmethod
    def run_command(args: list):
        """Run a shell command and return output."""
        try:
            result = subprocess.run(args, capture_output=True, text=True)
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
        Execute an SNMP GET request using net-snmp snmpget (FOSS).
        """
        cmd = ["snmpget", "-v", "2c", "-c", community, ip, oid]
        return ShellTool.run_command(cmd)

class NetworkScanner:
    @staticmethod
    def scan_local_network(ip_range: str):
        """
        Scan a local IP range using nmap (FOSS).
        """
        cmd = ["nmap", "-sn", ip_range]
        return ShellTool.run_command(cmd)

class DiagnosticTools:
    @staticmethod
    def ping(target: str, count: int = 4):
        """
        Execute a ping to a target host using FOSS ping.
        """
        # Determine flag based on platform
        flag = "-n" if platform.system() == "Windows" else "-c"
        cmd = ["ping", flag, str(count), target]
        return ShellTool.run_command(cmd)

    @staticmethod
    def traceroute(target: str):
        """
        Trace the network route using traceroute (FOSS).
        """
        cmd_name = "tracert" if platform.system() == "Windows" else "traceroute"
        cmd = [cmd_name, target]
        return ShellTool.run_command(cmd)

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
