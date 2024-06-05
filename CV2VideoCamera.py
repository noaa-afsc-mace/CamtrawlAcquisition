
"""
CV2VideoCamera.py provides an interface for cameras supported by OpenCV's VideoCapture
class. VideoCapture is a Swiss Army knife of camera interfaces that supports a wide number
of backends that support a crazy number of cameras. Exactly what backends are supported
depends on your specific version of OpenCV and your OS. You can use this module's
get_camera_backends() method to list the supported backends.

VideoCapture brings wide, but fairly shallow support for cameras. Not all methods for
camera control are supported by all cameras and most cameras have fairly limited
support for basic tasks such as setting resolution, exposure, white balance, and gain.
To further complicate things, the level of control depends on your OS and backend
and a camera can be supported by multiple backends. If you do not specify a backend,
OpenCV will use the first compatible backend it finds which may not be the optimal
backend for your camera. For best results, you are going to have to work at determining
the best backend to use for your camera, OpenCV version, and OS.


Kresimir Williams
Rick Towler
MACE Group
NOAA Alaska Fisheries Science Center

"""

import os
import logging
import datetime
#import subprocess
from PyQt5 import QtCore
import ImageWriter
import numpy as np
import cv2


class CV2VideoCamera(QtCore.QObject):

    #  define PyQt Signals
    imageData = QtCore.pyqtSignal(str, str, dict)
    saveImage = QtCore.pyqtSignal(str, dict)
    imageSaved = QtCore.pyqtSignal(object, str)
    videoSaved = QtCore.pyqtSignal(str, str, int, int, datetime.datetime, datetime.datetime)
    error = QtCore.pyqtSignal(str, str)
    acquisitionStarted = QtCore.pyqtSignal(object, str, bool)
    stoppingAcquisition = QtCore.pyqtSignal()
    acquisitionStopped = QtCore.pyqtSignal(object, str, bool)
    triggerReady = QtCore.pyqtSignal(object, list, bool)
    triggerComplete = QtCore.pyqtSignal(object, bool)


    def __init__(self, cv_device_path, camera_name, resolution=(None, None), backend=None,
            parent=None):

        super(CV2VideoCamera, self).__init__(parent)

        self.rotation = 'none'
        self.timeout = 2000
        self.hdr_enabled = False
        self.acquiring = False
        self.save_path = '.'
        self.date_format = "D%Y%m%d-T%H%M%S.%f"
        self.n_triggered = 0
        self.total_triggers = 0
        self.save_stills_divider = 1
        self.save_stills = True
        self.save_video = False
        self.save_video_divider = 1
        self.trigger_divider = 1
        self.label = 'camera'
        self.ND_pixelFormat = None
        self.camera_name = camera_name
        self.device_path = cv_device_path
        self.device_info = {}
        self.device_info['DeviceID'] = 'CV2VideoCamera' + '{' + str(self.device_path) + '}'
        self.device_info['DeviceVersion'] = ''
        self.camera_id = camera_name
        self.cam = None
        self.logger = logging.getLogger('Acquisition')

        #  get some basic properties
        self.pixelFormat = 0

        #  initialize the HDR parameters
        self.hdr_parameters = 0

        #  create a timer to handle software trigger sequencing
        self.sw_trig_timer = QtCore.QTimer(self)
        self.sw_trig_timer.timeout.connect(self.software_trigger)
        self.sw_trig_timer.setSingleShot(True)

        #  if a backend is provided, check that it is available
        has_backend= False
        if backend is not None:
            #  get a dict of supported backends
            backends = get_camera_backends()
            #  now iterate thru the supported backends to see if the provided backend macthes
            for this_backend in backends:
                #  check if the specified backend matches by name
                if backend in this_backend:
                    backend = backends[this_backend]
                    has_backend= True
                    break
                #  check if the specified backend matches by number
                if backend == backends[this_backend]:
                    has_backend= True
                    break

        #  now try to create an instace of OpenCV VideoCapture. This can fail if
        #  an incorrect camera path and/or backend are provided.
        if has_backend:
            #  backend is provided and seems available
            self.cam = cv2.VideoCapture(self.device_path, backend)
            if not self.cam.isOpened():
                self.cam = None
                raise ValueError("Incorrect device path/index or backend specified. Path:" +
                        str(self.device_path) + " Backend:" + str(backend))
        else:
            #  no backend provided, let OpenCV try to figure it out
            self.cam = cv2.VideoCapture(self.device_path)
            if not self.cam.isOpened():
                self.cam = None
                raise ValueError("Incorrect device path/index specified. Path:" +
                        str(self.device_path))

        #  note the backend that we ultimately ended up with
        self.cv_backend = self.cam.getBackendName()

        #  if a camera resolution was provided set it here. This must be done
        #  before any frames are acquired from the camera
        if resolution[0]:
            self.cam.set(cv2.CAP_PROP_FRAME_WIDTH, resolution[0])
        if resolution[1]:
            self.cam.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution[1])

        #  now query VideoCapture for the resolution - Setting resolution is not
        #  always reliable. Some backend and camera combinations work, some don't.
        #  Some only work at certain resolutions.
        self.resolution = []
        self.resolution.append(self.cam.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.resolution.append(self.cam.get(cv2.CAP_PROP_FRAME_HEIGHT))

        #  These probably wil not work but we try anyways
        self.exposure = self.cam.get(cv2.CAP_PROP_EXPOSURE)
        self.gain = self.cam.get(cv2.CAP_PROP_GAIN)


    def get_hdr_settings(self):
        '''
        get_hdr_settings queries the camera and returns the camera's HDR
        settings in a dict

        HDR mode is not supported by VideoCapture but this is here in case
        someone wants to implement a pure software implementation.
        '''

        hdr_parameters = {}
        hdr_parameters["Image1"] = {'exposure':0, 'gain':0, 'emit_signal':True, 'save_image':True}
        hdr_parameters["Image2"] = {'exposure':0, 'gain':0, 'emit_signal':True, 'save_image':True}
        hdr_parameters["Image3"] = {'exposure':0, 'gain':0, 'emit_signal':True, 'save_image':True}
        hdr_parameters["Image4"] = {'exposure':0, 'gain':0, 'emit_signal':True, 'save_image':True}

        return hdr_parameters


    def get_gain(self):
        '''
        get_gain returns the camera gain. This method may or may not return valid
        information depending on the backend VideoCapture is using. Most likely it
        will not return anything useful. this method is requried to ensure API
        compatibility.
        '''
        if self.cam:
            if self.cv_backend in ['MSMF','DSHOW']:
                val = self.cam.get(cv2.CAP_PROP_GAIN)
        else:
            val = 0

        return val


    def get_exposure(self):
        '''
        get_exposure returns the camera exposure. This method may or may not return
        valid information depending on the backend VideoCapture is using. Most likely it
        will not return anything useful.
        '''
        if self.cam:
            val = self.cam.get(cv2.CAP_PROP_EXPOSURE)
        else:
            val = 0

        return val


    def get_binning(self):
        '''
        Binning is not supported by VideoCapture
        '''
        return 1


    def set_binning(self, crap):
        '''
        Binning is not supported by VideoCapture
        '''
        return True


    def enable_HDR_mode(self):
        '''
        HDR mode is not supported by VideoCapture
        '''

        return False


    def disable_hdr_mode(self):
        '''
        HDR mode is not supported by VideoCapture
        '''

        return False


    def set_hdr_settings(self, hdr_parameters):
        '''
        set_hdr_settings sets the camera's HDR settings

        HDR mode is not supported by VideoCapture but this is here in case
        someone wants to implement a pure software implementation.
        '''

        #  update the internal HDR parameteres dict
        self.hdr_parameters = hdr_parameters

        return True


    @QtCore.pyqtSlot(list, int, datetime.datetime, bool, bool)
    def trigger(self, cam_list, image_number, timestamp, save_image, emit_signal):
        '''trigger sets the camera up for the next trigger event and then either executes
        the software trigger or emits the triggerReady signal if using hardware triggering.
        The controlling application needs to receive that signal and ultimately trigger
        the camera.

        Note that trigger is this context means a collection event. That is a single image
        when *not* in HDR mode and 4 images when in HDR mode. So, if your cameras are
        configured to collect in HDR mode, you only call this method once and it will
        execute the 4 triggers required for HDR collection.

        cam_list (list): list of camera objects to trigger - empty list triggers all
        image_number (int): current image number - will be used in image filename
        timestamp (datetime): timestamp of the trigger - used to generate image file name
        save_image (bool): Set to True to save the image to disk
        emit_signal (bool): set to True to emit the "imageData" signal after receiving image

        Both the save_image and emit_signal arguments will override these same settings
        for the individual HDR exposures (and merged
        '''

        #  don't do anything if we're not acquiring
        if not self.acquiring:
            return

        #  reset the save still image and save video frame state vars
        self.save_this_still = self.save_stills
        self.save_this_frame = self.save_video

        #  increment the internal trigger counter
        self.total_triggers += 1

        #  set the trigger counter - this counter is used to track the
        #  number of triggers in this collection event. This will always be
        #  1 for standard acquisition and 4 for HDR acquisition.
        self.n_triggered = 1

        #  check if we should trigger because of the divider
        if (self.total_triggers % self.trigger_divider) != 0:
            #  nope, don't trigger. We emit the complete signal but unset
            #  the trigger argument so acquisition knows the camera trigger
            #  was skipped.
            self.triggerComplete.emit(self, False)
            return

        #  If specific cameras are specified, check if we're one
        if (len(cam_list) > 0 and self not in cam_list):
            #  nope, don't trigger. We emit the complete signal but unset
            #  the trigger argument so acquisition knows the camera trigger
            #  was skipped.
            self.triggerComplete.emit(self, False)
            return

        #  Lastly, check if the save_image or save_video dividers
        #  will override the save_image value passed into this method.
        if (self.total_triggers % self.save_stills_divider) != 0:
            self.save_this_still = False
        if (self.total_triggers % self.save_video_divider) != 0:
            self.save_this_frame = False
        #  If we're not saving either, unset save_image
        if not self.save_this_frame and not self.save_this_still:
            save_image = False

        #  initialize some lists used to help us do what we do
        self.filenames = []
        self.do_signals = []
        self.save_image = []
        self.exposures = []
        self.save_hdr = False
        self.emit_hdr = False
        self.trig_timestamp = timestamp
        self.image_number = image_number

        #  Generate the image number string
        if (image_number > 999999):
            num_str = '%09d' % image_number
        else:
            num_str = '%06d' % image_number
        self.image_num_str = num_str

        #  generate the time string
        time_str = timestamp.strftime(self.date_format)[:-3]

        #  single images follow the "standard" camtrawl naming convention
        self.filenames.append(self.save_path + num_str + '_' + time_str +
                '_' + self.camera_name)

        self.exposures.append(self.exposure)
        if emit_signal:
            self.do_signals.append(True)
        else:
            self.do_signals.append(False)
        if save_image:
            self.save_image.append(True)
        else:
            self.save_image.append(False)

        #  set the trigger counter - this counter is used to track the
        #  number of triggers in this collection event. This will always be
        #  1 for standard acquisition and 4 for HDR acquisition.
        self.n_triggered = 1

        self.logger.debug("%s triggered: Image number %d Save image: %s" %
                (self.camera_name, image_number, save_image))

        #  Software trigger the camera
        self.sw_trig_timer.start(0)


    @QtCore.pyqtSlot()
    def software_trigger(self):
        '''software_trigger is the slot called when the sw_trig_timer expires.

        Most cameras require some delay before they can software trigger. We use
        a timer so we an asynchronously execute the delay.
        '''
        self.exposure_end('get image')


    @QtCore.pyqtSlot(str)
    def exposure_end(self, event_name):
        '''exposure_end is called when the camera calls the EndExposure event callback.

        The basic function of this method is to retrieve the most recent image from
        the camera buffer. It will optionally save the image and optionally emit a signal
        with the image data for display or transmission over the wire. (These options are
        specified in the call to the trigger method.)

        When in HDR mode, this method also handles the details of HDR acquisition.
        '''

        #  make sure this isn't a stale event and/or ignore buffer flushes. Sometimes
        #  we need to trigger the camera but don't want to process the images at all.
        #  In these cases, we set self.n_triggered = 0 and trigger the camera directly
        #  without calling spin_camera.trigger method.
        if self.n_triggered == 0:
            return

        #  get the index this event is associated with
        idx = self.n_triggered - 1

        #  get the next image from the camera buffers
        image_data = self.get_image()
        image_data['timestamp'] = self.trig_timestamp
        image_data['filename'] = self.filenames[idx]
        image_data['image_number'] = self.image_number
        image_data['save_still'] = self.save_this_still
        image_data['save_frame'] = self.save_this_frame

        #  check if we got an image
        if (image_data['ok']):

            #  check if we're supposed to do anything with this image
            if self.do_signals[idx] or self.save_image[idx] or self.save_hdr or self.emit_hdr:
                # We're saving and/or emitting some form of this image

                #  apply rotation if required
                if self.rotation == 'cw90':
                    image_data['data'] = np.rot90(image_data['data'], k=-1)
                    height = image_data['height']
                    width = image_data['width']
                    image_data['width'] = height
                    image_data['height'] = width
                elif self.rotation == 'cw180':
                    image_data['data'] = np.rot90(image_data['data'], k=-2)
                elif self.rotation == 'cw270':
                    image_data['data'] = np.rot90(image_data['data'], k=-3)
                    height = image_data['height']
                    width = image_data['width']
                    image_data['width'] = height
                    image_data['height'] = width
                elif self.rotation == 'flipud':
                    image_data['data'] = np.flipud(image_data['data'])
                elif self.rotation == 'fliplr':
                    image_data['data'] = np.fliplr(image_data['data'])


                #  check if we need to emit a signal for this image
                if self.do_signals[idx]:
                    self.imageData.emit(self.camera_name, self.label, image_data)

                #  check if we're saving this image
                if self.save_image[idx]:
                    self.saveImage.emit(self.camera_name, image_data)

        else:
            #  there was a problem receiving image
            pass

        #  we are done with this trigger event
        self.triggerComplete.emit(self, True)
        self.n_triggered = 0


    def load_hdr_reponse(self, filename):
        '''load_hdr_reponse loads a numpy file containing the camera sensor reposonse data
        which is used for certain HDR image fusion methods.
        '''
        #TODO Implement this feature
        raise NotImplementedError()


    def set_camera_trigger(self, mode):
        '''
        The VideoCapture backend doesn't really support hardware triggers so this
        method does nothing but is requried to ensure API compatibility.
        '''

        return True


    def set_exposure(self, exposure):
        '''
        set_exposure sets the camera exposure. This method may or may not work
        depending on the backend VideoCapture is using and your specific camera.
        This method is requried to ensure API compatibility.
        '''

        try:
            self.cam.set(cv2.CAP_PROP_EXPOSURE, exposure)
            #  set the value based on what was passed in, not from querying VideoCapture
            #  since that doesn't usually return valid data.
            self.exposure = exposure
        except:
            return False

        return True


    def set_gain(self, gain):
        '''
        set_gain sets the camera gain. This method may or may not work
        depending on the backend VideoCapture is using and your specific camera.
        This method is requried to ensure API compatibility.
        '''
        try:
            self.cam.set(cv2.CAP_PROP_GAIN, gain)
            #  set the value based on what was passed in, not from querying VideoCapture
            #  since that doesn't usually return valid data.
            self.gain = gain
        except:
            return False

        return True


    def get_image(self):
        '''get_image gets the next image from the camera buffers, does some error
        checking, converts the image, and then returns it.
        '''
        #  define the return dict
        image_data = {'data':None, 'ok':False, 'exposure':-1, 'gain':-1, 'is_hdr':False}

        #  get the image
        state, raw_image =  self.cam.read()
        if not state:
            #  timed out waiting for image
            self.error.emit(self.camera_name, 'Timed out waiting for image...')
            return image_data
        #  populate the return dict
        image_data['data'] = raw_image.copy()
        image_data['ok'] = True
        image_data['exposure'] = self.exposure
        image_data['gain'] = self.gain
        image_data['height'] = raw_image.shape[1]
        image_data['width'] = raw_image.shape[0]

        #  and return the converted one
        return image_data


    def set_pixel_format(self, format):
        '''
        set_pixel_format is not supported by VideoCapture but is required
        for API compatibility.
        '''

        return True


    @QtCore.pyqtSlot(list, str, bool, dict, bool, dict)
    def start_acquisition(self, cam_list, file_path, save_images, image_options,
            save_video, video_options):


        #  just return if we're already acquiring or if we failed to init the camera
        if self.acquiring or self.cam is None:
            return

        #  Reset n_triggered
        self.n_triggered = 0

        #  set up the file logging directory - create if needed
        self.save_path = os.path.normpath(file_path) + os.sep + self.camera_name + os.sep

        try:
            if not os.path.exists(self.save_path):
                os.makedirs(self.save_path)
        except:
            self.error.emit(self.camera_name, 'Unable to create file logging directory: %s' %
                    self.save_path)
            self.acquisitionStarted.emit(self, self.camera_name, False)
            return

        #  create a instance of image_writer
        self.image_writer = ImageWriter.ImageWriter(self.camera_name)

        #  update the writer image and video properties
        self.image_writer.video_options.update(video_options)
        self.image_writer.save_video = save_video
        self.image_writer.image_options.update(image_options)
        self.image_writer.save_images = save_images

        #  create a thread and move the image writer to it
        thread = QtCore.QThread()
        self.image_writer_thread = thread
        self.image_writer.moveToThread(thread)

        #  connect up our signals
        self.saveImage.connect(self.image_writer.WriteImage)
        self.stoppingAcquisition.connect(self.image_writer.StopRecording)
        self.image_writer.writerStopped.connect(self.image_writer_stopped)
        self.image_writer.error.connect(self.image_writer_error)
        self.image_writer.writeComplete.connect(self.image_write_complete)
        self.image_writer.videoFileClosed.connect(self.image_writer_video_closed)

        #  these signals handle the cleanup when we're done
        self.image_writer.writerStopped.connect(thread.quit)
        thread.finished.connect(self.image_writer.deleteLater)
        thread.finished.connect(thread.deleteLater)

        #  and start the thread
        thread.start()

        try:

            #  read a few frames to get things rolling
            for i in range(5):
                state, raw_image = self.cam.read()

            #  Begin acquiring images
            self.acquiring = True

            #  and emit the acquisitionStarted signal
            self.acquisitionStarted.emit(self, self.camera_name, True)

        except Exception as e:
            self.error.emit(self.camera_name, 'Start Acquisition Error:' + str(e))
            self.acquisitionStarted.emit(self, self.camera_name, False)


    @QtCore.pyqtSlot()
    def image_writer_stopped(self):
        '''The image_writer_stopped slot is called when the image_writer has
        been told to stop and it is finished shutting down (i.e. closing
        any open files.)
        '''

        #  image_writer_stopped - if self.acquiring == False, we're in the process
        #  of stopping acquisition and were waiting for the writer to close files.
        #  The writer has now stopped so we signal that acquisition has stopped.
        if not self.acquiring:
            self.acquisitionStopped.emit(self, self.camera_name, True)


    @QtCore.pyqtSlot(str, str)
    def image_write_complete(self, camera_name, filename):
        '''The image_write_complete slot is called when the image_writer has
       finished writing each image/frame.
        '''

        #  re-emit as a camera signal
        self.imageSaved.emit(self, filename)


    @QtCore.pyqtSlot(str, str)
    def image_writer_error(self, camera_name, error_string):
        '''The image_write_complete slot is called when the image_writer has
       finished writing each image/frame.
        '''

        #  re-emit as a camera signal
        self.error.emit(self.camera_name, error_string)


    @QtCore.pyqtSlot(str, str, int, int, datetime.datetime, datetime.datetime)
    def image_writer_video_closed(self, cam, filename, start_frame, end_frame, start_time, end_time):
        '''
        The image_writer_video_closed slot is called when the image_writer has
        finished writing a video file.
        '''

        #  re-emit as a camera signal
        self.videoSaved.emit(cam, filename, start_frame, end_frame, start_time, end_time)


    @QtCore.pyqtSlot(list)
    def stop_acquisition(self, cam_list):

        #  check that we're supposed to stop
        if (len(cam_list) > 0 and self not in cam_list):
            return

        try:
            # End acquisition
            self.cam.release()
            self.acquiring = False

            #  Emit the stoppingAcquisition signal that we use to tell child threads
            #  to shut down
            self.stoppingAcquisition.emit()

            # We don't actually emit the acquisitionStopped signal here. We wait
            # for the image_writer to signal it has stopped before we signal that
            # acquisition has stopped.

        except :
            self.error.emit(self.camera_name, 'Stop Acquisition Error:')
            self.acquisitionStopped.emit(self, self.camera_name, False)


    def set_white_balance(self):
        pass


    def __sync_settings(self, nodemap=None):
        '''__sync_settings will trigger the camera a few times to push settings into the
        CMOS ASIC so the next trigger executed will return images with the specified
        settings. When in trigger mode, most CMOS cameras will require 1-2 triggers for
        a setting to be active. (Normally 1 trigger, but changing HDR settings and
        activating HDR will take 2 triggers.

        This is done by switching to software triggering, triggering a few times,
        discarding the images, then re-enabling the original trigger settings
        '''

        pass


    def __sync_hdr(self, nodemap=None):
        '''__sync_HDR will trigger the camera (discarding any imaged) until the
        HDR sequence counter is pointing at the start of the sequence.

        Currently HDR acquisition is not supported with this driver. This method
        is included for API compatibility.
        '''

        pass




def get_camera_backends():
    '''
    get_camera_backends returns a dict, keyed by backend name containing
    the camera backends that the platform's OpenCV supports.
    '''
    cam_backends = cv2.videoio_registry.getCameraBackends()
    backend_names = {}

    for backend in cam_backends:
        name = cv2.videoio_registry.getBackendName(backend)
        backend_names[name] = backend

    return backend_names


def get_stream_backends():
    '''
    get_stream_backends returns a dict, keyed by backend name containing
    the stream backends that the platform's OpenCV supports.
    '''

    cam_backends = cv2.videoio_registry.getStreamBackends()
    backend_names = {}

    for backend in cam_backends:
        name = cv2.videoio_registry.getBackendName(backend)
        backend_names[name] = backend

    return backend_names



'''

https://gist.github.com/jwhendy/12bf558011fe5ff58bd5849954e84af4

### for reference, the output of v4l2-ctl -d /dev/video1 -l (helpful for min/max/defaults)
#                     brightness (int)    : min=0 max=255 step=1 default=128 value=128
#                       contrast (int)    : min=0 max=255 step=1 default=128 value=128
#                     saturation (int)    : min=0 max=255 step=1 default=128 value=128
# white_balance_temperature_auto (bool)   : default=1 value=1
#                           gain (int)    : min=0 max=255 step=1 default=0 value=0
#           power_line_frequency (menu)   : min=0 max=2 default=2 value=2
#      white_balance_temperature (int)    : min=2000 max=6500 step=1 default=4000 value=2594 flags=inactive
#                      sharpness (int)    : min=0 max=255 step=1 default=128 value=128
#         backlight_compensation (int)    : min=0 max=1 step=1 default=0 value=0
#                  exposure_auto (menu)   : min=0 max=3 default=3 value=1
#              exposure_absolute (int)    : min=3 max=2047 step=1 default=250 value=333
#         exposure_auto_priority (bool)   : default=0 value=1
#                   pan_absolute (int)    : min=-36000 max=36000 step=3600 default=0 value=0
#                  tilt_absolute (int)    : min=-36000 max=36000 step=3600 default=0 value=0
#                 focus_absolute (int)    : min=0 max=250 step=5 default=0 value=125
#                     focus_auto (bool)   : default=1 value=0
#                  zoom_absolute (int)    : min=100 max=500 step=1 default=100 value=100

### I created a dict of the settings of interest
### note that if you have any auto settings on, e.g. focus_auto=1,
### it will complain when it goes to set focus_absolute, but I didn't have
### any issues other than the warning
cam_props = {'brightness': 128, 'contrast': 128, 'saturation': 180,
             'gain': 0, 'sharpness': 128, 'exposure_auto': 1,
             'exposure_absolute': 150, 'exposure_auto_priority': 0,
             'focus_auto': 0, 'focus_absolute': 30, 'zoom_absolute': 250,
             'white_balance_temperature_auto': 0, 'white_balance_temperature': 3300}

### go through and set each property; remember to change your video device if necessary~
### on my RPi, video0 is the usb webcam, but for my laptop the built-in one is 0 and the
### external usb cam is 1
for key in cam_props:
    subprocess.call(['v4l2-ctl -d /dev/video1 -c {}={}'.format(key, str(cam_props[key]))],
                    shell=True)

### uncomment to print out/verify the above settings took
# subprocess.call(['v4l2-ctl -d /dev/video1 -l'], shell=True)

### showing that I *think* one should only create the opencv capture object after these are set
### also remember to change the device number if necessary
cam = cv2.VideoCapture(1)

'''
