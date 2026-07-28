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

The config file, `config.json` in the repo root, contains the PIN numbers of
the rotary encoder and the IP address of the load cell host, along with
`sensors_config.yaml` for sensor definitions. If these differ from the
defaults, edit them before (or after) running `install.sh`, then restart the
service:

```
$ sudo systemctl restart hepic_server
```

To pick up code updates later, `git pull` and rerun `./install.sh`.

