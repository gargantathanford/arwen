#!/bin/sh
# PX4 v1.16.0 SIH instance 0 only. Run inside PX4's rootfs working directory.
. etc/init.d-posix/rcS
mavlink stream -u 14580 -s HEARTBEAT -r 5
mavlink stream -u 14580 -s LOCAL_POSITION_NED -r 20
mavlink stream -u 14580 -s EXTENDED_SYS_STATE -r 5
mavlink stream -u 14580 -s ODOMETRY -r 20
mavlink stream -u 14580 -s ESTIMATOR_STATUS -r 10
