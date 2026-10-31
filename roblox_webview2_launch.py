import os
import subprocess
from pathlib import Path


def _resolve_local_appdata(local_appdata=None, base_env=None):
    if local_appdata:
        return str(local_appdata)
    env_source = os.environ if base_env is None else base_env
    resolved = env_source.get("LOCALAPPDATA")
    if resolved:
        return str(resolved)
    return str(Path.home() / "AppData" / "Local")


def build_webview2_launch_env(worker_index, remote_debug_port, *, local_appdata=None, base_env=None):
    resolved_local_appdata = _resolve_local_appdata(local_appdata=local_appdata, base_env=base_env)
    instance_dir = str(Path(resolved_local_appdata) / "Roblox" / "CdpData" / f"Profile_{worker_index}")
    launch_env = dict(os.environ if base_env is None else base_env)
    launch_env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"--remote-debugging-port={remote_debug_port}"
    launch_env["WEBVIEW2_USER_DATA_FOLDER"] = instance_dir
    return launch_env, remote_debug_port, instance_dir


def launch_with_webview2_env(
    protocol_or_exe,
    worker_index,
    remote_debug_port,
    *,
    local_appdata=None,
    base_env=None,
    makedirs_func=os.makedirs,
    popen_func=subprocess.Popen,
):
    launch_env, port, instance_dir = build_webview2_launch_env(
        worker_index=worker_index,
        local_appdata=local_appdata,
        remote_debug_port=remote_debug_port,
        base_env=base_env,
    )
    makedirs_func(instance_dir, exist_ok=True)
    popen_func(
        ["cmd.exe", "/c", "start", "", protocol_or_exe],
        env=launch_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return port
