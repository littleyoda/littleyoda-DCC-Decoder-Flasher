from .qtvariant import QtCore

# Firmware update repository
UPDATE_REPOSITORY = 'https://www.madavi.de/sensor/update/data/'

# URI prefixes (protocol parts, essentially) to be downloaded using requests
ALLOWED_PROTO = ('http://', 'https://')

# vid/pid pairs of known NodeMCU/ESP8266 development boards
PREFERED_PORTS = [
    # CH341
    (0x1A86, 0x7523),

    # CP2102
    (0x10c4, 0xea60),
]

ROLE_DEVICE = QtCore.Qt.UserRole + 1