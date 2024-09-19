#!/usr/bin/python3

import argparse
import asyncio
import sys
import os.path
import re

dirname = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(1, os.path.join(dirname, "ext", "aiovelib"))

from .bridge import HwDbusBridge, ROLES

parser = argparse.ArgumentParser()
parser.add_argument('ip', metavar='IP', type=str,
    help="IP address of a Homewizard kWh meter")
parser.add_argument('--role', default="pvinverter", choices=ROLES,
    help="What is the source of the power the device measures")
parser.add_argument('--instance', metavar='NUMBER', type=int, default=10,
    help="Unique device instance number")
parser.add_argument('--phase', type=int, default=1, choices=(1, 2, 3),
    help="Phase number we're monitoring (In case of single phase meter) [default: %(default)d]")
parser.add_argument('--position', type=int, default=0, choices=(0, 1, 2),
    help="0=AC input 1; 1=AC output; 2=AC input 2  [default: %(default)d]")
parser.add_argument('--name', default='homewizard',
    help="Unique postfix for the dbus service name for separation and identification")
parser.add_argument('--maxpower', type=int,
    help="Max rated power (in Watts) of the inverter")
parser.add_argument('--pollinterval', type=float, default=1,
    help="Poll interval in seconds. Should be no less than 0.5")
args = parser.parse_args()

if not re.match(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", args.ip):
    print(f"Invalid IP address {args.ip!r}")
    sys.exit(1)

if args.role == 'grid' and args.maxpower is not None:
    print("Warning: The --maxpower argument is ignored for the 'grid' role")

if args.pollinterval < 0.5:
    print("Warning: Homewizard advises against poll intervals below 500ms")
    if args.pollinterval < 0.2:
        print("Error: Poll interval is too low (to fast)")
        sys.exit(2)

bridge = HwDbusBridge(args.ip, args.role, args.instance, args.phase,
                      args.position, args.name, args.maxpower, args.pollinterval)
asyncio.run(bridge.run())
