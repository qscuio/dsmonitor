from __future__ import annotations

import ctypes
from ctypes import wintypes


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
_ADVAPI32 = ctypes.WinDLL("advapi32", use_last_error=True)


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_ADVAPI32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
_ADVAPI32.CredWriteW.restype = wintypes.BOOL
_ADVAPI32.CredReadW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
]
_ADVAPI32.CredReadW.restype = wintypes.BOOL
_ADVAPI32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
_ADVAPI32.CredDeleteW.restype = wintypes.BOOL
_ADVAPI32.CredFree.argtypes = [ctypes.c_void_p]
_ADVAPI32.CredFree.restype = None


def credential_target(tunnel_name: str) -> str:
    return f"dsmonitor/proxy/{tunnel_name}"


def write_password(tunnel_name: str, username: str, password: str) -> None:
    encoded = password.encode("utf-16-le")
    blob = (wintypes.BYTE * len(encoded)).from_buffer_copy(encoded)
    credential = CREDENTIALW()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = credential_target(tunnel_name)
    credential.UserName = username
    credential.CredentialBlobSize = len(encoded)
    credential.CredentialBlob = blob
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    if not _ADVAPI32.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def read_password(tunnel_name: str) -> str | None:
    pointer = ctypes.POINTER(CREDENTIALW)()
    ok = _ADVAPI32.CredReadW(
        credential_target(tunnel_name),
        CRED_TYPE_GENERIC,
        0,
        ctypes.byref(pointer),
    )
    if not ok:
        return None
    try:
        credential = pointer.contents
        size = credential.CredentialBlobSize
        if size == 0:
            return ""
        raw = ctypes.string_at(credential.CredentialBlob, size)
        return raw.decode("utf-16-le")
    finally:
        _ADVAPI32.CredFree(pointer)


def delete_password(tunnel_name: str) -> None:
    ok = _ADVAPI32.CredDeleteW(credential_target(tunnel_name), CRED_TYPE_GENERIC, 0)
    if not ok:
        error = ctypes.get_last_error()
        if error != 1168:
            raise ctypes.WinError(error)


def has_password(tunnel_name: str) -> bool:
    return read_password(tunnel_name) is not None
