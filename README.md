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

Then enter the folder and run the installation script as the user the service
should run under (e.g. `pi`), **not** as root -- it calls `sudo` itself for the
specific steps that need it:

```
$ cd hepic_server
$ ./install.sh
```

The script will:

1. install the `python3-venv` system package
2. create a Python virtual environment at `.venv` inside the repo and install
   `hepic_server` into it
3. install and start the `hepic_server` systemd service (via
   `scripts/install_service.sh`), running in place out of the repo checkout

The live config lives in `/etc/hepic_server/` (`config.json` and
`sensors_config.yaml`), not in the repo checkout. `config.json` contains the
PIN numbers of the rotary encoder and the IP address of the load cell host;
`sensors_config.yaml` has the sensor definitions. On first install, these are
seeded from the copies in the repo root; if they already exist in
`/etc/hepic_server/`, `install.sh` leaves them untouched, so a later `git
pull` + `./install.sh` never clobbers a device's live config. Edit the
`/etc/hepic_server/` copies directly (before or after running `install.sh`),
then restart the service:

```
$ sudo systemctl restart hepic_server
```

To pick up code updates later, `git pull` and rerun `./install.sh`.

