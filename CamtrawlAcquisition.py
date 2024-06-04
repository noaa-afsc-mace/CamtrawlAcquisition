#!/usr/bin/env python3
# coding=utf-8
#
#     National Oceanic and Atmospheric Administration (NOAA)
#     Alaskan Fisheries Science Center (AFSC)
#     Resource Assessment and Conservation Engineering (RACE)
#     Midwater Assessment and Conservation Engineering (MACE)
#
#  THIS SOFTWARE AND ITS DOCUMENTATION ARE CONSIDERED TO BE IN THE PUBLIC DOMAIN
#  AND THUS ARE AVAILABLE FOR UNRESTRICTED PUBLIC USE. THEY ARE FURNISHED "AS
#  IS."  THE AUTHORS, THE UNITED STATES GOVERNMENT, ITS INSTRUMENTALITIES,
#  OFFICERS, EMPLOYEES, AND AGENTS MAKE NO WARRANTY, EXPRESS OR IMPLIED,
#  AS TO THE USEFULNESS OF THE SOFTWARE AND DOCUMENTATION FOR ANY PURPOSE.
#  THEY ASSUME NO RESPONSIBILITY (1) FOR THE USE OF THE SOFTWARE AND
#  DOCUMENTATION; OR (2) TO PROVIDE TECHNICAL SUPPORT TO USERS.
#
"""
.. module:: CamtrawlAcquisition.CamtrawlAcquisition

    :synopsis: CamtrawlAcquisition is the main application that provides
               image acquisition, sensor logging, and control of the
               Camtrawl underwater stereo camera platform.

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


import os
import shutil
import datetime
from AcquisitionBase import AcquisitionBase
from PyQt5 import QtCore
import CamtrawlController


class CamtrawlAcquisition(AcquisitionBase):
    """
    CamtrawlAcquisition.py is the image acquisition application for the
    Camtrawl image acquisition platform. The application can be used to
    collect images and video from Flir machine vision cameras compatible
    with the Flir Spinnaker SDK. Experimental support for V4L2 cameras
    is in the works.

    If available, the application will connect to the Camtrawl power and
    control interface (aka the Camtrawl Controller) to log its sensor
    streams and trigger the cameras and strobes. If the controller is not
    available, the application will trigger cameras using software triggering.
    """

    def __init__(self, **kwargs):
        # call the parent class's init method, passing our args along
        super().__init__(**kwargs)

        # Define additional default properties
        self.controller = None
        self.controllerStarting = False
        self.HWTriggerHDR = {}
        self.controller_port = {}
        self.controllerCurrentState = 0
        self.readyToTrigger = {}

        #  Add default config values for the controller. We add the controller to the
        #  sensors section in AcquisitionSetup2 to ensure that it is ignored during sensor
        #  setup in AcquisitionSetup since we treat the controller differently than a
        #  standard sensor.
        self.configuration['controller'] = {}

        self.configuration['controller']['use_controller'] = False
        self.configuration['controller']['serial_port'] = 'COM3'
        self.configuration['controller']['baud_rate'] = 921600
        self.configuration['controller']['strobe_pre_fire'] = 150


    def AcquisitionSetup2(self):
        '''
        AcquisitionSetup2 completes setup by starting the controller,
        configuring the cameras and then starting acquisition. The application
        will exit here if there are any issues encountered during setup.
        '''

        #  if the free space is ok, configure the cameras
        if self.disk_ok:
            self.cam_ok = self.ConfigureCameras()
            if not self.cam_ok:
                #  we were unable to find any cameras
                self.logger.critical("CRITICAL ERROR: Unable to find any cameras. " +
                        "The application will exit.")
        else:
            self.cam_ok = False

        #  check if everything is ok with disk and cameras
        if self.cam_ok and self.disk_ok:

            #  start the server, if enabled
            if self.configuration['server']['start_server']:
                self.StartServer()

            #  camera and disk OK, check if we're using the controller
            if self.configuration['controller']['use_controller']:

                #  we are, add the camtrawl controller to the sensors config. Adding this here
                #  ensures that the controller is ignored when general sensors are configured
                #  in AcquisitionBase.AcquisitionSetup.
                self.configuration['sensors']['synchronous'].extend(['$OHPR'])
                self.configuration['sensors']['asynchronous'].extend(['$CTCS', '$SBCS', '$IMUC', '$CTSV'])
                self.configuration['sensors']['installed_sensors']['CTControl'] = {}
                self.configuration['sensors']['installed_sensors']['CTControl']['logging_interval_ms'] = None

                #  start the controller.
                self.StartController()

                #  acquisition may or may not be started in ControllerStateChanged based on the
                #  state of the CamtrawlController. Acquisition will start if it is in deployed
                #  or force-on mode, and it will not start if it is in download mode.

            else:
                #  if we're not using the controller we just start acquiring since
                #  we don't know the system state. Set the isAcquiring property
                self.isAcquiring = True

                #  start the trigger timer. Set a long initial interval
                #  to allow the cameras time to finish getting ready.
                self.isTriggering = True
                self.triggerTimer.start(1000)

        else:
            #  no, something didn't work out so check if we're supposed to shut down.
            #  If so, we will delay the shutdown to allow the user to exit the app
            #  before the PC shuts down and correct the problem.
            if self.configuration['application']['shut_down_on_exit']:
                self.logger.error("Shutdown on exit is set. The PC will shut down in 5 minutes.")
                self.logger.error("You can exit the application by pressing CTRL-C to " +
                        "circumvent the shutdown and keep the PC running.")

                #  set the shutdownOnExit attribute so we, er shutdown on exit
                self.shutdownOnExit = True

                #  complete setup of our shutdown timer and start it
                self.shutdownTimer.timeout.connect(self.AcqisitionTeardown)
                #  delay shutdown for 5 minutes
                self.shutdownTimer.start(5000 * 60)

            else:
                #  Stop acquisition and close the app
                self.StopAcquisition(exit_app=True, shutdown_on_exit=False)


    def StartController(self):
        '''
        StartController sets up and starts the CamtrawlController interface.
        CamtrawlController is an interface for the Camtrawl power and control
        board which provides power control, sensor integration, and camera and
        strobe triggering for the Camtrawl camera platform.
        '''
        self.logger.info("Connecting to Camtrawl controller on port: " +
                self.configuration['controller']['serial_port'] + " baud: " +
                str(self.configuration['controller']['baud_rate']))

        #  create an instance of CamtrawlController
        self.controller = CamtrawlController.CamtrawlController(serial_port=
                self.configuration['controller']['serial_port'], baud=
                self.configuration['controller']['baud_rate'])

        #  connect its signals
        self.controller.sensorData.connect(self.SensorDataAvailable)
        self.controller.parameterData.connect(self.ControllerParamData)
        self.controller.systemState.connect(self.ControllerStateChanged)
        self.controller.error.connect(self.ControllerError)
        self.controller.controllerStopped.connect(self.ControllerStopped)

        #  and start the controller object - we set the controllerStarting
        #  attribute so we know if we receive an error signal from the
        #  controller we know that the controller serial port could not be opened.
        self.controllerStarting = True
        self.controllerCurrentState = 0
        self.controller.startController()


    @QtCore.pyqtSlot(str, str, datetime.datetime, dict)
    def ControllerParamData(self, sensor_id, header, rx_time, data):
        '''
        ControllerParamData slot is called when the Camtrawl controller
        emits controller parameter messages. We are currently only requesting
        these at system startup and are only logging them for the record.
        '''

        if header == 'getP2DParms':
            if data['mode'] in [1,2]:
                self.logger.info("Pressure sensor is installed.")
                if data['mode'] == 1:
                    self.logger.info("    Type: PA4-LD")
                else:
                    self.logger.info("    Type: Analog")
                self.logger.info("    Depth conversion slope: %8.4f" % (data['slope']))
                self.logger.info("    Depth conversion offset: %8.4f" % (data['intercept']))
                self.logger.info("    System turn-on depth: %d" % (data['turn_on_depth']))
                self.logger.info("    System turn-off depth: %d" % (data['turn_off_depth']))
            else:
                self.logger.info("Pressure sensor is not installed.")

        elif header == 'getStartupVoltage':

            if data['enabled'] > 0:
                self.logger.info("System voltage monitoring enabled.")
                self.logger.info("    Startup voltage threshold: %8.4f" % (data['startup_threshold']))
            else:
                self.logger.info("System voltage monitoring disabled.")

        elif header == 'getShutdownVoltage':

            if data['enabled'] > 0:
                self.logger.info("    Shutdown voltage threshold: %8.4f" % (data['shutdown_threshold']))


    @QtCore.pyqtSlot(int)
    def ControllerStateChanged(self, new_state):
        '''
        the ControllerStateChanged slot is called when the Camtrawl controller emits
        a state change message. The Camtrawl controller operates in one of a number of
        states based on the sensor and logic inputs. When it changes state it will
        emit the systemState signal and this slot will act based on the controller
        state.

        Args:
            new_state (TYPE):
                DESCRIPTION

        Returns:
            None
        '''

        #  When the controller starts, it immediately sends a getState request.
        #  The response indicates that the controller started and is communicating
        #  so we can unset the controllerStarting state.
        if self.controllerStarting:
            self.controllerStarting = False

            #  next, tell the controller we're ready
            self.controller.sendReadySignal()

            #  and request a few controller parameters so they are logged for this
            #  acquisition session.
            self.controller.getP2DParameters()
            self.controller.getStartupVoltage()
            self.controller.getShutdownVoltage()

            #  finish the setup by checking the camera and disk states
            if self.cam_ok and self.disk_ok:
                #  the cameras are ready to acquire and we have some disk space.
                self.isAcquiring = True

            else:
                #  something went wrong during startup. The error has already been logged
                #  but we need to exit the app appropriately. If the system is in any other
                #  state than forced on, we shut it down.

                forcedOn = new_state == self.controller.FORCED_ON

                if not forcedOn or self.configuration['application']['shut_down_on_exit']:
                    #  ok, we're shutting down.

                    #  we don't want to get into a boot loop so we want to give the
                    #  user time to exit the app before the shutdown command is issued.
                    #  But, the delay is much shorter when the system isn't forced on.
                    if forcedOn:
                        delay = 1000 * 60 * 5
                        self.logger.critical("shut_down_on_exit is set in the config. " +
                                "The PC will shut down in 5 minutes.")
                    else:
                        delay = 1000 * 30
                        self.logger.critical("Since we are unable to collect data and we're" +
                                " at depth, the PC will shut down in 30 seconds.")
                    self.logger.critical("You can exit the application by pressing CTRL-C to " +
                            "circumvent the shutdown and keep the PC running.")

                    #  configure the shutdown timer to call a method that sends the controller
                    #  the shutdown command. The controller will respond with the new
                    #  state and the next shutdown tasks are handled below.
                    self.shutdownTimer.timeout.connect(self.DelayedShutdownHandler)
                    self.shutdownTimer.start(delay)

                else:
                    #  shut_down_on_exit is not set and the system is in maintenance mode
                    #  so we just exit without shutting down.

                    #  Stop acquisition and close the app
                    self.StopAcquisition(exit_app=True, shutdown_on_exit=False)

                #  since we have a problem, we don't have any further business here.
                return

        if self.controllerCurrentState == new_state:
            #  If the state hasn't changed we just return. This wouldn't normally happen
            return

        self.logger.info("Camtrawl controller state changed. New state is " +
                str(new_state))

        if ((new_state == self.controller.FORCED_ON) and not
                self.configuration['application']['always_trigger_at_start']):
            #  the system has been forced on and we're not being forced to start
            #  so we *do not* start triggering.

            self.logger.info("System operating in download mode.")

        elif ((new_state == self.controller.FORCED_ON) and
                self.configuration['application']['always_trigger_at_start']):
            #  the system has been forced on and we're configured to always
            #  trigger when starting so we start the trigger timer.

            self.logger.info("System operating in forced trigger mode - starting triggering...")
            self.internalTriggering = True
            self.isTriggering = True
            #  The first trigger interval is long to ensure the cameras are ready
            self.triggerTimer.start(500)

        elif new_state == self.controller.AT_DEPTH:
            #  the pressure sensor reports a depth >= the controller turn on depth
            #  We assume we're deployed at depth

            self.logger.info("System operating in deployed mode (@depth) - starting triggering...")
            self.internalTriggering = True
            self.isTriggering = True
            #  The first trigger interval is long to ensure the cameras are ready
            self.triggerTimer.start(500)

        elif new_state == self.controller.PRESSURE_SW_CLOSED:
            #  the "pressure switch" has closed - we assume we're deployed at depth

            self.logger.info("System operating in deployed mode (p-switch) - starting triggering...")
            self.internalTriggering = True
            self.isTriggering = True
            #  The first trigger interval is long to ensure the cameras are ready
            self.triggerTimer.start(500)

        elif new_state >= self.controller.FORCE_ON_REMOVED:
            #  The controller is in one of many shutdown states

            #  Stop the shutdownTimer in the rare case it is running and the system
            #  entered into a new shutdown state.
            self.shutdownTimer.stop()

            #  branch on the type to report why we're shutting down then shut down.
            if new_state == self.controller.FORCE_ON_REMOVED:
                self.logger.info("The system is shutting down because the force on plug has been pulled.")
            elif new_state == self.controller.SHALLOW:
                self.logger.info("The system is shutting down because the system has reached the turn-off depth.")
            elif new_state == self.controller.PRESSURE_SW_OPENED:
                self.logger.info("The system is shutting down because the pressure switch has opened.")
            elif new_state == self.controller.LOW_BATT:
                self.logger.info("The system is shutting down due to low battery.")
            elif new_state == self.controller.PC_ERROR:
                self.logger.info("The system is shutting down due to an acquisition software error.")

            #  The controller is telling us to shut down.
            self.logger.info("Initiating a normal shutdown...")

            #  ACK the controller so it knows we're shutting down
            self.controller.sendShutdownAckSignal()

            #  start the shutdown process by calling StopAcquisition. We set the
            #  exit_app keyword to True to exit the app after the cameras have
            #  stopped. We also force the shutdown_on_exit keyword to True since
            #  the controller will cut power to the PC after a minute or so
            #  when in a shutdown state.
            self.StopAcquisition(exit_app=True, shutdown_on_exit=True)

        #  lastly, we update our tracking of the state
        self.controllerCurrentState = new_state


    @QtCore.pyqtSlot()
    def DelayedShutdownHandler(self):
        '''
        DelayedShutdownHandler is called after the shutdown delay timer expires.
        We delay certain shutdown scenarios to try to eliminate boot loops where
        the system cannot run and shuts down and the user doesn't have time to
        intervene and stop it.
        '''

        #  Send the shutdown signal to the controller. This will cause the
        #  controller state to change and it will emit the StateChanged signal.
        #  The state will be a shutdown state, and the ControllerStateChanged
        #  method will handle the rest of the shutdown initiation.
        self.logger.debug("Sending shutdown signal to the controller")
        self.controller.sendShutdownSignal()


    @QtCore.pyqtSlot()
    def CheckDiskFreeSpace(self):
        '''
        CheckDiskFreeSpace checks the available free space for the data directory and
        stops acquisition if it drops below the min threshold.
        '''

        #  get the disk free space in MB
        disk_stats = shutil.disk_usage(self.image_dir)
        disk_free_mb = disk_stats.free / 1024 / 1024

        if disk_free_mb <= self.configuration['application']['disk_free_min_mb']:

            #  stop the timer
            self.diskStatTimer.stop()

            #  Log that we're stopping because we're out of disk space
            self.logger.critical("The system is stopping because the data disk is full.")
            self.logger.critical("  Free space: %d MB is less than the " % (disk_free_mb) +
                    "minimum allowed %d MB" % (self.configuration['application']['disk_free_min_mb']))

            #  if we're using the controller, we don't stop, but signal the controller
            #  we want to stop.
            if self.configuration['controller']['use_controller']:
                #  If we're using the controller, we send the PC ERROR signal which
                #  will result in the controller sending a shutdown command to the
                #  application.
                self.logger.debug("Sending PC Error signal to the controller...")
                self.controller.sendShutdownSignal()
            else:
                #  Stop acquisition and close the app
                self.StopAcquisition(exit_app=True,
                        shutdown_on_exit=self.configuration['application']['shut_down_on_exit'])


    @QtCore.pyqtSlot(str, str)
    def ControllerError(self, device_name, error):

        if self.controllerStarting:
            #  If there is an error when controllerStarting is set, we know that the
            #  issue is related to opening the serial port and we will assume we
            #  will not be able to use the controller. If we're told to use the
            #  controller and we can't we consider this a fatal error and bail.
            self.logger.critical("Unable to connect to the Camtrawl controller @ port: "+
                self.configuration['controller']['serial_port'] + " baud: " +
                str(self.configuration['controller']['baud_rate']))
            self.logger.critical("    ERROR: " + error)
            #TODO: Need to clean up this exit path - there is still a thread
            #      running when we exit here
            self.StopAcquisition(exit_app=True)
            return

        #  log the serial error. Normally this will never get called.
        self.logger.error("Camtrawl Controller Serial error: " + error)


    def ConfigureCameras(self):
        """
        ConfigureCameras runs through the cameras visible to Spinnaker and configures
        cameras according to the settings in the camera section of the configuration file.
        """

        # call the base class's ConfigureCameras method
        ok = super().ConfigureCameras()

        #  initialize some properties specific to CamtrawlAcquisition
        self.controller_port = {}

        # now we work through our configured cameras and set up some Camtrawl
        # controller specific bits.
        for cam_name in self.cameras:

            #  get a reference to our spinCamera object
            sc = self.cameras[cam_name]

            # get the configuration for this camera.
            _, config = self.GetCameraConfiguration(sc.camera_name)

            # The Camtrawl controller has two camera trigger ports, 0 and 1.
            # You must specify the controller port each camera is connected to
            # to ensure they are triggered correctly. This dict allows us
            # to map the individual camera objects to their controller ports.
            self.controller_port[sc] = config['controller_trigger_port']

            # Here we connect the camera's triggerReady signal to this class's
            # HWTriggerReady slot. This signal informs the app when a
            # camera is ready to trigger and when all cameras are ready, the
            # app tells the controller to hardware trigger the cameras.
            if self.configuration['controller']['use_controller']:
                if sc in self.hw_triggered_cameras:
                    sc.triggerReady.connect(self.HWTriggerReady)

        return ok


    def AcqisitionTeardown(self):
        """
        AcqisitionTeardown is called when the application is shutting down.
        The cameras will have already been told to stop acquiring
        """

        #  stop the controller (if started)
        if self.controller:
            self.logger.info("Stopping the CamtrawlController...")
            self.controller.stopController()

            #  we wait for the cotroller thread to exit then pick up teardown
            #  in the ControllerStopped slot. This allows the controller thread to
            #  exit cleanly before continuing.

        else:
            #  we're not using the controller so we can jump right into the parent
            #  class's teardown method.

            # call the base class's AcqisitionTeardown method
            super().AcqisitionTeardown()

            #  clean up some CamtrawlAcquisition specific objects
            self.readyToTrigger = {}
            self.HWTriggerHDR = {}
            self.controller_port = {}


    @QtCore.pyqtSlot()
    def ControllerStopped(self):
        '''The ControllerStopped slot is called when the CamtrawlController's thread
        finishes running. During teardown we stop the controller, wait for it to signal
        it is stopped, then continue with teardown.
        '''

        self.logger.info("CamtrawlController stopped.")

        # call the base class's AcqisitionTeardown method
        super().AcqisitionTeardown()

        #  clean up some CamtrawlAcquisition specific objects
        self.readyToTrigger = {}
        self.HWTriggerHDR = {}
        self.controller_port = {}


    @QtCore.pyqtSlot(str, str, str)
    def SetParameterRequest(self, module, parameter, value):
        '''SetParameterRequest is overridden from AcquisitionBase and handles Camtrawl
        controller specific parameter requests. "acquisition" requests will be
        passed onto the parent method.
        '''

        #  split the parameter path
        params = parameter.split('/')
        if params[0] == '':
            #  no parameter provided
            return

        if module.lower() == 'controller':


            #  set_thrusters is deprecated - thruster control has moved to a separate
            #  "sensor" and is now controlled by sending parameters to the "sensors"
            #  module. This code is here to provide a simple template for adding
            #  the handling of setting controller params via the server if this is
            #  something that is needed in the future.

#            if params[0].lower() == 'set_thrusters':
#                #  the set_thrusters value argument should be a string of two
#                #  integers in the form thrusterOneVal, thrusterTwoVal. For example:
#                #  value = "1500,1500"
#                try:
#                    vals = value.split(',')
#                    self.controller.setThrusters(int(vals[0]), int(vals[1]))
#                except:
#                    #  for now we silently fail if there is an error parsing the value
#                    pass

            pass

        else:
            #  this message is not specific to Camtrawl so call the parent method
            super().SetParameterRequest(module, parameter, value)


    @QtCore.pyqtSlot()
    def TriggerCameras(self):
        '''
        The TriggerCameras slot is called by the trigger timer and will "trigger"
        the active cameras. This action doesn't directly trigger the cameras. It
        prepares them for triggering but the actual trigger depends on if a camera
        is being hardware or software triggered.

        If a camera is software triggered, it will prepare for and trigger itself
        when this signal is received.

        If a camera is hardware triggered, it will prepare for triggering and then
        emit the "TriggerReady" signal. That signal is connected to this application's
        HWTriggerReady slot which will track the ready state of all hardware triggered
        cameras and when they are all ready, it will call the hardware trigger device's
        trigger method.
        '''

        #  if any cameras are hardware triggered we have to track some other info
        if self.hwTriggered:
            #  reset the image received state for hardware triggered cameras
            #  The CamtrawlController v2 has 2 trigger ports
            self.ctcTriggerChannel = [False] * 2
            self.maxExposure = 0
            for c in self.hw_triggered_cameras:
                self.readyToTrigger[c] = False
                self.HWTriggerHDR[c] = False

        # call the base class's TriggerCameras method
        super().TriggerCameras()


    @QtCore.pyqtSlot(object, int, bool)
    def HWTriggerReady(self, cam, exposure_us, is_HDR):
        '''
        The HWTriggerReady slot is called by each hardware triggered camera when it
        is ready to be triggered. We track the responses and when all cameras are
        ready, we call the CamtrawlController's trigger method which will trigger
        the strobes and the cameras.
        '''

        #  for debugging, indicate that this camera is ready
        self.logger.debug(cam.camera_name + ":  Ready to hardware trigger")

        #  update some state info for this camera
        self.readyToTrigger[cam] = True
        self.HWTriggerHDR[cam] = is_HDR

        #  if this camera is set to trigger the exposure will be greater than zero.
        if exposure_us > 0:
            #  update the list that tracks which cameras should be triggered
            #  The controller port numbering starts at 1 so we have to subtract
            #  one when indexing the list.
            self.ctcTriggerChannel[self.controller_port[cam] - 1] = True
        else:
            #  If this camera is not going to be triggered, we set self.received
            #  for this camera to True so we don't wait for it.
            self.received[cam.camera_name] = True

        #  track the longest camera exposure - this ends up being our strobe exposure
        if self.maxExposure < exposure_us:
            self.maxExposure = exposure_us

        #  if all of the HW triggered cameras are ready, we trigger them
        if all(self.readyToTrigger.values()):

            #  strobe pre-fire is the time, in microseconds, that the strobe
            #  trigger signal goes high before the cameras are triggered. This
            #  allows LED strobes to ramp a bit before exposure. Since we want
            #  the total HDR exposure time as short as possible, we disable
            #  strobe pre fire for HDR exposures

            #  disable strobe pre-fire for HDR exposures 2,3 and 4
            if any(self.HWTriggerHDR.values()):
                strobePreFire = 0
            else:
                #  not an HDR trigger so we use the configured pre-fire
                strobePreFire = self.configuration['controller']['strobe_pre_fire']

            #  set the strobe exposures to the longest hardware triggered exposure
            strobe_chan = self.configuration['controller']['strobe_channel']
            if strobe_chan == 1:
                #  only trigger strobe channel 1
                strobe1Exp = self.maxExposure
                #  strobe channel 2 exposure is set to 0 to disable
                strobe2Exp = 0
            elif strobe_chan == 2:
                #  only trigger strobe channel 2
                #  strobe channel 1 exposure is set to 0 to disable
                strobe1Exp = 0
                strobe2Exp = self.maxExposure
            else:
                #  trigger both strobe channels
                strobe1Exp = self.maxExposure
                strobe2Exp = self.maxExposure

            #  call the camtrawl controller's trigger method to trigger the
            #  cameras and strobes.
            self.controller.trigger(strobePreFire, strobe1Exp, strobe2Exp,
                    self.ctcTriggerChannel[0], self.ctcTriggerChannel[1])


def exitHandler(a,b=None):
    '''
    exitHandler is called when CTRL-c is pressed on Windows
    '''
    global ctrlc_pressed

    if not ctrlc_pressed:
        #  make sure we only act on the first ctrl-c press
        ctrlc_pressed = True
        print("CTRL-C detected. Shutting down...")
        acquisition.StopAcquisition(exit_app=True)

    return True


def signal_handler(signum, frame):
    '''
    signal_handler is called when ctrl-c is pressed when the python console
    has focus. On Linux this is also called when the terminal window is closed
    or when the Python process gets the SIGTERM signal.
    '''
    global ctrlc_pressed

    if not ctrlc_pressed:
        #  make sure we only act on the first ctrl-c press
        ctrlc_pressed = True
        if signum != signal.SIGHUP:
            print("CTRL-C or SIGTERM detected. Shutting down...")
        acquisition.StopAcquisition(exit_app=True)

    return True


if __name__ == "__main__":
    import sys
    import argparse

    #  create a state variable to track if the user typed ctrl-c to exit
    ctrlc_pressed = False

    #  Set up the handlers to trap ctrl-c
    if sys.platform == "win32":
        #  On Windows, we use win32api.SetConsoleCtrlHandler to catch ctrl-c
        import win32api
        win32api.SetConsoleCtrlHandler(exitHandler, True)
    else:
        #  On linux we can use signal to get not only ctrl-c, but
        #  termination and hangup signals also.
        import signal
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGHUP, signal_handler)

    #  set the default application config file path
    config_file = "./CamtrawlAcquisition.yml"
    profiles_file = './VideoProfiles.yml'

    #  parse the command line arguments
    parser = argparse.ArgumentParser(description='CamtrawlAcquisition')
    parser.add_argument("-c", "--config_file", help="Specify the path to the yml configuration file.")
    parser.add_argument("-p", "--profiles_file", help="Specify the path to the yml video profiles definition file.")
    args = parser.parse_args()

    if (args.config_file):
        config_file = os.path.normpath(str(args.config_file))
    if (args.profiles_file):
        profiles_file = os.path.normpath(str(args.profiles_file))

    #  create an instance of QCoreApplication and and instance of the acquisition application
    app = QtCore.QCoreApplication(sys.argv)
    acquisition = CamtrawlAcquisition(config_file=config_file, profiles_file=profiles_file,
            parent=app)

    #  and start the event loop
    sys.exit(app.exec_())


