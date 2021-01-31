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
import zipfile
import base64
import os


import requests
from requests.auth import HTTPBasicAuth
import serial
from esptool import ESPLoader, erase_flash

import airrohrFlasher

from airrohrFlasher.qtvariant import QtGui, QtCore, QtWidgets, QtSerialPort
from PyQt5.QtWidgets import QTableWidget,QTableWidgetItem,QFileDialog,QStyle
from PyQt5.QtCore import Qt
from airrohrFlasher.utils import QuickThread
from airrohrFlasher.workers import PortDetectThread, FirmwareListThread, \
    ZeroconfDiscoveryThread, LogListenerThread

from gui import mainwindow

from airrohrFlasher.consts import UPDATE_REPOSITORY, ALLOWED_PROTO, \
    PREFERED_PORTS, ROLE_DEVICE, DRIVERS_URL, DATA_ADDR,DATA_INFO, DATA_NAME, TYP_REMOTE, TYP_USB, TYP_UNKNOWN

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

        self.addIcon(self.fileopenButton, "SP_FileDialogStart")
        self.addIcon(self.discoveryRefreshButton, "SP_BrowserReload")

    def addIcon(self, widget, iconname):
        widget.setIcon(self.style().standardIcon(getattr(QStyle, iconname)))        

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

        self.statusbar.clearMessage()

    def populate_boards(self, ports):
        """Populates board selection combobox from list of pyserial
        ListPortInfo objects"""

        #self.boardBox.clear()

        prefered, others = self.group_ports(ports)
        for b in others:
            self.statusbar.showMessage("Not Supported: %s:%s %s" %(hex(b.vid), hex(b.pid), str(b)) )
            print("Filtered: " + str(b))

        for b in prefered:
            print("Found: " + str(b))
            rowPosition = self.discoveryList.rowCount()
            self.discoveryList.insertRow(rowPosition)
            data = QTableWidgetItem(b.device)
            data.setData(ROLE_DEVICE, TYP_USB)
            data.setData(DATA_ADDR, b.device)
            data.setData(DATA_NAME, b.description)
            data.setData(DATA_INFO, "")
            self.discoveryList.setItem(rowPosition , 0, data)
            self.discoveryList.setItem(rowPosition , 1, QTableWidgetItem(b.description))
            self.discoveryList.setItem(rowPosition , 2, QTableWidgetItem(""))

    #TODO
    #     if not prefered:
