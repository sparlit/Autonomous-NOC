import asyncio
import json
import os

class AsyncRunner:
    @staticmethod
    async def run_command(cmd: list[str]):
        """Run a command asynchronously and return output."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            return {
                "stdout": stdout.decode(),
                "stderr": stderr.decode(),
                "returncode": proc.returncode
            }
        except Exception as e:
            return {"error": str(e)}

class SNMPTool:
    @staticmethod
    async def get_value(ip: str, community: str, oid: str):
        """
        Execute an SNMP GET request.
        Uses standard net-snmp snmpget command.
        """
        cmd = ["snmpget", "-v", "2c", "-c", community, ip, oid]
        return await AsyncRunner.run_command(cmd)

class NetworkScanner:
    @staticmethod
    async def scan_local_network(ip_range: str):
        """
        Scan a local IP range to discover which hosts are reachable.
        
        Parameters:
            ip_range (str): The target IP range to scan (e.g., "192.168.1.0/24").
        """
        cmd = ["nmap", "-sn", ip_range]
        return await AsyncRunner.run_command(cmd)

class DiagnosticTools:
    @staticmethod
    async def ping(target: str, count: int = 4):
        """
        Execute a ping to a target host.
        """
        cmd = ["ping", "-c", str(count), target]
        return await AsyncRunner.run_command(cmd)

    @staticmethod
    async def traceroute(target: str):
        """
        Trace the network route (hops) to the specified host or IP.
        """
        cmd = ["traceroute", target]
        return await AsyncRunner.run_command(cmd)

class DiscoveryTool:
    @staticmethod
    async def discover_topology(ip_range: str = "127.0.0.1"):
        """
        Discover network topology using nmap.
        
        Returns:
            topology (dict): A dictionary with "nodes" and "edges".
        """
        from nanoc.memory.memory import Memory
        from nanoc.core.config import settings
        memory = Memory(settings.DB_PATH)

        result = await NetworkScanner.scan_local_network(ip_range)
        if "error" in result:
            return {"error": result["error"]}

        nodes = []
        edges = []

        # Simple parser for nmap -sn output
        lines = result["stdout"].split("\n")
        current_node = None
        for line in lines:
            if "Nmap scan report for" in line:
                parts = line.split()
                ip = parts[-1].strip("()")
                name = parts[4] if len(parts) > 5 else ip
                node_id = ip
                nodes.append({
                    "id": node_id,
                    "label": name,
                    "type": "host",
                    "status": "online"
                })
                # In a real discovery, edges would be determined by traceroute or LLDP
                # For now, we link everything to a gateway/local host for visualization
                if node_id != "127.0.0.1":
                    edges.append({"from": "127.0.0.1", "to": node_id, "label": "LAN"})

        if not nodes:
            # Fallback if no nodes found (e.g. permission issues)
            nodes = [{"id": "127.0.0.1", "label": "localhost", "type": "host", "status": "online"}]

        topology = {"nodes": nodes, "edges": edges}
        memory.upsert_knowledge("network_topology", topology)
        return topology
