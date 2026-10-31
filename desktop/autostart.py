import subprocess
import winreg

from desktop import paths

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def is_enabled(value_name=paths.APP_NAME):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, value_name)
        return True
    except FileNotFoundError:
        return False


def enable(value_name=paths.APP_NAME):
    command = subprocess.list2cmdline(paths.app_command("--background"))
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command)


def disable(value_name=paths.APP_NAME):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, value_name)
    except FileNotFoundError:
        pass