#            sep = QtGui.QStandardItem(self.tr('No boards found'))
 ##           sep.setEnabled(False)
   #         self.boardBox.model().appendRow(sep)

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

        # if others:
        #     sep = QtGui.QStandardItem(self.tr('Others...'))
        #     sep.setEnabled(False)
        #     self.boardBox.model().appendRow(sep)

        # for b in others:
        #     item = QtGui.QStandardItem(
        #         '{0.description} ({0.device})'.format(b))
        #     item.setData(b.device, ROLE_DEVICE)
        #     self.boardBox.model().appendRow(item)

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
        self.serialOutText.setText("")
        
    @QtCore.Slot()
    def on_serialOutText_returnPressed(self):
        s = self.serialOutText.text()
        self.serial.writeData(s.encode('utf-8'))
        self.serialOutText.setText("")

    @QtCore.Slot(bool)
    def on_serialConnectButton_clicked(self,checked):
        if not checked:
            if self.serial:
                self.serial.close()
                self.statusbar.showMessage(self.tr("Disconnected."))
                return

        data = self.discoveryList.selectionModel().selectedRows()[0]
        device = data.data(DATA_ADDR)
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
    def on_fileuploadButton_clicked(self):
            self.statusbar.showMessage(self.tr("Villeicht"))
            options = QFileDialog.Options()
            options |= QFileDialog.DontUseNativeDialog
            fileName, _ = QFileDialog.getOpenFileName(self,"QFileDialog.getOpenFileName()", "","Config-File (config.json);;CSS-File (*.css);;All Files (*)", options=options)
            if fileName:
                print(fileName)
                data = self.discoveryList.selectionModel().selectedRows()[0]
                device = data.data(DATA_ADDR)
                print(device)
                with serial.Serial(device, 115200, timeout=3) as ser:
                    ser.write("xdebug".encode('utf-8'))
                    s = ser.readline().decode('utf-8').rstrip('\r\n')
                    print(s)
                    if (s != "Debugmodus aktiviert"):                
                        s = ser.readline().decode('utf-8').rstrip('\r\n')
                    print(s)
                    if (s != "Debugmodus aktiviert"):               
                        self.statusbar.showMessage(self.tr("Aktivierung des Debugmodus fehlgeschlagen!"))
                        return
                    ser.write("_".encode('utf-8'))
                    s = ser.readline().decode('utf-8').rstrip('\r\n')
                    if (s != "TRANSFER ACTIVE"):               
                        self.statusbar.showMessage(self.tr("Aktivierung des Transfers fehlgeschlagen!"))
                        return
                    with open(fileName, "rb") as f:
                        size = os.fstat(f.fileno()).st_size
                        b64 = base64.b64encode(f.read())
                        s = "PUT " + str(size) + " config.json\r\n"
                        ser.write(s.encode('iso-8859-1'))
                        print(b64)
                        print(s)
                        print("Len " + str(len(b64)))
                        count =ser.write(b64)
                        print("Writting " + str(count) 
                               +" bytes (expected: " + str(len(b64)))
                        ser.flush()
                        s = ser.readline().decode('utf-8').rstrip('\r\n')
                        print(s)
                        s = ser.readline().decode('utf-8').rstrip('\r\n')
                        print(s)
                    ser.write("x".encode('utf-8'))
                    s = ser.readline().decode('utf-8').rstrip('\r\n')

    @QtCore.Slot()
    def on_flashButton_clicked(self):
        self.statusbar.clearMessage()

        data = self.discoveryList.selectionModel().selectedRows()[0]
        typ = data.data(ROLE_DEVICE)
        if (typ == TYP_USB):
            device = data.data(DATA_ADDR)
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

        if (typ == TYP_REMOTE):
            try:
                progress = self.uploadProgress
                version = self.versionBox.currentText()
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
                    self.statusbar.showMessage(self.tr("Invalid version / file does not exist"))
                    return
            
                if binary_uri.startswith(ALLOWED_PROTO):
                    binary_uri = self.cache_download(progress, binary_uri)

                QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
                info = data.data(DATA_INFO)

                flashModus = ""
                if b'FlashModus' in info.properties:
                    flashModus =  info.properties.get(b'FlashModus')


                url = "http://"  + data.data(DATA_ADDR) + "/firmware" 
                auth=HTTPBasicAuth('admin', 'admin')
                if (flashModus == "Arduino_Esp8266_2.6"):
                    files = {'firmware': open(binary_uri,'rb')}
                elif (flashModus == "Arduino_Esp8266_2.5" or flashModus == ""):
                    files = {'file': open(binary_uri,'rb')}

                values = {}
                progress.emit(self.tr('Uploading...'), 1)
                r = requests.post(url, files=files, data=values,auth=auth)
                if (r.status_code == 200):
                    string = re.sub('<.*?>', '', r.text)
                    progress.emit(self.tr("Finish. {text}").format(text=string), 100)
                else:
                    progress.emit(self.tr('Error {code} : {text}').format(code = str(r.status_code), text = r.text), 1)
            finally:
                QtWidgets.QApplication.restoreOverrideCursor() 


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
        data = self.discoveryList.selectionModel().selectedRows()[0]
        device = data.data(DATA_ADDR)

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


        t = time.time()
        if zipfile.is_zipfile(binary_uri):
            with zipfile.ZipFile(binary_uri) as myzip:
                for fname in myzip.namelist():
                    if fname.startswith("0x"):
                        addr = int(fname, 16)
                        with myzip.open(fname) as myfile:
                            data = myfile.read()
                            print("Segment: " + fname + " / " + str(addr) + " Size: " + str(len(data)))
                            self.flashBlock(data, progress, esp, addr)
                    else:
                        print("Cannot handle " + fname)

        else:
            with open(binary_uri, 'rb') as fd:
                uncimage = fd.read()
            self.flashBlock(uncimage, progress, esp, 0x0)
        t = time.time() - t


        esp.flash_finish(True)

        progress.emit(self.tr(
            'Finished in {time:.2f} seconds.').format(
                time=t), 100)


    def flashBlock(self, uncimage, progress, esp, address):
        image = zlib.compress(uncimage, 9)

        blocks = esp.flash_defl_begin(len(uncimage), len(image), address)
        seq = 0
        written = 0
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
        try:
         if (name.lower().startswith('ly-dcc-')):
            rowPosition = self.discoveryList.rowCount()
            self.discoveryList.insertRow(rowPosition)

            data = QTableWidgetItem(address)
            data.setData(ROLE_DEVICE, TYP_REMOTE)
            data.setData(DATA_ADDR, address)
            data.setData(DATA_NAME, name)
            data.setData(DATA_INFO, info)
            self.discoveryList.setItem(rowPosition , 0, data)
            self.discoveryList.setItem(rowPosition , 1, QTableWidgetItem(name.split('.')[0]))
            self.discoveryList.setItem(rowPosition , 2, QTableWidgetItem(info.properties.get(b"Version").decode('utf-8')))
        except:
            print("Error")


    def enableDiscoveryButton(self, selectedTyp):
        self.discoveryBrowser.setEnabled(selectedTyp == TYP_REMOTE)
        self.flashButton.setEnabled(selectedTyp == TYP_REMOTE or selectedTyp == TYP_USB)
        self.eraseButton.setEnabled(selectedTyp == TYP_USB)
        self.fileuploadButton.setEnabled(selectedTyp == TYP_USB)
        self.fileuploadButton.hide()
        #self.versionBox.setEnabled(selectedTyp == TYP_REMOTE or selectedTyp == TYP_USB)
        self.enableLoggingButton.setEnabled(selectedTyp == TYP_REMOTE)
        #self.fileopenButton.setEnabled(selectedTyp == TYP_USB)
        self.serialConnectButton.setEnabled(selectedTyp == TYP_USB)

    @QtCore.Slot()
    def on_discoveryBrowser_clicked(self):
        data = self.discoveryList.selectionModel().selectedRows()[0]
        url = "http://"  + data.data(DATA_ADDR) 
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    @QtCore.Slot()
    def on_enableLoggingButton_clicked(self):
        data = self.discoveryList.selectionModel().selectedRows()[0]
        url = "http://"  + data.data(DATA_ADDR) + "/set?id=sys&key=log&value=bcast"
        r = requests.get(url)
        if (r.status_code == 200):
            self.statusbar.showMessage(self.tr("Started."))
        else:
            self.statusbar.showMessage(self.tr('Error {code} : {text}').format(code = str(status_code), text = r.text))

    @QtCore.Slot()
    def on_discoveryList_itemSelectionChanged(self):
        rows = self.discoveryList.selectionModel().selectedRows()
        typ = rows[0].data(ROLE_DEVICE) if (len(rows) > 0) else TYP_UNKNOWN
        self.enableDiscoveryButton(typ)


    @QtCore.Slot()
    def on_fileopenButton_clicked(self):
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        fileName, _ = QFileDialog.getOpenFileName(self,"QFileDialog.getOpenFileName()", "","Fimrware (*.bin *.ly32);;ESP8266 Firmware (*.bin);;ESP32 Firmware (*.ly32);;All Files (*)", options=options)
        if fileName:
            item = QtGui.QStandardItem("File: " + fileName)
            item.setData(fileName, ROLE_DEVICE)
            self.versionBox.model().insertRow(0, item)
            self.versionBox.setCurrentIndex(0)
            # idx = self.versionBox.currentIndex()
            # self.versionBox.selected
            # print(idx)
            # self.versionBox.setText(filename)

    @QtCore.Slot()
    def on_discoveryRefreshButton_clicked(self):
        self.discovery_start()
        self.port_detect.restart()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow(app=app)
    window.show()
    sys.exit(app.exec_())
