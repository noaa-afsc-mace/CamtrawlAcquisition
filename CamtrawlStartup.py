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
.. module:: CamtrawlAcquisition.CamtrawlStartup

    :synopsis: Script that is run when a camtrawl system boots to
               start and stop various components based on the system
               state.

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
import sys
import argparse
import collections
import yaml
from PyQt5 import QtCore
import CamtrawlController


class CamtrawlStartup(QtCore.QObject):

    WIN_SYNC_SCRIPT = 'C:\\camtrawl\\scripts\\sync_time.bat'
    LINUX_SYNC_SCRIPT = '/camtrawl/scripts/sync_time.sh'
    CONTROLLER_TIMEOUT = 1500

    def __init__(self, config_file=None, parent=None):

        super(CamtrawlStartup, self).__init__(parent)

        #  Set the configuration file path if provided
        if config_file:
            self.config_file = config_file
        else:
            self.config_file = './CamtrawlAcquisition.yml'
        self.config_file = os.path.normpath(self.config_file)

        # Define default properties
        self.systemMode = 'maintenance'

        #  create the default configuration dict. These values are used for application
        #  configuration if they are not provided in the config file.
        self.configuration = {}
        self.configuration['application'] = {}
        self.configuration['controller'] = {}
        self.configuration['system'] = {}

        self.configuration['application']['log_level'] = 'INFO'

        self.configuration['controller']['use_controller'] = True
        self.configuration['controller']['serial_port'] = '/dev/ttySAC0'
        self.configuration['controller']['baud_rate'] = 921600

        self.configuration['system']['ntp_sync_clock_at_boot'] = False
        self.configuration['system']['ntp_sync_while_deployed'] = False
        self.configuration['system']['ntp_server_address'] = '192.168.0.99'

        self.configuration['system']['wifi_disable_while_deployed'] = False

        #  continue the setup after QtCore.QCoreApplication.exec_() is called
        #  by using a timer to call StartSetup. This ensures that the
        #  application event loop is running as we continue setup.
        startTimer = QtCore.QTimer(self)
        startTimer.timeout.connect(self.StartSetup)
        startTimer.setSingleShot(True)
        startTimer.start(0)


    def StartSetup(self):
        '''StartSetup reads the configuration file then connects to the controller
        (if configured), requests the system state, and then sets up the computer
        based on the state.
        '''

        #  read the configuration file - we start with the default values and
        #  recursively update them with values from the config file in the
        #  ReadConfig method.
        self.configuration = self.ReadConfig(self.config_file, self.configuration)

        #  if we're using the controller, start it
        if self.configuration['controller']['use_controller']:

            #  create an instance of CamtrawlController
            self.controller = CamtrawlController.CamtrawlController(serial_port=
                    self.configuration['controller']['serial_port'], baud=
                    self.configuration['controller']['baud_rate'])

            #  connect the signals we care about
            self.controller.systemState.connect(self.ControllerStateChanged)
            self.controller.controllerStopped.connect(self.ControllerStopped)

            #  and start the controller object - we set the controllerStarting
            #  attribute so we know if we receive an error signal from the
            #  controller we know that the controller serial port could not be opened.
            self.controllerStarting = True
            self.controllerCurrentState = 0
            self.controller.startController()

            #  create the controller timeout timer
            self.timeoutTimer = QtCore.QTimer(self)
            self.timeoutTimer.setInterval(self.CONTROLLER_TIMEOUT)
            self.timeoutTimer.timeout.connect(self.ControllerTimeout)
            self.timeoutTimer.setSingleShot(True)

        else:
            #  if we're not using the controller, we just start the pc as if
            #  we were in maintenance mode.
            self.controller = None
            self.systemMode = 'maintenance'

            #  we don't need to wait for the
            self.finishSetup()


    def finishSetup(self):
        '''
        finishSetup is called after we know the system state. Here we do the needed
        tasks based on the system state.
        '''

        print("System is in " + self.systemMode + " mode")

        if self.systemMode == 'maintenance':
            #  the system is in maintenance/download mode

            if self.configuration['system']['ntp_sync_clock_at_boot']:
                #  sync the clock
                self.syncClock()

                #  depending on how the OS is configured, we can, for example
                #  boot into console mode and start the desktop here.

        else:
            #  the system is in deployed mode

            if (self.configuration['system']['ntp_sync_while_deployed'] and
                    self.configuration['system']['ntp_sync_clock_at_boot']):
                #  sync the clock
                self.syncClock()

            #  This is where we would disable WiFi
            if self.configuration['system']['wifi_disable_while_deployed']:
                self.disableWiFi()

        #  Currently the linux image always boots into desktop mode. When it
        #  boots into desktop mode CamtrawlAcquisition is started by the desktop
        #  system (gnome or whatever it is) so we don't have to start it here.
        #  If we change that so the system boots into console mode, we would need
        #  to start acquisition here.

        #  we're done here
        self.exitStartup()


    @QtCore.pyqtSlot()
    def ControllerStopped(self):
        '''
        ControllerStopped is called when the controller is done cleaning up.
        Here we just exit after it is done.
        '''
        QtCore.QCoreApplication.instance().quit()


    def disableWiFi(self):
        '''
        disableWiFi uses rfkill to shut down all radios. This means WiFi and Bluetooth.
        This is not supported on windows and does nothing.
        '''

        if sys.platform == "win32":
            cmdString = None
        else:
            cmdString = 'rfkill block all'

        #  execute rfkill
        if (cmdString):
            os.system(cmdString)


    def syncClock(self):
        '''
        syncClock calls the platform specific NTP clock sync script
        '''

        if sys.platform == "win32":
            cmdString = (self.WIN_SYNC_SCRIPT + ' ' +
                    self.configuration['system']['ntp_server_address'])
        else:
            #  when this is changed to use subprocess remove the '&' argument
            cmdString = (self.LINUX_SYNC_SCRIPT + ' ' +
                        self.configuration['system']['ntp_server_address'] + ' &')

        # TODO: This needs to be changed to use subprocess.popen instead of os.system
        #       so windows sync will not block.

        #  run the time sync script
        os.system(cmdString)


    @QtCore.pyqtSlot()
    def ControllerTimeout(self):
        '''
        ControllerTimeout is called when we're supposed to use the controller
        but it doesn't respond. When this happens we just assume the system is
        in maintenance/download mode.
        '''

        self.systemMode = 'maintenance'

        #  finish what we need to do
        self.finishSetup()


    @QtCore.pyqtSlot(int)
    def ControllerStateChanged(self, new_state):
        '''
        the ControllerStateChanged slot is called when the Camtrawl controller emits
        a state change message. If we never connect, the timeout timer will expire.
        '''

        #  For this script we only care about the initial state
        if self.controllerStarting:
            self.controllerStarting = False

            #  stop the timeout timer
            self.timeoutTimer.stop()

            #  check our state
            if (new_state == self.controller.AT_DEPTH):
                #  the system is at depth aka "deployed"
                self.systemMode = 'deployed'
            else:
                #  The system is in some other state
                self.systemMode = 'maintenance'

            #  finish what we need to do
            self.finishSetup()


    def exitStartup(self):
        '''

        '''
        #  stop the controller
        if self.controller:
            self.controller.stopController()
        else:
            #  we're done
            QtCore.QCoreApplication.instance().quit()


    def ReadConfig(self, config_file, config_dict):
        '''ReadConfig reads the yaml configuration file and returns the updated
        configuration dictionary.
        '''

        #  read the configuration file
        with open(config_file, 'r') as cf_file:
            try:
                config = yaml.safe_load(cf_file)
            except:
                pass

        # Update/extend the configuration values and return
        return self.__update(config_dict, config)


    def __update(self, d, u):
            """
            Update a nested dictionary or similar mapping.

            Source: https://stackoverflow.com/questions/3232943/update-value-of-a-nested-dictionary-of-varying-depth
            Credit: Alex Martelli / Alex Telon
            """
            for k, v in u.items():
                if isinstance(v, collections.abc.Mapping):
                    #  if a value is None, just assign the value, otherwise keep going
                    if d.get(k, {}) is None:
                        d[k] = v
                    else:
                        d[k] = self.__update(d.get(k, {}), v)
                else:
                    d[k] = v
            return d


