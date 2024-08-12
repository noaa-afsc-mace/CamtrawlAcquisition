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
.. module:: CamtrawlAcquisition.SpinCamera

    :synopsis: SpinCamera provides a high level interface for FLIR machine
               vision cameras using the FLIR PySpin SDK.

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

from PyQt5 import QtCore
import os
import logging
import datetime
from time import sleep
import PySpin
import ImageWriter
import numpy as np
import cv2


class SpinCamera(QtCore.QObject):
    """
    This class provides a high level interface to Point Grey/Flir machine
    vision cameras using the Flir pySpin Spinnaker SDK.

    This class is designed to acquire single images from a Flir MV camera
    using triggering. While it can be used to write video files, it is not
    optimized to record video at high frame rates.

    During the development of this class I stumbled on a bug in the
    Chameleon3 firmware where "chunk data" exposure values were 1 frame ahead
    of the actual exposure. This is related to the fact that with CMOS cameras
    settings lag 2 triggers. When you apply a setting such as exposure, it is
    copied to the camera register. On the next trigger it will be copied to the
    sensor registers and the image returned will have the old exposure. The next
    trigger will return an image with the commanded exposure. The Chameleon3
    firmware didn't take this into account.

    If when in HDR mode, your camera is not syncing correctly and the reported
    exposures do not match the actual exposures then you may need to update your
    camera's firmware.

    More info on when settings take effect can be found here:

    https://www.flir.com/support-center/iis/machine-vision/knowledge-base/when-does-a-change-to-a-camera-setting-take-effect/

    """

    #  Specify the delay, in ms, required after the exposure ends before the
    #  camera will be ready for the next software trigger. I'm not sure if this
    #  is consistent between platforms. If you're using HDR and software
    #  triggering and the camera is getting stuck while acquiring the 4 images
    #  make this delay bigger. Start with something big, like 250 or 500. If
    #  that doesn't fix it, you have another issue and set it back. But if it
    #  does reliably acquire HDR sequences, start working back until you get
    #  the smallest value that works.
    #
    #  The total HDR exposure in us will be the sum of your individual HDR exposures
    #  plus (3x this value * 1000).
    HDR_SW_TRIG_DELAY = 250

    #  Specify the number of frames that should be discarded when starting acquisition
    #  to ensure that the first triggered image has the expected settings.
    SETTINGS_LAG = 2

    #  define PyQt Signals
    imageData = QtCore.pyqtSignal(str, str, dict)
    saveImage = QtCore.pyqtSignal(str, dict)
    imageSaved = QtCore.pyqtSignal(object, str)
    videoSaved = QtCore.pyqtSignal(str,str, int, int, datetime.datetime, datetime.datetime)
    error = QtCore.pyqtSignal(str, str)
    acquisitionStarted = QtCore.pyqtSignal(object, str, bool)
    stoppingAcquisition = QtCore.pyqtSignal()
    acquisitionStopped = QtCore.pyqtSignal(object, str, bool)
    triggerReady = QtCore.pyqtSignal(object, int, bool)
    triggerComplete = QtCore.pyqtSignal(object, bool)


    def __init__(self, spin_cam, parent=None):

        super(SpinCamera, self).__init__(parent)

        self.cam = spin_cam
        self.device_info = None
        self.rotation = 'none'
        self.timeout = 2000
        if PySpin.FLIR_SPINNAKER_VERSION_MAJOR > 2:
            self.raw_conversion = PySpin.SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR
        else:
            self.raw_conversion = PySpin.HQ_LINEAR
        self.hdr_enabled = False
        self.hdr_save_merged = False
        self.hdr_signal_merged = False
        self.hdr_merge_method = 'mertens'
        self.hdr_is_syncing = False
        self.hdr_tonemap_saturation = 1.0
        self.hdr_tonemap_bias = 0.85
        self.hdr_tonemap_gamma = 2.0
        self.hdr_images = [None] * 4
        self.acquiring = False
        self.save_path = '.'
        self.date_format = "D%Y%m%d-T%H%M%S.%f"
        self.trig_timestamp = None
        self.trigger_mode = PySpin.TriggerSource_Software
        self.n_triggered = 0
        self.total_triggers = 0
        self.save_stills_divider = 1
        self.save_stills = True
        self.save_video = False
        self.save_video_divider = 1
        self.trigger_divider = 1
        self.dbResponse = None
        self.label = 'camera'
        self.ND_pixelFormat = PySpin.PixelFormat_BGR8 #PySpin.PixelFormat_BGR16
        self.logger = logging.getLogger('Acquisition')

        # Retrieve TL device nodemap and extract device information
        nodemap_tldevice = self.cam.GetTLDeviceNodeMap()
        self.device_info = self.get_device_info(nodemap_tldevice)
        self.camera_name = self.device_info['DeviceModelName'] + '_' + \
                self.device_info['DeviceSerialNumber']
        self.camera_id = self.device_info['DeviceSerialNumber']

        # Initialize camera
        self.cam.Init()

        #  get some basic properties
        self.exposure = self.cam.ExposureTime.GetValue()
        self.gain = self.cam.Gain.GetValue()
        self.pixelFormat = self.cam.PixelFormat.GetValue()
        self.auto_exposure = bool(self.cam.ExposureAuto.GetValue())

        #  initialize the HDR parameters
        self.hdr_parameters = self.get_hdr_settings()

        #  create a timer to handle software trigger sequencing
        self.sw_trig_timer = QtCore.QTimer(self)
        self.sw_trig_timer.timeout.connect(self.software_trigger)
        self.sw_trig_timer.setSingleShot(True)


    def get_hdr_settings(self, nodemap=None):
        '''
        get_hdr_settings queries the camera and returns the camera's HDR settings in a dict
        '''

        hdr_parameters = {}
        hdr_parameters["Image1"] = {'exposure':0, 'gain':0, 'emit_signal':True, 'save_image':True}
        hdr_parameters["Image2"] = {'exposure':0, 'gain':0, 'emit_signal':True, 'save_image':True}
        hdr_parameters["Image3"] = {'exposure':0, 'gain':0, 'emit_signal':True, 'save_image':True}
        hdr_parameters["Image4"] = {'exposure':0, 'gain':0, 'emit_signal':True, 'save_image':True}

        #  get the current state of hdr_enabled so we can set it back when we're done
        hdr_enabled = self.hdr_enabled

        if nodemap is None:
            nodemap = self.cam.GetNodeMap()

        #  NOTE: As of Spinnaker 2.2.0.48 the HDR node names have changed but were not updated in the examples.
        hdr_image_selector = PySpin.CEnumerationPtr(nodemap.GetNode("pgrHDRImageSelector"))
        hdr_exposure_abs = PySpin.CFloatPtr(nodemap.GetNode("pgrHDR_ExposureTimeAbs"))
        hdr_gain_abs = PySpin.CFloatPtr(nodemap.GetNode("pgrHDR_GainAbs"))

        #  enable HDR mode so we can access the HDR parameter nodes.
        self.enable_hdr_mode(nodemap=nodemap)

        #  make sure we can access the required nodes
        if (not self.check_node_accessibility(hdr_image_selector) or
            not self.check_node_accessibility(hdr_exposure_abs) or
            not self.check_node_accessibility(hdr_gain_abs)):
            return hdr_parameters

        # Extract the HDR settings from the camera
        for k in hdr_parameters.keys():
            hdr_image_selector.SetIntValue(hdr_image_selector.GetEntryByName(k).GetValue())
            hdr_parameters[k]['exposure'] = hdr_exposure_abs.GetValue()
            hdr_parameters[k]['gain'] = hdr_gain_abs.GetValue()

        #  check if we should disable HDR mode
        if not hdr_enabled:
            self.disable_hdr_mode(nodemap=nodemap)

        return hdr_parameters


    def enable_hdr_mode(self, nodemap=None):
        '''enable_hdr_mode enables the HDR sequencer in the camera and results in
        collecting 4 images per "trigger" where each image has a unique exposure
        and gain value. This can be used to generate images with much higher dynamic
        ranges for scenes that are relatively static.
        '''

        if not self.hdr_enabled:

            if nodemap is None:
                nodemap = self.cam.GetNodeMap()

            node_hdr_enabled = PySpin.CBooleanPtr(nodemap.GetNode("pgrHDRModeEnabled"))

            #  check if we can write to the HDR mode node
            if self.check_node_accessibility(node_hdr_enabled):
                #  we can, enable HDR mode
                node_hdr_enabled.SetValue(True)
                self.hdr_enabled = True

                #  if we're currently acquiring, sync hdr. Otherwise sync will
                #  happen when the camera starts acquiring.
                if self.acquiring:
                    self.__sync_hdr(nodemap)

            else:
                self.hdr_enabled = False

        return self.hdr_enabled


    def disable_hdr_mode(self, nodemap=None):
        '''disable_hdr_mode disables HDR acquisition.
        '''

        if self.hdr_enabled:

            if nodemap is None:
                nodemap = self.cam.GetNodeMap()

            node_hdr_enabled = PySpin.CBooleanPtr(nodemap.GetNode("pgrHDRModeEnabled"))

            #  check if we can write to the HDR mode node
            if self.check_node_accessibility(node_hdr_enabled):
                #  we can, disable HDR mode
                node_hdr_enabled.SetValue(False)
                self.hdr_enabled = False

        return self.hdr_enabled


    def set_strobe_trigger(self, io_line, invert=True):

        try:
            self.cam.LineSelector.SetValue(io_line)
            self.cam.LineMode.SetValue(1)
            self.cam.LineSource.SetIntValue(1)
            self.cam.LineInverter.SetValue(invert)
            ok = True
        except:
            ok = False

        return ok


    def set_hdr_settings(self, hdr_parameters, nodemap=None):
        '''
        set_hdr_settings sets the camera's HDR settings
        '''

        if nodemap is None:
            nodemap = self.cam.GetNodeMap()

        hdr_image_selector = PySpin.CEnumerationPtr(nodemap.GetNode("pgrHDRImageSelector"))
        hdr_exposure_abs = PySpin.CFloatPtr(nodemap.GetNode("pgrHDR_ExposureTimeAbs"))
        hdr_gain_abs = PySpin.CFloatPtr(nodemap.GetNode("pgrHDR_GainAbs"))

        #  enable HDR mode so we can access the HDR parameter nodes
        self.enable_hdr_mode(nodemap=nodemap)

        #  make sure we can access the other required nodes
        if (not self.check_node_accessibility(hdr_image_selector) or
            not self.check_node_accessibility(hdr_exposure_abs) or
            not self.check_node_accessibility(hdr_gain_abs)):
            return False

        # Extract the HDR settings from the camera
        for k in hdr_parameters.keys():
            hdr_image_selector.SetIntValue(hdr_image_selector.GetEntryByName(k).GetValue())
            hdr_exposure_abs.SetValue(hdr_parameters[k]['exposure'])
            hdr_gain_abs.SetValue(hdr_parameters[k]['gain'])

        #  update the internal HDR parameteres dict
        self.hdr_parameters = hdr_parameters

        #  check if we should disable HDR mode
        if not self.hdr_enabled:
            self.disable_hdr_mode(nodemap=nodemap)

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

        #  if we're running in auto exposure mode, update the exposure value to
        #  ensure that this instance is in sync with the camera.
        if self.auto_exposure:
            self.exposure = self.cam.ExposureTime.GetValue()
            #print(self.exposure)

        #  check if we should trigger because of the divider
        if (self.total_triggers % self.trigger_divider) != 0:
            #  nope, don't trigger. If we're hardware triggered we still need
            #  to signal that we *shouldn't* trigger
            if (self.trigger_mode != PySpin.TriggerSource_Software):
                #  send an exposure of 0 for this camera so it is not triggered
                self.triggerReady.emit(self, 0, False)
            else:
                #  and if we're software triggering, we emit the complete signal
                #  but unset the trigger argument so acquisition knows the camera
                #  trigger was skipped.
                self.triggerComplete.emit(self, False)
            return

        #  If specific cameras are specified, check if we're one
        if (len(cam_list) > 0 and self not in cam_list):
            #  nope, don't trigger. If we're hardware triggered we still need
            #  to signal that we *shouldn't* trigger
            if (self.trigger_mode != PySpin.TriggerSource_Software):
                #  send an exposure of 0 for this camera so it is not triggered
                self.triggerReady.emit(self, 0, False)
            else:
                #  and if we're software triggering, we emit the complete signal
                #  but unset the trigger argument so acquisition knows the camera
                #  trigger was skipped.
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

        #  generate the filename(s) and
        if (self.hdr_enabled):
            #  for HDR images we add the exposure and gain values to the image number section
            n = 1
            for e in self.hdr_parameters:
                exp_str = '%d-%d-%d' % (n, self.hdr_parameters[e]['exposure'],
                    self.hdr_parameters[e]['gain'])
                self.filenames.append(self.save_path + num_str + '_' + time_str +
                    '_' + self.camera_id + '_HDR-' + exp_str)

                self.exposures.append(self.hdr_parameters[e]['exposure'])
                if emit_signal:
                    self.do_signals.append(self.hdr_parameters[e]['emit_signal'])
                else:
                    self.do_signals.append(False)
                if save_image:
                    self.save_image.append(self.hdr_parameters[e]['save_image'])
                else:
                    self.save_image.append(False)
                n += 1

            #  check if we're saving or emitting a merged HDR file
            if (self.hdr_save_merged and save_image) or \
                (self.hdr_signal_merged and emit_signal):

                self.hdr_merged_filename = (self.save_path + num_str + '_' + time_str +
                    '_' + self.camera_id + '_HDR-merged')
                if save_image:
                    self.save_hdr = self.hdr_save_merged
                if emit_signal:
                    self.emit_hdr = self.hdr_signal_merged

        else:
            #  single images follow the "standard" camtrawl naming convention
            self.filenames.append(self.save_path + num_str + '_' + time_str +
                    '_' + self.camera_id)

            self.exposures.append(self.exposure)
            if emit_signal:
                self.do_signals.append(True)
            else:
                self.do_signals.append(False)
            if save_image:
                self.save_image.append(True)
            else:
                self.save_image.append(False)


        self.logger.debug("%s triggered: Image number %d Save image: %s" %
                (self.camera_name, image_number, save_image))

        #  trigger the camera if we're using software triggering
        if (self.trigger_mode == PySpin.TriggerSource_Software):
            #  Software trigger the camera
            self.sw_trig_timer.start(5)
        else:
            #  for hardware triggering, emit the triggerReady signal.
            self.triggerReady.emit(self, self.exposures[0], False)


    @QtCore.pyqtSlot()
    def software_trigger(self):
        '''software_trigger is the slot called when the sw_trig_timer expires.

        Most cameras require some delay before they can software trigger. We use
        a timer so we an asynchronously execute the delay.
        '''
        self.cam.TriggerSoftware.Execute()


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
        #  without calling SpinCamera.trigger method.
        if self.n_triggered == 0:
            return

        if self.trig_timestamp is None:
            return

        #  get the index this event is associated with
        idx = self.n_triggered - 1

        #  If we're in HDR mode, we need to check if we have to trigger again. In HDR mode
        #  this class handles triggering the camera for each of the 4 exposures.
        if self.hdr_enabled and self.n_triggered < 4:
            #  Yes, we're doing HDR and we've triggered less than 4 times - trigger again
            print("HDR TRIGGER")
            self.n_triggered = self.n_triggered + 1
            if self.trigger_mode == PySpin.TriggerSource_Software:
                #  If we're software triggering in HDR mode, we have to delay our
                #  trigger to allow the camera to get ready.
                self.sw_trig_timer.start(SpinCamera.HDR_SW_TRIG_DELAY)
            else:
                #  for hardware triggering, emit the trigger signal.
                self.triggerReady.emit(self, self.exposures[idx], True)

        #  get the next image from the camera buffers
        image_data = self.get_image()

        #  add some metadata to the image_data dict
        image_data['timestamp'] = self.trig_timestamp
        image_data['filename'] = self.filenames[idx]
        image_data['image_number'] = self.image_number

        #  add the save_still and save_frame states - this is used by
        #  the image_writer to determine if an image should be written as
        #  an image file and/or a video frame and it is logged in the
        #  database images table to track stills and frames saved.
        image_data['save_still'] = self.save_this_still
        image_data['save_frame'] = self.save_this_frame

        #  only rotate good images
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

                #  check if we need to keep a copy of this image
                if self.save_hdr or self.emit_hdr:
                    #  save a reference to this image because we're going
                    #  to merge the HDR images when the sequence is done.
                    self.hdr_images[idx] = image_data

        else:
            #  there was a problem receiving image

            #  if we're in hdr mode we'll need to bail
            if self.hdr_enabled:
                #  cancel any pending software triggers
                self.sw_trig_timer.stop()

                #  sync the camera
                self.__sync_hdr()

                #  unset save and emit HDR states since we can't merge
                self.save_hdr = False
                self.emit_hdr = False

                #  free the references to any HDR images we did capture
                self.hdr_images = [None] * 4

                #  force idx=3 to end hdr sequence
                idx = 3

                #  and emit an error
                self.error.emit(self.camera_name, 'HDR Sequence aborted.')

            #  we still emit a signal even if the image is "bad"
            if self.do_signals[idx]:
                self.imageData.emit(self.camera_name, self.label, image_data)


        #  check if this is the last image in our sequence.
        if (not self.hdr_enabled) or idx == 3:

            #  If we're in hdr mode, check if we're merging the image
            #  TODO: The code below for merging these frames needs work. I got most of this
            #        from a few OpenCV examples on the web but I don't get reasonable results
            #        so I am obviously missing something.
            if self.save_hdr or self.emit_hdr:

                #  merge the HDR exposures
                merged_image = {}
                images = []
                exposures = []
                for image in self.hdr_images:
                    images.append(image['data'])
                    #  store the *inverse* exposure
                    exposures.append(1.0 / (image['exposure'] / 1000000.))
                exposures = np.array(exposures, dtype=np.float32)

                if self.hdr_merge_method.lower() == 'mertens':
                    #  mertens (at least how it is implemented here) performs image fusion
                    #  and does not generate a true HDR iamge
                    merge_mertens = cv2.createMergeMertens()
                    merge_mertens.setContrastWeight(0.005)
                    #merge_mertens.setSaturationWeight(0.1)
                    hdr_data = merge_mertens.process(images)

                    #  per OpenCV docs - it is recommenced to perform linear tonemapping on the result
                    #tonemap = cv2.createTonemap(self.hdr_tonemap_gamma)
                    #hdr_data = tonemap.process(hdr_data)

                    #  convert to uint8
                    hdr_data = np.clip(hdr_data*255, 0, 255).astype('uint8')

                    #  we either linear tonemap or gamma correct
                    invGamma = 1.0 / self.hdr_tonemap_gamma
                    table = np.array([((i / 255.0) ** invGamma) * 255
                        for i in np.arange(0, 256)]).astype("uint8")

                    # apply gamma correction using the lookup table
                    hdr_data = cv2.LUT(hdr_data , table)

                    merged_image['is_hdr'] = False

                elif self.hdr_merge_method.lower() == 'debevec':
                    if self.dbResponse is None:
                        calibrateDebevec = cv2.createCalibrateDebevec()
                        self.dbResponse = calibrateDebevec.process(images, exposures)

                    merge_debevec = cv2.createMergeDebevec()
                    hdr_data = merge_debevec.process(images, exposures, self.dbResponse)

                    tonemap = cv2.createTonemap(gamma=1.5)
                    hdr_data = tonemap.process(hdr_data)

                    merged_image['is_hdr'] = True

                elif self.hdr_merge_method.lower() == 'robertson':
                    merge_robertson = cv2.createMergeRobertson()
                    hdr_data = merge_robertson.process(images, times=exposures)
                    tonemap = cv2.createTonemap(gamma=1.5)
                    hdr_data = tonemap.process(hdr_data)
                    merged_image['is_hdr'] = True

                #  create an image dict with the merged image data
                merged_image['data'] = hdr_data
                merged_image['height'] = image['height']
                merged_image['width'] = image['width']
                merged_image['timestamp'] = image['timestamp']
                merged_image['filename'] = self.hdr_merged_filename
                merged_image['image_number'] = self.image_number
                merged_image['ok'] = True

                #  add the save_still and save_frame states - this is used by
                #  the image_writer to determine if an image should be written as
                #  an image file and/or a video frame and it is logged in the
                #  database images table to track stills and frames saved.
                merged_image['save_still'] = self.save_this_still
                merged_image['save_frame'] = self.save_this_frame

                #  and emit our image signals
                if self.emit_hdr:
                    self.imageData.emit(self.camera_name, self.label, merged_image)
                if self.save_hdr:
                    self.saveImage.emit(self.camera_name, merged_image)

                #  free the references to the source images
                self.hdr_images = [None] * 4


            #  if we're here, we are done with this trigger event
            self.triggerComplete.emit(self, True)
            self.n_triggered = 0
            self.trig_timestamp = None


    def load_hdr_response(self, filename):
        '''load_hdr_response loads a numpy file containing the camera sensor response data
        which is used for certain HDR image fusion methods.
        '''
        #TODO Implement this feature
        raise NotImplementedError()


    def set_camera_trigger(self, mode, source=PySpin.TriggerSource_Line0, edge='rising'):
        '''
            modes: 'Software' - camera is configured to software triggered when trigger method is called
                   'Hardware' - camera is configured to be hardware triggered
                   'None' - triggers are disabled
            source:int value specifying the trigger source for hardware triggering. Default PySpin.TriggerSource_Line0
            edge:  'rising' - trigger on the leading edge of the signal (default)
                   'falling - trigger on the falling edge of the signal
        '''

        result = True

        try:
            #  Turn triggering off
            self.cam.TriggerMode.SetValue(PySpin.TriggerMode_Off)

            #  Set the trigger source to FrameStart
            self.cam.TriggerSource.SetValue(PySpin.TriggerSelector_FrameStart)

            #  Set the trigger edge to activate on
            if edge.lower() == 'falling':
                #  Set the trigger to the falling edge
                self.cam.TriggerActivation.SetValue(PySpin.TriggerActivation_FallingEdge)
            else:
                #  Set the trigger to the rising edge
                self.cam.TriggerActivation.SetValue(PySpin.TriggerActivation_RisingEdge)

            if mode.lower() in ['hardware', 'software']:
                if mode.lower() == 'software':
                    self.trigger_mode = PySpin.TriggerSource_Software
                else:
                    self.trigger_mode = source

                #  set the trigger mode
                self.cam.TriggerSource.SetValue(self.trigger_mode)

                #  and enable triggering
                self.cam.TriggerMode.SetValue(PySpin.TriggerMode_On)
            else:
                #  disable triggering
                self.cam.TriggerMode.SetValue(PySpin.TriggerMode_Off)
                self.trigger_mode = None


        except PySpin.SpinnakerException as ex:
            self.error.emit(self.camera_name, 'Error: %s' % ex)
            result = False

        return result


    def set_binning(self, bin_value):
        '''
        set_binning enables pixel binning on the camera (if supported).
        Binning increases the sensitivity of the camera at the expense of
        resolution.

        Valid bin values are 1 (disabled), 2, and possibly 4 or even 8
        depending on your camera's sensor
        '''

        result = True

        try:

            # Not all cameras support both vertical and horizontal
            # binning and of those that do, sometimes horizontal is
            # linked to vertical and is not writable so we have to
            # check if the nodes exist and are writable.

            #  first get the nodemap
            nodemap = self.cam.GetNodeMap()

            #  Get the vertical binning node
            node = nodemap.GetNode('BinningVertical')
            #  check if it exists and is writable
            if self.check_node_accessibility(node):
                #  check if we should set or disable binning
                if bin_value in [2,4,8,16]:
                    #  clamp the bin value to the max
                    if bin_value > self.cam.BinningVertical.GetMax():
                        bin_value = self.cam.BinningVertical.GetMax()
                    #  set the vertical binning.
                    self.cam.BinningVertical.SetValue(bin_value)
                else:
                    #  disable binning
                    self.cam.BinningVertical.SetValue(1)

                #  now make sure the height is set correctly. The height
                #  will automatically be reduced when increasing binning
                #  but it will not be increased when you reduce or disable
                #  binning so we force it here.
                node = nodemap.GetNode('Height')
                if self.check_node_accessibility(node):
                    self.cam.Height.SetValue(self.cam.HeightMax.GetValue())


            #  now do the same thing for horizontal binning
            node = nodemap.GetNode('BinningHorizontal')
            if self.check_node_accessibility(node):
                if bin_value in [2,4,8,16]:
                    if bin_value > self.cam.BinningHorizontal.GetMax():
                        bin_value = self.cam.BinningHorizontal.GetMax()
                    self.cam.BinningHorizontal.SetValue(bin_value)
                else:
                    self.cam.BinningHorizontal.SetValue(1)

                node = nodemap.GetNode('Width')
                if self.check_node_accessibility(node):
                    self.cam.Width.SetValue(self.cam.WidthMax.GetValue())

        except PySpin.SpinnakerException as ex:
            self.error.emit(self.camera_name, 'Error: %s' % ex)
            result = False

        return result


    def get_binning(self):

        #  we'll assume that binning has been set on the camera by this
        #  class which means both the vertical and horizontal binning
        #  will be the same. Thus we just read the vertical binning
        #  and return that.

        try:
            binning = self.cam.BinningVertical.GetValue()

        except PySpin.SpinnakerException as ex:
            self.error.emit(self.camera_name, 'Error: %s' % ex)
            binning = None

        return binning


    def set_exposure(self, exposure_us, nodemap=None):

        result = True

        try:
            #  manual exposure for values > 0 otherwise we enable auto exposure
            if (exposure_us > 0):

                #  First need to disable auto exposure
                self.cam.ExposureAuto.SetValue(PySpin.ExposureAuto_Off)
                self.auto_exposure = False

                # Set the exposure. Make sure exposure doesn't exceed the camera min/max
                exposure_time_to_set = min(self.cam.ExposureTime.GetMax(), exposure_us)
                exposure_time_to_set = max(self.cam.ExposureTime.GetMin(), exposure_time_to_set)
                self.cam.ExposureTime.SetValue(exposure_time_to_set)

            else:
                #  get the auto exposure bounds and the current exposure setting, then clamp
                #  the current exposure setting to the auto bounds. This ensures that the first
                #  image acquired after switching to auto exposure falls within the limits.

                if nodemap is None:
                    nodemap = self.cam.GetNodeMap()

                auto_exp_min = PySpin.CFloatPtr(nodemap.GetNode("AutoExposureTimeLowerLimit")).GetValue()
                auto_exp_max = PySpin.CFloatPtr(nodemap.GetNode("AutoExposureTimeUpperLimit")).GetValue()
                exposure_time_to_set = self.cam.ExposureTime.GetValue()

                #  clamp exposure value if necessary
                if exposure_time_to_set < auto_exp_min:
                    self.cam.ExposureTime.SetValue(auto_exp_min)
                    exposure_time_to_set = auto_exp_min
                elif exposure_time_to_set > auto_exp_max:
                    self.cam.ExposureTime.SetValue(auto_exp_max)
                    exposure_time_to_set = auto_exp_max

                #  now turn on auto exposure
                self.cam.ExposureAuto.SetValue(PySpin.ExposureAuto_Continuous)
                self.auto_exposure = True

            #  update our exposure value
            self.exposure = exposure_time_to_set

        except PySpin.SpinnakerException as ex:
            self.error.emit(self.camera_name, 'Error: %s' % ex)
            result = False

        return result


    def get_exposure(self):

        try:
            exposure_us = self.cam.ExposureTime.GetValue()

        except PySpin.SpinnakerException as ex:
            self.error.emit(self.camera_name, 'Error: %s' % ex)
            exposure_us = None

        return exposure_us


    def set_gain(self, gain):

        result = True

        try:
            #  manual gain for values > 0 otherwise we enable auto gain
            if (gain > 0):

                if self.cam.GainAuto.GetAccessMode() != PySpin.RW:
                    self.error.emit(self.camera_name, 'Unable to disable automatic gain - GainAuto node is read-only.')
                    return False
                else:
                    self.cam.GainAuto.SetValue(PySpin.GainAuto_Off)

                #  check if we can access the exposure time node
                if self.cam.Gain.GetAccessMode() != PySpin.RW:
                    self.error.emit(self.camera_name, 'Unable to set gain - Gain node is read-only.')
                    return False

                # Set the exposure. Make sure exposure doesn't exceed the camera min/max
                gain_to_set = min(self.cam.Gain.GetMax(), gain)
                gain_to_set = max(self.cam.Gain.GetMin(), gain_to_set)
                self.cam.Gain.SetValue(gain_to_set)
                self.gain = gain_to_set

            else:
                #  turn on auto exposure
                if self.cam.GainAuto.GetAccessMode() != PySpin.RW:
                    self.error.emit(self.camera_name, 'Unable to enable automatic gain - GainAuto node is read-only.')
                    return False
                else:
                    self.cam.GainAuto.SetValue(PySpin.GainAuto_Continuous)

        except PySpin.SpinnakerException as ex:
            self.error.emit(self.camera_name, 'Error: %s' % ex)
            result = False

        return result


    def get_gain(self):

        try:
            gain = self.cam.Gain.GetValue()

        except PySpin.SpinnakerException as ex:
            self.error.emit(self.camera_name, 'Error: %s' % ex)
            gain = None

        return gain


    def get_image(self):
        '''get_image gets the next image from the camera buffers, does some error
        checking, converts the image, and then returns it.
        '''
        #  define the return dict
        image_data = {'data':None, 'ok':False, 'exposure':-1, 'gain':-1, 'is_hdr':False}

        #  get the image
        try:
            raw_image = self.cam.GetNextImage(self.timeout)
        except:
            #  timed out waiting for image
            self.error.emit(self.camera_name, 'Timed out waiting for image...')
            return image_data

        #  check if it is complete
        if raw_image.IsIncomplete():
            #  image is incomplete - emit error
            self.error.emit(self.camera_name, 'Image incomplete with image status %d ...' %
                    raw_image.GetImageStatus())
            raw_image.Release()
            return image_data

        #  get the chunk data
        chunk_data = raw_image.GetChunkData()

        #  convert from raw to our preferred Numpy format
        if PySpin.FLIR_SPINNAKER_VERSION_MAJOR > 2:
            converted_image = self.processor.Convert(raw_image, self.ND_pixelFormat)
        else:
            converted_image = raw_image.Convert(self.ND_pixelFormat, self.raw_conversion)

        #  populate the return dict
        image_data['data'] = converted_image.GetNDArray().copy()
        image_data['ok'] = True
        image_data['exposure'] = round(chunk_data.GetExposureTime())
        image_data['gain'] = round(chunk_data.GetGain(), 2)
        image_data['height'] = converted_image.GetHeight()
        image_data['width'] = converted_image.GetWidth()

        #  release the raw image
        try:
            raw_image.Release()
            converted_image.Release()
        except:
            pass

        #  and return the converted one
        return image_data


    def set_pixel_format(self, format):

        #  set the pixel format
        if self.cam.PixelFormat.GetAccessMode() == PySpin.RW:
            self.cam.PixelFormat.SetValue(self.pixelFormat)
        else:
            self.error.emit(self.camera_name, 'Specified pixel format: %d not available.' %
                    self.pixelFormat)
            return False
        return True


    @QtCore.pyqtSlot(list, str, bool, dict, bool, dict)
    def start_acquisition(self, cam_list, file_path, save_images, image_options,
            save_video, video_options):


        if self.acquiring:
           return

        #  check that we're supposed to start
        if (len(cam_list) > 0 and self not in cam_list):
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

        if PySpin.FLIR_SPINNAKER_VERSION_MAJOR > 2:
            #  create an instance of the PySpin ImageProcessor
            self.processor = PySpin.ImageProcessor()
            self.processor.SetColorProcessing(self.raw_conversion)
        else:
            #  PySpin versions < 3 don't have the processor class and conversion
            #  is handled by the raw_image class
            self.processor = None

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

            #  Set up the camera - first get the camera and stream nodemaps
            nodemap = self.cam.GetNodeMap()
            s_node_map = self.cam.GetTLStreamNodeMap()

            #  We're using an event callback tied to the exposure end event to signal when
            #  an image is ready to read from the buffer. Here we enable this event and
            #  create an instance of the callback object.

            #  Enable the end exposure event - Set up the camera to callback when it is done
            #  with an exposure. This allows us to optimize triggering and image retrieval.
            node_event_selector = PySpin.CEnumerationPtr(nodemap.GetNode('EventSelector'))

            #  Set the event selector to ExposureEnd
            exposure_end_node = PySpin.CEnumEntryPtr(node_event_selector.GetEntryByName('ExposureEnd'))
            node_event_selector.SetIntValue(exposure_end_node.GetValue())

            #  Set up the event notifications
            node_event_notification = PySpin.CEnumerationPtr(nodemap.GetNode('EventNotification'))
            node_event_notification_on = PySpin.CEnumEntryPtr(node_event_notification.GetEntryByName('On'))
            node_event_notification.SetIntValue(node_event_notification_on.GetValue())

            #  Now create the event handler object and register it with the camera
            self.end_exposure_ev_handler = CameraEventHandler('EventExposureEnd', self)
            self.cam.RegisterEventHandler(self.end_exposure_ev_handler, 'EventExposureEnd')

            # Retrieve Buffer Handling Mode Information
            handling_mode = PySpin.CEnumerationPtr(s_node_map.GetNode('StreamBufferHandlingMode'))

            # Ensure buffer is set to oldest first
            if self.check_node_accessibility(handling_mode):
                #handling_mode_entry = handling_mode.GetEntryByName('OldestFirst')
                handling_mode_entry = handling_mode.GetEntryByName('NewestOnly')
                handling_mode.SetIntValue(handling_mode_entry.GetValue())

            #  Enable chunk data for exposures and gain and set chunk data mode as active
            chunk_selector = PySpin.CEnumerationPtr(nodemap.GetNode('ChunkSelector'))
            exposure_entry = chunk_selector.GetEntryByName('ExposureTime')
            gain_entry = chunk_selector.GetEntryByName('Gain')
            chunk_selector.SetIntValue(exposure_entry.GetValue())
            chunk_enable = PySpin.CBooleanPtr(nodemap.GetNode('ChunkEnable'))
            chunk_enable.SetValue(True)
            chunk_selector.SetIntValue(gain_entry.GetValue())
            chunk_enable = PySpin.CBooleanPtr(nodemap.GetNode('ChunkEnable'))
            chunk_enable.SetValue(True)
            chunk_mode_active = PySpin.CBooleanPtr(nodemap.GetNode('ChunkModeActive'))
            if PySpin.IsAvailable(chunk_mode_active) and PySpin.IsWritable(chunk_mode_active):
                chunk_mode_active.SetValue(True)

            # Set acquisition mode to continuous
            self.cam.AcquisitionMode.SetValue(PySpin.AcquisitionMode_Continuous)

            #  Begin acquiring images
            self.cam.BeginAcquisition()
            self.acquiring = True

            #  clear out the camera's buffers - normally they should be empty
            #  but we check just to make sure.

            #  try to get any pending images.
            try:
                raw_image = self.cam.GetNextImage(1)
                while not raw_image.IsIncomplete():
                    raw_image.Release()
                    raw_image = self.cam.GetNextImage(1)
                raw_image.Release()
            except:
                pass

            #  Settings can take from 0 to 2 frames to take effect depending on
            #  the camera and setting. Here we'll flush a few images through the
            #  camera to make sure our first triggered image is acquired with
            #  the correct settings.
            self.__sync_settings(nodemap=nodemap)

            #  The cameras seem to randomly start in the middle of the HDR sequence
            #  but we want to trigger in order starting at Image1. We'll trigger the
            #  camera here until the next image will be Image1.
            if self.hdr_enabled:
                self.__sync_hdr(nodemap=nodemap)

            #  and emit the acquisitionStarted signal
            self.acquisitionStarted.emit(self, self.camera_name, True)

        except PySpin.SpinnakerException as ex:
            self.error.emit(self.camera_name, 'Start Acquisition Error: %s' % ex)
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
        '''
        The image_write_complete slot is called when the image_writer has
        finished writing each image/frame.
        '''
        #  re-emit as a camera signal
        self.imageSaved.emit(self, filename)


    @QtCore.pyqtSlot(str, str)
    def image_writer_error(self, camera_name, error_string):
        '''
        The image_writer_error slot is called when the image_writer runs into
        an error. We just pass this along...
        '''
        #  re-emit as a camera signal
        self.error.emit(self.camera_name, error_string)


    @QtCore.pyqtSlot(str,str, int, int, datetime.datetime, datetime.datetime)
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
            self.cam.EndAcquisition()
            self.acquiring = False

            #  Emit the stoppingAcquisition signal that we use to tell child threads
            #  to shut down
            self.stoppingAcquisition.emit()

            # We don't actually emit the acquisitionStopped signal here. We wait
            # for the image_writer to signal it has stopped before we signal that
            # acquisition has stopped.

        except PySpin.SpinnakerException as ex:
            self.error.emit(self.camera_name, 'Stop Acquisition Error: %s' % ex)
            self.acquisitionStopped.emit(self, self.camera_name, False)


    def set_white_balance(self):
        '''
        https://www.flir.com/support-center/iis/machine-vision/knowledge-base/achieving-greater-color-balance-across-multiple-cameras/
        https://www.flir.com/support-center/iis/machine-vision/knowledge-base/controlling-the-white-balance-of-your-camera/
        https://www.flir.com/support-center/iis/machine-vision/application-note/using-white-balance-with-blackfly-s-and-spinnaker/

        The white balancing coefficients can then be written as below:

        a = G / R   b = G / B

        The coefficients can then be calculated by using the average color channel values.

        Navigate to the Settings tab.
        Turn off Balance White Auto.
        Select the appropriate Balance Ratio Selector and change the value in Balance Ratio.
        Selecting Red in the Balance Ratio changes a and selecting Blue changes b.

        // Retrieve nodes for manually adjusting white balance settings.
        CEnumerationPtr ptrBalanceWhiteAuto = nodeMap.GetNode("BalanceWhiteAuto");
        CEnumEntryPtr ptrBalanceWhiteAutoOff = ptrBalanceWhiteAuto->GetEntryByName("Off");
        ptrBalanceWhiteAuto->SetIntValue(ptrBalanceWhiteAutoOff->GetValue());
        CEnumerationPtr ptrBalanceRatioSelector = nodeMap.GetNode("BalanceRatioSelector");
        CEnumEntryPtr ptrBalanceRatioSelectorRed = ptrBalanceRatioSelector->GetEntryByName("Red");
        ptrBalanceRatioSelector->SetIntValue(ptrBalanceRatioSelectorRed->GetValue());
        CFloatPtr ptrBalanceRatio = nodeMap.GetNode("BalanceRatio");
        ptrBalanceRatio->SetValue(1.5);
        '''

    def __sync_settings(self, nodemap=None):
        '''__sync_settings will trigger the camera a few times to push settings into the
        CMOS ASIC so the next trigger executed will return images with the specified
        settings. When in trigger mode, most CMOS cameras will require 1-2 triggers for
        a setting to be active. (Normally 1 trigger, but changing HDR settings and
        activating HDR will take 2 triggers.

        This is done by switching to software triggering, triggering a few times,
        discarding the images, then re-enabling the original trigger settings
        '''

        if nodemap is None:
            nodemap = self.cam.GetNodeMap()

        self._trig_mode = self.cam.TriggerMode.GetValue()
        self.cam.TriggerMode.SetValue(PySpin.TriggerMode_On)
        self._trig_source = self.cam.TriggerSource.GetValue()
        self.cam.TriggerSource.SetValue(PySpin.TriggerSource_Software)

        #  Disable event notifications
        node_event_notification = PySpin.CEnumerationPtr(nodemap.GetNode('EventNotification'))
        node_event_notification_on = PySpin.CEnumEntryPtr(node_event_notification.GetEntryByName('On'))
        node_event_notification_off = PySpin.CEnumEntryPtr(node_event_notification.GetEntryByName('Off'))
        node_event_notification.SetIntValue(node_event_notification_off.GetValue())

        #  trigger, get image, and discard
        for i in range(SpinCamera.SETTINGS_LAG):
            self.cam.TriggerSoftware.Execute()
            sleep(SpinCamera.HDR_SW_TRIG_DELAY / 1000.)
            try:
                _ = self.get_image()
            except:
                pass
        sleep(SpinCamera.HDR_SW_TRIG_DELAY / 1000.)

        #  Enable event notifications and restore the trigger state
        node_event_notification.SetIntValue(node_event_notification_on.GetValue())
        self.cam.TriggerSource.SetValue(self._trig_source)
        self.cam.TriggerMode.SetValue(self._trig_mode)


    def __sync_hdr(self, nodemap=None):
        '''__sync_HDR will trigger the camera (discarding any imaged) until the
        HDR sequence counter is pointing at the start of the sequence.

        Flir cameras seem to start at a random point in the HDR sequence and there
        isn't an obvious way of resetting it. We need to know where the camera is
        in the sequence in order to save the images with the correct exposure and
        gain data. This method will trigger the camera, advancing the camera
        through the sequence, until it pointed back at "Image1".

        This method will do this using software triggering. It will store the
        current trigger source and state, switch to software, and trigger. After
        the sync is complete the trigger settings are returned to their original
        state.
        '''

        if nodemap is None:
            nodemap = self.cam.GetNodeMap()

        self._trig_mode = self.cam.TriggerMode.GetValue()
        self.cam.TriggerMode.SetValue(PySpin.TriggerMode_On)
        self._trig_source = self.cam.TriggerSource.GetValue()
        self.cam.TriggerSource.SetValue(PySpin.TriggerSource_Software)

        #  Disable event notifications
        node_event_notification = PySpin.CEnumerationPtr(nodemap.GetNode('EventNotification'))
        node_event_notification_on = PySpin.CEnumEntryPtr(node_event_notification.GetEntryByName('On'))
        node_event_notification_off = PySpin.CEnumEntryPtr(node_event_notification.GetEntryByName('Off'))
        node_event_notification.SetIntValue(node_event_notification_off.GetValue())

        #  trigger, get image, and check if this image has the same exposure as
        #  HDR Image4. If not, continue to trigger until Image4 is obtained.
        self.cam.TriggerSoftware.Execute()
        sleep(SpinCamera.HDR_SW_TRIG_DELAY / 1000.)
        spin_image = self.get_image()
        #  Check for an exposure that is within 15 us of the commanded exposure for
        #  Image4. We allow for a 15 us difference because the actual exposure will
        #  rarely be the exact commanded exposure.
        while abs(spin_image['exposure'] - self.hdr_parameters["Image4"]['exposure']) > 15:
            self.cam.TriggerSoftware.Execute()
            sleep(SpinCamera.HDR_SW_TRIG_DELAY / 1000.)
            spin_image = self.get_image()
        sleep(SpinCamera.HDR_SW_TRIG_DELAY / 1000.)

        #  Enable event notifications and restore the trigger state
        node_event_notification.SetIntValue(node_event_notification_on.GetValue())
        self.cam.TriggerSource.SetValue(self._trig_source)
        self.cam.TriggerMode.SetValue(self._trig_mode)


    def get_device_info(self, nodemap, node='DeviceInformation'):
        """
        This function returns a dict that contains device information
        """

        dev_info = {}

        node_device_information = PySpin.CCategoryPtr(nodemap.GetNode(node))

        if PySpin.IsAvailable(node_device_information) and PySpin.IsReadable(node_device_information):
            features = node_device_information.GetFeatures()
            for feature in features:
                node_feature = PySpin.CValuePtr(feature)
                dev_info[node_feature.GetName()] = (node_feature.ToString()
                        if PySpin.IsReadable(node_feature) else 'Node not readable')

        return dev_info


    def check_node_accessibility(self, node, is_readable=True):
        """
        Helper for checking GenICam node accessibility

        :param node: GenICam node being checked
        :type node: CNodePtr
        :return: True if accessible, False otherwise
        :rtype: bool
        """

        return PySpin.IsAvailable(node) and (PySpin.IsReadable(node) or PySpin.IsWritable(node))



