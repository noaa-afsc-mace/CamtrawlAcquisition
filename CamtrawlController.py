# coding=utf-8

#     National Oceanic and Atmospheric Administration (NOAA)
#     Alaskan Fisheries Science Center (AFSC)
#     Resource Assessment and Conservation Engineering (RACE)
#     Midwater Assessment and Conservation Engineering (MACE)

#  THIS SOFTWARE AND ITS DOCUMENTATION ARE CONSIDERED TO BE IN THE PUBLIC DOMAIN
#  AND THUS ARE AVAILABLE FOR UNRESTRICTED PUBLIC USE. THEY ARE FURNISHED "AS
#  IS."  THE AUTHORS, THE UNITED STATES GOVERNMENT, ITS INSTRUMENTALITIES,
#  OFFICERS, EMPLOYEES, AND AGENTS MAKE NO WARRANTY, EXPRESS OR IMPLIED,
#  AS TO THE USEFULNESS OF THE SOFTWARE AND DOCUMENTATION FOR ANY PURPOSE.
#  THEY ASSUME NO RESPONSIBILITY (1) FOR THE USE OF THE SOFTWARE AND
#  DOCUMENTATION; OR (2) TO PROVIDE TECHNICAL SUPPORT TO USERS.

"""
.. module:: CamtrawlAcquisition.CamtrawlController

    :synopsis: CamtrawlController provides a simple interface for
               interacting with the Camtrawl system power and IO
               board (aka the Camtrawl controller). The controller
               provides power control, sensor integration, and
               hardware camera and strobe triggers for the
               Camtrawl system.

| Developed by:  Rick Towler   <rick.towler@noaa.gov>
| National Oceanic and Atmospheric Administration (NOAA)
| National Marine Fisheries Service (NMFS)
| Alaska Fisheries Science Center (AFSC)
| Midwater Assesment and Conservation Engineering Group (MACE)
|
| Author:
|       Rick Towler   <rick.towler@noaa.gov>
| Maintained by:
|       Rick Towler   <rick.towler@noaa.gov>
"""

import datetime
import logging
from PyQt5 import QtCore
from SerialMonitor import SerialDevice


