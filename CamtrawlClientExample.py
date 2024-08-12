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
.. module:: CamtrawlAcquisition.CamtrawlClientExample

    :synopsis: CamtrawlClientExample is a simple example of using
               the Camtrawl client to request and display images
               being collected by a running Camtrawl system.
    system

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


import sys
import logging
import datetime
import cv2
from CamtrawlServer import CamtrawlClient
from PyQt5 import QtCore


class CamtrawlClientExample(QtCore.QObject):
    '''
    CamtrawlClientExample is a simple example of using the Camtrawl client
    to request and display images being collected by a running Camtrawl
    system. If you do not have a live system to connect to, you can use
    CamtrawlServerSim.py to serve up data from a previously recorded deployment.

    See the BOTTOM of the script for options.
    '''

    #  specify the server timeout in ms
    SERVER_TIMEOUT = 3000

    #  specify the interval in ms that sensor data will be requested (and optionally) sent
    SENSOR_DATA_INTERVAL = 1000

    #  specify a list containing some fake sensor data to send if sendSensorData is True
    SENSOR_DATA_DATA = [['GPS','$GPRMC,235951.00,A,5635.8679,N,15335.9930,W,13.1,227.2,070524,13.3,E,D*2F'],
                        ['Temperature','$YCMTW,7.1,C,44.8,F']]

    #  define PyQt Signals
    stopApp = QtCore.pyqtSignal(bool)


    def __init__(self, host, port, compressed, scale, quality, txSensorData=False,
        parent=None):

        super(CamtrawlClientExample, self).__init__(parent)

        #  store the server's host and port info
        self.host = str(host)
        self.port = int(port)

        #  store the image request parameters
        self.compressed = compressed
        self.scale = scale
        self.quality = quality
        self.txSensorData = txSensorData

        #  create an instance of our CamtrawlClient and connect its signals
        self.client = CamtrawlClient.CamtrawlClient()

        #  The imageReceived signal is emitted by the client when it has
        #  received an image from the server.
        self.client.imageData.connect(self.imageReceived)

        #  The syncSensorData and asyncSensorData signals are emitted when the
        #  client receives sensor or control data from the server. In this
        #  example we don't make a distinction between synced and async sensors
        #  so we connect both of them to the same slot.
        self.client.syncSensorData.connect(self.GetSensorData)
        self.client.asyncSensorData.connect(self.GetSensorData)

        #  the parameterData signal is emitted after a getParameter or
        #  set parameter call and it will contain the current parameter
        #  value that was requested or set.
        self.client.parameterData.connect(self.GetParamData)

        #  The error signal is emitted when the client encounters an
        #  error. The errors will primarily be socket related errors.
        self.client.error.connect(self.clientError)

        #  The connected signal is emitted when the client connects to the server.
        self.client.connected.connect(self.connected)

        #  The disconnected signal is emitted when the client is disconnected
        #  from the server
        self.client.disconnected.connect(self.disconnected)

        #  connect the stopApp signal to the shutdown method.
        self.stopApp.connect(self.shutdown)

        #  create a logger
        self.logger = logging.getLogger(__name__)
        self.logger.propagate = False
        self.logger.setLevel('DEBUG')
        formatter = logging.Formatter('%(asctime)s : %(levelname)s : %(module)s - %(message)s')
        consoleLogger = logging.StreamHandler(sys.stdout)
        consoleLogger.setFormatter(formatter)
        self.logger.addHandler(consoleLogger)

        #  create a timeout timer that is called when we disconnect from the server
        self.timeoutTimer = QtCore.QTimer(self)
        self.timeoutTimer.timeout.connect(self.disconnected)
        self.timeoutTimer.setSingleShot(True)

        #  create a couple of timers to request sensor data (and optionally) send it
        self.sendSensorTimer = QtCore.QTimer(self)
        self.sendSensorTimer.timeout.connect(self.sendSensorData)
        self.getSensorTimer = QtCore.QTimer(self)
        self.getSensorTimer.timeout.connect(self.getSensorData)

        #  set a timer to allow the event loop to start before continuing
        timer = QtCore.QTimer(self)
        timer.timeout.connect(self.connectToServer)
        timer.setSingleShot(True)
        timer.start(0)


    @QtCore.pyqtSlot()
    def sendSensorData(self):
        '''
        sendSensorData sends fake sensor data to the server as an example of
        how to send data to the server. It is periodically called by a timer to
        simulate actual sensors.
        '''

        for data in self.SENSOR_DATA_DATA:
            sensorTime = datetime.datetime.now()
            self.logger.debug("Sending Sensor Data: " + " : " + data[0] + " : "  + data[1])
            self.client.setData(data[0], data[1], time=sensorTime)


    @QtCore.pyqtSlot()
    def getSensorData(self):
        '''
        getSensorData sends a request to the server for sensor data. In this example
        we are using a timer to periodically request sensor data.
        '''
        #  call the client's getData method. We do not provide a sensorID so we'll get data
        #  from all sensors
        self.client.getData()

        #  An example of setting the sensorID argument to get GPS data only:
        #self.client.getData(sensorID='GPS')


    @QtCore.pyqtSlot()
    def connectToServer(self):
        '''
        connectToServer is called by a timer after the application is instantiated and
        the event loop started. We, er, connect to the server and then request info
        about the cameras.
        '''

        #  connect to the server - the client will emit the connected signal when it's connected.
        self.logger.info("Connecting to server %s:%i" % (self.host, self.port))
        self.client.connectToServer(self.host, self.port)


    def disconnectFromServer(self):
        '''
        disconnectFromServer disconnects the client from the server.
        '''
        self.client.disconnectFromServer()


    @QtCore.pyqtSlot(str, str, dict)
    def imageReceived(self, camera, label, imageData):
        '''
        the imageReceived slot is called when the client receives an image from the server.

        It is important to remember that this slot will be called once for each camera
        that has a pending image request on the server. This means that if you request
        a simultaneous pair of images, you must track the received state of each camera
        so you know when you have both images to process.

            camera          - a string containing the camera name the image belongs to.
            label           - a string containing the camera label
            imageData       - a dict in the following form containing the image data and
                              metadata with the following keys:

                                imageData['data'] - image data as OpenCV numpy array
                                imageData['ok'] - True if image was received, False if there was a problem
                                imageData['exposure'] - camera exposure
                                imageData['gain'] - camera gain
                                imageData['height'] - image height
                                imageData['width'] - image width
                                imageData['timestamp'] - trigger timestamp
                                imageData['filename'] - image filename (if any)
                                imageData['image_number'] - global image number

                            imageData['data'] is a numpy array (height, width, depth)
                            and will typically be of dtype 'uint8' (theoretical support
                            for 'uint16' data exists but is not tested.) The depth will
                            be 1 for mono images and 3 for color images. The pixel order
                            is BGR and suitable for use with OpenCV but may need to be
                            converted before using with other image processing libraries.
        '''

        #  In this example we're simply going to display images as they are received.

        #  put some text on the image - first set the text color
        if (len(imageData['data'].shape) == 2):
            #  image is mono
            textColor = (200)
        else:
            textColor = (20,245,20)

        #  Starting with OpenCV4.9 you cannot write to the image data array directly
        #  so we'll make a copy of the image.
        displayImage = imageData['data'].copy()

        #  now add the text
        cv2.putText(displayImage,'Camera: ' + camera, (10,50),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, textColor, 4)
        cv2.putText(displayImage,'Label: ' + label, (10,100),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, textColor, 4)
        cv2.putText(displayImage,'Image number: ' + str(imageData['image_number']), (10,150),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, textColor, 4)
        cv2.putText(displayImage,'Filename: ' + imageData['filename'], (10,200),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, textColor, 4)
        cv2.putText(displayImage,'Time: ' + str(imageData['timestamp']), (10,250),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, textColor, 4)
        cv2.putText(displayImage,'Size: ' + str(imageData['width']) + ' x ' +
                str(imageData['height']), (10,300), cv2.FONT_HERSHEY_SIMPLEX, 1.5, textColor, 4)
        cv2.putText(displayImage,'Exposure: ' + str(imageData['exposure']) + ' us', (10,350),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, textColor, 4)
        cv2.putText(displayImage,'Gain: ' + str(imageData['gain']), (10,400),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, textColor, 4)

        #  and then show it
        cv2.imshow(camera, displayImage)

        #  Now request another image from this camera. A new image will be sent
        #  as soon as it is available. Back to back requests for the same camera
        #  will not queue but may cause images to be skipped, especially when
        #  requesting stereo pairs. Regarding stereo pairs, if you want paired
        #  images, you must call getImage and pass a list containing the camera
        #  names of the stereo cameras. When requested separately, the images
        #  may not be synced.
        #
        #  In this example we'll just request data for the camera we just displayed.
        #  The data will not really be synced, but we don't care. You can set the
        #  compressed keyword to True to encode the data as jpeg on the server to
        #  reduce bandwidth requirements at the expense of some CPU cycles. You
        #  can set the scale from 1-100 to scale the image before sending to
        #  further reduce bandwidth requirements. It is also worth scaling if you
        #  plan to scale as part of your image processing.
        self.client.getImage(camera, compressed=self.compressed, scale=self.scale,
                quality=self.quality)


    @QtCore.pyqtSlot(str, str, datetime.datetime, str)
    def GetSensorData(self, sensor_id, header, time, data):
        '''
        The GetSensorData slot is called when the client receives the response
        from a GetData request.
        '''

        self.logger.info("Sensor Data received: " + str(time) + " : " + sensor_id +
                " : "  + data)


    @QtCore.pyqtSlot(str, str, str, bool, str)
    def GetParamData(self, module, parameter, value, ok, err_string):
        '''
        The GetParamData slot is called when the client receives a response from
        either a getParameter or setParameter request
        '''

        self.logger.info("Get/SetParameter response received for module: " + module +
            " parameter: " + parameter + " value:" + value + " ok:" + str(ok))


    @QtCore.pyqtSlot()
    def connected(self):

        #  create a dict that will contain our image data
        self.images = {}

        #  create output windows for our cameras
        for cam in self.client.cameras:
            cv2.namedWindow(cam, cv2.WINDOW_NORMAL)

        self.logger.info("Connected to the server. Requesting images...")

        #  now request images from all of the cameras
        self.client.getImage(self.client.cameras.keys(), compressed=self.compressed,
                scale=self.scale, quality=self.quality)

        #  start the get sensor data timer
        self.getSensorTimer.start(self.SENSOR_DATA_INTERVAL)

        #  if we're senting test sensor data, start that timer here
        if self.txSensorData:
            self.sendSensorTimer.start(self.SENSOR_DATA_INTERVAL)


    @QtCore.pyqtSlot()
    def disconnected(self):
        '''
        disconnected is called when we disconnect from the server.
        '''
        self.logger.info("Disconnected from the server. Shutting down...")

        #  stop the timeout timer (if it was started)
        self.timeoutTimer.stop()

        #  clean up code goes here - in this example, we just need to destroy the OpenCV window
        cv2.destroyAllWindows()

        #  finally, exit the application
        QtCore.QCoreApplication.instance().quit()


    @QtCore.pyqtSlot(int, str)
    def clientError(self, errnum, errorText):
        '''
        the clientError slot is called when the client encounters an error
        '''

        if (errnum == 1):
            #  server closed connection - exit
            self.logger.error("Server closed connection. Exiting...")
        else:
            #  some other socket error
            self.logger.error("Socket Error: %s (%i). Exiting..." % (errorText, errnum))

        cv2.destroyAllWindows()
        QtCore.QCoreApplication.instance().quit()


    @QtCore.pyqtSlot()
    def shutdown(self):
        '''
        shutdown is called when the stopApp signal is received. It will disconnect from the server
        and also start a timeout timer
        '''

        #  stop the sensor data timers
        self.sendSensorTimer.stop()
        self.getSensorTimer.stop()

        #  tell the client to disconnect - if the client responds, the disconnected method will be called.
        self.client.disconnectFromServer()

        #  start a timeout timer that will force a shutdown if the server never responds
        self.timeoutTimer.start(self.SERVER_TIMEOUT)


    def ExternalStop(self):
        '''
        ExternalStop is called when one of the main thread exit handlers are called.
        It emits a stop signal that is then received by the QCoreApplication which then
        shuts everything down in the QCoreApplication thread.
        '''
        self.stopApp.emit(True)



