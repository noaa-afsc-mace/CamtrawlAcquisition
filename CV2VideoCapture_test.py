
"""
piCamera.py


Rick Towler
MACE Group
NOAA Alaska Fisheries Science Center

"""


import cv2
import time
#import v4l2

cam = cv2.VideoCapture()
cam.open(1)

#cam.open(0, apiPreference=cv2.CAP_V4L2)

#cam.set(cv2.CAP_PROP_AUTO_WB, 0)
autoexposure = 0
cam.set(cv2.CAP_PROP_AUTO_EXPOSURE, autoexposure)

#  -11 to 1  = 2 ** -
time.sleep(1)
exposure = -9
cam.set(cv2.CAP_PROP_EXPOSURE, exposure)





cv2.namedWindow("Video")
#cv2.createTrackbar("FPS", "Video", fps, 30, lambda v: cam.set(cv2.CAP_PROP_FPS, v))
#cv2.createTrackbar("Focus", "Video", focus, 100, lambda v: cam.set(cv2.CAP_PROP_FOCUS, v / 100))


while True:

    #  use grab as a software trigger, then call retrieve to get the
    #  data after each camera is triggered.
    ret = cam.grab()
    ret, frame = cam.retrieve()

    if not ret:
        print("failed to grab frame")
    else:
        x = cam.get(cv2.CAP_PROP_EXPOSURE)
        y = cam.get(cv2.CAP_PROP_AUTO_EXPOSURE)
        cv2.putText(frame, "AutoExp: {}".format(y), (15, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0))
        cv2.putText(frame, "exposure: {}".format(x), (15, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0))
        cv2.imshow("Video", frame)

    k = cv2.waitKey(1)

    if k == 27:
        break
    elif k == 43:
        exposure += 1
        if exposure > 1:
            exposure = 1
        cam.set(cv2.CAP_PROP_EXPOSURE, exposure)

    elif k == 45:
        exposure -= 1
        if exposure < -11:
            exposure = -11
        cam.set(cv2.CAP_PROP_EXPOSURE, exposure)

    elif k == 97:
        if autoexposure == 1:
            autoexposure=0
        else:
            autoexposure=1

        cam.set(cv2.CAP_PROP_AUTO_EXPOSURE, autoexposure)




cam.release()
cv2.destroyAllWindows()

