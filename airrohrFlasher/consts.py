import sys

from .qtvariant import QtCore


# Firmware update repository
UPDATE_REPOSITORY = 'https://raw.githubusercontent.com/littleyoda/littleyoda-DCC-Decoder/flashinfo/flash.json'


# URI prefixes (protocol parts, essentially) to be downloaded using requests
ALLOWED_PROTO = ('https://')

# vid/pid pairs of known NodeMCU/ESP8266 development boards
PREFERED_PORTS = [
    # CH341
    (0x1A86, 0x7523),

    # CP2102
    (0x10c4, 0xea60),
]

ROLE_DEVICE = QtCore.Qt.UserRole + 1
DATA_NAME = QtCore.Qt.UserRole + 2
DATA_ADDR = QtCore.Qt.UserRole + 3
DATA_INFO = QtCore.Qt.UserRole + 4
TYP_REMOTE = QtCore.Qt.UserRole + 5
TYP_USB = QtCore.Qt.UserRole + 6
TYP_UNKNOWN = QtCore.Qt.UserRole + 7

if sys.platform.startswith('darwin'):
    DRIVERS_URL = 'http://www.wch.cn/downloads/CH341SER_MAC_ZIP.html'
elif sys.platform.startswith(('cygwin', 'win32')):
    DRIVERS_URL = 'http://www.wch.cn/downloads/CH341SER_ZIP.html'
else:
    DRIVERS_URL = None
