REM Simple script to compile the ProtoBuff .proto file

set THISDIR=%~dp0..

set SRC_DIR=%THISDIR%\CamtrawlServer
set DST_DIR=%THISDIR%\CamtrawlServer

protoc -I=%SRC_DIR% --python_out=%DST_DIR% %SRC_DIR%\CamtrawlServer.proto
rem protoc -I=%SRC_DIR% --cpp_out=%DST_DIR% %SRC_DIR%\CamtrawlServer.proto
