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
.. module:: CamtrawlAcquisition.ImageWriter

    :synopsis: Class that handles writing image data to disk. Handles
               still and video file writing.

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
import subprocess as sp
import shlex
import numpy as np
from PyQt5 import QtCore
import cv2


class ImageWriter(QtCore.QObject):
    '''
    The ImageWriter class handles writing image data to disk for the
    camera classes. A camera will instantiate the writer and move it to
    its own thread and then pass images to it for writing.

    Credit use of subprocess and the ffmpeg binary to Rotem:

    https://stackoverflow.com/questions/61260182/how-to-output-x265-compressed-video-with-cv2-videowriter

    This is a work in progress...

    '''

    VIDEO_SUPPORTED_EXT = ['.avi', '.mp4', '.mpeg4', '.mkv']

    # specify the common video options. These are options that are explicitly
    # handled by the writer. Any options not in this list will be passed as
    # video encoder specific parameters to ffmpeg.
    VIDEO_COMMON_OPTIONS = ['encoder', 'framerate', 'pixel_format', 'max_frames_per_file',
            'scale', 'file_ext', 'ffmpeg_path', 'ffmpeg_debug_out']

    #  define PyQt Signals
    writeComplete = QtCore.pyqtSignal(str, str)
    writerStopped = QtCore.pyqtSignal(str)
    writerDebug = QtCore.pyqtSignal(str,str)
    error = QtCore.pyqtSignal(str, str)

    def __init__(self, camera_name, parent=None):

        super(ImageWriter, self).__init__(parent)

        self.camera_name = camera_name
        self.frame_number = 0
        self.is_recording = False
        self.ffmpeg_process = None
        self.ffmpeg_out = None
        self.filename = ''
        self.save_video = False
        self.video_options = {'encoder':'libx265',
                              'file_ext':'.mp4',
                              'preset':'fast',
                              'crf':26,
                              'pixel_format':'yuv420p',
                              'max_frames_per_file': 1000}

        self.save_images = True
        self.image_options = {'file_ext':'.jpg',
                              'jpeg_quality':90,
                              'scale':100}


    @QtCore.pyqtSlot(str, dict)
    def WriteImage(self, camera_name, image_data):
        '''The WriteImage slot writes image data to disk. It
        '''

        save_this_image = self.save_images and image_data['save_still'] and image_data['ok']
        if save_this_image:
            #  we're writing image files

            #  check if we should scale the image before writing
            if self.image_options['scale'] < 100 and self.image_options['scale'] > 0:
                scale = self.image_options['scale'] / 100.0
                scaled_image = cv2.resize(image_data['data'], (0,0), fx=scale, fy=scale,
                        interpolation=cv2.INTER_AREA)
            else:
                scaled_image = image_data['data']

            #  set the full file name
            if self.image_options['file_ext'][0] != '.':
                self.image_options['file_ext'] = '.' + self.image_options['file_ext']
            filename = image_data['filename'] + self.image_options['file_ext']

            #  check if this is an hdr image and convert if required
            if image_data['is_hdr']:
                #  it is, check if the output format supports full dynamic range
                if self.image_options['file_ext'] not in ['.hdr', '.pic', '.exr']:
                    #  need to convert to 24 bits for this format

                    tonemapDrago = cv2.createTonemapDrago(1.0, 1.5,0.85)
                    scaled_image = tonemapDrago.process(scaled_image)
                    #scaled_image = scaled_image2 * 255

                    scaled_image = np.clip(scaled_image*255, 0, 255).astype('uint8')

            try:
                #  write the image
                if self.image_options['file_ext'] in ['.jpg', '.jpeg']:
                    #  pass the quality flag for JPEGs
                    cv2.imwrite(filename, scaled_image, [int(cv2.IMWRITE_JPEG_QUALITY),
                            self.image_options['jpeg_quality']])
                else:
                    #  no options for this image type
                    cv2.imwrite(filename, scaled_image)

                self.writeComplete.emit(self.camera_name, self.filename)

            except Exception as ex:
                self.error.emit(self.camera_name, 'write_image Error: %s' % ex)


        #  check if we're writing a video frame
        if self.save_video and image_data['save_frame'] and image_data['ok']:

            #  check if we should scale the image before writing
            same_image = False
            if not save_this_image or (self.video_options['scale'] !=
                    self.image_options['scale']):

                if self.video_options['scale'] < 100 and self.video_options['scale'] > 0:
                    scale = self.video_options['scale'] / 100.0
                    scaled_image = cv2.resize(image_data['data'], (0,0), fx=scale, fy=scale,
                            interpolation=cv2.INTER_AREA)
                else:
                    #  no need to scale
                    scaled_image = image_data['data']
            else:
                same_image = True

            #  convert this HDR image if we haven't already
            if ((not same_image and image_data['is_hdr']) or
                    (same_image and self.image_options['file_ext'] in ['.hdr', '.pic', '.exr'])):

                #  TODO: implement tonemap conversion here too. Should just write a module to
                #        do this that can be used here and in SpinCamera.
                scaled_image = np.clip(scaled_image*255, 0, 255).astype('uint8')

            #  we're recording a video - check if one is currently open
            if self.is_recording:
                #  check if we've hit our max frame limit
                if (self.frame_number >= self.video_options['max_frames_per_file']):
                    #  yes, stop this file and start a new one
                    self.StopRecording(signal_stop=False)

                    #  start a new file
                    if self.video_options['file_ext'][0] != '.':
                        self.video_options['file_ext'] = '.' + self.video_options['file_ext']
                    filename = image_data['filename'] + self.video_options['file_ext']

                    self.StartRecording(filename, scaled_image.shape[1],
                            scaled_image.shape[0])
            else:
                #  we don't have a file open, start a new file
                if self.video_options['file_ext'][0] != '.':
                    self.video_options['file_ext'] = '.' + self.video_options['file_ext']
                filename = image_data['filename'] + self.video_options['file_ext']

                self.StartRecording(filename, scaled_image.shape[1],
                            scaled_image.shape[0])

            #  add this frame
            try:
                # increase the video frame counter
                self.frame_number = self.frame_number + 1

                # pass the image data to ffmpeg
                self.ffmpeg_process.stdin.write(scaled_image.tobytes())

                # emit the write complete signal
                self.writeComplete.emit(self.camera_name, self.filename)

            except Exception as ex:
                # there was a problem...
                self.error.emit(self.camera_name, 'write_image Error: %s' % ex)


    @QtCore.pyqtSlot(str, int, int)
    def StartRecording(self, filename, width, height):

        if (self.is_recording):
            #  first close the old file before starting a new one
            self.StopRecording()

        try:
            #  generate the base ffmpeg command string
            command_string = (f'ffmpeg -y -s {width}x{height} -pixel_format bgr24 ' +
                    f'-f rawvideo -r {self.video_options["framerate"]} -i pipe: -c:v ' +
                    f'{self.video_options["encoder"]}  ')

            #  insert the codec specific options
            for option, value in self.video_options.items():
                if option not in self.VIDEO_COMMON_OPTIONS:
                    command_string += f'-{option} {value} '

            #  add the pixel format
            command_string += f'-pix_fmt {self.video_options["pixel_format"]} '

            #  and end with the output file name
            command_string += "'" + filename + "'"

            #  emit the ffmpeg command string so we can log it
            self.writerDebug.emit(self.camera_name, 'Encoder started: ' + command_string)

            #  parse the command line args and add the
            command_args = shlex.split(command_string)

            if self.video_options["ffmpeg_path"] in [None, '']:
                #  no path passed, we're using whatever is on the system path
                command_args[0] = command_args[0]
            else:
                #  we've been passed a path so we need to add the separator
                command_args[0] = self.video_options["ffmpeg_path"] + os.sep + command_args[0]

            if self.video_options["ffmpeg_debug_out"]:
                out_filename = os.path.splitext(filename)[0] + '_debug.txt'
                self.ffmpeg_out = open(out_filename, 'w')
                self.ffmpeg_out.write(command_string)
                self.ffmpeg_process = sp.Popen(command_args, stdin=sp.PIPE, stderr=self.ffmpeg_out)
            else:
                #  send output to NULL
                self.ffmpeg_out = None
                #  On windows, sending to DEVNULL seems to eventually cause the ffmpeg process to
                #  hang. So here we'll try to write to a more portable devnull
                if os.name == 'nt':
                    self.ffmpeg_out = open(os.devnull, 'w')
                    self.ffmpeg_process = sp.Popen(command_args, stdin=sp.PIPE, stderr=self.ffmpeg_out)
                else:
                    #  on linux, this seems to work perfectly fine so we'll not change it
                    self.ffmpeg_process = sp.Popen(command_args, stdin=sp.PIPE, stderr=sp.DEVNULL)


            #  reset the frame counter and set the recording state
            self.frame_number = 0
            self.filename = filename
            self.is_recording = True

        except Exception as ex:
            self.ffmpeg_process = None
            self.is_recording = False
            self.error.emit(self.camera_name, 'Start Recording Error: %s' % ex)


    @QtCore.pyqtSlot()
    def StopRecording(self, signal_stop=True):
        '''the StopRecording slot will close the video file (if writing a video)
        and emit the writerStopped signal when done. If we're just writing stills
        there's nothing to close so this method just emits the signal.

        The signal_stop keyword is used internally to stop without signaling in
        cases where we need to roll the video file (close the old one and open a
        new one.) In this case we don't want to signal we've stopped.
        '''

        if (self.is_recording):
            try:

                if self.ffmpeg_process:
                    # Close and flush stdin
                    self.ffmpeg_process.stdin.close()
                    # Wait for sub-process to finish
                    self.ffmpeg_process.wait()
                    # Terminate the sub-process
                    self.ffmpeg_process.terminate()

                if self.ffmpeg_out:
                    self.ffmpeg_out.close()

                self.ffmpeg_process = None
                self.ffmpeg_out = None
                self.frame_number = 0
                self.filename = ''
                self.is_recording = False
                if signal_stop:
                    self.writerStopped.emit(self.camera_name)
            except Exception as ex:
                self.error.emit(self.camera_name, 'Stop Recording Error: %s' % ex)
        else:
            #  If we're not recording a video, there is nothing to close
            #  so just emit the signal.
            if signal_stop:
                self.writerStopped.emit(self.camera_name)

