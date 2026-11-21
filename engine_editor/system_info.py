"""Read the current program's memory use using the Windows process API."""

import ctypes
from ctypes import wintypes


class _ProcessMemoryCounters(ctypes.Structure):
    """Memory-statistics layout required by Windows' GetProcessMemoryInfo."""

    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def get_process_memory_mb():
    """Return the current process working set in MiB on Windows."""
    if not hasattr(ctypes, "WinDLL"):
        raise OSError("App memory readings are supported on Windows only.")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    get_process_memory_info = psapi.GetProcessMemoryInfo
    get_process_memory_info.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    get_process_memory_info.restype = wintypes.BOOL

    # The Windows API needs the structure size in cb before it fills the fields.
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not get_process_memory_info(
        kernel32.GetCurrentProcess(),
        ctypes.byref(counters),
        counters.cb,
    ):
        error_code = ctypes.get_last_error()
        raise OSError(error_code, "Could not read this app's memory usage.")

    return counters.WorkingSetSize / (1024 * 1024)