class CameraEventHandler(QtCore.QObject, PySpin.DeviceEventHandler):
    """
    This class defines the properties, parameters of the camera events event handler. This
    is adapted from the PySpin DeviceEvents.py example.

    Camera event handlers must inherit from PySpin.DeviceEventHandler but this class also
    inherits from QtCore.QObject so it can use signals/slots to call the camera's end
    exposure method in a thread safe way.
    """

    endExposure = QtCore.pyqtSignal(str)

    def __init__(self, eventname, cam_obj):
        """
        This constructor registers an event name to be used on device events.
        """
        super(CameraEventHandler, self).__init__()
        self.event_name = eventname

        #  connect the endExposure signal to the camera's end exposure method
        self.endExposure.connect(cam_obj.exposure_end)


    def OnDeviceEvent(self, eventname):
        """
        Callback function called when *any* device event occurs. This is adapted
        from the PySpin DeviceEvents.py example.

        Note eventname is a wrapped gcstring, not a Python string, but basic operations
        such as printing and comparing with Python strings are supported.
        """

        #  This is called for all camera events - I'm chosing to filter the events
        #  here but this could easily be modified to pass all events through to the
        #  camera and act on event_name if one wanted to enable multiple events.
        if eventname == self.event_name:
            self.endExposure.emit(self.event_name)

