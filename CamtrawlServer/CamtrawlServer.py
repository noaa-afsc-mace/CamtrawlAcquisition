#!/usr/bin/env python3
'''
CamtrawlServer provides a simple server for Camtrawl system telemetry over TCP/IP.

The following Python packages are required:

OpenCV 4.x
PyQt5
protobuf

'''

from PyQt5 import QtCore, QtNetwork
import logging
import datetime
import struct
import cv2
from . import CamtrawlServer_pb2


class CamtrawlServer(QtCore.QObject):
    '''CamtrawlServer provides a simple server for Camtrawl
    system telemetry over TCP/IP.
    '''

    #  define CamtrawlServer signals
    sensorData = QtCore.pyqtSignal(str, str, datetime.datetime, str)
    getParameterRequest = QtCore.pyqtSignal(str, str)
    setParameterRequest = QtCore.pyqtSignal(str, str, str)
    serverClosed = QtCore.pyqtSignal()
    error = QtCore.pyqtSignal(str)


    def __init__(self, local_address='127.0.0.1', local_port=7889,
            cameras={}, parent=None):

        super(CamtrawlServer, self).__init__(parent)

        #  set some initial properties
        self.localAddress = QtNetwork.QHostAddress(local_address)
        self.localPort = int(local_port)

        #  create a dict keyed by camera name with values that are dicts
        #  that contain the 'label' key. This is not required as the
        #  server will add cameras to this dict when it receives an image
        #  from them, but pre-populating this dict allows the server to
        #  return data from a GETCAMERAINFO request before any images
        #  are acquired.
        self.cameras = cameras

        self.logger = logging.getLogger(__name__)


    @QtCore.pyqtSlot()
    def startServer(self):
        '''
        start_server, er, starts the server
        '''

        #  start the QTcpServer instance
        self.tcpServer = QtNetwork.QTcpServer(self)
        self.tcpServer.newConnection.connect(self.clientConnect)
        if (not self.tcpServer.listen(self.localAddress, self.localPort)):
            self.logger.critical("Unable to start server: " + self.tcpServer.errorString())
            return False

        self.logger.info("Server started at " + self.localAddress.toString() + ":" + str(self.localPort))

        # reset the clients and sensor data dicts
        self.clients = {}
        self.sensorDataDict = {}

        return True


    @QtCore.pyqtSlot()
    def clientReadyRead(self):
        '''
        clientReadyRead is called by QTcpSocket when data has been received.
        Data from the socket is buffered until a full datagram is received.
        When a complete datagram has been received, it is processed and the
        appropriate action is taken.
        '''

        #  get the socket object
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
                    request.ParseFromString(bytes(self.clients[thisSocket]['buffer']
                        [0:self.clients[thisSocket]['datagramSize']]))

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

                    #  process the get sensor data request
                    elif (request.type == CamtrawlServer_pb2.msg.msgType.Value('GETSENSOR')):
                        dataRequest = CamtrawlServer_pb2.getSensorData()
                        dataRequest.ParseFromString(request.data)

                        #  build a response - create a sensorData protobuf
                        sensorData = CamtrawlServer_pb2.sensorData()

                        #  if the sensor ID is 'None' we return all sensor data
                        if dataRequest.id.lower() == 'none':
                            for id in self.sensorDataDict:
                                for header in self.sensorDataDict[id]:
                                    s = sensorData.sensors.add()
                                    s.id = id
                                    s.header = header
                                    s.timestamp = self.sensorDataDict[id][header]['time'].timestamp()
                                    s.data = self.sensorDataDict[id][header]['data']

                        #  otherwise we only return data from the specified sensor
                        else:
                            if dataRequest.id in self.sensorDataDict:
                                for header in self.sensorDataDict[dataRequest.id]:
                                    s = sensorData.sensors.add()
                                    s.id = dataRequest.id
                                    s.header = header
                                    s.timestamp = self.sensorDataDict[dataRequest.id]['time'].timestamp()
                                    s.data = self.sensorDataDict[dataRequest.id]['data']

                        #  build the response
                        response = CamtrawlServer_pb2.msg()
                        response.type = CamtrawlServer_pb2.msg.msgType.Value('SENSORDATA')
                        response.data = sensorData.SerializeToString()

                        #  and send it
                        self.sendResponse(response.SerializeToString(), thisSocket)

                    #  process the set sensor data request
                    elif (request.type == CamtrawlServer_pb2.msg.msgType.Value('SETSENSOR')):
                        setData = CamtrawlServer_pb2.setSensorData()
                        setData.ParseFromString(request.data)

                        for sensor in setData.sensors:
                            #  emit one of the sensorData signals for this sensor+header

                            #  convert the timestamp to a datetime object
                            time_obj = datetime.datetime.fromtimestamp(sensor.timestamp)

                            #  emit the sensor data signal
                            self.sensorData.emit(sensor.id, sensor.header, time_obj, sensor.data)
                            self.logger.debug("setSensorData request received: " + sensor.id + "," +
                                    sensor.header + "," + sensor.data)


                    #  process a get parameter request
                    elif (request.type == CamtrawlServer_pb2.msg.msgType.Value('GETPARAMETER')):
                        #  decode the getParameter proto
                        getParam = CamtrawlServer_pb2.getParameter()
                        getParam.ParseFromString(request.data)

                        #  and emit the getParameterRequest signal
                        self.getParameterRequest.emit(getParam.module, getParam.parameter)
                        self.logger.debug("getParameter request received: " + getParam.module + "," + getParam.parameter)

                    #  process a set parameter request
                    elif (request.type == CamtrawlServer_pb2.msg.msgType.Value('SETPARAMETER')):
                        #  decode the setParameter proto
                        setParam = CamtrawlServer_pb2.setParameter()
                        setParam.ParseFromString(request.data)

                        #  and emit the setParameterRequest signal
                        self.setParameterRequest.emit(setParam.module, setParam.parameter, setParam.value)
                        self.logger.debug("setParameter request received: " + setParam.module + "," + setParam.parameter
                                      + "," + setParam.value)


                    #  process a get sensor info request
                    elif (request.type == CamtrawlServer_pb2.msg.msgType.Value('GETSENSORINFO')):
                        self.logger.info("GETSENSORINFO request received - This message type is not implemented yet.")


                    #  and finally, remove this datagram from the buffer and reset the
                    #  datagramSize to 0 so we're ready to process the next datagram.
                    del self.clients[thisSocket]['buffer'][0:self.clients[thisSocket]['datagramSize']]
                    self.clients[thisSocket]['datagramSize'] = 0


    def sendImage(self, imgRequest, clientSocket):
        '''
        sendImage constructs the image response datagram based on the request type
        and sends the data to the client connected to clientSocket.
        '''

        for cam in imgRequest.cameras:
            #  check to make sure the camera exists
            if cam in self.cameras and 'image_data' in self.cameras[cam]:

                #  get this camera's image data
                image_data = self.cameras[cam]['image_data']

                #  check if we're scaling the image
                if (imgRequest.scale != 100):
                    #  we are scaling - compute the scaled width and height
                    image_data['width'] = int(image_data['data'].shape[1] *
                            (imgRequest.scale / 100.))
                    image_data['height'] = int(image_data['data'].shape[0] *
                            (imgRequest.scale / 100.))

                    #  and then scale the image
                    data = cv2.resize(image_data['data'],
                            (image_data['width'], image_data['height']))

                else:
                    #  no scaling - send original image
                    data = image_data['data']

                #  build the response based on the request image type
                if (imgRequest.type == CamtrawlServer_pb2.getImage.imageType.Value('CVMAT')):

                    #  build the cvMat payload object
                    cvMat = CamtrawlServer_pb2.cvMat()
                    cvMat.camera = cam
                    cvMat.image_number = image_data['image_number']
                    cvMat.exposure = image_data['exposure']
                    cvMat.gain = image_data['gain']
                    cvMat.filename = image_data['filename']
                    cvMat.timestamp = image_data['timestamp'].timestamp()
                    cvMat.rows = data.shape[0]
                    cvMat.cols = data.shape[1]
                    cvMat.label = self.cameras[cam]['label']

                    if (len(data.shape) == 3):
                        #  this is a multi-channel (color) image
                        cvMat.depth = data.shape[2]
                    else:
                        #  this is a single channel (mono) image
                        cvMat.depth = 1
                    cvMat.elt_type = data.dtype.str
                    cvMat.elt_size = data.dtype.itemsize
                    cvMat.mat_data = data.tobytes()

                    #  build the response
                    response = CamtrawlServer_pb2.msg()
                    response.type = CamtrawlServer_pb2.msg.msgType.Value('CVMATDATA')
                    response.data = cvMat.SerializeToString()

                    #  and send
                    self.sendResponse(response.SerializeToString(), clientSocket)

                elif (imgRequest.type == CamtrawlServer_pb2.getImage.imageType.Value('JPEG')):

                    #  encode the image as a jpeg
                    ok, encodedImage = cv2.imencode(".jpg", data,
                            (int(cv2.IMWRITE_JPEG_QUALITY), imgRequest.quality))

                    #  and construct the jpeg payload
                    jpeg = CamtrawlServer_pb2.jpeg()
                    jpeg.camera = cam
                    jpeg.image_number = image_data['image_number']
                    jpeg.timestamp = image_data['timestamp'].timestamp()
                    jpeg.width = data.shape[1]
                    jpeg.height = data.shape[0]
                    jpeg.exposure = image_data['exposure']
                    jpeg.gain = image_data['gain']
                    jpeg.filename = image_data['filename']
                    jpeg.label = self.cameras[cam]['label']
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
            self.logger.error("Error writing response to socket. Encoded bytes=" +
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

        self.logger.debug("Client connected from " + sockAddress + ":" + sockPort)


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

        self.logger.debug("Client disconnected from " + sockAddress + ":" + sockPort)


    @QtCore.pyqtSlot(str, str, dict)
    def newImageAvailable(self, camera_name, label, image_data):
        '''
        The newImageAvailable slot should be connected to your image data source signal.
        Usually this will be a CamTrawl camera object.

        camera_name is the camera name as string
        label is the camera's label as string
        image_data is a dict containing image data with the following keys:

        image_data['data'] - image data as OpenCV numpy array
        image_data['ok'] - True if image was received, False if there was a problem
        image_data['exposure'] - camera exposure
        image_data['gain'] - camera gain
        image_data['height'] - image height
        image_data['width'] - image width
        image_data['timestamp'] - trigger timestamp
        image_data['filename'] - image filename (if any)
        image_data['image_number'] - global image number

        '''

        # check if we have received an image from this camera before
        if camera_name not in self.cameras:
            # we have not - add it to our camera dict
            self.cameras[camera_name] = {}

        # update this camera with this latest data
        self.cameras[camera_name].update({'label':label, 'image_data':image_data})

        #  now that we have a fresh image, update the 'sentResponse' state for all clients
        #  and check if we have any pending requests that need to be sent
        for thisSocket in self.clients:

            # first check if this client has seen this camera before
            if camera_name not in self.clients[thisSocket]['requestState']:
                # nope - create a new requestState dict for this camera
                self.clients[thisSocket]['requestState'][camera_name] = {'currentRequest':None,
                    'sentResponse':True}

            #  unset sentReponse
            self.clients[thisSocket]['requestState'][camera_name]['sentResponse'] = False

            #  check if we have a request and send if so
            thisRequest = self.clients[thisSocket]['requestState'][camera_name]['currentRequest']
            if (thisRequest):
                self.sendImage(thisRequest, thisSocket)


    @QtCore.pyqtSlot()
    def stopServer(self):

        #  close the server
        self.logger.debug("Closing CamTrawl server...")
        self.tcpServer.close()

        self.cameras = {}
        self.clients = {}
        self.sensorDataDict = {}

        self.serverClosed.emit()


    @QtCore.pyqtSlot(str, str, datetime.datetime, str)
    def sensorDataAvailable(self, id, header, time_obj, data):
        '''The sensorDataAvailable slot buffers the most recent sensor data by
        sensor ID and header.
        '''
        if id not in self.sensorDataDict:
            self.sensorDataDict[id] = {}

        self.sensorDataDict[id][header] = {'time':time_obj, 'data':data}


    @QtCore.pyqtSlot(str, str, str, bool, str)
    def parameterDataAvailable(self, module, parameter, value, ok, err_string):
        '''
        The parameterDataAvailable slot should be connected to the parameterChanged
        signal of components that can be queried/controlled by the CamtrawlServer.

        Parameter changes are broadcast to all clients
        module, parameter_string, value, ok, error_string
        parameterChanged = QtCore.pyqtSignal(str, str, str, str)
        '''

        if ok:
            ok = 1
        else:
            ok = 0

        #  create a parameterData protobuf and populate
        paramData = CamtrawlServer_pb2.parameterData()
        paramData.module = module
        paramData.parameter = parameter
        paramData.value = value
        paramData.ok = ok
        paramData.error_string = err_string

        #  create a message to wrap our parameterData message
        response = CamtrawlServer_pb2.msg()
        response.type = CamtrawlServer_pb2.msg.msgType.Value('PARAMDATA')
        response.data = paramData.SerializeToString()

        #  broadcast parameter changes to all clients
        response = response.SerializeToString()
        for thisSocket in self.clients:
            self.sendResponse(response, thisSocket)


#  the code below could be used if we need to implement password access to the server.
#
#  from https://stackoverflow.com/questions/9594125/salt-and-hash-a-password-in-python/56915300#56915300
#
#
#import os
#import hashlib
#import hmac
#def hash_new_password(password: str) -> Tuple[bytes, bytes]:
#    """
#    Hash the provided password with a randomly-generated salt and return the
#    salt and hash to store in the database.
#    """
#    salt = os.urandom(16)
#    pw_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
#    return salt, pw_hash
#
#
#def is_correct_password(salt: bytes, pw_hash: bytes, password: str) -> bool:
#    """
#    Given a previously-stored salt and hash, and a password provided by a user
#    trying to log in, check whether the password is correct.
#    """
#    return hmac.compare_digest(
#        pw_hash,
#        hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
#    )
#
