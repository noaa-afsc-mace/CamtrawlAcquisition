
# CamtrawlAcquisition

**CamtrawlAcquisition is the main data acquisition application for the Camtrawl underwater stereo camera system.**

The Camtrawl system is a self-contained underwater stereo camera system mounted near the codend of a research trawl which provides information on species and size composition along the trawl path to inform scientists conducting fisheries research and stock assessment surveys. CamtrawlAcquisition is designed to collect the image and sensor data that is subsequently processed to provide the species and size data.

The system is comprised of a single board computer, two Flir machine vision cameras, LED strobes, and some custom power and control electronics we call the Camtrawl Controller. While on deck, the camera system is sleeping. When it is deployed, the SBC is turned on and the application starts and data is collected. As the system nears the surface at the end of the deployment the control electronics signal the acquisition application to exit and shut down the PC. A single run of the application is considered a collection event and all data for that event is contained within a single directory that is named after the time the application was started.


This package also contains a simplified acquisition application called (unimaginatively) SimpleAcquisition.py. This application does not require the Camtrawl Controller and can be used to collect data from one or more compatible cameras that are software triggered.

These applications are console based and use YAML files for configuration. **Everything is configured within the .yml files.** While formal documentation currently limited, the code and config files are heavily commented.

## Example
```plaintext
C:\CamTrawl\CamtrawlAcquisition>python SimpleAcquisition.py
2022-01-08 13:29:54,773 : Camtrawl Acquisition Starting...
2022-01-08 13:29:54,773 : CamtrawlAcquisition version: 4.0
2022-01-08 13:29:54,773 : Configuration file loaded: SimpleAcquisition.yml
2022-01-08 13:29:54,779 : Profiles file loaded: VideoProfiles.yml
2022-01-08 13:29:54,779 : Logging data to: C:\camtrawl\data\D20220108-T132954
2022-01-08 13:29:54,781 : Opening database file: C:\camtrawl\data\D20220108-T132954\logs\CamtrawlMetadata.db3
2022-01-08 13:29:54,803 : Python version: 3.8.9 (tags/v3.8.9:a743f81, Apr  2 2021, 11:10:41) [MSC v.1928 64 bit (AMD64)]
2022-01-08 13:29:54,803 : Numpy version: 1.20.2
2022-01-08 13:29:54,803 : OpenCV version: 4.5.3
2022-01-08 13:29:54,803 : PyQt5 version: 5.15.2
2022-01-08 13:29:56,961 : Spin library version: 2.5.0.80
2022-01-08 13:29:56,961 : Getting available cameras...
2022-01-08 13:29:57,418 : 1 camera found.
2022-01-08 13:29:57,418 : Configuring camera:
2022-01-08 13:29:57,518 :   Adding: Chameleon3 CM3-U3-31S4M_16081034
2022-01-08 13:29:57,522 :     Chameleon3 CM3-U3-31S4M_16081034: trigger divider: 1  save image divider: 1
2022-01-08 13:29:57,527 :     Chameleon3 CM3-U3-31S4M_16081034: Software triggering enabled.
2022-01-08 13:29:57,527 :     Chameleon3 CM3-U3-31S4M_16081034: label: camera  gain: 16  exposure_us: 8000  rotation:None
2022-01-08 13:29:57,532 :     Chameleon3 CM3-U3-31S4M_16081034: Saving stills as .jpg  Scale: 100
2022-01-08 13:29:57,532 :     Chameleon3 CM3-U3-31S4M_16081034: Saving video as .mkv  Video profile: x265-fast
2022-01-08 13:29:57,532 :     Chameleon3 CM3-U3-31S4M_16081034: Image data will be written to: C:\camtrawl\data\D20220108-T132954\images\Chameleon3 CM3-U3-31S4M_16081034
2022-01-08 13:29:57,532 : Camera setup complete.
2022-01-08 13:29:57,690 : Chameleon3 CM3-U3-31S4M_16081034: acquisition started.
2022-01-08 13:30:08,028 : Trigger limit of 150 triggers reached. Shutting down...
2022-01-08 13:30:09,023 : Chameleon3 CM3-U3-31S4M_16081034: acquisition stopped.
2022-01-08 13:30:09,023 : All cameras stopped.
2022-01-08 13:30:09,023 : Acquisition is Stopping...
2022-01-08 13:30:09,322 : Acquisition Stopped.
2022-01-08 13:30:09,322 : Application exiting...

C:\CamTrawl\CamtrawlAcquisition>
```


## Limitations

Currently these applications only work with Teledyne FLIR (formerly Point Grey Research) machine vision cameras compatible with their [Spinnaker SDK](https://www.flir.com/products/spinnaker-sdk/).  We are working to add support for V4L2 compatible cameras which includes Raspberry Pi CSI and many USB based cameras on linux.

## Python Dependencies

* Python > 3.5
* PyYAML
* PyQt5
* [Numpy](https://pypi.org/project/numpy/)
* PySpin (from [Flir Spinnaker SDK](https://www.flir.com/support-center/iis/machine-vision/downloads/spinnaker-sdk-and-firmware-download/))
* [OpenCV](https://pypi.org/project/opencv-python/)
* [protobuf](https://pypi.org/project/protobuf/)

ffmpeg is required if writing video files. Most linux distributions ship with ffmpeg. Windows users can download ffmpeg [here](https://www.ffmpeg.org/download.html). You must also review the ffmpeg_path setting in the application section of the configuration file to ensure that it is set correctly.


