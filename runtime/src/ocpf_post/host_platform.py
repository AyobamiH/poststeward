"""Host capabilities and service selection; never grants publishing authority."""
from __future__ import annotations

import os
from pathlib import Path
import platform
import sys
from typing import Any


def host() -> dict[str, Any]:
    try:
        kernel = Path('/proc/version').read_text(errors='replace').lower()
    except OSError:
        kernel = ''
    wsl = sys.platform.startswith('linux') and ('microsoft' in kernel or bool(os.environ.get('WSL_DISTRO_NAME')))
    return {
        'platform': sys.platform,
        'architecture': platform.machine(),
        'macos_version': platform.mac_ver()[0] if sys.platform == 'darwin' else None,
        'wsl': wsl,
        'service_manager': 'launchd' if sys.platform == 'darwin' else 'systemd',
        'persistence': 'Only while this user is logged in and the Mac is awake.' if sys.platform == 'darwin'
        else 'Only while the WSL distribution and Windows host are running.' if wsl
        else 'Requires a reachable systemd user manager; login/linger policy belongs to the owner.',
    }


def service_controller():
    # Lazy import keeps the existing activation protocol independent of host choice.
    if sys.platform == 'darwin':
        from ocpf_post.launchd_services import LaunchdServiceController
        return LaunchdServiceController()
    from ocpf_post.setup_activation import SystemdServiceController
    return SystemdServiceController()
