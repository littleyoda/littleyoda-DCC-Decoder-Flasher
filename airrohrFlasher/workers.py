import time
import socket

import serial
import serial.tools.list_ports
import zeroconf
import socket
from .qtvariant import QtCore
from .utils import indexof, QuickThread
from .consts import UPDATE_REPOSITORY
from typing import cast

class PortDetectThread(QuickThread):
    interval = 1.0
    portsUpdate = QtCore.Signal([list])
    ports = None

    def target(self):
        """Checks list of available ports and emits signal when necessary"""

        while True:
            new_ports = serial.tools.list_ports.comports()

            if self.ports is None or [p.name for p in self.ports] != [p.name for p in new_ports]:
                self.portsUpdate.emit(new_ports)

            time.sleep(self.interval)

            self.ports = new_ports

    def restart(self):
        if not self.ports is None:
                self.portsUpdate.emit(self.ports)


class FirmwareListThread(QuickThread):
    listLoaded = QtCore.Signal([list])

    def target(self):
        """Downloads list of available firmware updates in separate thread."""
        self.listLoaded.emit(list(indexof(UPDATE_REPOSITORY)))


class ZeroconfDiscoveryThread(QuickThread):
    deviceDiscovered = QtCore.Signal(str, str, object)
    browser = None

    def target(self):
        """This thread scans for Bonjour/mDNS devices and emits
        deviceDiscovered signal with its name, address and info object"""
        self.zc = zeroconf.Zeroconf()
        self.browser = zeroconf.ServiceBrowser(
            self.zc, "_http._tcp.local.", handlers=[self.on_state_change])
        while True:
            time.sleep(0.5)

    def on_state_change(self, zeroconf, service_type, name, state_change):
        info = zeroconf.get_service_info(service_type, name)
        if info:
            for addr in info.parsed_addresses():
             print("  Addresses: %s" % (addr))
             self.deviceDiscovered.emit(name, addr, info)

    def stop(self):
        if self.browser:
            self.browser.cancel()



class LogListenerThread(QuickThread):
    logReceived = QtCore.Signal(str, str)
#    deviceDiscovered = QtCore.Signal(str, str, object)
    #browser = None

    def target(self):
        serverSock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        serverSock.bind(("0.0.0.0", 5514))
        while True:
                data, addr = serverSock.recvfrom(1024) # buffer size is 1024 bytes   
                self.logReceived.emit(data.decode("utf-8"), addr[0])

