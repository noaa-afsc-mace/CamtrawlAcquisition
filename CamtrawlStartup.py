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
import subprocess
import shlex
import logging
from logging.handlers import RotatingFileHandler
import yaml
from PyQt5 import QtCore
import CamtrawlController

#  THIS SCRIPT MUST BE RUN AS ROOT

class CamtrawlStartup(QtCore.QObject):

    #  define the names of the scripts used to 
    WIN_SYNC_SCRIPT = 'sync_time.bat'
    LINUX_SYNC_SCRIPT = 'sync_time.sh'
    LINUX_GUI_SCRIPT = 'start_desktop.sh'
    LINUX_CAMTRAWL_SCRIPT = 'run_camtrawl.sh'
    
    #  specify if the system is running headless. Headless systems
    #  do not have a screen and do not support remote desktop. Because
    #  of this, they will not start the desktop environment when in
    #  maintenance mode and the acquisition software is *always*
    #  started as a service from this script.
    HEADLESS = True
    
    #  specify the timeout for communicating with the controller
    #  If the controller doesn't respond within this time it is
    #  assumed that it is not working/available.
    CONTROLLER_TIMEOUT = 2000

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
        self.configuration['controller'] = {}
        self.configuration['system'] = {}

        self.configuration['controller']['use_controller'] = True
        self.configuration['controller']['serial_port'] = '/dev/ttySAC0'
        self.configuration['controller']['baud_rate'] = 921600

        self.configuration['system']['ntp_sync_clock_at_boot'] = False
        self.configuration['system']['ntp_sync_while_deployed'] = False
        self.configuration['system']['ntp_server_address'] = '192.168.0.99'
        self.configuration['system']['ntp_server_retries'] = 10
        self.configuration['system']['wifi_disable_while_deployed'] = False
        self.configuration['system']['start_desktop_while_deployed'] = False
        self.configuration['system']['scripts_path'] = '/camtrawl/scripts'
        self.configuration['system']['startup_log_path'] = '/camtrawl/startup_logs'

        #  continue the setup after QtCore.QCoreApplication.exec_() is called and
        #  the event loop is running.
        startTimer = QtCore.QTimer(self)
        startTimer.timeout.connect(self.StartStartup)
        startTimer.setSingleShot(True)
        startTimer.start(0)


    def StartStartup(self):
        '''StartStartup reads the configuration file then connects to the controller
        (if configured) and requests the system state. Startup then continues in
        FishishStartup one the system state is know. If the controller is not installed,
        or it does not respond, the system will boot into maintenance mode.
        '''

        #  read the configuration file - we start with the default values and
        #  recursively update them with values from the config file in the
        #  ReadConfig method.
        self.configuration = self.ReadConfig(self.config_file, self.configuration)

        #  get the scripts and logging paths
        self.scripts_path = os.path.normpath(self.configuration['system']['scripts_path']) + os.sep
        self.log_file = (os.path.normpath(self.configuration['system']['startup_log_path']) +
                os.sep + 'CamtrawlStartup.log')

        #  set up logging
        try:
            #  create the logger
            self.logger = logging.getLogger('AcquisitionStartup')
            self.logger.propagate = False
            self.logger.setLevel(self.configuration['application']['log_level'])
            fileHandler = RotatingFileHandler(self.log_file, mode='a', maxBytes=1*1024*1024, 
                    backupCount=1, encoding=None, delay=0)
            formatter = logging.Formatter('%(asctime)s : %(levelname)s - %(message)s')
            fileHandler.setFormatter(formatter)
            self.logger.addHandler(fileHandler)
        except:
            #  we failed to open the log file - try to keep going...
            print("CRITICAL ERROR: Unable to create log file " + self.log_file)
            self.logger = None

        #  if we're using the controller, start it
        if self.configuration['controller']['use_controller']:

            #  create an instance of CamtrawlController
            if self.logger:
                self.logger.info('Starting camtrawl controller - port: ' +
                        self.configuration['controller']['serial_port'] + '    baud: ' +
                        str(self.configuration['controller']['baud_rate']))
            self.controller = CamtrawlController.CamtrawlController(serial_port=
                    self.configuration['controller']['serial_port'], baud=
                    self.configuration['controller']['baud_rate'])

            #  connect the signals we care about
            self.controller.systemState.connect(self.ControllerStateChanged)
            self.controller.controllerStopped.connect(self.ControllerStopped)

            #  and start the controller object
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
            self.systemMode = 'maintenance'

            if self.logger:
                self.logger.info('Controller not installed. Starting the' +
                        'system in maintenance mode.')

            #  we don't need to wait for the controller so we just finish up our
            #  startup tasks.
            self.finishStartup()


    def finishStartup(self):
        '''
        finishSetup is called after we know the system state. Here we do the needed
        tasks based on the system state.
        '''

        if self.logger:
            self.logger.info("System is in " + self.systemMode + " mode")

        if self.systemMode == 'maintenance':
            #  the system is in maintenance/download mode

            if self.configuration['system']['ntp_sync_clock_at_boot']:
                #  sync the clock - we do  this via a script so we can control when
                #  the sync happens. This way we ensure that the sync is at least
                #  attempted before acquisition starts.
                self.syncClock()
            else:
                if self.logger:
                    self.logger.info('NTP clock sync is not enabled.')

            #  if this is a non-windows platform we will start the GUI desktop
            #  when in maintenance mode. If we're running on a headless system
            #  we will just start acquisition.
            if sys.platform != "win32":
                if not self.HEADLESS:
                    cmdString = self.scripts_path + self.LINUX_GUI_SCRIPT
                    if self.logger:
                        self.logger.info("Starting GUI desktop using command: " + cmdString)
                    subprocess.Popen([cmdString])
                    
                    #  When in maintenance mode, CamtrawlAcquistion is started by the
                    #  GUI desktop Startup Applications so nothing more needs to be
                    #  done here.

                else:
                    #  we're running headless, we start acquisition via systemd since
                    #  it will not be started by the desktop system (since there is
                    #  no desktop system.)
                    cmdString = 'systemctl start camtrawl_acquisition'
                    if self.logger:
                        self.logger.info('Starting acquistion using command: ' + cmdString)
                    commands = shlex.split(cmdString)
                    subprocess.run(commands)

        else:
            #  the system is in deployed mode

            #  check if we're syncing the clock while deployed
            if (self.configuration['system']['ntp_sync_while_deployed'] and
                    self.configuration['system']['ntp_sync_clock_at_boot']):
                #  sync the clock
                self.syncClock()
            else:
                if self.logger:
                    self.logger.info('NTP clock sync is not enabled when the system is deployed.')

            #  Check if we're disabling WiFi/Bluetooth
            if self.configuration['system']['wifi_disable_while_deployed']:
                self.disableWiFi()
            else:
                if self.logger:
                    self.logger.info('WiFi will not be disabled during deployment.')

            #  if this is a non-windows platform we will start acquisition here
            if sys.platform != "win32":
                cmdString = 'systemctl start camtrawl_acquisition'
                if self.logger:
                    self.logger.info('Starting acquistion using command: ' + cmdString)
                commands = shlex.split(cmdString)
                subprocess.run(commands)

        #  we're done here
        if self.logger:
            self.logger.info("CamtrawlStartup complete. Exiting.")
            logging.shutdown()
        QtCore.QCoreApplication.instance().quit()


    @QtCore.pyqtSlot()
    def ControllerStopped(self):
        '''
        ControllerStopped is called when the controller is done cleaning up.
        '''
        #  now that the controller has stopped, finish our startup tasks
        self.finishStartup()
        

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
            if self.logger:
                self.logger.info("Disabling WiFi using command: " + cmdString)
            commands = shlex.split(cmdString)
            subprocess.run(commands)


    def syncClock(self):
        '''
        syncClock calls the platform specific NTP clock sync script
        '''

        if sys.platform == "win32":
            cmdString = (self.scripts_path + self.WIN_SYNC_SCRIPT + ' ' +
                    self.configuration['system']['ntp_server_address'])
        else:
            cmdString = (self.scripts_path + self.LINUX_SYNC_SCRIPT + ' ' +
                    str(self.configuration['system']['ntp_server_retries']) +
                    ' ' + self.configuration['system']['ntp_server_address'])

        if self.logger:
            self.logger.info("Syncing clock with command: " + cmdString)
                
        #  run the time sync script
        commands = shlex.split(cmdString)
        result = subprocess.run(commands)
        if result.returncode == 0:
            if self.logger:
                self.logger.info("Clock synced to NTP server at " + 
                        self.configuration['system']['ntp_server_address'])
        else:
            if self.logger:
                self.logger.info("FAILED Clock sync! NTP server: " + 
                        self.configuration['system']['ntp_server_address'])


    @QtCore.pyqtSlot()
    def ControllerTimeout(self):
        '''
        ControllerTimeout is called when we're supposed to use the controller
        but it doesn't respond. When this happens we just assume the system is
        in maintenance/download mode.
        '''
        
        if self.logger:
            self.logger.info("Failed to connect to CamtrawlController. " +
                    "Assuming system is in maintenance mode.")
        
        #  if we can't connect to the controller - we assume we're in maintenance mode.
        self.systemMode = 'maintenance'

        #  stop the controller (probably isn't started but just in case...)
        self.controller.stopController()


    @QtCore.pyqtSlot(int)
    def ControllerStateChanged(self, new_state):
        '''
        the ControllerStateChanged slot is called when the Camtrawl controller emits
        a state change message. If we never connect, the timeout timer will expire.
        '''
        
        #  stop the timeout timer
        self.timeoutTimer.stop()

        #  check our state
        if (new_state == self.controller.AT_DEPTH):
            #  the system is at depth aka "deployed"
            self.systemMode = 'deployed'
        else:
            #  The system is in some other state
            self.systemMode = 'maintenance'

        #  stop the controller
        self.controller.stopController()


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



if __name__ == "__main__":

    #  set the default application config file path
    config_file = "./CamtrawlAcquisition.yml"

    #  parse the command line arguments
    parser = argparse.ArgumentParser(description='CamtrawlStartup')
    parser.add_argument("-c", "--config_file", help="Specify the path to the yml configuration file.")
    args = parser.parse_args()

    if (args.config_file):
        config_file = os.path.normpath(str(args.config_file))

    #  create an instance of QCoreApplication and and instance of the startup application
    app = QtCore.QCoreApplication(sys.argv)
    startup = CamtrawlStartup(config_file=config_file, parent=app)

    #  and start the event loop
    sys.exit(app.exec_())
