
"""
The UDPDevice class handles I/O for a UDP based "serial port". It is simple class with
an interface that mimics SerialDevice and is used by SerialMonitor when the user
specifies a UDP based port:

    udp://0.0.0.0:1234

and emits "whole messages" via the SerialDataReceived signal. Typically this class
is used internally by the SerialMonitor class which manages the thread and the
creation and destruction of this object.

NOTE! This class does not support transmitting data.
"""


import re
from PyQt5.QtCore import pyqtSignal, QObject, pyqtSlot
from PyQt5 import QtNetwork


class UDPDevice(QObject):

    #  define the UDPDevice class's signals
    DCEControlState = pyqtSignal(str, list)
    SerialControlChanged = pyqtSignal(str, str, bool)
    SerialDataReceived = pyqtSignal(str, str, object)
    SerialPortClosed = pyqtSignal(str)
    SerialError = pyqtSignal(str, object)

    def __init__(self, deviceParams):

        super(UDPDevice, self).__init__(None)

        #  set default values
        self.rxBuffer = ''
        self.txBuffer = []
        self.filtRx = ''
        self.rts = deviceParams['initialState'][0]
        self.dtr = deviceParams['initialState'][1]
        self.udp_socket = None

        #  define a list that stores the state of the control lines: order is [CTS, DSR, RI, CD]
        self.controlLines = [False, False, False, False]

        #  define the maximum line length allowed - no sane input should exceed this
        self.maxLineLen = 16384

        #  set the device name
        self.deviceName = deviceParams['deviceName']

        #  set the parsing parameters
        if (deviceParams['parseType']):
            if deviceParams['parseType'].upper() == 'REGEX':
                self.parseType = 2
                try:
                    #  compile the regular expression
                    self.parseExp = re.compile(deviceParams['parseExp'])
                except Exception as e:
                    self.SerialError.emit(self.deviceName, SerialError('Invalid regular expression configured for ' +
                            self.deviceName, parent=e))
            elif deviceParams['parseType'].upper() == 'DELIMITED':
                self.parseType = 1
                self.parseExp = deviceParams['parseExp']
            elif deviceParams['parseType'].upper() == 'RFIDFDXB':
                self.parseType = 13
                self.parseExp = ''
                self.maxLineLen = int(deviceParams['parseIndex'])
            elif deviceParams['parseType'].upper() == 'HEXENCODE':
                self.parseType = 12
                self.parseExp = ''
                self.maxLineLen = int(deviceParams['parseIndex'])
            elif deviceParams['parseType'].upper() == 'FIXEDLEN':
                self.parseType = 11
                self.parseExp = ''
                self.maxLineLen = int(deviceParams['parseIndex'])
            else:
                self.parseType = 0
                self.parseExp = ''
        else:
            self.parseType = 0
            self.parseExp = ''

        try:
            self.parseIndex = int(deviceParams['parseIndex'])
        except:
            self.parseIndex = 0

        #  Set the command prompt  - This is required for devices that present a
        #  command prompt that must be responded to.
        self.cmdPrompt = deviceParams['cmdPrompt']
        self.cmdPromptLen = len(self.cmdPrompt)

        try:
            #  create the local UDP port we'll use to listen on
            portParts = deviceParams['port'].split(':')
            self.ip = portParts[1].strip('/')
            self.port = int(portParts[2])

        except Exception as e:
            self.SerialError.emit(self.deviceName, SerialError('Unable to create UDP based port for ' +
                    self.deviceName + '. Invalid port option.', parent=e))
            self.udp_socket = None


    @pyqtSlot()
    def startPolling(self):
        """
          Open the UDP port
        """

        #  check that we're not currently bound
        if self.udp_socket is None:
            try:
                #  create and open the UDP port
                self.udp_socket = QtNetwork.QUdpSocket()
                self.udp_socket.readyRead.connect(self.udp_data_available)
                self.udp_socket.bind(self.port)

            except Exception as e:
                self.SerialError.emit(self.deviceName, SerialError('Unable to open UDP based port for device ' +
                       self.deviceName + '.', parent=e))


    @pyqtSlot(list)
    def stopPolling(self, deviceList):
        """
          Close our UDP port and emit the SerialPortClosed signal
        """

        #  check if this signal is for us
        if (self.deviceName not in deviceList):
            #  this is not the droid we're looking for
            return

        if self.udp_socket.state() > 0:

            self.udp_socket.readyRead.disconnect()

            #  close the UDP socket
            self.udp_socket.close()

            self.udp_socket = None

            #  emit the SerialPortClosed signal
            self.SerialPortClosed.emit(self.deviceName)

        else:
            #  if the poll timer is None, we aren't running so we immediately emit the closed signal
            self.SerialPortClosed.emit(self.deviceName)


    @pyqtSlot(str, bool)
    def setRTS(self, deviceName, state):
        """
          Set/Unset the RTS line on this serial port. This method is not supported for
          UDP sockets
        """
        pass


    @pyqtSlot(str, bool)
    def setDTR(self, deviceName, state):
        """
          Set/Unset the DTR line on this serial port. This method is not supported for
          UDP sockets
        """
        pass


    @pyqtSlot(str)
    def getControlLines(self, deviceName):
        """
            Returns the state of the DCE control lines. This method is not supported for
            UDP sockets
        """
        if deviceName == self.deviceName:
            self.DCEControlState.emit(self.deviceName, self.controlLines)


    @pyqtSlot(str, str)
    def write(self, deviceName, data):
        """
          Write data to the serial port. This method is not supported for UDP sockets
          which are listen/read only
        """

        pass


    def filterRAMSESChars(self, data):
        """
            replace control characters in RAMSES sensor data stream
        """

        controlChars = {'@e':'\x23', '@d':'\x40', '@f':'\x11', '@g':'\x13'}
        for i, j in controlChars.iteritems():
            data = data.replace(i, j)

        return data


    @pyqtSlot()
    def udp_data_available(self):

        #  get a reference to the port object that Rx'd the data
        udp_source = self.sender()

        while udp_source.hasPendingDatagrams():

            #  get the length of the next datagram and read it
            datagram_len = udp_source.pendingDatagramSize()
            data, source_host, source_port = udp_source.readDatagram(datagram_len)

            #  decode
            try:
                rxData = data.decode('utf-8')
            except:
                rxData = ''

            #  check if there is data in the buffer and append if so
            buffLength = len(self.rxBuffer)
            if buffLength > 0:
                rxData = self.rxBuffer + rxData
                #  reset the buffer
                self.rxBuffer = ''

            #  get the new length of our rx buffer
            buffLength = len(rxData)

            #  Parse the received data
            if (self.parseType <= 10):
                #  Parse types 0-10 are "line based" and are strings of chars
                #  that are terminated by an EOL (\n or \r\n) characters.

                #  check if we have to force the buffer to be processed
                if buffLength > self.maxLineLen:
                    #  the buffer is too big - force process it
                    rxData = rxData + '\n'

                #  split lines into a list
                lines = rxData.splitlines(True)

                #  loop thru the extracted lines
                for line in lines:
                    err = None
                    #  check for complete lines
                    if line.endswith('\n') or line.endswith('\r'):
                        #  this line is complete - strip the newline character(s) and whitespace
                        line = line.rstrip('\r\n').strip()

                        #  and make sure we have some text
                        if line:
                            #  we do, process line
                            try:
                                if self.parseType == 2:
                                    #  use regular expression to parse
                                    parts = self.parseExp.findall(line)
                                    data = parts[self.parseIndex]
                                elif self.parseType == 1:
                                    #  use a delimiter to parse
                                    parts = line.split(self.parseExp)
                                    data = parts[self.parseIndex]
                                else:
                                    # do not parse - pass whole line
                                    data = line
                            except Exception as e:
                                data = None
                                err = SerialError('Error parsing input from ' + self.deviceName + \
                                                   '. Incorrect parsing configuration or malformed data stream.', \
                                                   parent=e)

                            # emit a signal containing data from this line
                            self.SerialDataReceived.emit(self.deviceName, data, err)

                    elif (self.cmdPromptLen > 0) and (line[-self.cmdPromptLen:] == self.cmdPrompt):
                        #  this line (or the end of it) matches the command prompt
                        self.SerialDataReceived.emit(self.deviceName, line, err)

                    else:
                        #  this line of data is not complete - insert in buffer
                        self.rxBuffer = line

            elif (self.parseType <= 20):
                #  Parse types 11-20 are length based. This method of parsing acts on a
                #  fixed number of characters.

                #  loop thru the rx buffer extracting our fixed length chunks of data
                lines = []
                for i in range(0, (buffLength // self.maxLineLen)):
                    #  generate the start and end indices into our chunk
                    si = i * self.maxLineLen
                    ei = si + self.maxLineLen
                    #  extract it
                    lines.append(rxData[si:ei])
                    #  remove the chunk from the working rx buffer
                    rxData = rxData[ei:]

                #  place any partial chunks back in the buffer
                self.rxBuffer = self.rxBuffer + rxData

                #  loop thru the extracted chunks and process
                for line in lines:
                    err = None
                    #  process chunk
                    try:

                        if (self.parseType == 12):
                            #  encode the entire chunk as hex
                            data = line.encode('hex')

                        if (self.parseType == 13):
                            #  Process this as a type FDX-B RFID tag

                            #  this parsing is based on a single RFID reader which outputs a fixed 8 byte
                            #  datagram with no newline. It doesn't appear to support the "extra data block"
                            #  so that data is not handled by this parsing routine.

                            bstr = ''
                            for c in line:
                                #  construct the original binary stream
                                bstr = bin(ord(c))[2:].zfill(8) + bstr
                            #  decode the binary string into the ID code, Country code, data block status bit, and animal bit
                            data = [str(int(bstr[26:64],2)), str(int(bstr[16:26],2)), bstr[15], bstr[0]]

                        else:
                            # do not do anything - pass whole chunk
                            data = line

                    except Exception as e:
                        data = None
                        err = SerialError('Error parsing input from ' + self.deviceName + \
                                           '. Incorrect parsing configuration or malformed data stream.', \
                                           parent=e)

                    # emit a signal containing data from this line
                    self.SerialDataReceived.emit(self.deviceName, data, err)


#
#  SerialDevice Exception class
#
class SerialError(Exception):
    def __init__(self, msg, parent=None):
        self.errText = msg
        self.parent = parent

    def __str__(self):
        return repr(self.errText)
