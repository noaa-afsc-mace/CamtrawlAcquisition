#
#  Should add NMEA parsing:
#       add a parse type of NMEA
#       the parse expression should be a list of NMEA talker+sentence ids to extract
#       The talker and sentence ID should be extracted
#       separate parsing function that accepts the sentence + data and returns dict with fields named for their contents
#         example: GLL {'latitude':12.34, 'ns':'N', 'longitude':123.45, 'ew':'W', 'utc':123456.78, 'status':'A'}
#
#       should include checksum verification:
#
#       from operator import xor
#       data='$GPAAM,A,A,0.10,N,WPTNME*32'
#       nmea = map(ord, data[1:data.index('*')])
#       checksum = reduce(xor, nmea)
#       print hex(checksum)
#
"""
The SerialDevice class handles I/O for an individual serial port. It is intended to be
run in its own thread and polls the serial port, buffers the received data, processes
it, and emits "whole messages" via the SerialDataReceived signal. Typically this
class is used internally by the SerialMonitor class which manages the threads and
the creation and destruction of this object.

"""


import re
import serial
from PyQt5.QtCore import pyqtSignal, QObject, QTimer, pyqtSlot


class SerialDevice(QObject):

    #  define the SerialDevice class's signals
    DCEControlState = pyqtSignal(str, list)
    SerialControlChanged = pyqtSignal(str, str, bool)
    SerialDataReceived = pyqtSignal(str, str, object)
    SerialPortClosed = pyqtSignal(str)
    SerialError = pyqtSignal(str, object)

    def __init__(self, deviceParams):

        super(SerialDevice, self).__init__(None)

        #  set default values
        self.rxBuffer = ''
        self.txBuffer = []
        self.filtRx = ''
        self.rts = deviceParams['initialState'][0]
        self.dtr = deviceParams['initialState'][1]
        self.partControl = False
        self.pollTimer = None
        self.txTimer = None

        #  define the transmit interval - some use cases require the transmit speed to be
        #  throttled because the connected device cannot process incoming data fast
        #  enough causing data loss. The tx interval can be set to mitigate this.
        self.txInterval = int(1000.0 / max(min(deviceParams['txRate'], 1000), 1))

        #  clamp the polling rate and convert to interval - QTimer supports ms resolution
        #  and we allow polling rates from 1-1000 Hz.
        self.pollInterval = int(1000.0 / max(min(deviceParams['pollRate'], 1000), 1))

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

        #  as of PySerial 3.0, assigning ports by int index is no longer supported. For backwards
        #  compatibility, convert int defined ports to string assuming the int is a 0 based index
        #  into the systems list of COM ports.
        #
        #  THIS IS NOT COMPATIBLE WITH LINUX/MAC but those ports should already be defined as strings
        #
        if  isinstance(deviceParams['port'], (int, float)):
            deviceParams['port'] = 'COM' + str(int(deviceParams['port']) + 1)

        try:
            #  create the serial port use the factory function serial_for_url to return either
            #  a native serial port instance or a RFC 2217 instance based on the port definition
            self.serialPort = serial.serial_for_url(deviceParams['port'], do_not_open=True,
                    baudrate=deviceParams['baud'], bytesize=deviceParams['byteSize'],
                    parity=deviceParams['parity'].upper(), stopbits=deviceParams['stopBits'])

            #  set flow control
            if deviceParams['flowControl'].upper() == 'RTSCTS':
                self.serialPort.rtscts = True
            elif deviceParams['flowControl'].upper() == 'DSRDTR':
                self.serialPort.dsrdtr = True
            elif deviceParams['flowControl'].upper() == 'SOFTWARE':
                self.serialPort.xonxoff = True

        except Exception as e:
            self.SerialError.emit(self.deviceName, SerialError('Unable to create serial port for ' +
                    self.deviceName + '. Invalid port option.', parent=e))


    @pyqtSlot()
    def startPolling(self):
        """
          Open the serial port and start the polling timers
        """

        #  check that we're not currently polling - assume if pollTimer is None
        #  we are not running.
        if (self.pollTimer is None):
            try:

                #  open the serial port
                self.serialPort.open()

                #  set RTS and DTR
                if (self.serialPort.rtscts == False):
                    self.serialPort.rts = self.rts
                if (self.serialPort.dsrdtr == False):
                    self.serialPort.dtr = self.dtr

                #  get the initial control pin states
                self.controlLines = [self.serialPort.cts, self.serialPort.dsr,
                                       self.serialPort.ri, self.serialPort.cd]

                #  create the timers we'll use to poll the serial port
                self.pollTimer = QTimer()
                self.pollTimer.timeout.connect(self.pollSerialPort)
                self.pollTimer.setInterval(self.pollInterval)
                self.txTimer = QTimer()
                self.txTimer.timeout.connect(self.txSerialPort)
                self.txTimer.setInterval(self.txInterval)

                # start polling
                self.pollTimer.start()
                self.txTimer.start()

            except Exception as e:
                self.SerialError.emit(self.deviceName, SerialError('Unable to open serial port for device ' +
                       self.deviceName + '.', parent=e))


    @pyqtSlot(list)
    def stopPolling(self, deviceList):
        """
          Stop the currently running thread which will also close the serial port and ultimately
          delete the thread we're running in.
        """

        #  check if this signal is for us
        if (self.deviceName not in deviceList):
            #  this is not the droid we're looking for
            return

        #  check that we're running
        if (self.pollTimer):

            self.pollTimer.timeout.disconnect()
            self.txTimer.timeout.disconnect()

            #  stop the polling timers
            self.pollTimer.stop()
            self.txTimer.stop()

            #  set their properties to None
            self.pollTimer = None
            self.txTimer = None

            #  flush the write buffer and close the serial port
            self.serialPort.flush()
            self.serialPort.close()

            #  emit the SerialPortClosed signal
            self.SerialPortClosed.emit(self.deviceName)
            
        else:
            #  if the poll timer is None, we aren't running so we immediately emit the closed signal
            self.SerialPortClosed.emit(self.deviceName)


    @pyqtSlot(str, bool)
    def setRTS(self, deviceName, state):
        """
          Set/Unset the RTS line on this serial port
        """
        if deviceName == self.deviceName:
            self.serialPort.setRTS(state)
            self.rts = state


    @pyqtSlot(str, bool)
    def setDTR(self, deviceName, state):
        """
          Set/Unset the DTR line on this serial port
        """
        if deviceName == self.deviceName:
            self.serialPort.setDTR(state)
            self.dtr = state


    @pyqtSlot(str)
    def getControlLines(self, deviceName):
        """
            Returns the state of the DCE control lines.
        """
        if deviceName == self.deviceName:
            self.DCEControlState.emit(self.deviceName, self.controlLines)


    @pyqtSlot(str, str)
    def write(self, deviceName, data):
        """
          Write data to the serial port. This method simply appends the data
          to the tx buffer list.  Data is written in the "run" method.
        """

        if deviceName == self.deviceName:
            self.txBuffer.append(data)


    def filterRAMSESChars(self, data):
        """
            replace control characters in RAMSES sensor data stream
        """

        controlChars = {'@e':'\x23', '@d':'\x40', '@f':'\x11', '@g':'\x13'}
        for i, j in controlChars.iteritems():
            data = data.replace(i, j)

        return data


    @pyqtSlot()
    def pollSerialPort(self):
        """
        This method polls the serial port and emits data when the specified
        parse method produces data. For the common parsing methods, data is
        emitted after a newline is received and the line of data has been
        parsed. For fixed length parsers, data is emitted after a specific
        number of bytes have been received and parsed.
        """

        #  check the state of the control lines - emit signal if changed
        if (self.controlLines[0] != self.serialPort.cts):
            self.controlLines[0] = self.serialPort.cts
            self.SerialControlChanged.emit(self.deviceName, 'CTS', self.controlLines[0])
        if (self.controlLines[1] != self.serialPort.dsr):
            self.controlLines[1] = self.serialPort.dsr
            self.SerialControlChanged.emit(self.deviceName, 'DSR', self.controlLines[1])
        if (self.controlLines[2] != self.serialPort.ri):
            self.controlLines[2] = self.serialPort.ri
            self.SerialControlChanged.emit(self.deviceName, 'RI', self.controlLines[2])
        if (self.controlLines[3] != self.serialPort.cd):
            self.controlLines[3] = self.serialPort.cd
            self.SerialControlChanged.emit(self.deviceName, 'CD', self.controlLines[3])

        #  check if we have any Rx business
        nBytesRx = self.serialPort.in_waiting
        if nBytesRx > 0:
            #  data available - read
            try:
                rxData = self.serialPort.read(nBytesRx).decode('utf-8')
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


    @pyqtSlot()
    def txSerialPort(self):
        """
        This method is called when the txTimer expires. It checks if there is any
        data to transmit in the buffer and transmits the data if so.

        This method takes a very simplistic approach to writing data that blocks
        while writing messages. It should be appropriate for most all application
        but may need to be modified if you're transmitting extremely large messages.

        """
        #  check if there is any data in the tx buffer
        nMessagesTx = len(self.txBuffer)

        #  there is, transmit one message
        if (nMessagesTx > 0):
            #  pop the message off the buffer and encode as utf-8
            txMessage = self.txBuffer.pop(0).encode('utf-8')

            #  determine the length of this message
            txBytes = len(txMessage)

            #  and write the full message to the device
            nBytes = 0
            while (nBytes < txBytes):
                nBytes += self.serialPort.write(txMessage)


#
#  SerialDevice Exception class
#
class SerialError(Exception):
    def __init__(self, msg, parent=None):
        self.errText = msg
        self.parent = parent

    def __str__(self):
        return repr(self.errText)
