# HEPiC Server

Server program for HEPiC. Collect / cache sensor data and send over to the client:

- extrusion force
- filament feed length / velocity

It is installed on a Raspberry Pi (the Klipper host device) as a systemd service.

## Installation

To install `hepic_server`, first clone the repository to your local Raspberry Pi

```
$ git clone https://github.com/ZLoverty/hepic_server.git
```

Then enter the folder, run the installation script as administrator

```
$ sudo ./install
```

The script will create a Python virtual environment in the `/opt` folder, and install a binary in there. The config file, which contains the PIN numbers of the rotary encoder and the IP address of the load cell host, is stored in `/etc/hepic_server/config.json`. If there numbers are different from the default, you need to manually edit this config file.

