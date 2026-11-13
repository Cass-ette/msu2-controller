"""Small operating-system boundaries shared by the GUI and diagnostics."""
import ctypes
import subprocess
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


def request_screen_capture_access():
    """Ask macOS on a user-initiated capture; diagnostics remain read-only."""
    access = screen_capture_access()
    if not access['required'] or access.get('granted') is True:
        return access
    try:
        core = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        request = core.CGRequestScreenCaptureAccess
        request.argtypes = []
        request.restype = ctypes.c_bool
        return {'required': True, 'granted': bool(request())}
    except (OSError, AttributeError) as exc:
        return {'required': True, 'granted': None, 'error': str(exc)}


def open_screen_capture_settings():
    """Open the permission pane; never change a permission on the user's behalf."""
    if sys.platform != 'darwin':
        return False
    subprocess.run(['/usr/bin/open',
                    'x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture'],
                   check=True, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return True


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