def exitHandler(a,b=None):
    '''
    exitHandler is called when CTRL-c is pressed on Windows
    '''
    global ctrlc_pressed

    if not ctrlc_pressed:
        #  make sure we only act on the first ctrl-c press
        ctrlc_pressed = True
        print("CTRL-C detected. Shutting down...")
        clientApp.ExternalStop()

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
        clientApp.ExternalStop()

    return True


if __name__ == "__main__":

    # =====================================================================

    #  set to the server host IP - if you're running this on the same machine
    #  as CamtrawlAcquisition or ImageServerSim set it to the loopback address.
    #  Otherwise specify the IP of the computer running one of those applications.
    #host = '192.168.0.200'
    host = '127.0.0.1'

    #  set to the server port - the default port for the server is 7889 and it is
    #  set in the CamtrawlAcquisition .ini file.
    port = 7889

    #  Set Compressed to True to have the server encode the image data to JPEG
    #  before transmission. This significantly reduces bandwidth requirements
    #  while increasing CPU requirements.
    compressed = False

    #  Set scale to a value between 1-100. For values less than 100, the server
    #  will reduce the size of the images before sending.
    scale = 50

    #  If compressed is set to True, this specifies the JPEG quality value. Set
    #  it to a value between 50-95. If compressed is False, this value is ignored.
    quality=80


    #  set txSensorData to True to periodically send sensor data to the server.
    #  This data will be logged in the deployment data database and will also
    #  be available to other clients.
    txSensorData = True

    # =====================================================================


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

    #  create an instance of QCoreApplication and and instance of the example client application
    app = QtCore.QCoreApplication(sys.argv)
    clientApp = CamtrawlClientExample(host, port, compressed, scale, quality,
            txSensorData=txSensorData, parent=app)

    #  and start the event loop
    sys.exit(app.exec_())

