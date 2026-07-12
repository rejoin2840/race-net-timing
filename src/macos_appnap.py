"""Opt this process out of macOS App Nap (no pyobjc dependency).

Why: the calm dashboard left running behind a locked screen for hours gets its
process throttled/suspended by App Nap — Qt timers stop firing and the
window's paint plumbing goes stale, which froze the standings table for ~3
hours during the 2026-07-12 WEC São Paulo race while the DB stayed current.
NSProcessInfo's activity API is the supported opt-out; we call it through
ctypes so the fix adds no dependency.

Uses NSActivityUserInitiatedAllowingIdleSystemSleep: App Nap and automatic
termination are disabled, but the machine may still idle-sleep — display and
system sleep policy stay with the user (caffeinate / Energy Saver), not the
dashboard.

Best-effort by design: any failure returns False and the app runs exactly as
before — this must never be able to take the dashboard down on race day.
"""
import ctypes
import ctypes.util
import sys

# NSActivityOptions (NSProcessInfo.h)
NS_ACTIVITY_USER_INITIATED_ALLOWING_IDLE_SYSTEM_SLEEP = 0x00FFFFFF

# retained NSActivity token — must stay referenced for the process lifetime,
# releasing it (or letting the autorelease pool drain it) re-enables App Nap
_token = None


def disable_app_nap(reason: str = "live timing refresh") -> bool:
    """Begin (and hold forever) an NSProcessInfo activity. True = App Nap is off."""
    global _token
    if _token is not None:
        return True
    if sys.platform != "darwin":
        return False
    try:
        objc = ctypes.CDLL(ctypes.util.find_library("objc"))
        # load Foundation so the NSProcessInfo/NSString classes are registered
        ctypes.CDLL(ctypes.util.find_library("Foundation"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        # objc_msgSend must be cast per call signature (arm64 has no varargs ABI)
        def sender(restype, argtypes):
            return ctypes.cast(objc.objc_msgSend, ctypes.CFUNCTYPE(restype, *argtypes))

        send_id = sender(ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p])
        send_utf8 = sender(ctypes.c_void_p,
                           [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p])
        send_begin = sender(ctypes.c_void_p,
                            [ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_uint64, ctypes.c_void_p])

        process_info = send_id(objc.objc_getClass(b"NSProcessInfo"),
                               objc.sel_registerName(b"processInfo"))
        ns_reason = send_utf8(objc.objc_getClass(b"NSString"),
                              objc.sel_registerName(b"stringWithUTF8String:"),
                              reason.encode("utf-8"))
        token = send_begin(process_info,
                           objc.sel_registerName(b"beginActivityWithOptions:reason:"),
                           NS_ACTIVITY_USER_INITIATED_ALLOWING_IDLE_SYSTEM_SLEEP,
                           ns_reason)
        if not token:
            return False
        # beginActivity returns an autoreleased token — retain it or the pool
        # drains it and the activity silently ends
        send_id(token, objc.sel_registerName(b"retain"))
        _token = token
        return True
    except Exception:
        return False
