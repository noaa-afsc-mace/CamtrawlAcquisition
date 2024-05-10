#!/usr/bin/env python3
'''
ImageServerSim simulates the Camtrawl acquisition server.

The Camtrawl acquisition server uses a simple request/response interface
to provide image and sensor data over a network. The server holds copies
of the most recent images and sensor data acquired and sends them upon
request to clients.

If an image request is received after the last image was sent but before
a new image is available, the server will wait to send the image until
the new image is available. Only the most recent request is queued.
When multiple requests are sent for a single camera, only the newest
request will be responded to. In practice, you should not request a new
image until you have received the previous image.

See the BOTTOM of the script for options.

The following Python packages are required:

numpy
OpenCV 4.x
PyQt5
protobuf

This software was written and tested using the following software packages:

Python 3.7.2 [MSC v.1916 64 bit (AMD64)]
numpy 1.16.2
OpenCV 4.1.2
PyQt 5.12.1
protobuf 3.7.0


'''

from PyQt5 import QtCore, QtSql, QtNetwork
import logging
import datetime
import time
import os
import sys
import struct
import numpy
import cv2
import CamtrawlServer_pb2


class ImageServerSim(QtCore.QObject):

    #  define a signal to indicate an external shutdown command was received
    exShutdown = QtCore.pyqtSignal()

    def __init__(self, deploymentDir, updateRate, localAddress, localPort,
            repeat=False, startDelay=0, startFrame=1, parent=None):

        super(ImageServerSim, self).__init__(parent)

        #  set some initial properties
        self.deploymentDir = os.path.normpath(deploymentDir)
        self.dbFile = os.path.normpath(self.deploymentDir + os.sep + 'logs' +
                os.sep + 'CamTrawlMetadata.db3')
        self.startFrame = int(startFrame)
        if (self.startFrame <= 0):
            self.startFrame = 1
        self.frameNumber = self.startFrame
        if (updateRate <= 0):
            updateRate = 1
        self.updateRate = round(1000 / float(updateRate))
        startDelay = round(float(startDelay) * 1000)
        self.clients = {}
        self.images = {}
        self.cameras = {}
        self.imageData = {}
        self.maxImages = 0
        self.exShutdownRequested = False
        self.repeat = repeat
        self.localAddress = QtNetwork.QHostAddress(localAddress)
        self.localPort = int(localPort)
        self.thisTime = None
        self.lastTime = None
        self.timeScalar = 1.0

        #  The actual server is written in C++ and will send OpenCV cvMat types
        #  to describe the data type. Python OpenCV internally maps these to
        #  Numpy types so we have to map back to cvMat types before transmitting
        #  image data. This dictionary provides the mapping. The server only supports
        #  single and three channel 8 or 16 bit images.
        self.numpyToMatMap = {}
        self.numpyToMatMap[3] = {numpy.uint8:16, numpy.int8:17, numpy.uint16:18, numpy.int16:19}
        self.numpyToMatMap[1] = {numpy.uint8:0, numpy.int8:1, numpy.uint16:2, numpy.int16:3}

        #  create a instance of QSqlDatabase to access the image metadata file
        self.db = QtSql.QSqlDatabase.addDatabase("QSQLITE", 'ImageServerSim')

        #  create a logger
        logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.DEBUG)

        #  connect our external shutdown request signal
        self.exShutdown.connect(self.stopServer)

        #  create a timer to update the image data
        self.updateTimer = QtCore.QTimer(self)
        self.updateTimer.timeout.connect(self.updateImages)
        #  set the initial interval to the startDelay if set
        if (startDelay > 0):
            self.updateTimer.setInterval(startDelay)
        else:
            self.updateTimer.setInterval(self.updateRate)

        #  set an event to start the server once the event loop starts
        timer = QtCore.QTimer(self)
        timer.timeout.connect(self.startServer)
        timer.setSingleShot(True)
        timer.start(0)


    @QtCore.pyqtSlot()
    def startServer(self):

        logging.info('Starting ImageServerSim with deployment ' + self.deploymentDir)
        logging.info('Update interval: ' + str(self.updateRate) + " ms")

        #  open the deployment database
        logging.info('Opening deployment database ' + self.dbFile)
        self.db.setDatabaseName(self.dbFile)
        if not self.db.open():
            logging.critical('Error opening SQLite database file ' + self.dbFile +'. Unable to continue.')
            QtCore.QCoreApplication.instance().quit()
            return

        #  query the camera table to get the camera names and labels
        self.cameras = {}
        self.images = {}
        sql = ("SELECT camera, label, rotation FROM cameras")
        query = QtSql.QSqlQuery(sql, self.db)
        while query.next():
            self.cameras[query.value(0)] = {'label':query.value(1), 'rotation':query.value(2),
                    'nimages':0}
            self.images[query.value(0)] = {}

        # create a dict that maps image number to camera to image file
        sql = ("SELECT number, camera, time, name FROM images ORDER BY number")
        query = QtSql.QSqlQuery(sql, self.db)
        while query.next():
            #  add this image to the images dict
            self.images[query.value(1)][int(query.value(0))] = [query.value(2),query.value(3)]
            #  update the per-camera nimages value
            self.cameras[query.value(1)]['nimages'] += 1



        #  report what we found and determine the max image count
        self.maxImages = 0
        for cam in self.cameras:
            logging.info("Found camera " + cam + " labeled '" + self.cameras[cam]['label'] +
                    "' with " + str(self.cameras[cam]['nimages']) + " images.")
            if (self.cameras[cam]['nimages'] > self.maxImages):
                self.maxImages = self.cameras[cam]['nimages']

        #  start the QTcpServer instance
        self.tcpServer = QtNetwork.QTcpServer(self)
        self.tcpServer.newConnection.connect(self.clientConnect)
        if (not self.tcpServer.listen(self.localAddress, self.localPort)):
            logging.critical("Unable to start server: " + self.tcpServer.errorString())
            QtCore.QCoreApplication.instance().quit()
            return
        logging.info("Server started at " + self.localAddress.toString() + ":" + str(self.localPort))

        #  and start the image update timer
        self.firstUpdate = True
        self.updateTimer.start()


    @QtCore.pyqtSlot()
    def clientReadyRead(self):

        #  get the socket
        thisSocket = self.sender()

        #  while data is available
        while (thisSocket.bytesAvailable() > 0):
            #  append this data to the receive buffer
            self.clients[thisSocket]['buffer'].extend(thisSocket.readAll())

            #  assemble and process datagrams - datagrams are in the form
            #    [size - (uint32) 4 bytes][data - protobuff size bytes]

            #  check if we have enough data to do anything - outer while loop
            #  ensures that we process all complete datagrams
            while ((self.clients[thisSocket]['datagramSize'] == 0 and
                len(self.clients[thisSocket]['buffer']) >= 4) or
                (self.clients[thisSocket]['datagramSize'] > 0 and
                len(self.clients[thisSocket]['buffer']) >=
                self.clients[thisSocket]['datagramSize'])):

                #  check if we need to unpack the datagram length
                if (self.clients[thisSocket]['datagramSize'] == 0 and
                    len(self.clients[thisSocket]['buffer']) >= 4):

                    #  we have rx'd at least 4 bytes, unpack the datagram length
                    #  datagram length is big endian uint32
                    self.clients[thisSocket]['datagramSize'] = \
                        struct.unpack('!I', self.clients[thisSocket]['buffer'][0:4])[0]

                    #  delete the len bytes from the buffer
                    del self.clients[thisSocket]['buffer'][0:4]

                #  check if we have at least 1 full datagram
                if (self.clients[thisSocket]['datagramSize'] > 0 and
                    len(self.clients[thisSocket]['buffer']) >=
                    self.clients[thisSocket]['datagramSize']):

                    #  parse the datagram to get type
                    request = CamtrawlServer_pb2.msg()
                    request.ParseFromString(self.clients[thisSocket]['buffer']
                        [0:self.clients[thisSocket]['datagramSize']])

                    #  parse the data based on the datagram type
                    if (request.type == CamtrawlServer_pb2.msg.msgType.Value('GETIMAGE')):
                        imgRequest = CamtrawlServer_pb2.getImage()
                        imgRequest.ParseFromString(request.data)

                        #  a getImage request can contain multiple cameras - we have to iterate
                        #  thru them, store the request, and check if there are any pending requests
                        #  (allSent == False when a request is pending)
                        allSent = True
                        for cam in imgRequest.cameras:
                            #  check if the requested camera exists - if so set/update the request
                            if (cam in self.cameras):
                                self.clients[thisSocket]['requestState'][cam]['currentRequest'] = imgRequest

                                #  check if all cameras have sent
                                allSent &= self.clients[thisSocket]['requestState'][cam]['sentResponse']

                        #  If we have a multiple camera request, we need to make sure the images are
                        #  synced. If *all* cameras have been sent, then we wait for the next image
                        #  refresh. If no or some camera images have been sent we have to unset all
                        #  sendResponse states and will send all of the images immediately.
                        if ((len(imgRequest.cameras) > 1) and not allSent):
                            #  not all camera images have been sent and we have a multi-image request.
                            #  Unset all of the sentResponse states.
                            for cam in imgRequest.cameras:
                                self.clients[thisSocket]['requestState'][cam]['sentResponse'] = False

                        #  and check if we have a fresh image to send
                        if (not allSent):
                            #  we haven't sent the latest image - send it now
                            self.sendImage(imgRequest, thisSocket)

                    elif (request.type == CamtrawlServer_pb2.msg.msgType.Value('GETCAMERAINFO')):
                        #  build a response - create a cameraInfo protobuf
                        cameraInfo = CamtrawlServer_pb2.cameraInfo()
                        for cam in self.cameras:
                            #  add a new camera info entry to it
                            c = cameraInfo.cameras.add()
                            #  and populate the fields
                            c.name = cam
                            c.label = self.cameras[cam]['label']

                        #  build the response
                        response = CamtrawlServer_pb2.msg()
                        response.type = CamtrawlServer_pb2.msg.msgType.Value('GETCAMERAINFO')
                        response.data = cameraInfo.SerializeToString()

                        #  and send it
                        self.sendResponse(response.SerializeToString(), thisSocket)


                    #  the following message types are not implemented on the test server
                    elif (request.type == CamtrawlServer_pb2.msg.msgType.Value('GETSENSOR')):
                        logging.info("GETSENSOR request received - This message type is not implemented.")


                    elif (request.type == CamtrawlServer_pb2.msg.msgType.Value('SETDATA')):
                        setData = CamtrawlServer_pb2.setSensorData()
                        setData.ParseFromString(request.data)

                        if (self.thisTime):
                            elapsedSeconds = (time.time() - self.elapsedTime) * self.timeScaler
                            currentTime = self.thisTime + datetime.timedelta(seconds=elapsedSeconds)
                            timeString = (currentTime.strftime("%Y-%m-%d %H:%M:%S") +
                                    str(round(currentTime.microsecond/1000)))

                            for sensor in setData.sensors:

                                if (sensor.type == CamtrawlServer_pb2.setSensorData.sensorType.Value('ASYNC')):

                                    sql = ("INSERT INTO async_data VALUES('" + timeString + "','" + sensor.id + "','" +
                                            sensor.header + "','" + sensor.data + "')")
                                    query = QtSql.QSqlQuery(sql, self.db)
                                    query.exec_()

                                elif (sensor.type == CamtrawlServer_pb2.setSensorData.sensorType.Value('SYNC')):
                                    #  setting of synced data after the data is recorded is beyond the
                                    #  scope of this test server.
                                    logging.error("SETDATA request received - Setting synced sensor data not supported on test server.")


                    elif (request.type == CamtrawlServer_pb2.msg.msgType.Value('GETPARAMETER')):
                        logging.info("GETPARAMETER request received - This message type is not implemented.")


                    elif (request.type == CamtrawlServer_pb2.msg.msgType.Value('SETPARAMETER')):
                        logging.info("SETPARAMETER request received - This message type is not implemented.")


                    elif (request.type == CamtrawlServer_pb2.msg.msgType.Value('GETSENSORINFO')):
                        logging.info("GETSENSORINFO request received - This message type is not implemented.")


                    #  and finally, remove this datagram from the buffer and reset the
                    #  datagramSize to 0 so we're ready to process the next datagram.
                    del self.clients[thisSocket]['buffer'][0:self.clients[thisSocket]['datagramSize']]
                    self.clients[thisSocket]['datagramSize'] = 0


    def sendImage(self, imgRequest, clientSocket):
        '''
        sendImage constructs the image response datagram based on the request type
        and sends the data to the client connected to clientSocket
        '''

        for cam in imgRequest.cameras:
            #  check to make sure the camera exists and that we have data for that camera
            if (cam in self.images and cam in self.imageData):

                #  check if we're scaling the image
                if (imgRequest.scale != 100):
                    #  we are scaling - compute the scaled width and height
                    width = int(self.imageData[cam].shape[1] * (imgRequest.scale / 100.))
                    height = int(self.imageData[cam].shape[0] * (imgRequest.scale / 100.))

                    #  and then scale the image
                    imageData = cv2.resize(self.imageData[cam], (width, height))
                else:
                    #  no scaling - send original image
                    imageData = self.imageData[cam]

                if (imgRequest.type == CamtrawlServer_pb2.getImage.imageType.Value('CVMAT')):

                    #  build the cvMat payload object
                    cvMat = CamtrawlServer_pb2.cvMat()
                    cvMat.camera = cam
                    cvMat.image_number = self.frameNumber
                    cvMat.rows = imageData.shape[0]
                    cvMat.cols = imageData.shape[1]
                    if (len(imageData.shape) == 3):
                        #  this is a multi-channel (color) image
                        cvMat.depth = imageData.shape[2]
                        cvMat.elt_type = self.numpyToMatMap[3][imageData.dtype.type]
                    else:
                        #  this is a single channel (mono) image
                        cvMat.depth = 1
                        cvMat.elt_type = self.numpyToMatMap[1][imageData.dtype.type]
                    cvMat.elt_size = imageData.dtype.itemsize
                    cvMat.mat_data = imageData.tobytes()

                    #  build the response
                    response = CamtrawlServer_pb2.msg()
                    response.type = CamtrawlServer_pb2.msg.msgType.Value('CVMATDATA')
                    response.data = cvMat.SerializeToString()

                    #  and send
                    self.sendResponse(response.SerializeToString(), clientSocket)

                elif (imgRequest.type == CamtrawlServer_pb2.getImage.imageType.Value('JPEG')):

                    #  encode the image as a jpeg
                    ok, encodedImage = cv2.imencode(".jpg", imageData,
                            (int(cv2.IMWRITE_JPEG_QUALITY), imgRequest.quality))

                    #  and construct the jpeg payload
                    jpeg = CamtrawlServer_pb2.jpeg()
                    jpeg.camera = cam
                    jpeg.image_number = self.frameNumber
                    jpeg.width = imageData.shape[1]
                    jpeg.height = imageData.shape[0]
                    jpeg.jpg_data = encodedImage.tobytes()

                    #  build the response
                    response = CamtrawlServer_pb2.msg()
                    response.type = CamtrawlServer_pb2.msg.msgType.Value('JPEGDATA')
                    response.data = jpeg.SerializeToString()

                    #  and send
                    self.sendResponse(response.SerializeToString(), clientSocket)

                #  update the request/response states for this socket/camera
                self.clients[clientSocket]['requestState'][cam]['currentRequest'] = None
                self.clients[clientSocket]['requestState'][cam]['sentResponse'] = True


    def sendResponse(self, message, thisSocket):
        '''
        sendResponse sends the length of the response datagram along with
        the serialized reponse contained in the provided message to the
        provided socket.
        '''

        #  write the message length as big endian uint32
        thisSocket.write(struct.pack('!I', len(message)))

        #  write the message data
        bytesWritten = thisSocket.write(message)

        #  report if somehow we didn't write the whole message.
        if (bytesWritten != len(message)):
            logging.error("Error writing response to socket. Encoded bytes=" +
                    str(len(message)) + " Sent bytes=" + str(bytesWritten))


    @QtCore.pyqtSlot()
    def clientConnect(self):
        '''
        slot called when client connects to the image server
        '''

        #  get the socket for this connection
        thisSocket = self.tcpServer.nextPendingConnection()

        #  get the remote address and port as strings
        sockAddress = thisSocket.peerAddress().toString()
        sockPort = str(thisSocket.peerPort())

        #  connect some signals
        thisSocket.readyRead.connect(self.clientReadyRead)
        thisSocket.disconnected.connect(self.clientDisconnect)

        #  set the TCP_NODELAY socket option to reduce latency
        thisSocket.setSocketOption(QtNetwork.QAbstractSocket.LowDelayOption, 1)

        #  add this client to our dict of clients - first build a dict that we use to
        #  track image request and response state by camera for each socket.
        requestState = {}
        for cam in self.cameras:
            requestState[cam] = {'currentRequest':None, 'sentResponse':False}
        #  then add the dict keyed by socket with the buffer, expected datagram size,
        #  and request state keys
        self.clients[thisSocket] = {'buffer':bytearray(), 'datagramSize':0,
                'requestState':requestState}

        logging.debug("Client connected from " + sockAddress + ":" + sockPort)


    @QtCore.pyqtSlot()
    def clientDisconnect(self):
        '''
        slot called when a client disconnects from the image server
        '''
        #  get the socket
        thisSocket = self.sender()

        #  get the remote address and port as strings
        sockAddress = thisSocket.peerAddress().toString()
        sockPort = str(thisSocket.peerPort())

        #  remove socket from our list of clients and set it to delete itself
        #  later.
        del self.clients[thisSocket]
        thisSocket.deleteLater()

        logging.debug("Client disconnected from " + sockAddress + ":" + sockPort)


    @QtCore.pyqtSlot()
    def updateImages(self):
        '''
        updateImages is called by the image update timer. It loads the next image
        in the dataset for each camera. It also checks if there are any pending
        image requests and services those requests if needed.
        '''

        #  make sure the interval is updated if we had an initial delay
        if (self.firstUpdate == True):
            self.updateTimer.setInterval(self.updateRate)
            self.firstUpdate = False

        #  load the next image for each camera
        updatedTime = False
        for cam in self.cameras:

            #  generate the path for this camera's image
            filepath = self.deploymentDir + os.sep + "images" + os.sep + cam + os.sep


            try:
                if (not updatedTime):
                    #  keep track of the current replay time and time scalar
                    self.lastTime = self.thisTime
                    self.thisTime = datetime.datetime.strptime(self.images[cam][self.frameNumber][0],
                            '%Y-%m-%d %H:%M:%S.%f')
                    self.elapsedTime = time.time()

                    if (self.lastTime):
                        recordedInterval = self.thisTime - self.lastTime
                        self.timeScaler = (recordedInterval.total_seconds() * 1000) / float(self.updateRate)

                    updatedTime = True

                #  get the image name for this camera/frame
                imageFile = self.images[cam][self.frameNumber][1] + '.jpg'
            except:
                #  frame is not available, camera must have dropped the image
                #  during acquisition.
                logging.info("Camera " + cam + " is missing image number " +
                        str(self.frameNumber) + ".")

            try:
                #  read the image data into our "source" array which stores the original unmodified data
                self.imageData[cam] = cv2.imread(filepath + imageFile, cv2.IMREAD_UNCHANGED)

                #  now that we have a fresh image, update the 'sentResponse' state for all clients
                #  and check if we have any pending requests that need to be sent
                for thisSocket in self.clients:
                    #  unset sentReponse
                    self.clients[thisSocket]['requestState'][cam]['sentResponse'] = False

                    #  check if we have a request and send if so
                    thisRequest = self.clients[thisSocket]['requestState'][cam]['currentRequest']
                    if (thisRequest):
                        self.sendImage(thisRequest, thisSocket)

            except:
                #  there was an issue loading the file
                logging.error("Unable to open image file " + filepath + imageFile)

        #  increment our frame counter
        self.frameNumber += 1

        #  check if we're at the end of our list of images
        if (self.frameNumber >= self.maxImages):
            if self.repeat:
                self.frameNumber = self.startFrame
                logging.info("All images have been served up - Repeat = True - Restarting with image number " +
                        str(self.startFrame) + ".")
            else:
                #  we're not repeating and we've worked thru all images
                #  so we'll shut down and exit
                logging.info("All images have been served up - Repeat = False - Shutting down image server.")
                self.stopServer()
                QtCore.QCoreApplication.instance().quit()


    @QtCore.pyqtSlot()
    def stopServer(self):

        #  stop the update timer
        logging.debug("Stopping image update timer...")
        self.updateTimer.stop()

        #  close the server
        logging.debug("Closing TCP server...")
        self.tcpServer.close()

        #  close the metadata database
        self.db.close()

        #  reset dicts
        self.cameras = {}
        self.images = {}
        self.imageData = {}
        self.clients = {}
        self.thisTime = None
        self.lastTime = None
        self.timeScalar = 1.0

        logging.debug("Done.")

        #  if we've been told to shut down from an external signal, exit the application.
        if (self.exShutdownRequested):
            QtCore.QCoreApplication.instance().quit()


    def emitShutdown(self):
        '''
        emitShutdown emits the shutdown signal which will shut down the server.
        We can call this method from outside the event loop context to shut
        the server down without making Qt angry because the actual shutdown
        happenes within the event loop.
        '''
        self.exShutdownRequested = True
        self.exShutdown.emit()


