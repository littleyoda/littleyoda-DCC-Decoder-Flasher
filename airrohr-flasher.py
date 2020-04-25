#!/usr/bin/env python3
# -* encoding: utf-8 *-

import sys
import os.path
import time
import tempfile
import hashlib
import zlib
import logging
import re

import requests
from requests.auth import HTTPBasicAuth
import serial
from esptool import ESPLoader, erase_flash

import airrohrFlasher

from airrohrFlasher.qtvariant import QtGui, QtCore, QtWidgets, QtSerialPort
from PyQt5.QtWidgets import QTableWidget,QTableWidgetItem
from PyQt5.QtCore import Qt
from airrohrFlasher.utils import QuickThread
from airrohrFlasher.workers import PortDetectThread, FirmwareListThread, \
    ZeroconfDiscoveryThread, LogListenerThread

from gui import mainwindow

from airrohrFlasher.consts import UPDATE_REPOSITORY, ALLOWED_PROTO, \
    PREFERED_PORTS, ROLE_DEVICE, DRIVERS_URL, ROLE_DNSSD_ADDR,ROLE_DNSSD_INFO, ROLE_DNSSD_NAME

if getattr(sys, 'frozen', False):
    RESOURCES_PATH = sys._MEIPASS
else:
    RESOURCES_PATH = os.path.dirname(os.path.realpath(__file__))


