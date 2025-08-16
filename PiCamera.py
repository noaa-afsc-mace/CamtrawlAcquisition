
"""
PiCamera.py provides an interface for cameras supported by Raspberry Pi's Picamera2 library.

Katherine Wilson
MACE Group
NOAA Alaska Fisheries Science Center

"""

import os
import logging
import datetime
import ImageWriter
from PyQt5 import QtCore
from picamera2 import Picamera2, Preview, Metadata
from libcamera import controls
import numpy as np

class PiCamera(QtCore.QObject):

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


    def __init__(self, pi_device_path, mode, resolution=(-1,-1), parent=None):

        super(PiCamera, self).__init__(parent)

        self.cam =  Picamera2(pi_device_path) 
        self.rotation = 'none'
        self.timeout = 2000
    
        self.hdr_enabled = False
        # more hdr settings
        
        self.acquiring = False
        self.save_path = '.'
        self.date_format = "D%Y%m%d-T%H%M%S.%f"
        
        self.n_triggered = 0
        self.total_triggers = 0
        # more trigger settings
        
        self.save_stills_divider = 1
        self.save_stills = True
        self.save_video = False
        self.save_video_divider = 1
        self.trigger_divider = 1
        
        self.label = 'camera'
        self.ND_pixelFormat = None
        
        # Pi camera info
        self.camera_name = self.cam.camera_properties['Model'] 		#model name_SN
        self.camera_id = str(pi_device_path)  # RasberryPi CPU SN could be used here; Picamera2.global_camera_info()[pi_device_path]['Num']
        self.device_info = {}
        self.device_info['DeviceID'] = 'PiCamera' + '{' + str(pi_device_path) + '}'
        self.device_info['DeviceVersion'] = ""
        self.sensor = None    
        self.mode = -1							# Default: use last mode with largest resolution
    
        self.logger = logging.getLogger('Acquisition')

        #  get some basic properties
        self.pixelFormat = 0
 #       self.exposure =  self.cam.camera_controls['ExposureTime'][2] 	# max
 #       self.gain = self.cam.camera_controls['AnalogueGain'][2] 		# max
 #       self.auto_exposure = self.cam.camera_controls['AeEnable']
        
        #  initialize the HDR parameters
        self.hdr_parameters = 0

        #  create a timer to handle software trigger sequencing
        self.sw_trig_timer = QtCore.QTimer(self)
        self.sw_trig_timer.timeout.connect(self.software_trigger)
        self.sw_trig_timer.setSingleShot(True)

        #  Set camera mode if provided
        if mode:
            # Check mode requested is an option
            if mode >= len(self.cam.sensor_modes):
                raise ValueError("Mode requested is not supported, using mode with largest resolution")
            else:
                self.mode = mode
        
        # Set resolution if provided
        if any(r!=-1 for r in resolution):
            if ((resolution[0]==-1 or resolution[0] <= self.cam.sensor_modes[self.mode]['size'][0]) and
                (resolution[1]==-1 or resolution[1] <= self.cam.sensor_modes[self.mode]['size'][1])):
                try:
                    if resolution[0]==-1:
                        self.cam.still_configuration.main.size = (self.cam.sensor_modes[self.mode]['size'][0],resolution[1])
                    elif resolution[1]==-1:
                        self.cam.still_configuration.main.size = (resolution[0], self.cam.sensor_modes[self.mode]['size'][1])
                    else:
                        self.cam.still_configuration.main.size = resolution
                except:
                    raise RuntimeError("Error setting resolution, using full resolution of camera mode")
            else:
                raise ValueError("Resolution requested is not supported, using full resolution of camera mode")
                   
        #  Get exposure and gain
        if hasattr(self.cam.still_configuration.controls, 'ExposureTime'):
            self.exposure = self.cam.still_configuration.controls.ExposureTime
        else:
            self.exposure = 'Auto'
        if hasattr(self.cam.still_configuration.controls, 'AnalogueGain'):    
            self.gain = self.cam.still_configuration.controls.AnalogueGain    
        else:
            self.gain = 'Auto'
            
    def get_hdr_settings(self):
        '''
        get_hdr_settings queries the camera and returns the camera's HDR
        settings in a dict.

        Hardware HDR mode is supported by the CM3 (IMX708) Raspberry Pi camera.
        Software HDR is supported with Pi5, but limited with Pi4 and earlier. 
        Below is only for implementing a pure software implementation.
        '''

        hdr_parameters = {}
        hdr_parameters["Image1"] = {'exposure':0, 'gain':0, 'emit_signal':True, 'save_image':True}
        hdr_parameters["Image2"] = {'exposure':0, 'gain':0, 'emit_signal':True, 'save_image':True}
        hdr_parameters["Image3"] = {'exposure':0, 'gain':0, 'emit_signal':True, 'save_image':True}
        hdr_parameters["Image4"] = {'exposure':0, 'gain':0, 'emit_signal':True, 'save_image':True}

        return hdr_parameters

    def get_gain(self):
        '''
        get_gain returns the camera gain.
        '''
        
        if hasattr(self.cam.still_configuration.controls, 'AnalogueGain'): 
            val = self.cam.still_configuration.controls.AnalogueGain
        else:
            val = 'Auto'

        return val


    def get_exposure(self):
        '''
        get_exposure returns the camera exposure.
        '''
        if hasattr(self.cam.still_configuration.controls, 'ExposureTime'):
            val = self.cam.still_configuration.controls.ExposureTime
        else:
            val = 'Auto' 
        return val
    
    def get_brightness(self):
        '''
        get_brightness returns the image brightness.
        '''
        if hasattr(self.cam.still_configuration.controls, 'Brightness'):
            val = self.cam.still_configuration.controls.Brightness
        else:
            val = 0 
        return val
    
    def get_color_gain(self):
        '''
        get_color_gain returns the image R & B gains.
        '''
        if hasattr(self.cam.still_configuration.controls, 'ColourGains'):
            val = self.cam.still_configuration.controls.ColourGains
        else:
            val = ('Auto', 'Auto') 
        return val
        

    def get_contrast(self):
        '''
        get_contrast returns the image contrast.
        '''
        if hasattr(self.cam.still_configuration.controls, 'Contrast'):
            val = self.cam.still_configuration.controls.Contrast
        else:
            val = 1
        return val
    
    def get_crop(self):
        '''
        get_crop returns the image crop setting.
        '''
        if hasattr(self.cam.still_configuration.controls, 'ScalarCrop'):
            val = self.cam.still_configuration.controls.ScalarCrop
        else:
            val = None 
        return val
    
    def get_fps(self):
        '''
        get_fps returns frame duration limits for the camera if it is set.
        '''
        if hasattr(self.cam.still_configuration.controls, 'FrameDurationLimits'):
            val = self.cam.still_configuration.controls.FrameDurationLimits
        elif self.save_video:
            val = (33333, 33333)
        else:
            val = (100, 1000000000)
        return val    
    
    def get_noise_reduction(self):
        '''
        get_noise_reduction returns the noise reduction mode.
        '''
        if hasattr(self.cam.still_configuration.controls, 'NoiseReductionMode'):
            val = self.cam.still_configuration.controls.NoiseReductionMode
        elif self.save_video:
            val = 'Fast'
        else:
            val = 'HighQuality'
        return val
        
    def get_saturation(self):
        '''
        get_saturation returns the image saturation.
        '''
        if hasattr(self.cam.still_configuration.controls, 'Saturation'):
            val = self.cam.still_configuration.controls.Saturation
        else:
            val = 1 
        return val
    
    def get_sharpness(self):
        '''
        get_sharpness returns the image sharpness.
        '''
        if hasattr(self.cam.still_configuration.controls, 'Sharpness'):
            val = self.cam.still_configuration.controls.Sharpness
        else:
            val = 1 
        return val

    def get_binning(self):
        '''
        Binning dependent on the pi camera and the mode.
        '''
        return 1


    def set_binning(self, crap):
        '''
        Binning dependent on the pi camera
        
        CM3 does 2x2 binning, mode = 0,1 ; no binning, mode  = 2
        GS 
        '''
        return True


    def enable_HDR_mode(self):
        '''
        Hardware HDR mode is supported by the CM3 (IMX708) Raspberry Pi camera.
        Software HDR is supported with Pi5, but limited with Pi4 and earlier.
        
        TO-DO: HDR implementation
        '''
