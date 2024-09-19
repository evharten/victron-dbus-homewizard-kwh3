#!/usr/bin/python3

import asyncio
import os.path
import requests
import sys
import time

try:
	from dbus_fast.aio import MessageBus
	from dbus_fast.constants import BusType
except ImportError:
	from dbus_next.aio import MessageBus
	from dbus_next.constants import BusType
from aiovelib.service import Service, IntegerItem, DoubleItem, TextItem


ROLES = ('grid', 'pvinverter')

class HwDbusBridge:
    '''Homewizard Energy kWh meter to D-Bus bridge'''

    def __init__(self, ip, role, dev_idx, phase, position, name, maxpower=None, pollinterval=1):
        # Sanity checks
        assert isinstance(ip, str)
        assert role in ROLES
        assert isinstance(dev_idx, int)
        assert isinstance(phase, int) and 1 <= phase <= 3
        assert isinstance(position, int) and 0 <= position <= 2
        assert isinstance(name, str)

        self.url = f"http://{ip}/api/v1/data"
        self.role = role
        self.dev_idx = dev_idx
        self.position = position
        self.maxpower = maxpower
        self.pollinterval = pollinterval
        self.dbus_name = f'com.victronenergy.{role}.{name}'
        self.phase = phase
        self.service = None

    async def register_dbus(self):
        # Setup the dbus service for the session bus
        await self.unregister_dbus()
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        service = Service(bus, self.dbus_name)

        # Create the generic dbus service objects
        service.add_item(TextItem('/ProductName', 'Homewizard kWh meter'))
        service.add_item(TextItem('/Mgmt/ProcessName', os.path.basename(os.path.dirname(__file__))))
        service.add_item(TextItem('/Mgmt/ProcessVersion', 'Unknown version'))
        service.add_item(TextItem('/Mgmt/Connection', 'Ethernet'))
        service.add_item(IntegerItem('/Connected', 0, writeable=True))
        service.add_item(IntegerItem('/DeviceInstance', self.dev_idx))
        service.add_item(IntegerItem('/ProductId', 45069))  # Carlo Gavazzi ET 340 Energy Meter
        service.add_item(IntegerItem('/DeviceType', 345))   # ET340 Energy Meter
        service.add_item(TextItem('/FirmwareVersion', "0.1"))
        service.add_item(TextItem('/HardwareVersion', None))
        service.add_item(TextItem('/Role', self.role))
        service.add_item(TextItem('/Serial', None))
        service.add_item(IntegerItem('/ErrorCode', 0, writeable=True))
        service.add_item(IntegerItem('/StatusCode', 0, writeable=True))
        if self.role == 'pvinverter':
            service.add_item(IntegerItem('/Position', self.position))
            if isinstance(self.maxpower, int):
                service.add_item(IntegerItem('/MaxPower', self.maxpower))

        # String formatters for dbus Item based intances
        unit_kwh = lambda v: "{:.2f}kWh".format(v)
        unit_watt = lambda v: "{:.0f}W".format(v)
        unit_volt = lambda v: "{:.1f}V".format(v)
        unit_amp = lambda v: "{:.1f}A".format(v)

        # Create meter dbus objects
        service.add_item(DoubleItem('/Ac/Energy/Forward', None, writeable=True, text=unit_kwh))
        service.add_item(DoubleItem('/Ac/Energy/Reverse', None, writeable=True, text=unit_kwh))
        service.add_item(DoubleItem('/Ac/Power', None, writeable=True, text=unit_watt))

        # Victron software only accepts 1 or 3 phase devices. If we have a
        # 1 phase device that's connected to phase 2 or 3 we have to register a
        # 3 phase device. In that case we'll initialize the unused phase items
        # with 0 and don't update them.
        for i in range(1, 4):
            prefix = f'/Ac/L{i}'
            if i == self.phase:
                initial = None
                writable = True
            else:
                initial = 0
                writable = False
            service.add_item(DoubleItem(prefix + '/Voltage', initial, writeable=writable, text=unit_volt))
            service.add_item(DoubleItem(prefix + '/Current', initial, writeable=writable, text=unit_amp))
            service.add_item(DoubleItem(prefix + '/Power', initial, writeable=writable, text=unit_watt))
            service.add_item(DoubleItem(prefix + '/Energy/Forward', initial, writeable=writable, text=unit_kwh))
            service.add_item(DoubleItem(prefix + '/Energy/Reverse', initial, writeable=writable, text=unit_kwh))
        await service.register()
        self.service = service

    async def unregister_dbus(self):
        if self.service is not None:
            self.service.__del__()
        self.service = None

    def __get_hw_data(self):
        try:
            response = requests.get(self.url, timeout=3)
        except requests.exceptions.Timeout:
            print(f"No responce on our GET request to {url}", file=sys.stderr)
            return None
        try:
            data = response.json()
        except requests.exceptions.JSONDecodeError:
            print(f"Unexpected response from {self.url}", file=sys.stderr)
            return None
        return data

    async def get_hw_data(self, loop=None):
        loop = loop or asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.__get_hw_data)

    def update_dbus(self, data):
        try:
            energy_exp = data['total_power_export_kwh']
            energy_imp = data['total_power_import_kwh']
            power = data['active_power_w']
            voltage = data['active_voltage_v']
            current = data['active_current_a']
        except (KeyError, TypeError) as exc:
            # data==None or is missing essential keys
            energy_exp = energy_imp = power = voltage = current = None
            connected = 0
        else:
            connected = 1

        if self.role == 'pvinverter':
            # Power is expected to be as seen from the inverter stand point
            # not from the grid so we need to inverse the readings.
            power = power * -1
            energy_fw = energy_exp
            energy_rv = energy_imp
        else:
            energy_fw = energy_imp
            energy_rv = energy_exp

        with self.service as ctx:
            # Generic
            ctx["/Connected"] = connected

            # Phase data
            prefix = f'/Ac/L{self.phase}'
            ctx[prefix + "/Voltage"] = voltage
            ctx[prefix + "/Current"] = current
            ctx[prefix + "/Power"] = power
            ctx[prefix + "/Energy/Forward"] = energy_fw
            ctx[prefix + "/Energy/Reverse"] = energy_rv

            # Totals
            ctx["/Ac/Power"] = power
            ctx["/Ac/Energy/Forward"] = energy_fw
            ctx["/Ac/Energy/Reverse"] = energy_rv

    async def run(self):
        loop = asyncio.get_running_loop()
        await self.register_dbus()

        while True:
            start = time.time()
            data = await self.get_hw_data(loop)
            self.update_dbus(data)

            if data is None:
                await asyncio.sleep(5)  # No data.. Re-poll in 5 seconds
            else:
                loop_duration = time.time() - start
                await asyncio.sleep(self.pollinterval - loop_duration)
