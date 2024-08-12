'''
CamtrawlClient.py

CamtrawlClient provides a client side interface for the CamTrawl
server and is used to monitor and control a CamTrawl system over a
network connection.


"Public" methods

connectToServer(host address, port)
disconnectFromServer()
getImage(camera, compressed=False, scale=100, quality=80)
setData(sensorID, data, asyncSensor=False, time=None)
getData(sensorID=None)
getParameter(module, parameter)
setParameter(module, parameter, value)


Qt Signals:

connected()

The connected signal is emitted when the client successfully connects
with the server. Any calls to getImage(), getData(), etc. before the
client is connected will be ignored so you must wait until this signal
is received before sending and requests.


disconnected()

The disconnected signal is emitted when the client disconnects from the
server. This can be after a call to disconnectFromServer() or if the server
disconnects the client.


error([int] errorNumber, [string] errorString)

The error signal is emitted when there is an error. Usually the errors will
be related to the socket (unable to connect/timeout etc.)

    errorNumber     - an int containing the errorNumber. When the error is
                      socket related, it will be the QAbstractSocket::SocketError
                      value. Non-socket errors will have a made up error number
                      which really isn't thought out well.

    errorString     - a string containing a message that descripes the error.


imageData([string] camera, [str] label, [dict] imageData)

The imageData signal is emitted when an image from a camera has been
received and decoded. The signal will be emitted once for each camera
you request an image from.

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



dataReceived([string] sensorID, [dict] sensorData)

The dataReceived signal is emitted when data from a getData() request has been
received an decoded.



The following Python packages are required:

numpy
OpenCV 4.x
PyQt5
protobuf


'''

import datetime
import struct
import numpy
import cv2
from . import CamtrawlServer_pb2
from PyQt5 import QtNetwork, QtCore