#         # Pi CM3 HDR enable
#         if (self.camera_name == 'imx708'):
#             from picamera2.devices.imx708 import IMX708
#             cam = IMX708(camera_num)
#             cam.set_sensor_hdr_mode(True)		#on
#             cam.set_sensor_hdr_mode(False)		#off
#             cam.close()
#         
#         # Pi 5 HDR enable
#         with open('/proc/device-tree/model') as f: model = f.read()
#         if 'Raspberry Pi 5' in model:
#             import libcamera
#             cam.set_controls({'HdrMode': libcamera.controls.HdrModeEnum.SingleExposure}) #on
#             cam.set_controls({'HdrMode': libcamera.controls.HdrModeEnum.Off}) #off

        return False


    def disable_hdr_mode(self):
        '''
        Disable HDR mode.
        
        HDR mode is supported by some Raspberry Pis and Pi cameras
        '''

        return False


    def set_hdr_settings(self, hdr_parameters):
        '''
        set_hdr_settings sets the camera's HDR settings

        HDR mode is supported by some Raspberry Pis and Pi cameras.
        Below is only for implementing a pure software implementation.
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

        Note that trigger in this context means a collection event. That is a single image
        when *not* in HDR mode and 4 images when in HDR mode. So, if your cameras are
        configured to collect in HDR mode, you only call this method once and it will
        execute the 4 triggers required for HDR collection.

        cam_list (list): list of camera objects to trigger - empty list triggers all
        image_number (int): current image number - will be used in image filename
        timestamp (datetime): timestamp of the trigger - used to generate image file name
        save_image (bool): Set to True to save the image to disk
        emit_signal (bool): set to True to emit the "imageData" signal after receiving image

        Both the save_image and emit_signal arguments will override these same settings
        for the individual HDR exposures and merged
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
        a timer so we can asynchronously execute the delay.
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
        Picamera2 can hardware trigger through a GPIO pin
        Use trigger_input in picam2.configure()
        '''

        return True

    def set_auto_exposure(self, mode, constraint=None, metering=None):
        '''
        set_auto_exposure sets the camera auto exposure mode. mode can
        be normal (default), short, or long. Optional argurements inlcude
        the auto exposure constraint mode (normal, highlight, shadows),
        and metering mode (center_weighted, spot, matrix).
        '''
        if mode.lower() in ['normal', 'short', 'long']:
            try:
                if mode.lower() == 'normal':
                    self.cam.still_configuration.controls.AeExposureMode = controls.AeExposureModeEnum.Normal
                elif mode.lower() == 'short':
                    self.cam.still_configuration.controls.AeExposureMode = controls.AeExposureModeEnum.Short
                elif mode.lower() == 'long':
                    self.cam.still_configuration.controls.AeExposureMode = controls.AeExposureModeEnum.Long
            except:
                return False
        else:
            self.error.emit(self.camera_name, '''Incorrect or no mode requested,
                normal mode will be used''')
            return False
        if constraint:
            if constraint.lower() in ['normal', 'highlight', 'shadows']:
                try:
                    if constraint.lower()=='normal':
                        self.cam.still_configuration.controls.AeConstraintMode = controls.AeConstraintModeEnum.Normal
                    if constraint.lower()=='highlight':
                        self.cam.still_configuration.controls.AeConstraintMode = controls.AeConstraintModeEnum.Highlight
                    if constraint.lower() == 'shadows':
                        self.cam.still_configuration.controls.AeConstraintMode = controls.AeConstraintModeEnum.Shadows
                except Exception as e:
                    return False
            else:
                self.error.emit(self.camera_name, '''Incorrect AE constraint mode requested,
                    normal mode will be used''')
                return False
        if metering:
            if metering.lower() in ['centerweighted', 'spot', 'matrix']:
                try:
                    if metering.lower() == 'centerweighted':
                        self.cam.still_configuration.controls.AeMeteringMode = controls.AeMeteringModeEnum.CentreWeighted
                    if metering.lower() == 'spot':
                        self.cam.still_configuration.controls.AeMeteringMode = controls.AeMeteringModeEnum.Spot
                    if metering.lower() == 'matrix':
                        self.cam.still_configuration.controls.AeMeteringMode = controls.AeMeteringModeEnum.Matrix
                except:
                    return False
            else:
                self.error.emit(self.camera_name, '''Incorrect AE metering mode requested,
                    default mode will be used''')
                return False
        return True		
        
    def set_exposure(self, exposure):
        '''
        set_exposure sets the camera exposure.
        '''
        if exposure and (exposure >= self.cam.camera_controls['ExposureTime'][0] and
            exposure <= self.cam.camera_controls['ExposureTime'][1]):
            try:
                self.cam.still_configuration.controls.ExposureTime = exposure
                self.exposure = exposure
            except:
                return False
        else:
            self.error.emit(self.camera_name, '''Exposure selected is
                none or outside %d to %d range, using default (auto)'''%(
                self.cam.camera_controls['ExposureTime'][0],self.cam.camera_controls['ExposureTime'][1]))
            return False
        return True

    def set_gain(self, gain):
        '''
        set_gain sets the camera gain. 
        '''
        if gain and (gain >= self.cam.camera_controls['AnalogueGain'][0] and
            gain <= self.cam.camera_controls['AnalogueGain'][1]):
            try:
                self.cam.still_configuration.controls.AnalogueGain = gain
                self.gain = gain
            except:
                return False
        else:
            self.error.emit(self.camera_name, '''Gain selected is
                none or outside %d to %d range, using default (auto)'''%(
                self.cam.camera_controls['AnalogueGain'][0],self.cam.camera_controls['AnalogueGain'][1]))
            return False
        return True
    
    def set_brightness(self, brightness):
        '''
        set_brightness sets the image brightness. 
        '''
        if brightness >= -1 and brightness <= 1:
            try:
                self.cam.still_configuration.controls.Brightness = brightness
            except:
                return False
        else:
            self.error.emit(self.camera_name,
                'Brightness selected is outside -1 to 1 range, using default (0)')
            return False
        return True
    
    def set_color_gain(self, color_gain):
        '''
        set_color_gain sets the image R and B color gains and disable white balancing.
        input color_gain must be tuple of form (Rgain, Bgain).
        '''
        if (isinstance(color_gain, tuple) and len(color_gain)==2 and
            all(g >= 0 and g <= 32 for g in color_gain)):
            try:
                self.cam.still_configuration.controls.ColourGains = color_gain
            except:
                return False
        else:
            self.error.emit(self.camera_name,
                'A color gain is outside 0 to 32 range, no gain applied')
            return False
        return True
    
    def set_contrast(self, contrast):
        '''
        set_contrast sets the image contrast. 
        '''
        if contrast >= 0 and contrast <= 32:
            try:
                self.cam.still_configuration.controls.Contrast = contrast
            except:
                return False
        else:
            self.error.emit(self.camera_name,'''Contrast selected is outside 0
                 to 32 range, using default (1)''')
            return False
        return True
    
    def set_crop(self, box):
        '''
        set_crop sets the area used to crop and scale the image and achieve digital
        pan and zoom. Box is tuple defining crop area by x, y, w, h.
        '''
        if (isinstance(box,tuple) and len(box) == 4 and
            all(x >= 0 for x in box[:2]) and all(x >=1 for x in box[2:]) and
            box[0] >= self.cam.sensor_modes[self.mode]['crop_limits'][0] and
            box[0]+box[2] <= self.cam.sensor_modes[self.mode]['crop_limits'][2] and
            box[1] >= self.cam.sensor_modes[self.mode]['crop_limits'][1] and
            box[1]+box[3] <= self.cam.sensor_modes[self.mode]['crop_limits'][3]):
            try:
                self.cam.still_configuration.controls.ScalerCrop = box
            except:
                return False
        else:
            self.error.emit(self.camera_name, '''Issue with crop area requested.
                Check that it is a tuple with 4 postive values and requested area
                doesn't exceed sensor size''')
            return False
        return True
        
    def set_fps(self, fps):
        '''
        set_fps sets the frames per second that the camera will capture.
        '''
        if (fps >= (1/self.cam.camera_controls['FrameDurationLimits'][1])*1e6
            and fps <= self.cam.sensor_modes[self.mode]['fps']):
            try:
                rate = round((1/fps)*1e6)
                self.cam.still_configuration.controls.FrameDurationLimits = (rate, rate)
            except:
                return False
        else:
            self.error.emit(self.camera_name, 'Requested fps is not valid')
            return False
        return True
    
    def set_noise_reduction(self, nr_mode):
        '''
        set_noise_reduction sets the mode used for noise reduction. 
        '''
        if nr_mode.lower() in ['off', 'fast', 'highquality']:
            try:
                if nr_mode.lower() == 'off':
                    self.cam.still_configuration.controls.NoiseReductionMode = controls.draft.NoiseReductionModeEnum.Off
                elif nr_mode.lower() == 'fast':
                    self.cam.still_configuration.controls.NoiseReductionMode = controls.draft.NoiseReductionModeEnum.Fast
                elif nr_mode.lower() == 'highquality':
                    self.cam.still_configuration.controls.NoiseReductionMode = controls.draft.NoiseReductionModeEnum.HighQuality
            except:
                return False
        else:
            self.error.emit(self.camera_name, '''Incorrect or no mode requested,
                picamera2 will select best mode''')
            return False
        return True																		
        
    def set_saturation(self, sat):
        '''
        set_saturation sets the image saturation. 
        '''
        if sat >= 0 and sat <= 32:
            try:
                self.cam.still_configuration.controls.Saturation = sat
            except:
                return False
        else:
            self.error.emit(self.camera_name,
                'Saturation selected is outside 0 to 32 range, using default (1)')
            return False

        return True

    def set_sharpness(self, sharp):
        '''
        set_sharpness sets the image sharpness. 
        '''
        if sharp >= 0 and sharp <= 16:
            try:
                self.cam.still_configuration.controls.Sharpness = sharp
            except:
                return False
        else:
            self.error.emit(self.camera_name,
                'Saturation selected is outside 0 to 16 range, using default (1)')
            return False

        return True

    def get_image(self):
        '''get_image gets the next image from the camera buffers, does some error
        checking, converts the image, and then returns it.
        '''
        #  define the return dict
        image_data = {'data':None, 'ok':False, 'exposure':-1, 'gain':-1, 'is_hdr':False}

        #  get the image
        try:
            job = self.cam.capture_array(wait=False)
            try:
                raw_image = job.get_result(timeout=2) 	#Wait 2s for capture
                #  populate the return dict
                image_data['data'] = raw_image.copy()
                image_data['ok'] = True
                image_data['height'] = raw_image.shape[1]
                image_data['width'] = raw_image.shape[0]
                
                if self.exposure:
                	image_data['exposure'] = self.exposure
                else: 
                    image_data['exposure'] = Metadata(self.cam.capture_metadata()).ExposureTime
                if self.gain:    
                    image_data['gain'] = self.gain
                else:
                    image_data['gain'] = Metadata(self.cam.capture_metadata()).AnalogueGain

            except TimeoutError:
                self.error.emit(self.camera_name, 'Time out waiting for image...')

        except Exception as e:
            self.error.emit(self.camera_name, 'Capture error: %s'%e)

        #  and return the converted one
        return image_data


    def set_pixel_format(self, format):
        '''
        set_pixel_format sets the image fomat.
        '''
        try:
            self.cam.still_configuration.format = format
        except:
            return False
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
            # Set camera configuration
            self.cam.still_configuration.queue = False
            if self.save_video:
                # TODO: may need to include format, colour_space?
                if (self.cam.still_configuration.buffer_count < 6):
                    self.cam.still_configuration.buffer_count = 6
                if not self.cam.still_configuration.encode:
                    self.cam.still_configuration.encode = 'main'
                #TODO: if flag for not user set
                self.cam.still_configuration.size = (1280, 720)
                self.cam.still_configuration.controls.NoiseReductionMode = controls.draft.NoiseReductionModeEnum.Fast
            
            self.cam.configure(self.cam.create_still_configuration())

            # Start preview
            self.cam.start_preview(Preview.NULL)
            
            # Start camera
            self.cam.start()
            
            #  read a few frames to get things rolling
            for i in range(5):
                raw_image = self.cam.capture_array()

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
            self.cam.stop_preview()
            self.cam.stop()
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

    def get_white_balance(self):
        '''
        get_gain returns the camera gain.
        '''
        if hasattr(self.cam.still_configuration.controls, 'AwbEnable'):
            val = self.cam.still_configuration.controls.AwbEnable
        else:
            val = True    
        if hasattr(self.cam.still_configuration.controls, 'AwbMode'): 
            mode = self.cam.still_configuration.controls.AwbMode
        else:
            mode = 'Auto'
        return val, mode
            
    def set_white_balance(self, wb_mode):
        '''
        set_white_balance sets the mode used for white balancing. 
        '''
        if wb_mode.lower() in ['auto', 'tungsten', 'fluorescent', 'indoor',
                'daylight', 'cloudy']:
            try:
                if wb_mode.lower() == 'auto':
                    self.cam.still_configuration.controls.AwbMode = controls.AwbModeEnum.Auto
                elif wb_mode.lower() == 'tungsten':
                    self.cam.still_configuration.controls.AwbMode = controls.AwbModeEnum.Tungsten
                elif wb_mode.lower() == 'fluorescent':
                    self.cam.still_configuration.controls.AwbMode = controls.AwbModeEnum.Fluorescent
                elif wb_mode.lower() == 'indoor':
                    self.cam.still_configuration.controls.AwbMode = controls.AwbModeEnum.Indoor
                elif wb_mode.lower() == 'daylight':
                    self.cam.still_configuration.controls.AwbMode = controls.AwbModeEnum.Daylight
                elif wb_mode.lower() == 'cloudy':
                    self.cam.still_configuration.controls.AwbMode = controls.AwbModeEnum.Cloudy
                self.cam.still_configuration.controls.AwbEnable = True
            except:
                return False
        elif (wb_mode.lower() == 'none'):
            try:
                self.cam.still_configuration.controls.AwbEnable = False
            except:
                return False
        else:
            self.cam.still_configuration.controls.AwbMode = controls.AwbModeEnum.Auto
            self.cam.still_configuration.controls.AwbEnable = True
            self.error.emit(self.camera_name, '''Incorrect white balance mode,
                default (auto) will be used''')
        return True


    def __sync_settings(self, nodemap=None):
        '''__sync_settings will trigger the camera a few times to push settings into the
        CMOS ASIC so the next trigger executed will return images with the specified
        settings. When in trigger mode, most CMOS cameras will require 1-2 triggers for
        a setting to be active. (Normally 1 trigger, but changing HDR settings and
        activating HDR will take 2 triggers.)

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
