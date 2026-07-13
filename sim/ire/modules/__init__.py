"""IRE component modules — observers and reconciliation implementations."""

__all__ = [
    "FirewallReconciliationModule",
    "SSHReconciliationModule",
    "StorageObserver",
    "TailscaleObserver",
    "build_storage_report",
    "build_tailscale_report",
    "observe_storage",
    "observe_tailscale",
    "storage_report_exit_code",
    "tailscale_report_exit_code",
]


def __getattr__(name: str):
    if name == "FirewallReconciliationModule":
        from sim.ire.modules.firewall import FirewallReconciliationModule
        return FirewallReconciliationModule
    if name == "SSHReconciliationModule":
        from sim.ire.modules.ssh import SSHReconciliationModule
        return SSHReconciliationModule
    if name in ("StorageObserver", "build_storage_report", "observe_storage", "storage_report_exit_code"):
        from sim.ire.modules import storage as storage_mod
        return getattr(storage_mod, name)
    if name in ("TailscaleObserver", "build_tailscale_report", "observe_tailscale", "tailscale_report_exit_code"):
        from sim.ire.modules import tailscale as tailscale_mod
        return getattr(tailscale_mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
