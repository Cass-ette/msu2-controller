"""Small operating-system boundaries shared by the GUI and diagnostics."""
import ctypes
import sys


def screen_capture_access():
    """Query access for this process without opening a permission prompt."""
    if sys.platform != 'darwin':
        return {'required': False, 'granted': None}
    try:
        core = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        preflight = core.CGPreflightScreenCaptureAccess
        preflight.argtypes = []
        preflight.restype = ctypes.c_bool
        return {'required': True, 'granted': bool(preflight()),
                'python_executable': sys.executable}
    except (OSError, AttributeError) as exc:
        return {'required': True, 'granted': None, 'error': str(exc),
                'python_executable': sys.executable}


def connection_failure(exc):
    """Separate OS access failures from timeouts and protocol failures."""
    import errno
    import serial
    code = getattr(exc, 'errno', None)
    message = str(exc).lower()
    if code in (errno.EPERM, errno.EACCES) or any(s in message for s in
            ('operation not permitted', 'permission denied', 'access is denied')):
        return 'permission'
    if code in (errno.EBUSY,) or 'resource busy' in message:
        return 'busy'
    if isinstance(exc, serial.SerialTimeoutException) or '超时' in message:
        return 'timeout'
    if isinstance(exc, OSError):
        return 'io'
    return 'protocol'