class CamtrawlClient(QtCore.QObject):
    """
    CamtrawlClient provides a client side interface for the CamTrawl
    server and is used to monitor and control a CamTrawl system over a
    network connection.
    """

    #  define CamtrawlClient signals
    imageData = QtCore.pyqtSignal(str, str, dict)
    sensorInfo = QtCore.pyqtSignal(dict)
    syncSensorData = QtCore.pyqtSignal(str, str, datetime.datetime, str)
    asyncSensorData = QtCore.pyqtSignal(str, str, datetime.datetime, str)
    dataRequestComplete = QtCore.pyqtSignal()
    parameterData = QtCore.pyqtSignal(str, str, str, bool, str)
    connected = QtCore.pyqtSignal()
    disconnected = QtCore.pyqtSignal()
    error = QtCore.pyqtSignal(int,str)

    def __init__(self):
        super(CamtrawlClient, self).__init__()

        #  create our client socket
        self.socket = QtNetwork.QTcpSocket(self)
        self.socket.readyRead.connect(self.socketReadyRead)
        self.socket.error.connect(self.socketError)

        #  create the receive buffer and other bookkeeping variables
        self.datagramBuffer = QtCore.QByteArray()
        self.thisDatagramSize = 0
        self.cameras = {}
        self.isConnected = False


    def getImage(self, camera, compressed=False, scale=100, quality=80):
        '''
        getImage sends an image request to the server for the specified camera.

        You can request an image from a single camera, or from multiple cameras at the
        same time. If you are requesting images from multiple cameras and need your images
        to be synced (same frame number for each image) you must provide the camera
        names as a list. While you can request images from multiple cameras independently
        (i.e. call getImage twice, passing each camera name as a string) you may or
        may not get synced images.

        The server stores the most recently acquired image from each camera and sends it
        when it receives a request. Once sent, it will queue a request from the same client
        for the same camera until a new image is acquired by the camera at which time it
        will send it. In practice this means that you can design consumers that follow a
        simple pattern where you request an image then enter a process->request loop.

        There is 1 case where the request queuing behavior is different. If you request
        an image from camera A, receive it, then request images from camera A and camera B,
        the server will immediately resend camera A's image (and then B's image), even if it
        hasn't received a new image from camera A. This is done to ensure the images are
        synced.

        The client will emit the imageReceived signal, once for each requested camera, as
        the images are received.

        camera (str or list)    As string, the name of the camera you are requesting an image from.

                                Example: 'Blackfly BFLY-PGE-50S5M_17219622'

                                As list, a list of camera strings. An image will be requested
                                from each camera in the list. This method ensures the images
                                will be synced (same frame number.)

                                ['Blackfly BFLY-PGE-50S5M_17219622', 'Blackfly BFLY-PGE-50S5M_17219631']

                                Requests for cameras that are unknown to the server will be
                                ignored.

        compressed (bool)       Set to True to have the server encode the image data as
                                a jpeg before sending. This reduces the amount of data
                                sent over the wire at the cost of CPU utilization. The
                                client will decode the image upon receipt. Set the
                                'quality' keyword to specify the level of compression.

        scale (int)             Set scale to a value between 1 and 100 to scale the
                                image before sending. A value of 50 will scale it 50%
                                both vertically and horizontally are result in an image
                                that is 1/4 the original size. This can be used to reduce
                                bandwidth utilization.

        quality (int)           Set quality to a value between 1 and 99 to set the JPEG
                                encoding quality. Values above ~98 will result in images
                                that are larger than the source image. This only has an
                                effect when compressed=True.  A reasonable value is in the
                                range of 65-85 though YMMV.
        '''

        if (self.isConnected and self.socket.isOpen()):

            #  if camera is passed as a string, put it in a list
            if (isinstance(camera, str)):
                camera = [camera]

            #  clamp jpeq quality and image scale values
            quality = max(1, min(quality, 99))
            scale = max(1, min(scale, 100))

            #  create a getImage message and set the properties
            getImage = CamtrawlServer_pb2.getImage()
            getImage.cameras.extend(camera)
            getImage.scale = scale
            getImage.quality = quality
            if (compressed):
                getImage.type = CamtrawlServer_pb2.getImage.imageType.Value('JPEG')
            else:
                getImage.type = CamtrawlServer_pb2.getImage.imageType.Value('CVMAT')

            #  create a msg message to wrap our GETIMAGE message
            request = CamtrawlServer_pb2.msg()
            request.type = CamtrawlServer_pb2.msg.msgType.Value('GETIMAGE')
            request.data = getImage.SerializeToString()

            #  and send the request
            self.sendRequest(request.SerializeToString())


    def getData(self, sensorID=None):
        '''getData sends the getSensorData request to the server. The response will
        contain the most recent data from the sensor identified by the supplied sensor
        ID.

                sensorID (str)  Set sensorID to a string representing the sensor ID of
                                the sensor you wish to query. The response will contain
                                all of the most recent datagrams from that sensor. Setting
                                sensorID to None will result in data from all sensors being
                                sent.

        An example of requesting the sensor string from the Camtrawl controller:

        client.getData(sensorID="CTControl")

        '''
        if (self.isConnected and self.socket.isOpen()):
            #  create the getSensorData protobuf obj
            getData = CamtrawlServer_pb2.getSensorData()

            #  set the ID
            if sensorID is None:
                getData.id = 'None'
            else:
                getData.id = str(sensorID)

             #  create a msg message to wrap our GETSENSOR message
            request = CamtrawlServer_pb2.msg()
            request.type = CamtrawlServer_pb2.msg.msgType.Value('GETSENSOR')
            request.data = getData.SerializeToString()

            #  and send the request
            self.sendRequest(request.SerializeToString())


    def setData(self, sensorID, data, time=None):
        '''
        setData will inject NMEA0183 style data into the CamtrawlAcquisition sensor data
        stream. Data from the sensor data stream is written to the acquisition
        metadata SQLLite file. Camtrawl has 2 types of sensors. synchronous sensors provide
        data that is recorded when the cameras are triggered. This data is stored in the
        sensor_data table and is keyed by image number. Examples are GPS location,
        camera depth, camera orientation. Asynchronous sensors are sensors that aren't
        directly tied to an image and are typically recorded at a much lower rate. These
        data are written to the async_data table and are keyed by time only. Synced data is
        written to the database when the cameras are next triggered. Async data is written
        immediately to the database. Sensor specifics are configured in the "sensor"
        section of the acquisition applications configuration file.

        sensorID (str)          A unique string defining the sensor ID. Avoid using the
                                following reserved sensor IDs:
                                    CTControl
                                    camera
                                    SBC

        data (str or list)      A string or list of strings containing the NMEA 0183 like
                                message(s). The strings do not have to strictly conform to the
                                NMEA 0183 standard, but should be provided as a comma delimited
                                string in the form:

                                    header_id, data

                                where header_id uniquely identifies the data contained in the data
                                string.

        time (datetime)         Set to a datetime object representing the time the data was
                                generated or received. This only has an effect on asynchronous
                                sensor data which is tagged with the provided time and logged
                                upon receipt.


        An example using GPS data:

        sensorID = "GarminGPS"
        data     = ["$GPGGA,134658.00,5106.9792,N,11402.3003,W,2,09,1.0,1048.47,M,-16.27,M,08,AAAA*60",
                    "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"]
        client.setData(sensorID, data)

        These GPS data strings would be sent to the server and written to the sensor_data
        table the next time the cameras were triggered.
        '''

        if (self.isConnected and self.socket.isOpen()):

            #  if data is passed as a string, put it in a list
            if (isinstance(data, str)):
                data = [data]

            #  set the sensor type (DEPRECATED)
            type = CamtrawlServer_pb2.sensorType.Value('SYNC')

            #  if time is not provided, use the current time
            if not time:
                time = datetime.datetime.now()

            #  create the setSensorData
            setData = CamtrawlServer_pb2.setSensorData()
            for d in data:
                sensor = setData.sensors.add()
                sensor.id = sensorID
                sensor.header = d.split(',')[0].strip()
                sensor.timestamp = time.timestamp()
                sensor.type = type
                sensor.data = d

            #  create a msg message to wrap our SETSENSOR message
            request = CamtrawlServer_pb2.msg()
            request.type = CamtrawlServer_pb2.msg.msgType.Value('SETSENSOR')
            request.data = setData.SerializeToString()

            #  and send the request
            self.sendRequest(request.SerializeToString())


    def getParameter(self, module, parameter):
        '''getParameter can be used to get operating parameters and
        results in the server emitting the getParameterRequest signal.
        Your application should connect to this signal if you want to
        receive these messages.

        Your server side application can use the module and parameter
        strings to determine which value to send back to the client.

        Args:
            module (str):
                A string representing the module or component the
                parameter is intended for. This is a free form field
                that allows you to branch as needed in your server
                side application.
            parameter (str):
                A string containing the parameter name. This is a free
                form field that allows you to branch as needed in your
                server side application.
        Returns:
            None
        '''
        if (self.isConnected and self.socket.isOpen()):
            getParam = CamtrawlServer_pb2.getParameter()
            getParam.module = str(module)
            getParam.parameter = str(parameter)

            #  create a msg message to wrap our GETPARAMETER message
            request = CamtrawlServer_pb2.msg()
            request.type = CamtrawlServer_pb2.msg.msgType.Value('GETPARAMETER')
            request.data = getParam.SerializeToString()

            #  and send the request
            self.sendRequest(request.SerializeToString())


    def setParameter(self, module, parameter, value):
        '''setParameter can be used to set operating parameters and
        results in the server emitting the setParameterRequest signal.
        Your application should connect to this signal if you want to
        receive these messages.

        Your server side application can use the module and parameter
        strings to determine what to do with the value you are sending.

        Args:
            module (str):
                A string representing the module or component the
                parameter is intended for. This is a free form field
                that allows you to branch as needed in your server
                side application.
            parameter (str):
                A string containing the parameter name. This is a free
                form field that allows you to branch as needed in your
                server side application.
            value (str):
                The value you are passing. This must be a string. You
                convert it to your required type when handling the
                setParameterRequest signal in your server side app.

        Returns:
            None
        '''
        if (self.isConnected and self.socket.isOpen()):
            setParam = CamtrawlServer_pb2.setParameter()
            setParam.module = str(module)
            setParam.parameter = str(parameter)
            setParam.value = str(value)

            #  create a msg message to wrap our SETPARAMETER message
            request = CamtrawlServer_pb2.msg()
            request.type = CamtrawlServer_pb2.msg.msgType.Value('SETPARAMETER')
            request.data = setParam.SerializeToString()

            #  and send the request
            self.sendRequest(request.SerializeToString())


    def connectToServer(self, host, port=7889):
        '''
        connectToServer attempts to resolve the provided hostname and connect
        to that IP and port. After successfully connecting it will
        request the list of cameras from the server. After receiving the list
        from the server the client will emit the connected() signal.

        host        - A string containing the fully qualified domain name
                      or IP address of the CamtrawlServer.

        port        - An int containing the port number the CamtrawlServer is
                      running on.
        '''

        #  make sure we're not already connected
        if (self.isConnected):
            #  we're already connected
            return

        #  look up the host IP
        hostInfo = QtNetwork.QHostInfo.fromName(host)
        if (len(hostInfo.addresses()) > 0):
            #  at least one IP resolved or we were given an IP
            self.hostAddress = hostInfo.addresses()[0]
            self.hostPort = int(port)
        else:
            errorMsg = 'Unable to get host address for %s. Error:%s' % \
                (host, str(hostInfo.errorString()))
            self.error.emit(2, errorMsg)
            return

        #  try to connect to the server will return -1 if we fail to connect
        self.socket.connectToHost(self.hostAddress, self.hostPort)
        ok = self.socket.waitForConnected()
        if (not ok):
            #  The socket will already have emitted an error - just return here
            return

        #  once connected, we query the camera info - once we receive the info
        #  we will emit the "connected" signal so the client application knows
        #  it can start requesting data.
        request = CamtrawlServer_pb2.msg()
        request.type = CamtrawlServer_pb2.msg.msgType.Value('GETCAMERAINFO')
        request.data = b'0'

        #  send the camera info request
        self.sendRequest(request.SerializeToString())


    def disconnectFromServer(self):
        '''
        disconnectFromServer disconnects from the CamtrawlServer. The disconnected()
        signal will be emitted after the socket is closed.
        '''

        if (self.isConnected):
            #  close the socket
            self.socket.close()

            #  reset state
            self.datagramBuffer = QtCore.QByteArray()
            self.thisDatagramSize = 0
            self.cameras = {}
            self.isConnected = False

            #  emit the disconnect signal
            self.disconnected.emit()


    @QtCore.pyqtSlot()
    def socketReadyRead(self):
        '''
        socketReadyRead is called when our socket receives data from the server.
        This method buffers and unpacks the data and emits various signals to
        pass that data on to the application using the client.
        '''

        #  while data is available
        while (self.socket.bytesAvailable() > 0):
            #  append this data to the receive buffer
            self.datagramBuffer.append(self.socket.readAll())

            #  assemble and process datagrams - datagrams are in the form
            #    [size - (uint32) 4 bytes][data - protobuff size bytes]

            #  check if we need to unpack anything.
            while ((self.thisDatagramSize == 0 and self.datagramBuffer.length() >= 4) or
                   (self.thisDatagramSize > 0 and self.datagramBuffer.length() >= self.thisDatagramSize)):

                #  check if we have enough to unpack the length
                if (self.thisDatagramSize == 0 and self.datagramBuffer.length() >= 4):

                    #  we have rx'd at least 4 bytes, unpack the datagram length
                    #  datagram length is big endian uint32
                    self.thisDatagramSize = struct.unpack('!I', self.datagramBuffer[0:4])[0]

                    #  delete the len bytes from the buffer
                    self.datagramBuffer.remove(0, 4)

                #  check if we have at least 1 full datagram
                if (self.thisDatagramSize > 0 and self.datagramBuffer.length() >= self.thisDatagramSize):

                    #  parse the datagram to get type
                    response = CamtrawlServer_pb2.msg()
                    response.ParseFromString(bytes(self.datagramBuffer[0:self.thisDatagramSize]))

                    #  parse the data based on the datagram type
                    if (response.type == CamtrawlServer_pb2.msg.msgType.Value('CVMATDATA')):
                        cvMat = CamtrawlServer_pb2.cvMat()
                        cvMat.ParseFromString(response.data)

                        # construct the image_data dict which we will emit below
                        image_data = {}
                        image_data['ok'] = True
                        image_data['exposure'] = cvMat.exposure
                        image_data['gain'] = cvMat.gain
                        image_data['height'] = cvMat.rows
                        image_data['width'] = cvMat.cols
                        image_data['timestamp'] = datetime.datetime.fromtimestamp(cvMat.timestamp)
                        image_data['filename'] = cvMat.filename
                        image_data['image_number'] = cvMat.image_number

                        #  construct numpy array from raw byte array, type and size info - Follow
                        #  the OpenCV standard where mono images are (height, width) and color
                        #  images are (heigh, width, depth)
                        if (cvMat.depth == 1):
                            image_data['data'] = numpy.frombuffer(cvMat.mat_data,
                                dtype=cvMat.elt_type).reshape((cvMat.rows,cvMat.cols))
                        else:
                            image_data['data'] = numpy.frombuffer(cvMat.mat_data,
                                    dtype=cvMat.elt_type).reshape((cvMat.rows,cvMat.cols,
                                    cvMat.depth))

                        #  emit the imageData signal
                        self.imageData.emit(cvMat.camera, cvMat.label, image_data)

                    elif (response.type == CamtrawlServer_pb2.msg.msgType.Value('JPEGDATA')):
                        jpeg = CamtrawlServer_pb2.jpeg()
                        jpeg.ParseFromString(response.data)

                        # construct the image_data dict which we will emit below
                        image_data = {}
                        image_data['ok'] = True
                        image_data['exposure'] = jpeg.exposure
                        image_data['gain'] = jpeg.gain
                        image_data['height'] = jpeg.height
                        image_data['width'] = jpeg.width
                        image_data['timestamp'] = datetime.datetime.fromtimestamp(jpeg.timestamp)
                        image_data['filename'] = jpeg.filename
                        image_data['image_number'] = jpeg.image_number

                        #  construct numpy array from raw byte array
                        data = numpy.frombuffer(jpeg.jpg_data, dtype='uint8')

                        #  decode the jpeg data
                        image_data['data'] = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)

                        #  emit the imageData signal
                        self.imageData.emit(jpeg.camera, jpeg.label, image_data)

                    elif (response.type == CamtrawlServer_pb2.msg.msgType.Value('GETCAMERAINFO')):
                        #  we received a getCameras response - unpack the response
                        cameraInfo = CamtrawlServer_pb2.cameraInfo()
                        cameraInfo.ParseFromString(response.data)

                        #  unpack the response info into our cameras property
                        self.cameras = {}
                        for cam in cameraInfo.cameras:
                            self.cameras[cam.name] = {'label':cam.label, 'received':False}

                        #  emit the connected signal to indicate we're connected to the
                        #  server and ready to request/receive data.
                        if not self.isConnected:
                            self.isConnected = True
                            self.connected.emit()

                    elif (response.type == CamtrawlServer_pb2.msg.msgType.Value('GETSENSORINFO')):
                        #  we received a sensorInfo response - unpack the response
                        sensorInfo = CamtrawlServer_pb2.sensorInfo()
                        sensorInfo.ParseFromString(response.data)

                        #  and build a dict keyed by sensor ID with values that are
                        #  a list of each sensor's unique data headers
                        sensors = {}
                        for sensor in sensorInfo.sensors:
                            if sensor.id not in sensors:
                                sensors[sensor.id] = []
                            sensors[sensor.id].append(sensor.header)

                        #  emit the sensorInfo signal
                        self.sensorInfo.emit(sensors)

                    elif (response.type == CamtrawlServer_pb2.msg.msgType.Value('SENSORDATA')):
                        #  we received a sensorData response - unpack the response
                        sensorData = CamtrawlServer_pb2.sensorData()
                        sensorData.ParseFromString(response.data)

                        #  emit the sensorDataAvailable signal for each id+header sent
                        for sensor in sensorData.sensors:

                            #  convert the timestamp to a datetime obj
                            time_obj = datetime.datetime.fromtimestamp(sensor.timestamp)

                            #  emit the sensor data signal
                            if sensor.type == CamtrawlServer_pb2.sensorType.Value('ASYNC'):
                                #  this data should be handled as async
                                self.asyncSensorData.emit(sensor.id, sensor.header, time_obj, sensor.data)
                            else:
                                #  this data should be handled as synced
                                self.syncSensorData.emit(sensor.id, sensor.header, time_obj, sensor.data)

                        self.dataRequestComplete.emit()

                    elif (response.type == CamtrawlServer_pb2.msg.msgType.Value('PARAMDATA')):
                        #  we received a parameterData response - unpack the response
                        paramData = CamtrawlServer_pb2.parameterData()
                        paramData.ParseFromString(response.data)

                        if paramData.ok:
                            ok = True
                        else:
                            ok = False

                        #  emit the parameterData signal.
                        self.parameterData.emit(paramData.module, paramData.parameter, paramData.value,
                                ok, paramData.error_string)

                    #  lastly, remove this datagram from the buffer
                    self.datagramBuffer.remove(0, self.thisDatagramSize)

                    # reset the datagram size
                    self.thisDatagramSize = 0


    @QtCore.pyqtSlot(QtNetwork.QAbstractSocket.SocketError)
    def socketError(self, sockError):
        '''
        socketError is called when there is a socket error
        '''

        if (int(sockError) == 1):
            #  server closed connection
            self.disconnected.emit()
        else:
            self.error.emit(int(sockError), self.socket.errorString())


    def sendRequest(self, message):
        '''
        sendRequest sends the length of the request datagram along with
        the serialized request contained in the provided message.
        '''
        #  write the message length as big endian uint32
        self.socket.write(struct.pack('!I', len(message)))

        #  write the message data
        bytesWritten = self.socket.write(message)

        if (bytesWritten != len(message)):
            self.error.emit(3, "Short write to socket :(  message length:" + str(len(message)) +
                    "  bytes written to socket:" + str(bytesWritten))
