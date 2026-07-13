"""Interactive UI helpers, built on Rich.

Implements the exact interaction pattern shown in the SIM spec's port
configuration mockups (numbered recommendations + custom-value fallback +
confirmation), as a reusable function so Stage 1's Port Manager and later
modules don't each reimplement prompt handling.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

console = Console()


def print_banner(text: str) -> None:
    console.rule(f"[bold cyan]{text}[/bold cyan]")


def choose_port(service_name: str, recommended: list[int]) -> int:
    """Reproduce the spec's interactive port selection flow.

    Example (Open WebUI, recommended=[3000, 8080, 8888]):

        Configure Open WebUI

        Recommended ports:
          1) 3000
          2) 8080
          3) 8888

        Select option (1-3) or enter a custom port:
        > 3000
    """
    print_banner(f"Configure {service_name}")
    table = Table(show_header=False, box=None)
    for i, port in enumerate(recommended, start=1):
        table.add_row(f"  {i})", str(port))
    console.print(table)

    prompt = f"Select option (1-{len(recommended)}) or enter a custom port"
    while True:
        raw = console.input(f"[bold]{prompt}[/bold]\n> ").strip()
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(recommended):
                return recommended[n - 1]
            if 1 <= n <= 65535:
                return n
        console.print(f"[red]Invalid input: {raw!r}. Enter a list index or a raw port number.[/red]")


def confirm(question: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    raw = console.input(f"{question} {suffix} ").strip().lower()
    if raw == "":
        return default
    return raw in ("y", "yes")


def port_table(rows: list[tuple[str, int, bool]]) -> Table:
    """Render the port verification table shown in the spec, e.g.:

        Service        Requested    Status
        ---------------------------------------
        Open WebUI       3000       [green]Free[/]
    """
    table = Table(title="Port Verification")
    table.add_column("Service")
    table.add_column("Requested", justify="right")
    table.add_column("Status")
    for name, port, is_free in rows:
        status = "[green]\u2713 Free[/green]" if is_free else "[red]\u2717 In use[/red]"
        table.add_row(name, str(port), status)
    return table
