"""Local Windows control signals; diagnostics reuse the app's existing worker.

Only event notifications cross processes. Snapshot data stays in the current
user's profile; the audio SDK is never loaded by a client of a running app.
"""
import ctypes
from ctypes import wintypes


def kernel_api():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateEventW.restype = wintypes.HANDLE
    kernel.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenEventW.restype = wintypes.HANDLE
    for name in ('SetEvent','ResetEvent','CloseHandle'):
        getattr(kernel,name).argtypes = [wintypes.HANDLE]
        getattr(kernel,name).restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    return kernel


class LocalControl:
    def __init__(self, name):
        self.kernel = kernel_api()
        self.events = {}
        try:
            for kind in ('quit','show','status','ready'):
                handle = self.kernel.CreateEventW(None, kind=='ready', False, self.event_name(name,kind))
                if not handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                self.events[kind] = handle
        except Exception:
            self.close()
            raise

    @staticmethod
    def event_name(name, kind):
        return 'Local\\'+name+'.Control.'+kind

    def pending(self, kind):
        return self.kernel.WaitForSingleObject(self.events[kind],0)==0

    def ready(self):
        self.kernel.SetEvent(self.events['ready'])

    def close(self):
        for handle in self.events.values():
            self.kernel.CloseHandle(handle)
        self.events.clear()

    @classmethod
    def request(cls, name, kind, timeout=5000):
        kernel = kernel_api()
        handles = []
        try:
            for event in (kind,'ready') if kind=='status' else (kind,):
                handle = kernel.OpenEventW(0x100002,False,cls.event_name(name,event))  # SYNCHRONIZE | MODIFY_STATE
                if not handle:
                    return False
                handles.append(handle)
            if kind=='status':
                kernel.ResetEvent(handles[1])
            if not kernel.SetEvent(handles[0]):
                return False
            return kind!='status' or kernel.WaitForSingleObject(handles[1],timeout)==0
        finally:
            for handle in handles:
                kernel.CloseHandle(handle)
