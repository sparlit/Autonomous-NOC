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
    async def scan_local_network(ip_range: str, xml_output: bool = False):
        """
        Scan a local IP range to discover which hosts are reachable.
        
        Parameters:
            ip_range (str): The target IP range to scan (e.g., "192.168.1.0/24").
            xml_output (bool): Whether to return XML output.
        """
        cmd = ["nmap", "-sn"]
        if xml_output:
            cmd.append("-oX")
            cmd.append("-")
        cmd.append(ip_range)
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
        Discover network topology using nmap XML output for robust parsing.
        
        Returns:
            topology (dict): A dictionary with "nodes" and "edges".
        """
        import xml.etree.ElementTree as ET
        from nanoc.memory.memory import Memory
        from nanoc.core.config import settings
        memory = Memory(settings.DB_PATH)

        result = await NetworkScanner.scan_local_network(ip_range, xml_output=True)
        if "error" in result:
            return {"error": result["error"]}

        nodes = []
        edges = []

        try:
            root = ET.fromstring(result["stdout"])
            for host in root.findall('host'):
                status = host.find('status').get('state')
                if status == 'up':
                    ip = host.find("address[@addrtype='ipv4']").get('addr')

                    hostname_elem = host.find('hostnames/hostname')
                    name = hostname_elem.get('name') if hostname_elem is not None else ip

                    nodes.append({
                        "id": ip,
                        "label": name,
                        "type": "host",
                        "status": "online"
                    })

                    if ip != "127.0.0.1":
                        edges.append({"from": "127.0.0.1", "to": ip, "label": "LAN"})
        except Exception as e:
            # Fallback to simple parsing if XML fails
            print(f"XML parsing failed, using fallback: {e}")
            fallback_result = await NetworkScanner.scan_local_network(ip_range, xml_output=False)
            lines = fallback_result["stdout"].split("\n")
            for line in lines:
                if "Nmap scan report for" in line:
                    parts = line.split()
                    ip = parts[-1].strip("()")
                    name = parts[4] if len(parts) > 5 else ip
                    nodes.append({"id": ip, "label": name, "type": "host", "status": "online"})
                    if ip != "127.0.0.1":
                        edges.append({"from": "127.0.0.1", "to": ip, "label": "LAN"})

        if not nodes:
            nodes = [{"id": "127.0.0.1", "label": "localhost", "type": "host", "status": "online"}]

        topology = {"nodes": nodes, "edges": edges}
        memory.upsert_knowledge("network_topology", topology)
        return topology
