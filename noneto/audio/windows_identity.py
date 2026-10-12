"""Register a per-user Start menu identity so SMTC can name the console app."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import uuid

APP_ID = "Noneto.CLI"


class GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def register_identity():
    """Only touches Noneto CLI.lnk; never changes file associations or other shortcuts."""
    ole = ctypes.WinDLL("ole32")
    create = ole.CoCreateInstance
    create.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                       ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    create.restype = ctypes.c_long
    link, store, persist = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()

    def call(pointer, index, types, *args):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        result = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[index])(pointer, *args)
        if result < 0:
            raise OSError("Cannot register Noneto media identity")

    def query(pointer, identifier, target):
        call(pointer, 0, [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
             ctypes.byref(GUID.parse(identifier)), ctypes.byref(target))

    try:
        result = create(ctypes.byref(GUID.parse("00021401-0000-0000-c000-000000000046")), None, 1,
                        ctypes.byref(GUID.parse("000214f9-0000-0000-c000-000000000046")), ctypes.byref(link))
        if result < 0:
            raise OSError("Cannot create Noneto shortcut")
        call(link, 20, [wintypes.LPCWSTR], sys.executable)
        call(link, 11, [wintypes.LPCWSTR], "-m noneto")
        call(link, 7, [wintypes.LPCWSTR], "Noneto CLI")
        icon = Path(__file__).resolve().parents[1] / "assets/noneto.ico"
        call(link, 17, [wintypes.LPCWSTR, ctypes.c_int], str(icon), 0)
        query(link, "886d8eeb-8cf2-4446-8d02-cdba1dbdcf99", store)

        class PropertyKey(ctypes.Structure):
            _fields_ = [("format", GUID), ("pid", wintypes.DWORD)]

        class Value(ctypes.Union):
            _fields_ = [("string", wintypes.LPWSTR), ("padding", ctypes.c_ubyte * 16)]

        class PropVariant(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ushort), ("reserved", ctypes.c_ushort * 3), ("value", Value)]

        key = PropertyKey(GUID.parse("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3"), 5)
        variant = PropVariant()
        variant.type = 31  # VT_LPWSTR; SetValue copies the string, no COM allocation is needed.
        variant.value.string = APP_ID
        call(store, 6, [ctypes.POINTER(PropertyKey), ctypes.POINTER(PropVariant)],
             ctypes.byref(key), ctypes.byref(variant))
        call(store, 7, [])
        query(link, "0000010b-0000-0000-c000-000000000046", persist)
        shortcut = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Noneto CLI.lnk"
        shortcut.parent.mkdir(parents=True, exist_ok=True)
        call(persist, 6, [wintypes.LPCWSTR, wintypes.BOOL], str(shortcut), True)
        notify = ctypes.WinDLL("shell32").SHChangeNotify
        notify.argtypes = [ctypes.c_long, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p]
        notify.restype = None
        notify(0x2000, 0x0005, ctypes.cast(ctypes.c_wchar_p(str(shortcut)), ctypes.c_void_p), None)
    finally:
        for pointer in (persist, store, link):
            if pointer.value:
                table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
                ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(table[2])(pointer)
