"""Host-level telemetry sampled straight from the kernel.

The NOC runs Prometheus for fleet-wide metrics, but the box the control plane
itself runs on needs a zero-dependency source of truth that works even when
Prometheus is unreachable — that is ``/proc/net/dev`` on Linux. Everything here
is pure/synchronous so it can be unit-tested with a fixture instead of a
container.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

PROC_NET_DEV = "/proc/net/dev"

#: Interfaces that are loopback or virtual and would only add noise to the
#: throughput figures. Matched as prefixes.
IGNORED_INTERFACE_PREFIXES: Tuple[str, ...] = (
    "lo",
    "docker",
    "br-",
    "veth",
    "virbr",
    "tun",
    "tap",
    "zt",
)


def parse_net_dev(text: str) -> Dict[str, Tuple[int, int]]:
    """
    Parse the contents of ``/proc/net/dev`` into per-interface byte counters.

    Each data line looks like::

        eth0: 123456 789 0 0 0 0 0 0  987654 321 0 0 0 0 0 0

    where field 0 is receive bytes and field 8 is transmit bytes.

    Parameters:
        text (str): Raw ``/proc/net/dev`` contents.

    Returns:
        Dict[str, Tuple[int, int]]: Interface name -> ``(rx_bytes, tx_bytes)``.
        Malformed lines are skipped rather than raising.
    """
    counters: Dict[str, Tuple[int, int]] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue  # the two header lines have no colon
        name, _, rest = line.partition(":")
        name = name.strip()
        fields = rest.split()
        if not name or len(fields) < 9:
            continue
        try:
            counters[name] = (int(fields[0]), int(fields[8]))
        except ValueError:
            continue
    return counters


def is_relevant_interface(name: str) -> bool:
    """
    Report whether an interface should count towards host throughput.

    Parameters:
        name (str): Interface name such as ``"eth0"`` or ``"lo"``.

    Returns:
        bool: ``False`` for loopback and container/bridge virtual interfaces.
    """
    return not any(name.startswith(prefix) for prefix in IGNORED_INTERFACE_PREFIXES)


def read_net_dev(path: str = PROC_NET_DEV) -> Dict[str, Tuple[int, int]]:
    """
    Read and parse byte counters from ``/proc/net/dev``.

    Parameters:
        path (str): Path to the counters file; injectable for tests.

    Returns:
        Dict[str, Tuple[int, int]]: Interface -> ``(rx_bytes, tx_bytes)``, or
        an empty mapping when the file is unavailable (non-Linux hosts).
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return parse_net_dev(handle.read())
    except OSError:
        return {}


def sum_relevant(counters: Dict[str, Tuple[int, int]]) -> Tuple[int, int]:
    """
    Total ``(rx, tx)`` byte counters across the relevant interfaces.

    Parameters:
        counters (Dict[str, Tuple[int, int]]): Output of :func:`parse_net_dev`.

    Returns:
        Tuple[int, int]: Aggregate receive and transmit bytes.
    """
    rx = tx = 0
    for name, (iface_rx, iface_tx) in counters.items():
        if is_relevant_interface(name):
            rx += iface_rx
            tx += iface_tx
    return rx, tx


@dataclass
class ThroughputSample:
    """One Mbps reading for the host, or ``None`` when not yet measurable."""

    inbound_mbps: Optional[float] = None
    outbound_mbps: Optional[float] = None
    interval_s: Optional[float] = None


class ThroughputSampler:
    """
    Turn monotonically increasing kernel byte counters into Mbps.

    The first :meth:`sample` call only establishes a baseline and therefore
    returns an empty sample — a rate needs two points in time.
    """

    def __init__(self, reader=read_net_dev):
        """
        Parameters:
            reader (Callable[[], Dict[str, Tuple[int, int]]]): Counter source.
        """
        self._reader = reader
        self._previous: Optional[Tuple[float, int, int]] = None

    def sample(self, now: float) -> ThroughputSample:
        """
        Compute the throughput since the previous call.

        Parameters:
            now (float): Monotonic timestamp in seconds.

        Returns:
            ThroughputSample: Mbps in/out plus the elapsed interval, or an empty
            sample on the first call / when counters are unavailable.
        """
        rx, tx = sum_relevant(self._reader())
        previous, self._previous = self._previous, (now, rx, tx)
        if previous is None or not rx and not tx:
            return ThroughputSample()

        prev_now, prev_rx, prev_tx = previous
        elapsed = now - prev_now
        if elapsed <= 0:
            return ThroughputSample()

        # Counters can move backwards when an interface is recreated.
        delta_rx = max(0, rx - prev_rx)
        delta_tx = max(0, tx - prev_tx)
        return ThroughputSample(
            inbound_mbps=round(delta_rx * 8 / elapsed / 1_000_000, 3),
            outbound_mbps=round(delta_tx * 8 / elapsed / 1_000_000, 3),
            interval_s=round(elapsed, 3),
        )
