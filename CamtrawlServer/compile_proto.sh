#!/bin/bash

inpath="/camtrawl/software/CamtrawlAcquisition/CamtrawlServer"
outpath="/camtrawl/software/CamtrawlAcquisition/CamtrawlServer"

/usr/local/bin/protoc -I=$inpath --python_out=$outpath $inpath/CamtrawlServer.proto
