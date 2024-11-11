This project registers your Homewizard kWh meter with a victron GX device.

The kWh meter can be configured as a PV meter or a gridmeter.

Steps to make this work:
 1. Copy the files from this repository to the data partition on your GX device.
 2. Play with run.sh untill you'r happy with the results. See `run.sh -h` for help on command line options for customization.
 3. To enable your service at every (re-)boot, replace `run.sh` in your command with `install.sh` while keeping the command line arguments the same.