class MainWindow(QtWidgets.QMainWindow, mainwindow.Ui_MainWindow):
    uploadProgress = QtCore.Signal([str, int])
    errorSignal = QtCore.Signal([str])
    uploadThread = None
    zeroconf_discovery = None
    boards_detected = False

    def __init__(self, parent=None, app=None):
        super(MainWindow, self).__init__(parent)
        self.setWindowFlags(QtCore.Qt.Dialog)

        # FIXME: dirty hack to solve relative paths in *.ui
        oldcwd = os.getcwd()
        os.chdir(os.path.join(RESOURCES_PATH, 'assets'))
        self.setupUi(self)
        os.chdir(oldcwd)

        self.app = app

        self.translator = QtCore.QTranslator()
        self.i18n_init(QtCore.QLocale.system())

        self.statusbar.showMessage(self.tr("Loading firmware list..."))

        self.versionBox.clear()
        self.DTversionBox.clear()
        self.firmware_list = FirmwareListThread()
        self.firmware_list.listLoaded.connect(self.populate_versions)
        self.firmware_list.error.connect(self.on_work_error)
        self.firmware_list.start()

        self.enableDiscoveryButton(False)

        self.port_detect = PortDetectThread()
        self.port_detect.portsUpdate.connect(self.populate_boards)
        self.port_detect.error.connect(self.on_work_error)
        self.port_detect.start()

        self.discovery_start()

        self.globalMessage.hide()

        # Hide WIP GUI parts...
        self.on_expertModeBox_clicked()
        self.expertModeBox.hide()
        #self.tabWidget.removeTab(self.tabWidget.indexOf(self.serialTab))

        self.uploadProgress.connect(self.on_work_update)
        self.errorSignal.connect(self.on_work_error)

        self.cachedir = tempfile.TemporaryDirectory()

        self.serial = None


        self.logTable.setHorizontalHeaderLabels(['Zeit', 'IP', 'Message'])
        header = self.logTable.horizontalHeader()       
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)

        self.logger = LogListenerThread()
        self.logger.logReceived.connect(self.on_logmessage_received)
        self.logger.start()


    def show_global_message(self, title, message):
        self.globalMessage.show()
        self.globalMessageTitle.setText(title)
        self.globalMessageText.setText(message)

    def on_work_update(self, status, progress):
        self.statusbar.showMessage(status)
        self.progressBar.setValue(progress)

    def on_work_error(self, message):
        self.statusbar.showMessage(message)

    @property
    def version(self):
        return airrohrFlasher.__version__

    @property
    def build_id(self):
        try:
            from airrohrFlasher._buildid import commit, builddate
        except ImportError:
            import datetime
            commit = 'devel'
            builddate = datetime.datetime.now().strftime('%Y%m%d')

        return '{}-{}/{}'.format(self.version, commit, builddate)

    def i18n_init(self, locale):
        """Initializes i18n to specified QLocale"""

        self.app.removeTranslator(self.translator)
        lang = QtCore.QLocale.languageToString(locale.language())
        self.translator.load(os.path.join(
            RESOURCES_PATH, 'i18n', lang + '.qm'))
        self.app.installTranslator(self.translator)
        self.retranslateUi(self)

    def retranslateUi(self, win):
        super(MainWindow, self).retranslateUi(win)

        win.setWindowTitle(win.windowTitle().format(
            version=self.version))
        win.buildLabel.setText(win.buildLabel.text().format(
            build_id=self.build_id))

    def populate_versions(self, files):
        """Loads available firmware versions into versionbox widget"""

        for fname in files:
            item = QtGui.QStandardItem(fname[0] + " (" + fname[1] + ")");
            item.setData(fname[2], ROLE_DEVICE)
            self.versionBox.model().appendRow(item)

            item2 = QtGui.QStandardItem(fname[0] + " (" + fname[1] + ")");
            item2.setData(fname[2], ROLE_DEVICE)
            self.DTversionBox.model().appendRow(item2)

        self.statusbar.clearMessage()

    def populate_boards(self, ports):
        """Populates board selection combobox from list of pyserial
        ListPortInfo objects"""

        self.boardBox.clear()

        prefered, others = self.group_ports(ports)

        for b in prefered:
            item = QtGui.QStandardItem(
                '{0.description} ({0.device})'.format(b))
            item.setData(b.device, ROLE_DEVICE)
            self.boardBox.model().appendRow(item)

        if not prefered:
            sep = QtGui.QStandardItem(self.tr('No boards found'))
            sep.setEnabled(False)
            self.boardBox.model().appendRow(sep)

            # No prefered boards has been found so far and there is a
            # suggested driver download URL available
            if not self.boards_detected and DRIVERS_URL:
                self.show_global_message(
                    self.tr('No boards found'),
                    self.tr('Have you installed <a href="{drivers_url}">'
                            'the drivers</a>?').format(drivers_url=DRIVERS_URL))
        else:
            self.globalMessage.hide()
            self.boards_detected = True

        if others:
            sep = QtGui.QStandardItem(self.tr('Others...'))
            sep.setEnabled(False)
            self.boardBox.model().appendRow(sep)

        for b in others:
            item = QtGui.QStandardItem(
                '{0.description} ({0.device})'.format(b))
            item.setData(b.device, ROLE_DEVICE)
            self.boardBox.model().appendRow(item)

    def group_ports(self, ports):
        prefered = []
        others = []

        for p in ports:
            if (p.vid, p.pid) in PREFERED_PORTS:
                prefered.append(p)
            else:
                others.append(p)
        return prefered, others

    @QtCore.Slot()
    def on_serialSendButton_clicked(self):
        # TODO Check if it is connected
        s = self.serialOutText.text()
        self.serial.writeData(s.encode('utf-8'))
        


        
    @QtCore.Slot(bool)
    def on_serialConnectButton_clicked(self,checked):
        if not checked:
            if self.serial:
                self.serial.close()
                self.statusbar.showMessage(self.tr("Disconnected."))
                return

        device = self.boardBox.currentData(ROLE_DEVICE)
        self.serialTextEdit.setText("")
        if not device:
            self.statusbar.showMessage(self.tr("No device selected."))
            return

        self.serial = QtSerialPort.QSerialPort(device,
            baudRate=QtSerialPort.QSerialPort.Baud115200,
            readyRead=self.receive
        )        
        if self.serial.open(QtCore.QIODevice.ReadWrite):        
            self.statusbar.showMessage(self.tr("Connected."))
        else:
            self.statusbar.showMessage(self.tr("Error while opening com port."))

    @QtCore.Slot()
    def receive(self):
        while self.serial.canReadLine():
            text = self.serial.readLine().data().decode()
            text = text.rstrip('\r\n')
            self.serialTextEdit.append(text)

    @QtCore.Slot()
    def on_uploadButton_clicked(self):
        self.statusbar.clearMessage()

        device = self.boardBox.currentData(ROLE_DEVICE)
        version = self.versionBox.currentText()

        if not device:
            self.statusbar.showMessage(self.tr("No device selected."))
            return

        if not version:
            self.statusbar.showMessage(self.tr("No version selected."))
            return

        sel = self.versionBox.model().item(
            self.versionBox.currentIndex())
        if sel:
            orig_version = sel.text()
        else:
            orig_version = ''

        if version == orig_version:
            # Editable combobox has been unchanged
            binary_uri = self.versionBox.currentData(ROLE_DEVICE)
        elif version.startswith(ALLOWED_PROTO):
            # User has provided a download URL
            binary_uri = version
        elif os.path.exists(version):
            binary_uri = version
        else:
            self.statusbar.showMessage(self.tr(
                "Invalid version / file does not exist"))
            return

        if self.flash_board.running():
            self.statusbar.showMessage(self.tr("Work in progess..."))
            return

        self.flash_board(self.uploadProgress, device, binary_uri,
                         error=self.errorSignal)

    def cache_download(self, progress, binary_uri):
        """Downloads and caches file with status reports via Qt Signals"""
        cache_fname = os.path.join(
            self.cachedir.name,
            hashlib.sha256(binary_uri.encode('utf-8')).hexdigest())

        if os.path.exists(cache_fname):
            return cache_fname

        with open(cache_fname, 'wb') as fd:
            progress.emit(self.tr('Downloading...'), 0)
            response = requests.get(binary_uri, stream=True)
            total_length = response.headers.get('content-length')

            dl = 0
            total_length = int(total_length or 0)
            for data in response.iter_content(chunk_size=4096):
                dl += len(data)
                fd.write(data)

                if total_length:
                    progress.emit(self.tr('Downloading...'),
                                  (100*dl) // total_length)

        return cache_fname

    @QuickThread.wrap
    def erase_board(self, progress, device, baudrate=460800):

        progress.emit(self.tr('Connecting...'), 0)
        init_baud = min(ESPLoader.ESP_ROM_BAUD, baudrate)
        esp = ESPLoader.detect_chip(device, init_baud, 'default_reset', False)

        progress.emit(self.tr('Connected. Chip type: {chip_type}').format(
                      chip_type=esp.get_chip_description()), 0)
        esp = esp.run_stub()
        esp.change_baud(baudrate)
        esp.erase_flash()
        progress.emit(self.tr('Erasing complete!'), 100)

    @QtCore.Slot()
    def on_eraseButton_clicked(self):
        self.statusbar.clearMessage()
        device = self.boardBox.currentData(ROLE_DEVICE)

        if not device:
            self.statusbar.showMessage(self.tr("No device selected."))
            return

        if self.erase_board.running():
            self.statusbar.showMessage(self.tr("Erasing in progress..."))
            return

        self.erase_board(self.uploadProgress, device,
                         error=self.errorSignal)

    @QuickThread.wrap
    def flash_board(self, progress, device, binary_uri, baudrate=460800):
        if binary_uri.startswith(ALLOWED_PROTO):
            binary_uri = self.cache_download(progress, binary_uri)

        progress.emit(self.tr('Connecting...'), 0)

        init_baud = min(ESPLoader.ESP_ROM_BAUD, baudrate)
        esp = ESPLoader.detect_chip(device, init_baud, 'default_reset', False)

        progress.emit(self.tr('Connected. Chip type: {chip_type}').format(
                      chip_type=esp.get_chip_description()), 0)
        esp = esp.run_stub()
        esp.change_baud(baudrate)

        with open(binary_uri, 'rb') as fd:
            uncimage = fd.read()

        image = zlib.compress(uncimage, 9)

        address = 0x0
        blocks = esp.flash_defl_begin(len(uncimage), len(image), address)

        seq = 0
        written = 0
        t = time.time()
        while len(image) > 0:
            current_addr = address + seq * esp.FLASH_WRITE_SIZE
            progress.emit(self.tr('Writing at 0x{address:08x}...').format(
                          address=current_addr),
                          100 * (seq + 1) // blocks)

            block = image[0:esp.FLASH_WRITE_SIZE]
            esp.flash_defl_block(block, seq, timeout=3.0)
            image = image[esp.FLASH_WRITE_SIZE:]
            seq += 1
            written += len(block)
        t = time.time() - t

        progress.emit(self.tr(
            'Finished in {time:.2f} seconds. Sensor ID: {sensor_id}').format(
                time=t, sensor_id=esp.chip_id()), 100)

        esp.flash_finish(True)

    @QtCore.Slot()
    def on_expertModeBox_clicked(self):
        self.expertForm.setVisible(self.expertModeBox.checkState())
        # self.centralwidget.setFixedHeight(
        #     self.centralwidget.sizeHint().height())
        # self.setFixedHeight(self.sizeHint().height())

    # Zeroconf page
    def discovery_start(self):
        if self.zeroconf_discovery:
            self.zeroconf_discovery.stop()

        self.zeroconf_discovery = ZeroconfDiscoveryThread()
        self.zeroconf_discovery.deviceDiscovered.connect(self.on_zeroconf_discovered)
        self.zeroconf_discovery.start()
        self.discoveryList.setRowCount(0)
        self.discoveryList.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.AdjustToContents)
        self.discoveryList.setHorizontalHeaderLabels(['IP', 'Name', 'Version'])
        header = self.discoveryList.horizontalHeader()
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)

    def on_logmessage_received(self, addr, data):
        print(data)
        print(addr)
        rowPosition = 0 # self.logTable.rowCount()
        self.logTable.insertRow(rowPosition)

        self.logTable.setItem(rowPosition , 0, QTableWidgetItem(""))
        self.logTable.setItem(rowPosition , 1, QTableWidgetItem(data))
        self.logTable.setItem(rowPosition , 2, QTableWidgetItem(addr))


    def on_zeroconf_discovered(self, name, address, info):
        """Called on every zeroconf discovered device"""
        if (name.lower().startswith('ly-dcc-')):
            rowPosition = self.discoveryList.rowCount()
            self.discoveryList.insertRow(rowPosition)

            data = QTableWidgetItem(address)
            data.setData(ROLE_DNSSD_ADDR, address)
            data.setData(ROLE_DNSSD_NAME, name)
            data.setData(ROLE_DNSSD_INFO, info)
            self.discoveryList.setItem(rowPosition , 0, data)
            self.discoveryList.setItem(rowPosition , 1, QTableWidgetItem(name.split('.')[0]))
            self.discoveryList.setItem(rowPosition , 2, QTableWidgetItem(info.properties.get(b"Version").decode('utf-8')))

    def enableDiscoveryButton(self, enabled):
        self.discoveryBrowser.setEnabled(enabled)
        self.uploadOverTheAirButton.setEnabled(enabled)
        self.DTversionBox.setEnabled(enabled)
        self.enableLoggingButton.setEnabled(enabled)



    @QtCore.Slot()
    def on_uploadOverTheAirButton_clicked(self):
        try:
            progress = self.uploadProgress
            version = self.DTversionBox.currentText()
            sel = self.DTversionBox.model().item(
                self.DTversionBox.currentIndex())
            if sel:
                orig_version = sel.text()
            else:
                orig_version = ''

            if version == orig_version:
                # Editable combobox has been unchanged
                binary_uri = self.DTversionBox.currentData(ROLE_DEVICE)
            elif version.startswith(ALLOWED_PROTO):
                # User has provided a download URL
                binary_uri = version
            elif os.path.exists(version):
                binary_uri = version
            else:
                self.statusbar.showMessage(self.tr("Invalid version / file does not exist"))
                return
        
            if binary_uri.startswith(ALLOWED_PROTO):
                binary_uri = self.cache_download(progress, binary_uri)

            QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
            data = self.discoveryList.selectionModel().selectedRows()[0]
            url = "http://"  + data.data(ROLE_DNSSD_ADDR) + "/firmware" 
            auth=HTTPBasicAuth('admin', 'admin')
            files = {'file': open(binary_uri,'rb')}
            values = {}
            progress.emit(self.tr('Uploading...'), 1)
            r = requests.post(url, files=files, data=values,auth=auth)
            if (r.status_code == 200):
                string = re.sub('<.*?>', '', r.text)
                progress.emit(self.tr("Finish. {text}").format(text=string), 100)
            else:
                progress.emit(self.tr('Error {code} : {text}').format(code = str(status_code), text = r.text), 1)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor() 

    @QtCore.Slot()
    def on_discoveryBrowser_clicked(self):
        data = self.discoveryList.selectionModel().selectedRows()[0]
        url = "http://"  + data.data(ROLE_DNSSD_ADDR) 
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    @QtCore.Slot()
    def on_enableLoggingButton_clicked(self):
        data = self.discoveryList.selectionModel().selectedRows()[0]
        url = "http://"  + data.data(ROLE_DNSSD_ADDR) + "/set?id=sys&key=log&value=bcast"
        r = requests.get(url)
        if (r.status_code == 200):
            self.statusbar.showMessage(self.tr("Started."))
        else:
            self.statusbar.showMessage(self.tr('Error {code} : {text}').format(code = str(status_code), text = r.text))
        



    @QtCore.Slot()
    def on_discoveryList_itemSelectionChanged(self):
        rows = self.discoveryList.selectionModel().selectedRows()
        self.enableDiscoveryButton(len(rows) > 0)

    @QtCore.Slot()
    def on_eraseButton_clicked(self):
        self.statusbar.clearMessage()
        device = self.boardBox.currentData(ROLE_DEVICE)

        if not device:
            self.statusbar.showMessage(self.tr("No device selected."))
            return

        if self.erase_board.running():
            self.statusbar.showMessage(self.tr("Erasing in progress..."))
            return

        self.erase_board(self.uploadProgress, device,
                         error=self.errorSignal)

    @QtCore.Slot()
    def on_discoveryRefreshButton_clicked(self):
        self.discovery_start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow(app=app)
    window.show()
    sys.exit(app.exec_())
