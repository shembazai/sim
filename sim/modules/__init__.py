"""Install modules package.

Concrete provisioning modules live here. Each module implements
``InstallModule`` and is executed via ``run_install_module``.
"""

from sim.modules.base import InstallModule, run_install_module
from sim.modules.cuda import CudaModule
from sim.modules.dependencies import DependenciesModule
from sim.modules.init_environment import InitEnvironmentModule
from sim.modules.nvidia_container import NvidiaContainerModule
from sim.modules.nvidia_driver import NvidiaDriverModule
from sim.modules.podman_runtime import PodmanModule
from sim.modules.python_runtime import PythonRuntimeModule
from sim.modules.quadlet import QuadletModule
from sim.modules.registry import RegistryModule

__all__ = [
    "CudaModule",
    "DependenciesModule",
    "InitEnvironmentModule",
    "InstallModule",
    "NvidiaContainerModule",
    "NvidiaDriverModule",
    "PodmanModule",
    "PythonRuntimeModule",
    "QuadletModule",
    "RegistryModule",
    "run_install_module",
]
