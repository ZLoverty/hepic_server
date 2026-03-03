# v1.0.x

## v1.0.0

- Unified interface for gateways and sensors, in order to simplify the process of adding / modifying sensors. We now use config file to list all the sensor related configurations, and the code automatically parse these configurations. NOTE: the data interface has changed, so HEPiC < 1.0.0 can no longer parse the data sent by the server. If you have trouble reading sensor data using HEPiC, check the version.