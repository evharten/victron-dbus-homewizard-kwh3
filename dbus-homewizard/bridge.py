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

        self.ip = ip
        self.api_url = f"http://{ip}/api"
        self.data_url = self.api_url + "/v1/data"
        self.role = role
        self.dev_idx = dev_idx
        self.position = position
        self.maxpower = maxpower
        self.pollinterval = pollinterval
        self.dbus_name = f'com.victronenergy.{role}.{name}'
        self.phase = phase
        self.service = None

    async def register_dbus(self, serial, product, fw_version):
        # Setup the dbus service for the session bus
        await self.unregister_dbus()
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        service = Service(bus, self.dbus_name)

        # Create the generic dbus service objects
        service.add_item(TextItem('/ProductName', f'HomeWizard {product}'))
        service.add_item(TextItem('/Mgmt/ProcessName', os.path.basename(os.path.dirname(__file__))))
        service.add_item(TextItem('/Mgmt/ProcessVersion', 'Unknown version'))
        service.add_item(TextItem('/Mgmt/Connection', 'Ethernet'))
        service.add_item(IntegerItem('/Connected', 0, writeable=True))
        service.add_item(IntegerItem('/DeviceInstance', self.dev_idx))
        service.add_item(IntegerItem('/ProductId', 45069))  # Carlo Gavazzi ET 340 Energy Meter
        service.add_item(IntegerItem('/DeviceType', 345))   # ET340 Energy Meter
        service.add_item(TextItem('/FirmwareVersion', fw_version))
        service.add_item(TextItem('/HardwareVersion', None))
        service.add_item(TextItem('/Role', self.role))
        service.add_item(TextItem('/Serial', serial))
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
            #service.add_item(DoubleItem(prefix + '/Energy/Forward', initial, writeable=writable, text=unit_kwh))
            #service.add_item(DoubleItem(prefix + '/Energy/Reverse', initial, writeable=writable, text=unit_kwh))
        await service.register()
        self.service = service

    async def unregister_dbus(self):
        if self.service is not None:
            self.service.__del__()
        self.service = None

    def __get_hw(self, url):
        try:
            response = requests.get(url, timeout=3)
        except requests.exceptions.Timeout:
            print(f"No responce on our GET request to {url}", file=sys.stderr)
            return None
        try:
            data = response.json()
        except requests.exceptions.JSONDecodeError:
            print(f"Unexpected response from {url}", file=sys.stderr)
            return None
        return data

    async def get_hw_data(self, loop=None):
        loop = loop or asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.__get_hw, self.data_url)

    async def get_hw_info(self, loop=None):
        loop = loop or asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.__get_hw, self.api_url)

    def update_dbus(self, data):
        try:
            energy_exp = data['total_power_export_kwh']
            energy_imp = data['total_power_import_kwh']
            power = data['active_power_w']
            power_l1 = data['active_power_l1_w']
            voltage_l1 = data['active_voltage_l1_v']
            current_l1 = data['active_current_l1_a']
            power_l2 = data['active_power_l2_w']
            voltage_l2 = data['active_voltage_l2_v']
            current_l2 = data['active_current_l2_a']
            power_l3 = data['active_power_l3_w']
            voltage_l3 = data['active_voltage_l3_v']
            current_l3 = data['active_current_l3_a']
        except (KeyError, TypeError) as exc:
            # data==None or is missing essential keys
            power_l1 = power_l2 = power_l3 = voltage_l1 = voltage_l2 = voltage_l3 = current_l1 = current_l2 = current_l3 = energy_exp = energy_imp = power = None
            connected = 0
        else:
            connected = 1

        if self.role == 'pvinverter':
            # Power is expected to be as seen from the inverter stand point
            # not from the grid so we need to inverse the readings.
            power = power * -1
            power_l1 = power_l1 * -1
            power_l2 = power_l2 * -1
            power_l3 = power_l3 * -1

            energy_fw = energy_exp
            energy_rv = energy_imp
        else:
            energy_fw = energy_imp
            energy_rv = energy_exp

        with self.service as ctx:
            # Generic
            ctx["/Connected"] = connected

            # Phase data
            ctx["/Ac/L1" + "/Voltage"] = voltage_l1
            ctx["/Ac/L1" + "/Current"] = current_l1
            ctx["/Ac/L1" + "/Power"] = power_l1
            ctx["/Ac/L2" + "/Voltage"] = voltage_l2
            ctx["/Ac/L2" + "/Current"] = current_l2
            ctx["/Ac/L2" + "/Power"] = power_l2
            ctx["/Ac/L3" + "/Voltage"] = voltage_l3
            ctx["/Ac/L3" + "/Current"] = current_l3
            ctx["/Ac/L3" + "/Power"] = power_l3

            # Totals
            ctx["/Ac/Power"] = power
            ctx["/Ac/Energy/Forward"] = energy_fw
            ctx["/Ac/Energy/Reverse"] = energy_rv

    async def run(self):
        loop = asyncio.get_running_loop()

        print(f"Contacting HomeWizard device at {self.ip}...")
        while True:
            info = await self.get_hw_info(loop)
            if info is not None:
                break
            await asyncio.sleep(5)

        serial = info['serial']
        fw_version = info['firmware_version']
        api_version = info['api_version']
        product_type = info['product_type']

        print(f"Found HomeWizard {product_type} with serial {serial}")
        if api_version != "v1":
            print("ERROR: The device has an unsupported api "
                  f"version: {api_version}", file=sys.stderr)
            sys.exit(1)

        # Register the HomeWizard device with the Victron services
        await self.register_dbus(serial, product_type, fw_version)

        while True:
            start = time.time()
            data = await self.get_hw_data(loop)
            self.update_dbus(data)

            if data is None:
                await asyncio.sleep(5)  # No data.. Re-poll in 5 seconds
            else:
                loop_duration = time.time() - start
                await asyncio.sleep(self.pollinterval - loop_duration)