class CamtrawlController(QtCore.QObject):
    '''CamtrawlController provides a simple interface for interacting with
    the Camtrawl system controller. The Camtrawl system controller provides
    power management, sensor integration, and camera and strobe triggering
    for the Camtrawl platform.
    '''

    #  define CamtrawlController signals
    sensorData = QtCore.pyqtSignal(str, str, datetime.datetime, str)
    parameterData = QtCore.pyqtSignal(str, str, datetime.datetime, dict)
    systemState = QtCore.pyqtSignal(int)
    txSerialData = QtCore.pyqtSignal(str, str)
    error = QtCore.pyqtSignal(str,str)
    stopDevice = QtCore.pyqtSignal(list)
    controllerStopped = QtCore.pyqtSignal()


    #  define the controller states
    SLEEP = 0
    FORCED_ON = 1
    AT_DEPTH = 2
    PRESSURE_SW_CLOSED = 3
    FORCE_ON_REMOVED =4
    SHALLOW = 5
    PRESSURE_SW_OPENED = 6
    LOW_BATT = 7
    PC_ERROR = 8


    def __init__(self, serial_port='COM3', baud=115200, parent=None):

        super(CamtrawlController, self).__init__(parent)

        self.logger = logging.getLogger('Acquisition')
        self.isRunning = False

        #  set the serial port poroperties
        self.deviceParams = {'deviceName':'CTControl',
                             'port':str(serial_port),
                             'baud':int(baud),
                             'parseType':'None',
                             'parseExp':'',
                             'parseIndex':0,
                             'pollRate':500,
                             'txRate':250,
                             'initialState':(True,True),
                             'cmdPrompt':'',
                             'byteSize':8,
                             'parity':'N',
                             'stopBits':1,
                             'flowControl':'NONE',
                             'thread':None}


    def startController(self):
        """startController opens the serial connection to the controller
        """

        #  create a SerialDevice object
        self.serialDevice = SerialDevice.SerialDevice(self.deviceParams)

        #  connect the SerialDevice's signals
        self.serialDevice.SerialDataReceived.connect(self.sensorDataReceived)
        self.serialDevice.SerialError.connect(self.serialError)

        #  and connect our stop signal
        self.stopDevice.connect(self.serialDevice.stopPolling)
        self.txSerialData.connect(self.serialDevice.write)

        #  create a thread to run the serial device
        self.deviceParams['thread'] = QtCore.QThread(self)

        #  move the device to it
        self.serialDevice.moveToThread(self.deviceParams['thread'])

        #  connect thread specific signals and slots - this facilitates starting,
        #  stopping, and deletion of the thread.
        self.deviceParams['thread'].started.connect(self.serialDevice.startPolling)
        self.deviceParams['thread'].started.connect(self.controllerStarted)
        self.serialDevice.SerialPortClosed.connect(self.deviceParams['thread'].quit)
        self.deviceParams['thread'].finished.connect(self.threadFinished)
        self.deviceParams['thread'].finished.connect(self.deviceParams['thread'].deleteLater)

        self.logger.info("Starting CamtrawlController. Port: " + self.deviceParams['port'] +
                "   Baud: " + str(self.deviceParams['baud']))

        #  queue up a controller state request - this will not be sent until
        #  the port is opened and starts polling.
        self.getSystemState()

        #  and finally, start the thread - this will also start polling
        self.deviceParams['thread'].start()


    def controllerStarted(self):
        """
        controllerStarted is called after the serial thread starts
        """
        self.isRunning = True


    def stopController(self):
        """
          stopController emits the ``stopDevice`` signal which informs the
          SerialDevice thread to stop polling, flush and close the serial port, and
          terminate the thread.

        """
        self.logger.debug("Stopping CamtrawlController...")
        self.stopDevice.emit([self.deviceParams['deviceName']])


    def sendReadySignal(self):
        '''sendReadySignal sends the "System Ready" signal to the controller
        to indicate that the PC has booted and the acquisition software has
        successfully started.
        '''
        msg = "setPCState,1\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def sendShutdownSignal(self):
        '''sendShutdownSignal sends the controller the "shutdown" signal indicating
        that the PC is shutting down. The PC typically only signals the controller
        to shut down when there is an unrecoverable acquisition error.
        '''
        msg = "setPCState,254\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def sendShutdownAckSignal(self):
        '''sendShutdownAckSignal is sent after the shutdown signal is received from
        the controller and the PC is starting the shutdown process.
        '''
        msg = "setPCState,0\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def getSystemState(self):
        '''getSystemState requests the controllers current state.
        '''

        msg = "getState\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def setSystemState(self, state):
        '''setSystemState sets the controller's current state.
        '''
        msg = "setState," + str(state) + "\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def getStrobeMode(self):
        '''getStrobeMode requests the current strobe mode from the controller.
        '''
        msg = "getStrobeMode\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def setStrobeMode(self, mode):
        '''setStrobeMode is used to set the strobe mode.
        '''
        msg = "setStrobeMode," + str(mode) + "\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def setRTCParameters(self, installed, startDelay):

        if installed > 1:
            installed = 1;
        elif installed < 0:
            installed = 0

        msg = "setRTCPar," + str(installed) + "," + str(startDelay) + "\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def getRTCParameters(self):

        msg = "getRTCPar\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def getRTC(self):

        msg = "getRTC\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def setRTC(self, time=None):
        '''setRTC sets the controller's RTC to the time specified as a datetime
        object. If no object is passed, the current time is used.

        '''

        if time is None:
            time = datetime.datetime.now()

        msg = "setRTC," + time.strftime("%Y,%m,%d,%H,%M,%S") + "\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def setP2DParameters(self, enabled, slope, intercept, turnOnDepth, turnOffDepth):

        if enabled > 1:
            enabled = 1
        elif enabled < 0:
            enabled = 0

        turnOnDepth = round(turnOnDepth)
        turnOnDepth = round(turnOffDepth)

        msg = ("setP2DParms," + str(enabled) +
                "," + str(slope) + "," + str(intercept) + "," + str(turnOnDepth) + "," +
                str(turnOffDepth) + "\n")
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def getP2DParameters(self):

        msg = "getP2DParms\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def getStartupVoltage(self):

        msg = "getStartupVoltage\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def getShutdownVoltage(self):
        '''
        '''

        msg = "getShutdownVoltage\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def trigger(self, strobePreFire, strobe1Exp, strobe2Exp, chanOneTrig, chanTwoTrig):
        '''trigger sends the trigger command to the controller

        Args:
            strobePreFire (uint):
                The time, in microseconds, the strobes will be triggered before the cameras.
                This provides time for LED "strobes" to reach full brightness.
            strobe1Exp (uint):
                The time, in microseconds, of the strobe channel 1 trigger. MACE/AFSC LED
                strobes are on when the trigger signal is high and this value sets the strobe
                exposure time for strobe channel 1.
            strobe2Exp (uint):
                The time, in microseconds, of the strobe channel 2 trigger. MACE/AFSC LED
                strobes are on when the trigger signal is high and this value sets the strobe
                exposure time for strobe channel 2.
            chanOneTrig (bool):
                Set this argument to True to trigger camera 1.
            chanTwoTrig (bool):
                Set this argument to True to trigger camera 2

        Returns:
            None
        '''

        chanOneTrig = bool(chanOneTrig)
        if chanOneTrig:
            chanOneTrig = 1
        else:
            chanOneTrig = 0

        chanTwoTrig = bool(chanTwoTrig)
        if chanTwoTrig:
            chanTwoTrig = 1
        else:
            chanTwoTrig = 0

        strobePreFire = int(round(strobePreFire))
        strobe1Exp = int(round(strobe1Exp))
        strobe2Exp = int(round(strobe2Exp))

        msg = ("trigger," + str(strobePreFire) + "," + str(strobe1Exp) +
                "," + str(strobe2Exp) + "," + str(chanOneTrig) +
                "," + str(chanTwoTrig) + "\n")

        self.txSerialData.emit(self.deviceParams['deviceName'], msg)

        self.logger.debug("CamtrawlController sent: " + msg)


    def setThrusters(self, thrusterOneVal, thrusterTwoVal):
        '''setThrusters sends the setThrusters command to the controller. Some variants
        of the CamtrawlController are compiled with support for two thrusters and this
        command will set the commanded direction and speed of these thrusters. This
        command will be ignored if the controller is not compiled with thruster support
        
        The current implementation drives two BlueRobotics ESCs and values come directly
        from the Arduino Servo library and range from 1100-1900 with ~1500 being neutral
        or off. Values outside of this range will be clamped. Note that changes are not
        immediate as the controller throttles the change in speed to minimize load on
        the power system. The allowed rate of change is set in the controller firmware.
        Also note that for safety, the controller will stop the thrusters if another
        setThruster command is not received within a specified window. The current 
        timeout value is 2 seconds. This ensures that the thrusters will turn off if
        there is a software or comms issue and the system becomes unresponsive.
        
        Args:
            thrusterOneVal (int):
                The value that the ESC for thruster one will be set to. Valid values are
                between 1100-1900 with 1500 being off/neutral.
            thrusterTwoVal (int):
                The value that the ESC for thruster two will be set to. Valid values are
                between 1100-1900 with 1500 being off/neutral.

        Returns:
            None
        '''

        #  make sure we have ints
        thrusterOneVal = int(round(thrusterOneVal))
        thrusterTwoVal = int(round(thrusterTwoVal))

        msg = ("setThrusters," + str(thrusterOneVal) + "," + str(thrusterTwoVal) + "\n")

        self.txSerialData.emit(self.deviceParams['deviceName'], msg)

        self.logger.debug("CamtrawlController sent: " + msg)


    @QtCore.pyqtSlot(str, str, object)
    def sensorDataReceived(self, sensorID, data, err):
        '''The sensorDataReceived slot is called when serial data is available

        Args:
            sensorID (str):
                A string representing the sensor ID.
            data (str):
                A string containing the serial data message.
            err (object):
                DESCRIPTION

        Returns:
            None
        '''

        #  here we process the various datagrams received from the controller.
        rxTime = datetime.datetime.now()
        dataBits = data.split(',')
        header = dataBits[0]

        #  we process specific controller parameters and assume everything
        #  else is sensor data.

        if header == "getState":
            # Convert the state to an int and emit the systemState signal
            state = int(dataBits[1])
            self.systemState.emit(state)

        elif header.lower() == "getp2dparms":
            # getP2DParms,<mode as int>,<slope as float>,<intercept as float>,
            #       <turn on depth as int>,<turn off depth as int>,<P2D Lat as float>\n
            
            #  Due to a typo in the controller firmware, some controllers return 'getP2Dparms'
            #  and others 'getP2DParms'. The latter is what is expected so we match on the lower()
            #  text and patch this issue here.
            header = 'getP2DParms'
        
            #  create the default dict
            params = {'mode':-999,
                      'slope':-999,
                      'intercept':-999,
                      'turn_on_depth':-999,
                      'turn_off_depth':-999,
                      'p2d_latitude':-999
                     }

            #  try to populate with data
            try:
                params['mode'] = int(dataBits[1])
                params['slope'] = float(dataBits[2])
                params['intercept'] = float(dataBits[3])
                params['turn_on_depth'] = float(dataBits[4])
                params['turn_off_depth'] = float(dataBits[5])
                params['p2d_latitude'] = float(dataBits[6])
            except:
                pass

            #  emit the result
            self.parameterData.emit(sensorID, header, rxTime, params)

        elif header == "getStartupVoltage":
            # getStartupVoltage,<startup voltage threshold as float>\n

            #  create the default dict
            params = {'enabled':-999,
                      'startup_threshold':-999
                     }

            #  try to populate with data
            try:
                params['enabled'] = int(dataBits[1])
                params['startup_threshold'] = float(dataBits[2])
            except:
                pass

            #  emit the result
            self.parameterData.emit(sensorID, header, rxTime, params)

        elif header == "getShutdownVoltage":
            # getShutdownVoltage,<enabled as int>,<shutdown threshold as float>\n

            #  create the default dict
            params = {'enabled':-999,
                      'shutdown_threshold':-999
                     }

            #  try to populate with data
            try:
                params['enabled'] = int(dataBits[1])
                params['shutdown_threshold'] = float(dataBits[2])
            except:
                pass

            #  emit the result
            self.parameterData.emit(sensorID, header, rxTime, params)

        elif header == "getRTC":
            # getRTC,<year as int>,<month as int>,<day as int>,<hour as int>,
            #       <minute as int>,<second as int>\n

            #  create the default dict
            params = {'year':-999,
                      'month':-999,
                      'day':-999,
                      'hour':-999,
                      'minute':-999,
                      'second':-999
                     }

            #  try to populate with data
            try:
                params['year'] = int(dataBits[1])
                params['month'] = int(dataBits[2])
                params['day'] = int(dataBits[3])
                params['hour'] = int(dataBits[4])
                params['minute'] = int(dataBits[5])
                params['second'] = int(dataBits[6])
            except:
                pass

            #  emit the result
            self.parameterData.emit(sensorID, header, rxTime, params)

        elif header == "getStartDelay":
            # getStartDelay,<Startup Delay in Secs as int>\n

            #  create the default dict
            params = {'delay_seconds':-999}

            #  try to populate with data
            try:
                params['delay_seconds'] = int(dataBits[1])
            except:
                pass

            #  emit the result
            self.parameterData.emit(sensorID, header, rxTime, params)

        elif header == "getIMUCal":
            #getIMUCal,<accel_offset_x as int>,<accel_offset_y as int>,<accel_offset_z as int>,
            #          <gyro_offset_x as int>,<gyro_offset_y as int>,<gyro_offset_z as int>,
            #          <mag_offset_x as int>,<mag_offset_y as int>,<mag_offset_z as int>,
            #          <accel_radius as int>,<mag_radius as int>\n

            #  create the default dict
            params = {'accel_offset_x':-999,
                      'accel_offset_y':-999,
                      'accel_offset_z':-999,
                      'gyro_offset_x':-999,
                      'gyro_offset_y':-999,
                      'gyro_offset_z':-999,
                      'mag_offset_x':-999,
                      'mag__offset_y':-999,
                      'mag__offset_z':-999,
                      'accel_radius':-999,
                      'mag_radius':-999
                     }

            #  try to populate with data
            try:
                params['accel_offset_x'] = float(dataBits[1])
                params['accel_offset_y'] = float(dataBits[2])
                params['accel_offset_z'] = float(dataBits[3])
                params['gyro_offset_x'] = float(dataBits[4])
                params['gyro_offset_y'] = float(dataBits[5])
                params['gyro_offset_z'] = float(dataBits[6])
                params['mag_offset_x'] = float(dataBits[7])
                params['mag_offset_y'] = float(dataBits[8])
                params['mag_offset_z'] = float(dataBits[9])
                params['accel_radius'] = float(dataBits[10])
                params['mag_radius'] = float(dataBits[11])
            except:
                pass

            #  emit the result
            self.parameterData.emit(sensorID, header, rxTime, params)
        elif header == "getStrobeMode":
            # getStrobeMode,<mode as int>, <flash on start as int>\n
                        #  create the default dict
            params = {'mode':-999,
                      'flash_on_start':-999
                     }

            #  try to populate with data
            try:
                params['mode'] = int(dataBits[1])
                params['flash_on_start'] = int(dataBits[2])
            except:
                pass

            #  emit the result
            self.parameterData.emit(sensorID, header, rxTime, params)

        else:
            #  re-emit everything else
            self.sensorData.emit(sensorID, header, rxTime, data)


    @QtCore.pyqtSlot(str, object)
    def serialError(self, sensorID, errorObj):

        self.logger.error("CamtrawlControl serial error [" + self.deviceParams['port']
                + "," + str(self.deviceParams['baud']) + ']:' + str(errorObj.errText))
        self.logger.error("    " + str(errorObj.parent))

        #  re-emit the error signal
        self.error.emit('CamtrawlControl', str(errorObj.errText))


    @QtCore.pyqtSlot()
    def threadFinished(self):
        """
          threadFinished is called when the SerialDevice thread instance finishes
          running and emits the "finished" signal.
        """

        #  discard our reference to the thread
        self.deviceParams['thread'] = None
        self.isRunning = False
        self.controllerStopped.emit()

        self.logger.debug("CamtrawlController thread finished.")