def exitHandler(a,b=None):
    '''
    exitHandler is called when CTRL-c is pressed on Windows
    '''
    global ctrlc_pressed

    if not ctrlc_pressed:
        #  make sure we only act on the first ctrl-c press
        ctrlc_pressed = True
        print("CTRL-C detected. Shutting down...")
        acquisition.exitStartup()

    return True


def signal_handler(*args):
    '''
    signal_handler is called when ctrl-c is pressed when the python console
    has focus. On Linux this is also called when the terminal window is closed
    or when the Python process gets the SIGTERM signal.
    '''
    global ctrlc_pressed

    if not ctrlc_pressed:
        #  make sure we only act on the first ctrl-c press
        ctrlc_pressed = True
        print("CTRL-C or SIGTERM/SIGHUP detected. Shutting down...")
        acquisition.exitStartup()

    return True


if __name__ == "__main__":

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

    #  parse the command line arguments
    parser = argparse.ArgumentParser(description='CamtrawlStartup')
    parser.add_argument("-c", "--config_file", help="Specify the path to the yml configuration file.")
    args = parser.parse_args()

    if (args.config_file):
        config_file = os.path.normpath(str(args.config_file))

    #  create an instance of QCoreApplication and and instance of the acquisition application
    app = QtCore.QCoreApplication(sys.argv)
    acquisition = CamtrawlStartup(config_file=config_file, parent=app)

    #  and start the event loop
    sys.exit(app.exec_())
