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
.. module:: CamtrawlAcquisition.AcquisitionBase

    :synopsis: Base class for the image acquisition software for the
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
import sys
import glob
import datetime
import logging
import functools
import importlib
import platform
import subprocess
import collections
import shutil
#  import order seems to matter on linux. QtCore and QtSql (in metadata_db)
#  have to be imported before (I think) cv2. If not you get a weird error
#  loading a shared library when importing them.
from PyQt5 import QtCore
from pathlib import Path
from metadata_db import metadata_db
import google.protobuf
import yaml
import numpy as np
import cv2
from SerialMonitor import SerialMonitor
from CamtrawlServer import CamtrawlServer


class AcquisitionBase(QtCore.QObject):

    #  specify the application version
    VERSION = '4.3'

    # CAMERA_CONFIG_OPTIONS defines the default camera configuration options.
    # These values are used if not specified in the configuration file.
    CAMERA_CONFIG_OPTIONS = {'exposure_us':4000,
                             'gain':18,
                             'driver': 'SpinCamera',
                             'label':'Camera',
                             'rotation':'none',
                             'trigger_divider': 1,
                             'sensor_binning': 1,
                             'trigger_source': 'Software',
                             'controller_trigger_port': 1,
                             'hdr_enabled':False,
                             'hdr_save_merged':False,
                             'hdr_signal_merged':False,
                             'hdr_merge_method':'mertens',
                             'hdr_save_format': 'hdr',
                             'hdr_settings':None,
                             'hdr_response_file': None,
                             'hdr_tonemap_saturation': 1.0,
                             'hdr_tonemap_bias': 0.85,
                             'hdr_tonemap_gamma': 2.0,
                             'save_stills': True,
                             'still_image_extension': '.jpg',
                             'still_image_divider': 1,
                             'jpeg_quality': 90,
                             'image_scale': 100,
                             'save_video': False,
                             'video_preset': 'default',
                             'video_force_framerate': -1,
                             'video_frame_divider': 1,
                             'video_scale': 100}

    #  DEFAULT_VIDEO_PROFILE defines the default options for the 'default' video profile.
    DEFAULT_VIDEO_PROFILE = {'encoder':'libx265',
                              'file_ext':'.mp4',
                              'preset':'fast',
                              'crf':23,
                              'pixel_format':'yuv420p',
                              'max_frames_per_file': 5000,
                              'ffmpeg_debug_out': False}

    #  VALID_DRIVERS contains the names of camera drivers available to the system.
    #  If a camera specifies a different driver than one listed here, it will be
    #  ignored. Driver names should be entered in lower case.
    VALID_DRIVERS = ['spincamera', 'cv2videocapture']

    #  specify the maximum number of times the application will attempt to open a
    #  metadata db file when running in combined mode and the original db file
    #  cannot be opened.
    MAX_DB_ALTERNATES = 100

    #  specify how long to wait (in ms) after triggering to force another trigger.
    #  This should be at a minimum 2x your exposure + data transfer time.
    ACQUISITION_TIMEOUT = 1000

    #  specify how many times we will timeout waiting for threads to finish when
    #  shutting down the application.
    TEARDOWN_TRIES = 12

    #  specify the maximum number of files allowed in the calibration folder. If
    #  more files exist, the copy is skipped. The calibration folder should only have
    #  one or a few calibration files and this is a simple sanity check to prevent
    #  gross misconfigurations filling up the disk copying the wrong directory.
    MAX_CAL_FOLDER_FILES = 10

    #  define PyQt Signals
    sensorData = QtCore.pyqtSignal(str, str, datetime.datetime, str)
    stopAcquiring = QtCore.pyqtSignal(list)
    startAcquiring = QtCore.pyqtSignal((list, str, bool, dict, bool, dict))
    trigger = QtCore.pyqtSignal(list, int, datetime.datetime, bool, bool)
    stopServer = QtCore.pyqtSignal()
    parameterChanged = QtCore.pyqtSignal(str, str, str, bool, str)
    stopApp = QtCore.pyqtSignal(bool)

    def __init__(self, config_file=None, profiles_file=None, parent=None):

        super(AcquisitionBase, self).__init__(parent)

        #  Set the configuration file path if provided
        if config_file:
            self.config_file = config_file
        else:
            self.config_file = './CamtrawlAcquisition.yml'
        self.config_file = os.path.normpath(self.config_file)
        if profiles_file:
            self.profiles_file = profiles_file
        else:
            self.profiles_file = './VideoProfiles.yml'
        self.profiles_file = os.path.normpath(self.profiles_file)

        # Define default properties
        self.shutdownOnExit = False
        self.isExiting = False
        self.isAcquiring = False
        self.isTriggering = False
        self.serverThread = None
        self.server = None
        self.spin_system = None
        self.diskStatTimer = None
        self.spin_cameras = {}
        self.cameras = {}
        self.threads = []

        self.hw_triggered_cameras = []
        self.sync_trigger_messages = []
        self.received = {}
        self.use_db = True
        self.syncdSensorData = {}
        self.readyToTrigger = {}
        self.acqisition_teardown_tries = 0
        self.serial_threads_finished = False
        self.server_finished = False
        self.saved_last_still = False
        self.saved_last_frame = False
        self.n_saved_frames = 0
        self.n_saved_stills = 0

        #  create the default configuration dict. These values are used for application
        #  configuration if they are not provided in the config file.
        self.configuration = {}
        self.configuration['metadata'] = {}
        self.configuration['application'] = {}
        self.configuration['acquisition'] = {}
        self.configuration['cameras'] = {}
        self.configuration['server'] = {}
        self.configuration['sensors'] = {}

        self.configuration['application']['output_mode'] = 'separate'
        self.configuration['application']['output_path'] = './data'
        self.configuration['application']['calibration_path'] = './calibration'
        self.configuration['application']['log_level'] = 'INFO'
        self.configuration['application']['database_name'] = 'CamtrawlMetadata.db3'
        self.configuration['application']['shut_down_on_exit'] = False
        self.configuration['application']['always_trigger_at_start'] = False
        self.configuration['application']['ffmpeg_path'] = ''
        self.configuration['application']['disk_free_monitor'] = True
        self.configuration['application']['disk_free_min_mb'] = 150
        self.configuration['application']['disk_free_check_int_ms'] = 5000

        self.configuration['acquisition']['trigger_rate'] = 5
        self.configuration['acquisition']['trigger_limit'] = -1
        self.configuration['acquisition']['video_log_frames'] = False
        self.configuration['acquisition']['video_sync_data_divider'] = 15
        self.configuration['acquisition']['still_sync_data_divider'] = 1

        self.configuration['server']['start_server'] = False
        self.configuration['server']['server_port'] = 7889
        self.configuration['server']['server_interface'] = '0.0.0.0'

        self.configuration['sensors']['default_type'] = 'synchronous'
        self.configuration['sensors']['synchronous'] = []
        self.configuration['sensors']['asynchronous'] = []
        self.configuration['sensors']['synchronous_timeout_secs'] = 5
        self.configuration['sensors']['installed_sensors'] = {}

        self.configuration['metadata']['vessel_name'] = ''
        self.configuration['metadata']['survey_name'] = ''
        self.configuration['metadata']['camera_name'] = 'Camtrawl'
        self.configuration['metadata']['survey_description'] = ''

        #  Create an instance of metadata_db which is a simple interface to the
        #  camtrawl metadata database
        self.db = metadata_db()

        #  Create a SerialMonitor instance which will manage serial sensor data.
        self.serialSensors = SerialMonitor.SerialMonitor(self)
        self.serialSensors.SerialDataReceived.connect(self.SerialDataReceived)
        self.serialSensors.SerialDevicesStopped.connect(self.SerialDevicesStopped)
        self.serialSensors.SerialError.connect(self.SerialDeviceError)

        #  create the trigger timer
        self.triggerTimer = QtCore.QTimer(self)
        self.triggerTimer.timeout.connect(self.TriggerCameras)
        self.triggerTimer.setSingleShot(True)
        self.triggerTimer.setTimerType(QtCore.Qt.PreciseTimer)

        #  create the trigger timer
        self.timeoutTimer = QtCore.QTimer(self)
        self.timeoutTimer.timeout.connect(self.TriggerTimeout)
        self.timeoutTimer.setSingleShot(True)

        #  create the shutdown timer - this is used to delay application
        #  shutdown when no cameras are found. It allows the user to exit
        #  the application and fix the issue when the application is set
        #  to shut the PC down upon exit.
        self.shutdownTimer = QtCore.QTimer(self)
        self.shutdownTimer.setSingleShot(True)

        #  connect the stopApp signal to the stopAcquisition method.
        self.stopApp.connect(self.StopAcquisition)

        #  continue the setup after QtCore.QCoreApplication.exec_() is called
        #  by using a timer to call AcquisitionSetup. This ensures that the
        #  application event loop is running when AcquisitionSetup is called.
        startTimer = QtCore.QTimer(self)
        startTimer.timeout.connect(self.AcquisitionSetup)
        startTimer.setSingleShot(True)
        startTimer.start(0)


    def AcquisitionSetup(self):
        '''AcquisitionSetup reads the configuration files, creates the log file,
        opens up the metadata database, and sets up the cameras.
        '''

        def import_module(module_name):
            '''import_module imports the Python module specified my module_name
            into the global namespace. This method is used to dynamically import
            camera driver modules at runtime.
            '''
            module_handle = importlib.import_module(module_name)
            setattr(sys.modules[__name__], module_name, module_handle)


        #  bump the prompt
        print()

        #  get the application start time
        start_time = datetime.datetime.now()
        start_time_string = start_time.strftime("D%Y%m%d-T%H%M%S")

        #  read the configuration file - we start with the default values and
        #  recursively update them with values from the config file in the
        #  ReadConfig method.
        self.configuration = self.ReadConfig(self.config_file, self.configuration)

        #  Do the same thing with the video profiles file. In this case we don't
        #  have any default values and pass in an empty dict.
        self.video_profiles = self.ReadConfig(self.profiles_file, {})

        #  set up the application paths
        if self.configuration['application']['output_mode'].lower() == 'combined':
            #  This is a combined deployment - we will not create a deployment directory
            self.base_dir = os.path.normpath(self.configuration['application']['output_path'])
        else:
            #  If not 'combined' we log data in separate deployment folders. Deployment folders
            #  are named Dyymmdd-Thhmmss where the date and time are derived from the application
            #  start time.
            self.base_dir = os.path.normpath(self.configuration['application']['output_path'] +
                    os.sep + start_time_string)

        #  create the paths to our logs, images, and settings directories
        self.log_dir = os.path.normpath(self.base_dir + os.sep + 'logs')
        self.image_dir = os.path.normpath(self.base_dir + os.sep + 'images')
        settings_dir = os.path.normpath(self.base_dir + os.sep + 'settings')

        #  set up logging
        try:
            logfile_name = self.log_dir + os.sep + start_time_string + '.log'

            #  make sure we have a directory to log to
            if not os.path.exists(self.log_dir):
                os.makedirs(self.log_dir)

            #  create the logger
            self.logger = logging.getLogger('Acquisition')
            self.logger.propagate = False
            self.logger.setLevel(self.configuration['application']['log_level'])
            fileHandler = logging.FileHandler(logfile_name)
            formatter = logging.Formatter('%(asctime)s : %(levelname)s - %(message)s')
            fileHandler.setFormatter(formatter)
            self.logger.addHandler(fileHandler)
            consoleLogger = logging.StreamHandler(sys.stdout)
            consoleformatter = logging.Formatter('%(asctime)s : %(message)s')
            consoleLogger.setFormatter(consoleformatter)
            self.logger.addHandler(consoleLogger)

        except:
            #  we failed to open the log file - bail
            print("CRITICAL ERROR: Unable to create log file " + logfile_name)
            print("Application exiting...")
            QtCore.QCoreApplication.instance().quit()
            return

        #  make sure we have a directory to write images to
        try:
            if not os.path.exists(self.image_dir):
                os.makedirs(self.image_dir)
        except:
            #  if we can't create the logging dir we bail
            self.logger.critical("Unable to create image logging directory %s." % self.image_dir)
            self.logger.critical("Application exiting...")
            QtCore.QCoreApplication.instance().quit()
            return

        #  copy the settings files - we do this so we have a copy of the settings
        #  for each deployment.
        try:
            #  make sure we have a settings directory. Assume that if the
            #  settings folder exists, we have already copied the files.
            if not os.path.exists(settings_dir):
                os.makedirs(settings_dir)

                #  copy the settings and profiles files
                shutil.copy2(self.config_file, settings_dir)
                shutil.copy2(self.profiles_file, settings_dir)
        except:
            #  we failed to copy the settings?
            self.logger.warning("Unable to copy settings files to " + settings_dir)

        #  copy the calibration files - this allows one to include camera calibration files
        #  with the collected data. We only do this if a calibration folder exists and if it
        #  contains fewer than the max allowed number of files.
        try:
            cal_path = os.path.normpath(self.configuration['application']['calibration_path'])
            dest_dir = os.path.normpath(self.base_dir + os.sep + 'calibration')
            if os.path.exists(cal_path):
                #  first do a sanity check on the number of files - since this is a blind
                #  recursive copy, we limit the total number of files to a handful
                n_check = glob(os.path.join(cal_path, '**', '*'), recursive=True)

                if n_check > self.MAX_CAL_FOLDER_FILES:
                    #  too many files in the cal folder
                    self.logger.warning("Unable to copy calibration folder. Too many files!")
                else:
                    #  there seems to be a sane number of files - copy the directory
                    shutil.copytree(cal_path, dest_dir)
                    self.logger.info("Copied calibration folder to " + dest_dir)
        except:
            #  we failed to copy the settings?
            self.logger.warning("Unable to copy calibration folder to " + dest_dir)


        #  log file is set up and directories created. Get some basic info into the logs
        self.logger.info("Camtrawl Acquisition Starting...")

        #  report versions
        self.logger.info('Platform: %s %s' % (platform.system(), platform.release()))
        self.logger.info('Python version: %s' % (sys.version))
        self.logger.info('Numpy version: %s' % (np.__version__))
        self.logger.info('OpenCV version: %s' % (cv2.__version__))
        self.logger.info('protobuf version: %s' % (google.protobuf.__version__))
        self.logger.info('PyQt version: %s' % (QtCore.QT_VERSION_STR))
        self.logger.info("CamtrawlAcquisition version: " + self.VERSION)

        #  create a list of enumerated cameras and determine what camera drivers
        #  we will need. Then do any initial setup that is required for the drivers.
        self.logger.info("Enumerating cameras...")
        self.enumerated_cameras = []
        drivers = []
        configured_cams = list(self.configuration['cameras'].keys())
        for camera in configured_cams:
            #  check if the driver parameter exists (it may not since the default
            #  camera configuration values have not been merged yet so if it isn't
            #  explicitly set in the config file, it will not exist.)
            if 'driver' in self.configuration['cameras'][camera]:
                #  it does, add this driver to the dict if needed
                driver = self.configuration['cameras'][camera]['driver'].lower()
                #  make sure we know about this driver
                if driver not in self.VALID_DRIVERS:
                    self.logger.warning("Camera '" + camera + "' has an unknown driver '" + camera['driver'] +
                            "' specified. This camera will be ignored.")
                    continue
                #  we do, so we add it to the list, if needed
                if driver not in drivers:
                    drivers.append(driver)
            else:
                #  there is no 'driver' parameter specified. We will default to
                #  SpinCamera to provide backwards compatibility.
                driver = 'spincamera'
                if 'spincamera' not in drivers:
                    drivers.append(driver)

            #  add this camera to the list of enumerated cameras
            if camera.lower() != 'default':
                self.enumerated_cameras.append(camera)

        #  now, do any initial setup required by the driver(s)
        for driver in drivers:
            if driver == 'spincamera':
                self.logger.info("At least one camera is configured to use the SpinCamera driver." +
                        " Initializing SpinCamera...")
                try:
                    #  do our imports - we want to import these globally so we
                    #  use our import_module function
                    import_module('PySpin')
                    import_module('SpinCamera')

                    #  set up the camera interface
                    self.spin_system = PySpin.System.GetInstance()
                    spin_cameras = self.spin_system.GetCameras()

                    #  now add the cameras spin can enumerate to our list if enumerated cameras
                    #  if they aren't already there.
                    for camera in spin_cameras:

                        #  extract the camera name from the Spin camera pointer - 'tis a
                        #  bit complicated....
                        device_info = {}
                        nodemap_tldevice = camera.GetTLDeviceNodeMap()
                        node_device_information = PySpin.CCategoryPtr(nodemap_tldevice.GetNode('DeviceInformation'))
                        if PySpin.IsAvailable(node_device_information) and PySpin.IsReadable(node_device_information):
                            features = node_device_information.GetFeatures()
                            for feature in features:
                                node_feature = PySpin.CValuePtr(feature)
                                device_info[node_feature.GetName()] = (node_feature.ToString()
                                        if PySpin.IsReadable(node_feature) else 'Node not readable')

                        #  for spinnaker cameras, we create the camera name using the model name,
                        #  underscore and the serial number.
                        camera_name = device_info['DeviceModelName'] + '_' + \
                                device_info['DeviceSerialNumber']

                        self.spin_cameras[camera_name] = camera

                        if camera_name not in self.enumerated_cameras:
                            self.enumerated_cameras.append(camera_name)

                except:
                    #  if we can't initialize this driver we bail
                    self.logger.critical("Error obtaining PySpin system instance. Have you installed the " +
                            "Spinnaker SDK and PySpin correctly?")
                    self.logger.critical("Application exiting...")
                    QtCore.QCoreApplication.instance().quit()
                    return
                version = self.spin_system.GetLibraryVersion()
                self.logger.info('Spinnaker/PySpin library version: %d.%d.%d.%d' % (version.major,
                        version.minor, version.type, version.build))

            elif driver == 'cv2videocapture':
                self.logger.info("At least one camera is configured to use the " +
                        "CV2VideoCapture driver. Importing CV2VideoCapture...")
                try:
                    import_module('CV2VideoCapture')
                except:
                    #  if we can't import this driver we bail
                    self.logger.critical("Error importing CV2VideoCapture!")
                    self.logger.critical("Application exiting...")
                    QtCore.QCoreApplication.instance().quit()
                    return

        #  report the number of cameras found
        num_cameras = len(self.enumerated_cameras)
        if num_cameras == 0:
            self.logger.critical("Enumeration complete. No cameras found!")
            self.logger.critical("Application exiting...")
            QtCore.QCoreApplication.instance().quit()
            return
        elif num_cameras == 1:
            self.logger.info('Enumeration complete. 1 camera found.')
        else:
            self.logger.info('Enumeration complete. %d cameras found.' % num_cameras)

        #  note the config files we loaded
        self.logger.info("Configuration file loaded: " + self.config_file)
        self.logger.info("Profiles file loaded: " + self.profiles_file)
        self.logger.info("Logging data to: " + self.base_dir)

        #  set the default_is_synchronous sensor data property
        if self.configuration['sensors']['default_type'].lower() in ['synchronous', 'syncd', 'sync', 'synced']:
            self.default_is_synchronous = True
        else:
            self.default_is_synchronous = False

        #  open/create the image metadata database file
        self.OpenDatabase()

        #  insert the metadata metadata
        if self.use_db:
            self.db.set_metadata_info(self.configuration['metadata']['vessel_name'],
                    self.configuration['metadata']['survey_name'],
                    self.configuration['metadata']['camera_name'],
                    self.configuration['metadata']['survey_description'],
                    start_time)

        #  log the acquisition rate and max image count
        self.logger.info("Acquisition Rate: %d images/sec   Max image count: %d" %
                (self.configuration['acquisition']['trigger_rate'],
                self.configuration['acquisition']['trigger_limit']))

        #  check if we should check the available free space on our destination device.
        if self.configuration['application']['disk_free_monitor']:

            #  get the starting free space and report
            disk_stats = shutil.disk_usage(self.image_dir)
            disk_free_mb = disk_stats.free / 1024 / 1024

            #  check if we even have enough space to start
            if disk_free_mb <= self.configuration['application']['disk_free_min_mb']:
                #  no, don't got the space
                self.disk_ok = False
                self.logger.critical("CRITICAL ERROR: Free space: %d MB is less than the " % (disk_free_mb) +
                    "minimum allowed %d MB" % (self.configuration['application']['disk_free_min_mb']))
                self.logger.critical("Application exiting due to lack of free disk space")
            else:
                #  free space is greater than min
                self.disk_ok = True
                self.logger.info("Starting to monitor disk free space. Starting free space: " +
                        "%d MB. Minimum free space set to: %d MB" % (disk_free_mb,
                        self.configuration['application']['disk_free_min_mb']))

                #  Create a timer to periodically check the disk free space
                self.diskStatTimer = QtCore.QTimer(self)
                self.diskStatTimer.timeout.connect(self.CheckDiskFreeSpace)
                self.diskStatTimer.setSingleShot(False)
                self.diskStatTimer.start(self.configuration['application']['disk_free_check_int_ms'])
        else:
            #  we're not checking the disk free space
            self.disk_ok = True

        #  set up the sensors - the user can log data from additional serial (or network)
        #  sensors along with image data. These sensors are defined in the sensors ->
        #  installed_sensors section of the config file. The sensor data is assumed to be
        #  NMEA like ASCII data terminated by LF or CR/LF. The sensors can be connected to
        #  a local serial port, or a network based serial server or a simple network socket.

        #  first check if the installed_sensors section of the .yml config file is empty.
        #  This is allowed, but it results in self.configuration['sensors']['installed_sensors']
        #  being set to None. We need it set to an empty dict. This is not an issue if it
        #  omitted as it is initialized to an empty dict.
        if self.configuration['sensors']['installed_sensors'] is None:
             self.configuration['sensors']['installed_sensors'] = {}

        #  now set up sensors if any are specified in the yml file
        if len(self.configuration['sensors']['installed_sensors']) > 0:
            self.logger.info("Adding sensors:")
            for sensor_name in self.configuration['sensors']['installed_sensors']:

                #  determine if the type for this sensor is provided - if so, set the is_synchronous
                #  key so we know if it is synced or not when we log it.
                if 'type' in self.configuration['sensors']['installed_sensors'][sensor_name]:
                    if (self.configuration['sensors']['installed_sensors'][sensor_name]['type'].lower() in
                            ['synced', 'sync', 'synchronous']):
                        self.configuration['sensors']['installed_sensors'][sensor_name]['is_synchronous'] = True
                    else:
                        self.configuration['sensors']['installed_sensors'][sensor_name]['is_synchronous'] = False
                else:
                    #  type was not provided so we use the default
                    self.configuration['sensors']['installed_sensors'][sensor_name]['is_synchronous'] = \
                            self.default_is_synchronous

                #  now add any per header synced/async configs - this overrides the type
                if 'synced_headers' in self.configuration['sensors']['installed_sensors'][sensor_name]:
                    for header in self.configuration['sensors']['installed_sensors'][sensor_name]['synced_headers']:
                        self.configuration['sensors']['synchronous'].append(header)
                if 'async_headers' in self.configuration['sensors']['installed_sensors'][sensor_name]:
                    for header in self.configuration['sensors']['installed_sensors'][sensor_name]['async_headers']:
                        self.configuration['sensors']['asynchronous'].append(header)

                if 'serial_port' in self.configuration['sensors']['installed_sensors'][sensor_name]:
                    port = self.configuration['sensors']['installed_sensors'][sensor_name]['serial_port']
                else:
                    #  if port is not defined, we assume the sensor is not local
                    port = None

                #  check if we're adding a header to this sensor's data messages
                if 'add_header' in self.configuration['sensors']['installed_sensors'][sensor_name]:
                    #  yes, make sure it is a string without leading/trailing whitespace
                    header = str(self.configuration['sensors']['installed_sensors'][sensor_name]['add_header']).strip()
                    self.configuration['sensors']['installed_sensors'][sensor_name]['add_header'] = header

                #  set up the logging interval if required
                if 'logging_interval_ms' in self.configuration['sensors']['installed_sensors'][sensor_name]:
                    self.configuration['sensors']['installed_sensors'][sensor_name]['last_write'] = None
                else:
                    self.configuration['sensors']['installed_sensors'][sensor_name]['logging_interval_ms'] = None

                #  see if the baud rate is provided
                if 'serial_baud' in self.configuration['sensors']['installed_sensors'][sensor_name]:
                    try:
                        baud = int(self.configuration['sensors']['installed_sensors'][sensor_name]['serial_baud'])
                    except:
                        #  if baud is not a number, default to 4800
                        baud = 4800
                else:
                    #  if baud isn't provided, default to 4800
                    baud = 4800

                #  if port is provided, we assume this sensor is local and add it to the sensor monitor
                if port:
                    try:
                        #  add this sensor to sensor monitor
                        self.serialSensors.addDevice(sensor_name, port, baud, 'None', '', 0)
                        #  and try to open the port
                        self.serialSensors.startMonitoring(devices=sensor_name)
                        self.logger.info("   added sensor: " + sensor_name +
                                " // port:" + port + " // baud:" + str(baud))

                    except Exception as e:
                        #  ran into an issue with the serial port
                        self.logger.error("   Error opening serial port for sensor: " + sensor_name +
                                " // port:" + port + " // baud:" + str(baud))
                        self.logger.error("   " + str(e))

        #  continue camera setup in another method so we can override that method
        #  in a subclass and allow for additional pre-camera setup.
        self.AcquisitionSetup2()


    def AcquisitionSetup2(self):
        '''
        AcquisitionSetup2 completes setup by configuring the cameras and
        starting acquisition. The application will exit here if there are
        any issues encountered during setup.
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

        #  check if everything is ok
        if self.cam_ok and self.disk_ok:

            #  start the server, if enabled
            if self.configuration['server']['start_server']:
                self.StartServer()

            #  the cameras are ready to acquire. Set the isAcquiring property
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

            #  Stop acquisition and close the app
            self.StopAcquisition(exit_app=True,
                    shutdown_on_exit=self.configuration['application']['shut_down_on_exit'])


    def ConfigureCameras(self):

        """
        ConfigureCameras runs through the cameras and configures them according to the settings
        in the camera section of the configuration file.
        """
        #  initialize some properties
        self.cameras = {}
        self.threads = []
        self.received = {}
        self.this_images = 1
        self.controller_port = {}
        self.hw_triggered_cameras = []
        self.hwTriggered = False

        # Retrieve list of cameras from the system
        self.logger.info('Configuring cameras...')

        #  work thru the list of discovered cameras
        for cam in self.enumerated_cameras:

            #  check config data for settings for this camera. This will get config params
            #  for this camera from the config data we read earlier. It will also return
            #  a boolean indicating whether the camera should be utilized or not.
            add_camera, config = self.GetCameraConfiguration(cam)

            if add_camera:
                #  we have an entry for this camera so we'll use it
                self.logger.info("  Adding: " + cam)

                #  create an instance of the appropriate camera driver class
                if config['driver'].lower() == 'spincamera':
                    #  create a camera object that uses Flir Spinnaker/PySpin as the
                    #  interface to the camera.

                    sc = SpinCamera.SpinCamera(self.spin_cameras[cam])

                    #  get the exposure config value for this driver
                    this_exposure = config['exposure_us']

                elif config['driver'].lower() == 'cv2videocapture':
                    #  create a camera object that uses CV2.VideoCapture as the
                    #  interface to the camera.

                    #  get some params required at instantiation
                    resolution = [None, None]
                    if 'cv2_cam_width' in config:
                        resolution[0] = int(config['cv2_cam_width'])
                    if 'cv2_cam_height' in config:
                        resolution[1] = int(config['cv2_cam_height'])
                    if 'cv2_cam_path' not in config:
                        cam_path = 0
                    else:
                        cam_path = config['cv2_cam_path']

                    #  get the exposure config value for this driver
                    this_exposure = config['exposure']

                    try:
                        sc = CV2VideoCapture.CV2VideoCapture(cam_path, cam, resolution=resolution)
                    except Exception as e:
                        self.logger.warning("Unable to instantiate driver for camera '" + cam + "'")
                        self.logger.warning("    Error: " + str(e))

                #  set up the options for saving image data
                image_options = {'file_ext':config['still_image_extension'],
                                 'jpeg_quality':config['jpeg_quality'],
                                 'scale':config['image_scale']}

                #  create the default video profile
                video_profile = AcquisitionBase.DEFAULT_VIDEO_PROFILE

                #  update it with the options from this camera's config
                if config['video_preset'] in self.video_profiles:
                    #  update the video profile dict with the preset values
                    #video_profile.update(self.video_profiles[config['video_preset']])
                    video_profile = self.video_profiles[config['video_preset']]

                #  insert the scaling factor into the video profile
                video_profile['scale'] = config['video_scale']

                #  set the video framerate - framerate (in frames/sec) is passed to the
                #  video encoder when recording video files.
                if config['video_force_framerate'] > 0:
                    #  the user has chosen to override the system acquisition rate
                    video_profile['framerate'] = config['video_force_framerate']
                else:
                    #  use the system acquisition rate as the video framerate
                    video_profile['framerate'] = self.configuration['acquisition']['trigger_rate']

                #  insert the ffmpeg path to the video profile. Convert relative paths to
                #  absolute. Empty/None assumes ffmpeg is on the system path
                if self.configuration['application']['ffmpeg_path'] in [None, '']:
                    video_profile['ffmpeg_path'] = None
                else:
                    if self.configuration['application']['ffmpeg_path'][0:1] in ['./', '.\\']:
                        #  get the directory containing this script
                        ffpath = functools.reduce(lambda l,r: l + os.path.sep + r,
                                os.path.dirname(os.path.realpath(__file__)).split(os.path.sep))
                    else:
                        ffpath = self.configuration['application']['ffmpeg_path']
                    video_profile['ffmpeg_path'] = os.path.normpath(ffpath)

                #  add or update this camera in the database
                if self.use_db:
                    link_speed = 0
                    if 'DeviceCurrentSpeed' in sc.device_info:
                        link_speed =  sc.device_info['DeviceCurrentSpeed']
                    elif 'DeviceLinkSpeed' in sc.device_info:
                        link_speed = sc.device_info['DeviceLinkSpeed']
                    self.db.update_camera(sc.camera_name, sc.device_info['DeviceID'], sc.camera_id,
                            config['label'], config['rotation'], sc.device_info['DeviceVersion'],
                            str(link_speed))

                # Set the camera's label
                sc.label = config['label']

                #  set the camera trigger and saving dividers
                sc.save_stills = config['save_stills']
                sc.save_stills_divider = config['still_image_divider']
                sc.save_video = config['save_video']
                sc.save_video_divider = config['video_frame_divider']
                sc.trigger_divider = config['trigger_divider']
                self.logger.info(('    %s: trigger divider: %d  save image divider: %d' +
                        '  save frame divider: %d') % (sc.camera_name, sc.trigger_divider,
                        sc.save_stills_divider, sc.save_video_divider))

                #  set up triggering
                if config['trigger_source'].lower() == 'hardware':
                    #  set up the camera to use hardware triggering
                    sc.set_camera_trigger('Hardware')
                    self.logger.info('    %s: Hardware triggering enabled.' % (sc.camera_name))

                    #  if any cameras are hardware triggered we set hwTriggered to True
                    self.hwTriggered = True

                    #  We need to keep a list of hardware triggered cameras so we can store
                    #  some state information about them when triggering. Add this camera
                    #  to the list.
                    self.hw_triggered_cameras.append(sc)

                else:
                    #  set up the camera for software triggering
                    sc.set_camera_trigger('Software')
                    self.logger.info('    %s: Software triggering enabled.' % (sc.camera_name))

                # This should probably be set on the camera to ensure the line is inverted
                # when the camera starts up.
                #ok = sc.set_strobe_trigger(1)

                #  set the camera exposure, gain, and rotation
                sc.set_exposure(this_exposure)
                sc.set_gain(config['gain'])
                sc.rotation = config['rotation']
                self.logger.info('    %s: label: %s  gain: %d  exposure_us: %d  rotation:%s' %
                        (sc.camera_name, config['label'], sc.get_gain(), sc.get_exposure(),
                        config['rotation']))

                #  set the sensor binning
                sc.set_binning(config['sensor_binning'])
                binning = sc.get_binning()
                self.logger.info('    %s: Sensor binning set to %i x %i' %
                        (sc.camera_name, binning, binning))

                #  set up HDR if configured
                if config['hdr_enabled']:
                    ok = sc.enable_hdr_mode()
                    if ok:
                        self.logger.info('    %s: Enabling HDR: OK' % (sc.camera_name))
                        if config['hdr_settings'] is not None:
                            self.logger.info('    %s: Setting HDR Params: %s' % (sc.camera_name,
                                    config['hdr_settings']))
                            sc.set_hdr_settings(config['hdr_settings'])
                        else:
                            self.logger.info('    %s: HDR Params not provided. Using values from camera.' %
                                    (sc.camera_name))

                        sc.hdr_save_merged = config['hdr_save_merged']
                        sc.hdr_signal_merged = config['hdr_signal_merged']
                        sc.hdr_merge_method = config['hdr_merge_method']
                        sc.hdr_tonemap_saturation = config['hdr_tonemap_saturation']
                        sc.hdr_tonemap_bias = config['hdr_tonemap_bias']
                        sc.hdr_tonemap_gamma = config['hdr_tonemap_gamma']

                        #  check if there is a camera response file to load
                        if config['hdr_response_file'] in ['none', 'None', 'NONE']:
                            config['hdr_response_file'] = None
                        if config['hdr_response_file'] is not None:
                            try:
                                sc.load_hdr_reponse(config['hdr_response_file'])
                                self.logger.info('    %s: Loaded HDR response file: %s' %
                                        (sc.camera_name, config['hdr_response_file']))
                            except:
                                self.logger.error('    %s: Failed to load HDR response file: %s' %
                                        (sc.camera_name, config['hdr_response_file']))
                    else:
                        self.logger.error('    %s: Failed to enable HDR.' % (sc.camera_name))
                else:
                    sc.disable_hdr_mode()

                #  create a thread for this camera to run in
                thread = QtCore.QThread()
                self.threads.append(thread)

                #  move the camera to that thread
                sc.moveToThread(thread)

                #  connect up our signals
                sc.imageData.connect(self.CamImageAcquired)
                sc.triggerComplete.connect(self.CamTriggerComplete)
                sc.error.connect(self.LogCamError)
                sc.cameraDebug.connect(self.LogCamDebug)
                sc.acquisitionStarted.connect(self.AcquisitionStarted)
                sc.acquisitionStopped.connect(self.AcquisitionStopped)
                self.trigger.connect(sc.trigger)
                self.stopAcquiring.connect(sc.stop_acquisition)
                self.startAcquiring.connect(sc.start_acquisition)

                #  these signals handle the cleanup when we're done
                sc.acquisitionStopped.connect(thread.quit)
                thread.finished.connect(sc.deleteLater)
                thread.finished.connect(thread.deleteLater)

                #  and start the thread
                thread.start()

                #  add this camera to our list of cameras and set the image
                #  received state to false
                self.cameras[sc.camera_name] = sc
                self.received[sc.camera_name] = False

                if config['save_stills']:
                    if image_options['file_ext'].lower() in ['.jpeg','.jpg']:
                        self.logger.info('    %s: Saving stills as %s  Scale: %i  Quality: %i' % (sc.camera_name,
                            image_options['file_ext'], image_options['scale'],image_options['jpeg_quality']))
                    else:
                        self.logger.info('    %s: Saving stills as %s  Scale: %i' % (sc.camera_name,
                            image_options['file_ext'], image_options['scale']))

                    if self.use_db:
                        #  update the deployment_data table with the image file type
                        self.db.set_image_extension(image_options['file_ext'])

                if config['save_video']:
                    self.logger.info('    %s: Saving video as %s  Video profile: %s' % (sc.camera_name,
                            video_profile['file_ext'], config['video_preset']))
                    if self.use_db:
                        #  update the deployment_data table with the video file type
                        self.db.set_video_extension(video_profile['file_ext'])

                #  issue a warning if a camera is not saving any image data
                if config['save_video'] or config['save_stills']:
                    self.logger.info('    %s: Image data will be written to: %s' % (sc.camera_name,
                                self.image_dir + os.sep + sc.camera_name))
                else:
                    self.logger.warning('    %s: WARNING: Both video and still saving is disabled. ' %
                            (sc.camera_name) + 'NO IMAGE DATA WILL BE RECORDED')

                #  emit the startAcquiring signal to start the cameras
                self.startAcquiring.emit([sc], self.image_dir, config['save_stills'],
                        image_options, config['save_video'], video_profile)

            else:
                #  There is no default section and no camera specific section
                #  so we skip this camera
                self.logger.info("  Skipped camera: " + sc.camera_name +
                        ". No configuration entry found.")

        #  we're done with setup
        self.logger.info("Camera setup complete.")

        #  we return true if we found at least 1 camera
        if len(self.cameras) > 0:
            return True
        else:
            return False


    @QtCore.pyqtSlot()
    def TriggerTimeout(self):
        '''
        The TriggerTimeout slot is called by the trigger timeout timer. This
        method simply calls the TriggerCameras method again in a heroic attempt
        to keep acquiring data after an unhandled issue causes acquisition to
        stall.
        '''

        #  We've timed out. Issue a warning in the log
        self.logger.warning("WARNING: Trigger timeout. One or more cameras failed " +
                "to respond after being triggered.")

        #  and try triggering again.
        self.TriggerCameras()


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
        emit the "TriggerReady" signal. You must connect these signals to a slot in
        your application that tracks the ready cameras and triggers them when all
        triggered cameras are ready.
        '''

        #  reset the received image state for *all* cameras
        for cam_name in self.cameras:
            self.received[cam_name] = False

        #  reset the per trigger save image/frame state
        self.saved_last_still = False
        self.saved_last_frame = False

        #  note the trigger time
        self.trig_time = datetime.datetime.now()

        #  start the trigger timeout timer. This timer ensures that if acquisition
        #  stalls for some unhandled reason, we'll keep trying.
        self.timeoutTimer.start(self.ACQUISITION_TIMEOUT)

        #  emit the trigger signal to trigger the cameras
        self.trigger.emit([], self.n_images, self.trig_time, True, True)

        # TODO: Currently we only write a single entry in the sensor_data table for
        #       HDR acquisition sequences because we're not incrementing the image
        #       counter for each HDR frame. Since we're not incrementing the number
        #       we don't have a unique key in the sensor_data table for the 3 other
        #       HDR exposures. If we want to change this, the easiest approach would
        #       be to use a decimal notation of image_number.HDR_exposure for the
        #       image numbers. For example, 143.1, 143.2, 143.3, 143.4

        #  and write synced sensor data  to the db
        if self.use_db:
            self.sync_trigger_messages = []
            for sensor_id in self.syncdSensorData:
                for header in self.syncdSensorData[sensor_id]:
                    #  check if the data is fresh
                    freshness = self.trig_time - self.syncdSensorData[sensor_id][header]['time']
                    if ((self.configuration['sensors']['synchronous_timeout_secs'] < 0) or
                        (abs(freshness.total_seconds()) <= self.configuration['sensors']['synchronous_timeout_secs'])):
                        #  it is fresh enough. Write it to the db - in order to selectively write sync
                        #  data based on still/video frame and implement sync data dividers as a method
                        #  for reducing data volume, we store the sync values here and then write them
                        #  in CamTriggerComplete where we know what was saved.
                        self.sync_trigger_messages.append([self.n_images, self.syncdSensorData[sensor_id][header]['time'],
                                sensor_id, header, self.syncdSensorData[sensor_id][header]['data']])
                        #self.db.insert_sync_data(self.n_images, self.syncdSensorData[sensor_id][header]['time'],
                        #        sensor_id, header, self.syncdSensorData[sensor_id][header]['data'])


    @QtCore.pyqtSlot(str, str, dict)
    def CamImageAcquired(self, cam_name, cam_label, image_data):
        '''CamImageAcquired is called when a camera has acquired an image
        or timed out waiting for one.

        When a camera is in HDR mode this method is called if an exposure
        has the emit_signal parameter set to True in the HDR settings.
        '''

        #  Check if we received an image or not
        if  not image_data['ok']:
            #  no image data
            log_str = (cam_name + ': FAILED TO ACQUIRE IMAGE')
            if self.use_db:
                self.db.add_dropped(self.n_images, cam_name, self.trig_time)
        else:
            #  we do have image data - check if we should log this image to the images table

            #  Only store the image file name, no path info
            filename = Path(image_data['filename']).name

            #  note if we have saved a still or video frame for this trigger cycle.
            if image_data['save_still']:
                if not self.saved_last_still:
                    self.saved_last_still = True
                    self.n_saved_stills += 1
            if image_data['save_frame']:
                if not self.saved_last_frame:
                    self.saved_last_frame = True
                    self.n_saved_frames += 1

            if self.use_db:
                #  only write an entry in the images table if we have saved a still or
                #  if we saved a video frame and video_log_frames == True
                if (image_data['save_still'] or (self.configuration['acquisition']['video_log_frames'] and
                        image_data['save_frame'])):
                    self.db.add_image(self.n_images, cam_name, self.trig_time, filename,
                            image_data['exposure'], image_data['gain'], image_data['save_still'],
                            image_data['save_frame'])

            log_str = (cam_name + ': Image Acquired: %dx%d  exp: %d  gain: %2.1f  filename: %s' %
                    (image_data['width'], image_data['height'], image_data['exposure'],
                    image_data['gain'], filename))
        self.logger.debug(log_str)


    @QtCore.pyqtSlot(object)
    def CamTriggerComplete(self, cam_obj):
        '''CamTriggerComplete is called when a camera has completed a trigger event.
        This is called regardless of whether an image was received and/or the camera
        is configured to emit a signal when it does acquire an image.
        '''

        #  note that this camera has completed the trigger event
        self.received[cam_obj.camera_name] = True

        #  emit some debugging info
        self.logger.debug(cam_obj.camera_name + ': Trigger Complete.')

        #  check if all triggered cameras have completed the trigger sequence
        if (all(self.received.values())):

            #  they have - check if we should write synced sensor data to the db
            if self.use_db:
                write_sync = False
                #  check if we saved this still and the total number of saved stills is evenly
                #  divisible by the still_sync_data_divider
                if ((self.n_saved_stills % self.configuration['acquisition']['still_sync_data_divider']) != 0 and
                        self.saved_last_still):
                    #  it is, so we'll write the data
                    write_sync = True
                #  if not, then we check for the same thing with the video frames
                elif ((self.n_saved_frames % self.configuration['acquisition']['video_sync_data_divider']) != 0 and
                        self.saved_last_frame):
                    write_sync = True
                if write_sync:
                    #  write all of the sync messages we cached when the cameras were triggered.
                    for sync_message in self.sync_trigger_messages:
                            message_data = self.sync_trigger_messages[sync_message]
                            self.db.insert_sync_data(message_data[0],message_data[1],message_data[2],
                                    message_data[3],message_data[4])

            #  Increment our counters
            self.n_images += 1
            self.this_images += 1

            #  cancel our timeout timer
            self.timeoutTimer.stop()

            #  check if we're configured for a limited number of triggers
            if ((self.configuration['acquisition']['trigger_limit'] > 0) and
                (self.this_images > self.configuration['acquisition']['trigger_limit'])):

                    self.logger.info("Trigger limit of %i triggers reached. Shutting down..." %
                            (self.this_images-1))

                    #  time to stop acquiring - call our StopAcquisition method and set
                    #  exit_app to True to exit the application after the cameras stop.
                    self.StopAcquisition(exit_app=True,
                            shutdown_on_exit=self.configuration['application']['shut_down_on_exit'])
            else:
                #  keep going - determine elapsed time and set the trigger for the next interval
                elapsed_time_ms = (datetime.datetime.now() - self.trig_time).total_seconds() * 1000
                acq_interval_ms = 1000.0 / self.configuration['acquisition']['trigger_rate']
                next_int_time_ms = int(acq_interval_ms - elapsed_time_ms)
                if next_int_time_ms < 0:
                    next_int_time_ms = 0

                self.logger.debug("Trigger %d completed. Last interval %8.4f ms" %
                        (self.this_images, elapsed_time_ms))

                #  start the next trigger timer
                if self.isTriggering:
                    self.logger.debug("Next trigger in  %8.4f ms." % (next_int_time_ms))
                    self.triggerTimer.start(next_int_time_ms)


    @QtCore.pyqtSlot(str, str)
    def LogCamError(self, cam_name, error_str):
        '''
        The LogCamError slot is called when a camera runs into an error. For now
        we just log the error and move on.
        '''
        #  log it.
        self.logger.error(cam_name + ':ERROR:' + error_str)


    @QtCore.pyqtSlot(str, str)
    def LogCamDebug(self, cam_name, debug_str):
        '''
        The LogCamDebug slot is called when a camera emits some debug info. For now
        we just log the error and move on.
        '''
        #  log it.
        self.logger.debug(cam_name + ':DEBUG:' + debug_str)


    @QtCore.pyqtSlot(str)
    def LogServerError(self, error_str):
        '''
        The LogServerError slot is called when a CamtrawlServer runs into an error.
        For now we just log the error and move on.
        '''
        #  log it.
        self.logger.error('CamtrawlServer:ERROR:' + error_str)


    @QtCore.pyqtSlot(object, str, bool)
    def AcquisitionStarted(self, cam_obj, cam_name, success):
        '''
        The AcquisitionStarted slot is called when a camera responds to the
        startAcquiring signal.
        '''
        if success:
            self.logger.info(cam_name + ': acquisition started.')
        else:
            self.logger.error(cam_name + ': unable to start acquisition.')
            #  NEED TO CLOSE THIS CAMERA?


    @QtCore.pyqtSlot(object, str, bool)
    def AcquisitionStopped(self, cam_obj, cam_name, success):
        '''
        The AcquisitionStopped slot is called when a camera responds to the
        stopAcquiring signal. If we're exiting the application, we start the
        process here.
        '''

        if success:
            self.logger.info(cam_name + ': acquisition stopped.')
        else:
            self.logger.error(cam_name + ': unable to stop acquisition.')

        #  update the received dict noting this camera has stopped
        self.received[cam_obj.camera_name] = True

        #  check if all cameras have stopped
        if (all(self.received.values())):
            self.logger.info('All cameras stopped.')

            #  if we're supposed to exit the application, do it
            if self.isExiting:
                self.logger.info("Acquisition is Stopping...")
                self.AcqisitionTeardown()


    def StopAcquisition(self, exit_app=False, shutdown_on_exit=False):
        '''
        StopAcquisition, starts the process of stopping image acquisition. This method
        updates a few properties and then emits the stopAcquiring signal which informs
        the cameras to stop acquiring and close.

        The process of stopping then continues in AcquisitionStopped when all cameras
        have responded to the stopAcquiring signal.
        '''

        #  stop the trigger and timeout timers
        self.isTriggering = False
        self.triggerTimer.stop()
        self.timeoutTimer.stop()

        #  use the received dict to track the camera shutdown. When all
        #  cameras are True, we know all of them have reported that they
        #  have stopped recording.
        for cam_name in self.cameras:
            self.received[cam_name] = False

        #  set the exit and shutdown states
        self.isExiting = bool(exit_app)
        self.shutdownOnExit = bool(shutdown_on_exit)

        if self.isAcquiring:
            #  stop the cameras
            self.stopAcquiring.emit([])
        else:
            self.AcqisitionTeardown()

        #  shutdown will continue in the AcquisitionStopped method after
        #  all cameras have stopped.


    def AcqisitionTeardown(self):
        """
        AcqisitionTeardown is called when the application is shutting down.
        The cameras will have already been told to stop acquiring
        """

        #  stop the shutdown delay timer (if it has been started)
        self.shutdownTimer.stop()

        #  if we have serial sensors, stop monitoring them
        if (len(self.serialSensors.devices) > 0) and (self.serialSensors.whosMonitoring()):
                #  at least one is running so we need to wait for them to finish
                self.logger.info("Closing serial sensor connections...")
                self.serial_threads_finished = False
                self.serialSensors.stopMonitoring()
        else:
            #  none of the sensor serial ports are open so there's nothing to wait for
            self.serial_threads_finished = True


        #  if we're using the database, close it
        if self.use_db and self.db.is_open:
            self.logger.info("Closing the database...")
            self.db.close()

        #  same with the server
        if self.configuration['server']['start_server']:
            self.logger.info("Shutting down the server...")
            self.server_finished = False
            self.stopServer.emit()

        #  we need to make sure we release all references to our SpinCamera
        #  objects so Spinnaker can clean up behind the scenes.
        self.logger.debug("Cleaning up references to Spinnaker objects...")
        self.received = {}
        self.hw_triggered_cameras = []
        self.cameras = {}

        #  now we'll wait a bit to allow the serial ports and server to finish closing.
        self.acqisition_teardown_tries = 0
        delayTimer = QtCore.QTimer(self)
        delayTimer.timeout.connect(self.AcqisitionTeardownTimeout)
        delayTimer.setSingleShot(True)
        delayTimer.start(500)


    @QtCore.pyqtSlot()
    def ServerStopped(self):
        '''The ServerStopped slot is called when the CamtrawlServer shuts down
        '''
        self.logger.info("CamtrawlServer stopped.")
        self.server_finished = True


    @QtCore.pyqtSlot()
    def AcqisitionTeardownTimeout(self):
        '''AcqisitionTeardownTimeout is called periodically after the initial teardown
        steps have been executed. Here we check if these initial steps have completed.
        When those steps have completed, we continue teardown in

        '''
        #  keep track of how long were waiting so we can bail if
        self.acqisition_teardown_tries += 1

        #  create a timer
        delayTimer = QtCore.QTimer(self)
        delayTimer.setSingleShot(True)

        #  check if we're ready to continue teardown
        if (self.acqisition_teardown_tries >= self.TEARDOWN_TRIES or
                self.server_finished and self.serial_threads_finished):
            #  either everything we're tracking has shut down or we're bailing
            delayTimer.timeout.connect(self.AcqisitionTeardown2)
        else:
            #  we're not ready to proceed - so we'll set the timer to call
            delayTimer.timeout.connect(self.AcqisitionTeardownTimeout)

        #  start the timer
        delayTimer.start(500)


    def AcqisitionTeardown2(self):
        '''
        AcqisitionTeardown2 is called to finish teardown. This last bit of cleanup
        is triggered by a delay timer to give threads and the Python GC a little time
        to finish cleaning up before we release the spinnaker instance and shut down.
        '''

        # Now we can release the Spinnaker system instance
        if (self.spin_system):
            self.logger.debug("Releasing Spinnaker system instance...")
            self.spin_system.ReleaseInstance()
            self.spin_system = None

        #  if we're supposed to shut the PC down on application exit,
        #  get that started here.
        if self.shutdownOnExit:

            self.logger.info("Initiating PC shutdown...")

            #  execute the "shutdown later" command
            if os.name == 'nt':
                #  on windows we can simply call shutdown and delay 10 seconds
                subprocess.Popen(["shutdown", "-s", "-t", "10"],
                        creationflags=subprocess.DETACHED_PROCESS |
                        subprocess.CREATE_NEW_PROCESS_GROUP)
            else:
                #  on linux we have a script we use to delay the shutdown.
                #  Since the shutdown command can't delay less than one minute,
                #  we use a script to delay 10 seconds and then call shutdown.
                #
                #  You must add an entry in the sudoers file to allow the user running this
                #  application to execute the shutdown command without a password. For example
                #  add these lines to your /etc/sudoers file:
                #    camtrawl ALL=NOPASSWD: /camtrawl/software/scripts/delay_shutdown.sh
                #    camtrawl ALL=NOPASSWD: /sbin/shutdown.sh
                subprocess.Popen(['sudo', '/camtrawl/software/scripts/delay_shutdown.sh'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        preexec_fn=os.setpgrp)

        self.logger.info("Acquisition Stopped.")
        self.logger.info("Application exiting...")

        #  we be done
        QtCore.QCoreApplication.instance().quit()


    def GetCameraConfiguration(self, camera_name):
        '''GetCameraConfiguration returns a bool specifying if the camera should
        be utilized and a dict containing any camera configuration parameters. It
        starts with a dict containing the default config files and then updates
        them with options/values specified in the application config file.

        It first looks for camera specific entries, if that isn't found it checks
        for a 'default' entry. If a camera specific entry doesn't exist and there
        is no 'default' section, the camera is not used by the application.
        '''

        add_camera = False

        #  start with the default camera configuration
        config = AcquisitionBase.CAMERA_CONFIG_OPTIONS.copy()

        # Look for a camera specific entry first
        if camera_name in self.configuration['cameras']:
            #  update this camera's config with the camera specific settings
            config = self.__update(config, self.configuration['cameras'][camera_name])
            #  we add cameras that are explicitly configured in the config file
            add_camera = True

        # If that fails, check for a default section
        elif 'default' in self.configuration['cameras']:
            #  update this camera's config with the camera specific settings
            config = self.__update(config, self.configuration['cameras']['default'])
            #  we add all cameras if there is a 'default' section in the config file
            add_camera = True

        return add_camera, config


    def StartServer(self):
        '''StartServer will start the CamtrawlServer. The Camtrawl server provides
        a command and control interface and serves up image and sensor data on the
        network. It can be used in conjunction with the Camtrawl client in applications
        for remote viewing and control of the system.
        '''

        self.logger.info("Opening Camtrawl server on  " +
                self.configuration['server']['server_interface'] + ":" +
                str(self.configuration['server']['server_port']))

        #  create a dict to pass to the server keyed by camera name that
        #  contains a dicts with a 'label' key which is used by the server
        #  to identify available cameras and store the most recent image
        #  from those cameras. This is not required, but if this isn't
        #  passed during init, the server will only become aware of the
        #  camera when it receives an image which gets awkward if you
        #  connect to the server before any images are acquired.
        server_cam_dict = {}
        for cam in self.cameras.keys():
            server_cam_dict[cam] = {'label':self.cameras[cam].label}

        #  create an instance of CamtrawlServer
        self.server = CamtrawlServer.CamtrawlServer(
                self.configuration['server']['server_interface'],
                self.configuration['server']['server_port'],
                cameras=server_cam_dict)

        #  connect the server's signals and slots
        self.server.sensorData.connect(self.SensorDataAvailable)
        self.sensorData.connect(self.server.sensorDataAvailable)
        self.server.getParameterRequest.connect(self.GetParameterRequest)
        self.server.setParameterRequest.connect(self.SetParameterRequest)
        self.server.error.connect(self.LogServerError)
        self.server.serverClosed.connect(self.ServerStopped)
        self.stopServer.connect(self.server.stopServer)

        #  connect our signals to the server
        self.parameterChanged.connect(self.server.parameterDataAvailable)

        #  connect our cameras imageData signals to the server
        for cam_name in self.cameras:
            self.cameras[cam_name].imageData.connect(self.server.newImageAvailable)

        #  create a thread to run CamtrawlServer
        self.serverThread = QtCore.QThread(self)

        #  move the server to it
        self.server.moveToThread(self.serverThread)

        #  connect thread specific signals and slots - this facilitates starting,
        #  stopping, and deletion of the thread.
        self.serverThread.started.connect(self.server.startServer)
        self.server.serverClosed.connect(self.serverThread.quit)
        #self.serverThread.finished.connect(self.appThreadFinished)
        self.serverThread.finished.connect(self.serverThread.deleteLater)

        #  and finally, start the thread - this will also start the server
        self.serverThread.start()


    def OpenDatabase(self):
        '''OpenDatabase opens the acquisition database file. This method creates a new
        db file or opens an existing file depending on the mode of operation. It also
        determines the starting image number if running in "combined" mode.

        When logging data in "combined" mode (all data in one folder, which also means
        all metadata in one sqlite file) this method will attempt to create a new sqlite
        file if the initial file becomes corrupted. While this is unlikely, we don't
        want to fail to acquire because of a bad sqlite file.
        '''

        # Open the database file
        dbFile = self.log_dir + os.sep + self.configuration['application']['database_name']
        self.logger.info("Opening database file: " + dbFile)

        if not self.db.open(dbFile):
            # If we're running in combined mode and we can't open the db file it is
            # possible that the file is corrupted. When this happens we don't want to
            # fail to acquire so we're going to try to open a new file. We'll just append
            # a number to the original file name so we can easily know the name. On subsequent
            # cycles the original db file will still be corrupt, but this code should
            # either create or open the next non-corrupt file.
            if self.configuration['application']['output_mode'].lower() == 'combined':
                self.logger.error('Error opening SQLite database file ' + dbFile +
                        '. Attempting to open an alternate...')

                #  to make the naming predictable we just append a number to it. MAX_DB_ALTERNATES
                #  sets an upper bound on this process so we don't stall here forever.
                for n_try in range(self.MAX_DB_ALTERNATES):

                    #  create the new filename
                    filename, file_ext = os.path.splitext(dbFile)
                    dbFile = filename + '-' + str(n_try) + file_ext

                    #  try to open it
                    self.logger.info("  Opening database file: " + dbFile)
                    if not self.db.open(dbFile):
                        self.logger.error('  Error opening alternate database file ' + dbFile +'.')
                    else:
                        # success!
                        break

                if not self.db.is_open:
                    #  we failed :(
                    self.logger.error('  Failed to open an alternate database file.')
                    self.logger.error('  Acquisition will continue without the database but ' +
                            'this situation is not ideal.')
                    self.logger.error('  Will use max(file image number) + 1 to determine ' +
                            'current image number.')
                    self.use_db = False

            else:
                # When we're not running in combined mode, we will always be creating
                # a new db file. If we cannot open a *new* file, we'll proceed as best we can.
                self.logger.error('Error opening SQLite database file ' + dbFile +'.')
                self.logger.error('  Acquisition will continue without the database but ' +
                            'this situation is not ideal.')
                self.use_db = False

        #  determine the starting image number - if we can't get the number from the
        #  metadata database, we'll pick through the data files.
        if self.use_db:
            self.n_images = self.db.get_next_image_number()
        else:
            #  don't have the db, pick through the files for the next image number.
            #  This is a failsafe for combined mode that allows us to keep acquiring
            #  images even if the metadata database gets corrupted.
            max_num = -1
            cam_dirs = os.listdir(self.image_dir)
            for cam_dir in cam_dirs:
                img_files = os.listdir(self.image_dir + os.sep + cam_dir)
                for file in img_files:
                    try:
                        img_num = int(file.split('_')[0])
                        if (img_num > max_num):
                            max_num = img_num
                    except:
                        pass
            if max_num < 0:
                self.n_images = 1
            else:
                self.n_images = max_num + 1


    @QtCore.pyqtSlot(str, str, object)
    def SerialDataReceived(self, sensor_id, data, err):
        '''SerialDataReceived is called when we receive data from a serial based sensor. This
        method will get the time, parse the header (or optionally add a header) and then call
        SensorDataAvailable to log it and emit it for other consumers.
        '''

        #  check if we have data - we drop empty strings here
        if data is not None and len(data) > 0:

            #  get the time
            rx_time = datetime.datetime.now()

            #  check if we're prepending a header
            if sensor_id in self.configuration['sensors']['installed_sensors']:
                #  check if we're adding a header to this data
                if 'add_header' in self.configuration['sensors']['installed_sensors'][sensor_id]:
                    #  yes, add the header to the data string
                    header = self.configuration['sensors']['installed_sensors'][sensor_id]['add_header']
                    data = header + ',' + data
                else:
                    #  no, we're not adding one. Parse it from the data string
                    data_bits = data.split(',')
                    header = data_bits[0]

            #  and call SensorDataAvailable
            self.SensorDataAvailable(sensor_id, header, rx_time, data)


    @QtCore.pyqtSlot()
    def SerialDevicesStopped(self):
        '''The SerialDevicesStopped slot is called when all serial device threads have
        finsihed.
        '''
        self.logger.info("All serial ports closed.")
        self.serial_threads_finished = True


    @QtCore.pyqtSlot(str, object)
    def SerialDeviceError(self, device, err):
        '''The SerialDeviceError slot is called when a sensor serial device emits an error
        '''
        self.logger.error("ERROR: serial device '" + device + "': " + str(err))


    @QtCore.pyqtSlot(str, str)
    def GetParameterRequest(self, module, parameter):
        '''The GetParameterRequest slot is called when a GetParameter command is sent ro the
        CamtrawlServer. Here we intercept and act on parameters that are common to AcquisitionBase.
        Parameters specific to child classes should be handled in the child class before calling
        this method.
        '''

        cam_names = list(self.received.keys())

        #  split the parameter path
        params = parameter.split('/')
        if params[0] == '':
            #  no parameter provided
            return

        if module.lower() == 'acquisition':

            if params[0].lower() == 'camera_list':
                #  build a comma separated list of cameras names and emit
                param_value = ','.join(cam_names)
                self.parameterChanged.emit(module, parameter, param_value, 1, '')

            elif params[0].lower() == 'is_triggering':
                #  emit "1" if we're currently triggering and "0" if not
                self.parameterChanged.emit(module, parameter, str(int(self.isTriggering)), 1, '')

            #  check if the first param path element is a camera
            elif params[0] in cam_names:

                #  make sure we have a parameter for this camera
                if len(params) < 2:
                    return

                if params[1].lower() == 'gain':
                    param_value = self.cameras[params[0]].get_gain()
                    if param_value:
                        self.parameterChanged.emit(module, parameter, str(param_value), 1, '')

                elif params[1].lower() == 'exposure':
                    param_value = self.cameras[params[0]].get_exposure()
                    if param_value:
                        self.parameterChanged.emit(module, parameter, str(param_value), 1, '')


    @QtCore.pyqtSlot(str, str, str)
    def SetParameterRequest(self, module, parameter, value):
        '''The SetParameterRequest slot is called when a SetParameter command is sent to the
        CamtrawlServer. Here we intercept and act on parameters that are common to AcquisitionBase
        which include the "acquisition" and "sensors" modules. Parameters specific to child
        classes should be handled in the child class before calling this method.

        The "modules" defined in AcquisitionBase are "acquisition" and "sensors". Parameters sent
        to the acquisition module handle basic acquisition functions like starting and stopping
        acquisition and setting gain and exposure on the cameras. Values sent to the "sensors"
        module are forwarded to the sensor defined in the parameter argument.

        The parameter argument is a string that defines the specific attribute that the provided
        value applies to. If additional specificity is required, the parameter string is delineated
        with "/". For example, when setting camera specific parameters, the parameter argument
        would be: "<camera name>/gain" or "<camera name>/exposure"

        '''

        #  get a list of our current cameras
        cam_names = list(self.received.keys())

        #  split the parameter path
        params = parameter.split('/')
        if params[0] == '':
            #  no parameter provided
            return

        if module.lower() == 'acquisition':
            #  this is a parameter related to acquisition

            if params[0].lower() == 'start_triggering':
                if not self.isTriggering:
                    self.isTriggering = True
                    self.triggerTimer.start(250)
                self.parameterChanged.emit(module, 'is_triggering', str(int(self.isTriggering)), 1, '')

            elif params[0].lower() == 'stop_triggering':
                if self.isTriggering:
                    self.isTriggering = False
                self.parameterChanged.emit(module, 'is_triggering', str(int(self.isTriggering)), 1, '')

            #  check if this is a camera specific parameter
            elif params[0] in cam_names:
                #  it is. Next make sure we have a parameter for this camera
                if len(params) < 2:
                    #  no param provided, don't know what to do so just return
                    return

                if params[1].lower() == 'gain':
                    #  this is a set gain command for the specified camera
                    try:
                        ok = self.cameras[params[0]].set_gain(float(value))
                        if ok:
                            param_value = self.cameras[params[0]].get_gain()
                            self.parameterChanged.emit(module, parameter, str(param_value), 1, '')
                    except:
                        pass

                elif params[1].lower() == 'exposure':
                    #  this is a set exposure command for the specified camera
                    try:
                        ok = self.cameras[params[0]].set_exposure(float(value))
                        if ok:
                            param_value = self.cameras[params[0]].get_exposure()
                            self.parameterChanged.emit(module, parameter, str(param_value), 1, '')
                    except:
                        pass

        #  Users can send data to attached sensors to configure or control them.
        #      module = "sensors"
        #      parameter = sensor name (as specified in configuration file)
        #      value = string containing the datagram to send to the sensor
        elif module.lower() == 'sensors':
            #  this is a param being sent to a sensor - check if the sensor exists
            if params[0] in self.serialSensors.devices:
                #  it does, send the datagram to the device
                self.serialSensors.txData(params[0], value)


    @QtCore.pyqtSlot(str, str, datetime.datetime, str)
    def SensorDataAvailable(self, sensor_id, header, rx_time, data):
        '''
        The SensorDataAvailable slot is called when sensor data is received.

        CamtrawlAcquisition lumps sensor data into 2 groups. Synced sensor data
        is cached when received and then logged to the database when the cameras
        are triggered and the data are linked to the image. Async sensor data is
        logged immediately and is not linked to any image. You configure sensor
        specifics in the "sensor" section of the configuration file.

        Args:
            sensor_id (str): A unique string defining the sensor. Sensors can
                             have multiple data types, each defined by a unique
                             header.
            header (str): A string specifying the datagram header of this datagram.

            rx_time (datetime): A datetime object defining the time the data was
                                received or created by the producer.
            data (str): A string containing the sensor data. The string is
                        assumed to be in the form:
                          <header>,<data>

        Returns:
            None
        '''

        #  check if we should log this data
        if sensor_id in self.configuration['sensors']['installed_sensors']:

            #  determine if this data is synced or async
            is_synchronous = self.default_is_synchronous
            if header in self.configuration['sensors']['synchronous']:
                is_synchronous = True
            elif header in self.configuration['sensors']['asynchronous']:
                is_synchronous = False

            if is_synchronous:
                #  this data should be cached to be written to the db when the cameras are triggered

                #  first check if we have an entry for this sensor
                if sensor_id not in self.syncdSensorData:
                    #  nope, add it
                    self.syncdSensorData[sensor_id] = {}

                #  add the data
                self.syncdSensorData[sensor_id][header] = {'time':rx_time, 'data':data}

            else:
                #  this is async sensor data so we (possibly) just write it
                if self.use_db:

                    #  assume that we will write this data to the database
                    write_async = True

                    #  check if we're logging this data on an interval
                    if self.configuration['sensors']['installed_sensors'][sensor_id]['logging_interval_ms']:
                        #  logging_interval_ms is not none, so yes. Check when we last wrote this data
                        if self.configuration['sensors']['installed_sensors'][sensor_id]['last_write']:
                            #  we have a last_write time - check the interval to see if we need to write this data
                            time_diff = rx_time - self.configuration['sensors']['installed_sensors'][sensor_id]['last_write']
                            if ((time_diff.seconds * 1000) >=
                                    self.configuration['sensors']['installed_sensors'][sensor_id]['logging_interval_ms']):
                                self.configuration['sensors']['installed_sensors'][sensor_id]['last_write'] = rx_time
                            else:
                                #  we don't need to log this data
                                write_async = False
                        else:
                            #  this is the first time we're logging this sensor's data
                            self.configuration['sensors']['installed_sensors'][sensor_id]['last_write'] = rx_time

                    if write_async:
                        self.db.insert_async_data(sensor_id, header, rx_time, data)

        #  lastly emit the sensorData signal to send it to the server
        self.sensorData.emit(sensor_id, header, rx_time, data)


    def ReadConfig(self, config_file, config_dict):
        '''ReadConfig reads the yaml configuration file and returns the updated
        configuration dictionary.
        '''

        #  read the configuration file
        with open(config_file, 'r') as cf_file:
            try:
                config = yaml.safe_load(cf_file)
            except yaml.YAMLError as exc:
                self.logger.error('Error reading configuration file ' + self.config_file)
                self.logger.error('  Error string:' + str(exc))
                self.logger.error('  We will try to proceed, but things are probably not going to ' +
                        'work like you want them too.')

        # Update/extend the configuration values and return
        return self.__update(config_dict, config)


    def ExternalStop(self):
        '''
        ExternalStop is called when one of the main thread exit handlers are called.
        It emits a stop signal that is then received by the QCoreApplication which then
        shuts everything down in the QCoreApplication thread.
        '''
        self.stopApp.emit(True)


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