def exitHandler(a,b=None):
    '''
    exitHandler is called when CTRL-c is pressed within the console
    running the server. Because this executes outside the event loop
    context we can't simply call stopServer here so we call the
    emitShutdown method which puts an event on the event queue and
    executes it in the server's thread making everyone happy.
    '''
    imageServer.emitShutdown()
    return True



if __name__ == "__main__":

    #  path to the deployment folder
    deploymentDir = 'C:/Users/Rick/Desktop/FernCal_7-17/D20200717-T212020'

    #  server update rate in images per second
    updateRate = 2

    #  server local address and port - for testing this will almost always be
    #  the loopback interface (127.0.0.1).
    localAddress = '127.0.0.1'

    #  server port - The default port for the Camtrawl server is 7889
    localPort = 7889

    #  delay in seconds before the server starts updating images
    startDelay = 0

    #  set repeat to True to loop back to the first image after all images
    #  in a deployment are read
    repeat = True

    #  set start frame to the image number the server should start with.
    startFrame = 1


    #  create an application instance
    app = QtCore.QCoreApplication(sys.argv)

    #  create the main application window
    imageServer = ImageServerSim(deploymentDir, updateRate, localAddress, localPort,
            repeat=repeat, startDelay=startDelay, startFrame=startFrame)

    #  install a handler to catch ctrl-C on windows
    if sys.platform == "win32":
        import win32api
        win32api.SetConsoleCtrlHandler(exitHandler, True)

    #  start event processing
    sys.exit(app.exec_())

